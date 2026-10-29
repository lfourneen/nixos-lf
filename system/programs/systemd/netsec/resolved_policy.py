"""Keep the managed TUN out of resolved's default resolver selection."""

import json
from pathlib import Path
import re
import subprocess
import sys

# `tailscale status --json` waits on the daemon; one second was shorter than a
# busy daemon's normal answer and every miss reverted the link.
TAILNET_STATUS_TIMEOUT = 5


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


def reconcile_tailnet(settings, check=False, run=subprocess.run):
    tailnet = settings.get("tailnet")
    if not tailnet:
        return
    def command(*args, timeout=3):
        return run(args, text=True, capture_output=True, timeout=timeout, check=True).stdout.strip()
    path = Path(tailnet["domain_file"])
    interface = tailnet["interface"]
    domain = ""
    try:
        command(settings["ip"], "link", "show", "dev", interface)
    except subprocess.CalledProcessError:
        # The interface is gone, so the tailnet really is absent: revoke.
        if not check:
            path.unlink(missing_ok=True)
        return
    if tailnet["enabled"]:
        try:
            status = json.loads(command(tailnet["cli"], "status", "--json", "--peers=false",
                                        timeout=TAILNET_STATUS_TIMEOUT))
            suffix = (status.get("CurrentTailnet") or {}).get("MagicDNSSuffix", "").lower().rstrip(".")
            if status.get("BackendState") == "Running" and (status.get("CurrentTailnet") or {}).get("MagicDNSEnabled") is True and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.ts\.net", suffix):
                domain = suffix
        except (subprocess.SubprocessError, ValueError):
            # Unreadable status is not a verdict: keep the last authenticated
            # route. Treating a slow CLI as "absent" reverted the link and
            # deleted the MagicDNS exception several times a minute.
            if check:
                raise ValueError("tailnet status is unreadable")
            return
    dns = command(settings["resolvectl"], "dns", interface).partition(":")[2].split()
    domains = command(settings["resolvectl"], "domain", interface).partition(":")[2].split()
    route = command(settings["resolvectl"], "default-route", interface).partition(":")[2].strip()
    expected_dns = [tailnet["resolver"]] if domain else []
    expected_domains = [domain] if domain else []
    dirty = dns != expected_dns or domains != expected_domains or route != "no"
    saved = path.read_text().strip() if path.exists() else ""
    if check:
        if dirty or saved != domain:
            raise ValueError("tailnet DNS differs from the authenticated conditional route")
        return
    if not dirty and saved == domain:
        return
    # Remove the old exception before changing domains, including on logout.
    path.unlink(missing_ok=True)
    if dirty:
        command(settings["resolvectl"], "revert", interface)
        if domain:
            command(settings["resolvectl"], "dns", interface, tailnet["resolver"])
            command(settings["resolvectl"], "domain", interface, domain)
        command(settings["resolvectl"], "default-route", interface, "no")
    if domain:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(domain + "\n")
        temporary.replace(path)


def reconcile(settings, check=False, run=subprocess.run):
    reconcile_tun(settings, check, run)
    reconcile_tailnet(settings, check, run)


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as stream:
            reconcile(json.load(stream), check="--check" in sys.argv[2:])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("resolved-policy: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
