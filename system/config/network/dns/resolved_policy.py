"""Keep the managed TUN out of resolved's default resolver selection."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time

# `tailscale status --json` waits on the daemon; one second was shorter than a
# busy daemon's normal answer and every miss reverted the link.
TAILNET_STATUS_TIMEOUT = 5
TAILNET_GRACE_SECONDS = 135
MAX_PRIVATE_SUFFIXES = 128
TAILNET_SUFFIX = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.ts\.net")


def boottime():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def write(path, content):
    pending = path.with_name(path.name + ".new")
    pending.write_text(content)
    pending.replace(path)


def read(path):
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


def read_object(path):
    try:
        value = json.loads(read(path))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def private_suffixes(path):
    names = set(read(path).splitlines())
    if len(names) > MAX_PRIVATE_SUFFIXES or not all(TAILNET_SUFFIX.fullmatch(name) for name in names):
        raise ValueError("invalid private tailnet suffix history")
    return names


@contextmanager
def servers_lock(settings):
    with Path(settings["server_file"]).with_name("servers.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def publish_servers(settings, base, suffixes, start, run):
    """Publish the mode's upstreams with exact local-only MagicDNS domains."""
    path = Path(settings["server_file"])
    history = sorted(suffixes)
    # servers-file accepts server= entries. A local-only route for the suffix
    # applies to every record type, including HTTPS and TXT.
    protected = "".join(f"server=/{suffix}/\n" for suffix in history)
    config = base.rstrip("\n") + "\n" + protected
    stamp = path.with_name("servers.applied.json")
    previous = read_object(stamp)
    checksum = hashlib.sha256(config.encode()).hexdigest()
    systemctl = settings["systemctl"]

    def control(*args):
        return run([systemctl, *args], timeout=8, capture_output=True, text=True, check=True)

    present = read(path) == config
    if not present:
        write(path, config)
    pid = control("show", "-p", "MainPID", "--value", "dnsmasq.service").stdout.strip()
    if not pid or pid == "0":
        if not start:
            return
        control("start", "dnsmasq.service")
    else:
        if present and previous == {"digest": checksum, "history": history, "pid": pid}:
            return
        # TCP handlers fork and retain their old view across SIGHUP. Close
        # them when a newly learned suffix first becomes private.
        if history and previous.get("history") != history:
            # The forwarder is already running. Reuse its existing
            # prerequisites and avoid recursively starting dns-upstream.
            control("restart", "--job-mode=ignore-dependencies", "dnsmasq.service")
        else:
            control("reload", "dnsmasq.service")
    pid = control("show", "-p", "MainPID", "--value", "dnsmasq.service").stdout.strip()
    if not pid or pid == "0":
        raise ValueError("DNS publisher did not obtain a running forwarder")
    write(stamp, json.dumps({"digest": checksum, "history": history, "pid": pid}) + "\n")
    run([settings["resolvectl"], "flush-caches"], timeout=3, capture_output=True, check=False)


def publish_mode(settings, base, run=subprocess.run):
    history = Path(settings["tailnet"]["history_file"])
    with servers_lock(settings):
        base_path = Path(settings["server_file"]).with_name("base-servers.conf")
        if read(base_path) != base:
            write(base_path, base)
        publish_servers(settings, base, private_suffixes(history), True, run)


def tailnet_scope(status):
    if not isinstance(status, dict) or not isinstance(status.get("BackendState"), str):
        raise ValueError("tailnet backend state missing")
    if status["BackendState"] in {"Stopped", "NeedsLogin", "NoState", "NeedsMachineAuth"}:
        return None
    if status["BackendState"] != "Running":
        raise ValueError("tailnet backend is still connecting")
    netmap = status.get("CurrentTailnet")
    if not isinstance(netmap, dict) or not isinstance(netmap.get("MagicDNSEnabled"), bool):
        raise ValueError("tailnet DNS state missing")
    if not netmap["MagicDNSEnabled"]:
        return None
    suffix = netmap.get("MagicDNSSuffix")
    if not isinstance(suffix, str):
        raise ValueError("tailnet DNS suffix missing")
    suffix = suffix.lower().removesuffix(".")
    if not TAILNET_SUFFIX.fullmatch(suffix):
        return None
    user = (status.get("Self") or {}).get("UserID")
    # Older control servers can omit StableID. The authenticated name and
    # account still identify their netmap, together with its exact DNS suffix.
    identity = netmap.get("StableID") or netmap.get("Name")
    if type(user) is not int or user <= 0 or not isinstance(identity, str) or not 0 < len(identity) <= 253:
        raise ValueError("tailnet account identity missing")
    digest = hashlib.sha256(json.dumps([identity, user, suffix]).encode()).hexdigest()
    return {"suffix": suffix, "scope": digest}


def network_lease(settings, index=None, until=0, epoch="", clock=boottime, run=subprocess.run):
    """Replace the interface-scoped MagicDNS wire grant under the policy lock."""
    with Path(settings["policy_lock"]).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        awake = not Path(settings["sleeping"]).exists() and epoch == sleep_epoch(settings)
        remaining = int((until - clock()) * 1000) if index is not None and awake else 0
        nft = "flush set inet lf_filter magic_dns_link\n"
        if remaining > 0:
            nft += f"add element inet lf_filter magic_dns_link {{ {index} timeout {remaining}ms }}\n"
        run([settings["nft"], "-f", "-"], input=nft, timeout=4, capture_output=True, text=True, check=True)
        return remaining > 0


def sleep_epoch(settings):
    return read(Path(settings["policy_lock"]).with_name("authorization-epoch")).strip() or "0"


def reconcile_tun(settings, check=False, run=subprocess.run):
    def command(*args):
        return run(args, text=True, capture_output=True, timeout=3, check=True).stdout.strip()
    try:
        command(settings["ip"], "link", "show", "dev", settings["tun"])
    except subprocess.CalledProcessError:
        return
    dns = command(settings["resolvectl"], "dns", settings["tun"])
    suffixes = command(settings["resolvectl"], "domain", settings["tun"])
    # Both commands print "Link N (name):" even when the value is empty.
    dirty = bool(dns.partition(":")[2].strip() or suffixes.partition(":")[2].strip())
    if check:
        if dirty:
            raise ValueError("TUN has independent resolved DNS configuration")
    else:
        if dirty:
            command(settings["resolvectl"], "revert", settings["tun"])
        command(settings["resolvectl"], "default-route", settings["tun"], "no")


def reconcile_tailnet(settings, check=False, run=subprocess.run, clock=boottime):
    tailnet = settings.get("tailnet")
    if not tailnet:
        return

    def command(*args, timeout=3):
        return run(args, text=True, capture_output=True, timeout=timeout, check=True).stdout.strip()

    path = Path(tailnet["domain_file"])
    history = Path(tailnet["history_file"])
    interface = tailnet["interface"]

    def ifindex():
        try:
            links = json.loads(command(settings["ip"], "-j", "link", "show", "dev", interface))
        except subprocess.CalledProcessError:
            return None
        if (not isinstance(links, list) or len(links) != 1 or links[0].get("ifname") != interface
                or type(links[0].get("ifindex")) is not int or not 0 < links[0]["ifindex"] <= 2**31 - 1):
            raise ValueError("tailnet link identity missing")
        return links[0]["ifindex"]

    index = ifindex()
    old = read_object(path)
    # Upgrade the previous plain-suffix file as a local-only rule. It lacks an
    # interface and expiration, so it cannot authorize traffic.
    legacy = read(path).strip()
    legacy = legacy if TAILNET_SUFFIX.fullmatch(legacy) else ""
    epoch = sleep_epoch(settings)
    candidate = None
    if tailnet["enabled"] and index is not None and not Path(settings["sleeping"]).exists():
        try:
            scope = tailnet_scope(json.loads(command(tailnet["cli"], "status", "--json", "--peers=false",
                                                      timeout=TAILNET_STATUS_TIMEOUT)))
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
            until = old.get("until", 0)
            if (old.get("ifindex") == index and old.get("sleep_epoch") == epoch
                    and type(until) in (int, float) and math.isfinite(until)
                    and 0 < until - clock() <= TAILNET_GRACE_SECONDS
                    and TAILNET_SUFFIX.fullmatch(str(old.get("suffix", "")))):
                candidate = old
        else:
            if scope:
                candidate = dict(scope, ifindex=index, until=clock() + TAILNET_GRACE_SECONDS, sleep_epoch=epoch)
    if candidate and ifindex() != index:
        index, candidate = None, None

    expected = candidate["suffix"] if candidate else ""
    same_scope = candidate is not None and all(old.get(k) == candidate[k]
        for k in ("suffix", "scope", "ifindex", "sleep_epoch")) and old.get("until", 0) > clock()
    domains = private_suffixes(history)
    if check:
        if legacy or old.get("suffix", "") != expected or expected and not same_scope:
            raise ValueError("tailnet DNS differs from the authenticated lease")
    else:
        if old and not same_scope:
            path.unlink(missing_ok=True)
            network_lease(settings, clock=clock, run=run)
        if legacy:
            domains.add(legacy)
        if expected:
            if expected not in domains and len(domains) >= MAX_PRIVATE_SUFFIXES:
                raise ValueError("private tailnet suffix history is full")
            domains.add(expected)
        if domains != private_suffixes(history):
            # Both simultaneous DNS publishers must retain this protection.
            # Do it before exposing the suffix or removing the old route.
            with servers_lock(settings):
                present = private_suffixes(history)
                present |= domains
                if len(present) > MAX_PRIVATE_SUFFIXES:
                    raise ValueError("private tailnet suffix history is full")
                write(history, "".join(name + "\n" for name in sorted(present)))
                base = Path(settings["server_file"]).with_name("base-servers.conf")
                publish_servers(settings, read(base), present, False, run)
        elif domains:
            with servers_lock(settings):
                # Retry a failed dispatch/restart before changing the route.
                base = Path(settings["server_file"]).with_name("base-servers.conf")
                publish_servers(settings, read(base), private_suffixes(history), False, run)
        if not same_scope and not old:
            path.unlink(missing_ok=True)
            if legacy or candidate:
                network_lease(settings, clock=clock, run=run)

    if index is None:
        return
    # Numeric link selection prevents a stale name from identifying a newly
    # created device during the D-Bus handoff.
    link = str(index)
    dns = command(settings["resolvectl"], "dns", link).partition(":")[2].split()
    domains = command(settings["resolvectl"], "domain", link).partition(":")[2].split()
    route = command(settings["resolvectl"], "default-route", link).partition(":")[2].strip()
    expected_dns = [tailnet["resolver"]] if candidate else []
    dirty = dns != expected_dns or domains != ([expected] if candidate else []) or route != "no"
    if check:
        if dirty:
            raise ValueError("tailnet DNS differs from the authenticated conditional route")
        return
    if dirty:
        path.unlink(missing_ok=True)
        if same_scope:
            network_lease(settings, clock=clock, run=run)
        command(settings["resolvectl"], "revert", link)
        command(settings["resolvectl"], "default-route", link, "no")
        if candidate:
            command(settings["resolvectl"], "domain", link, expected)
            command(settings["resolvectl"], "dns", link, tailnet["resolver"])
    if dirty or not same_scope and (old or legacy or candidate):
        command(settings["resolvectl"], "flush-caches")
    if candidate and ifindex() == index and network_lease(
            settings, index, candidate["until"], epoch, clock, run):
        write(path, json.dumps(candidate) + "\n")


def reconcile(settings, check=False, run=subprocess.run):
    try:
        reconcile_tailnet(settings, check, run)
    finally:
        reconcile_tun(settings, check, run)


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as stream:
            settings = json.load(stream)
        if "--publish-servers" in sys.argv[2:]:
            publish_mode(settings, sys.stdin.read())
        else:
            reconcile(settings, check="--check" in sys.argv[2:])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("resolved-policy: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
