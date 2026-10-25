"""Short-lived DNS permissions. Only this small broker needs NET_ADMIN."""

import fcntl
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


def addresses(values):
    result = set()
    for value in values:
        address = ipaddress.ip_address(value)
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
            raise ValueError("non-routable DNS permission")
        result.add(str(address))
    return sorted(result)


def domains(values):
    result = set()
    for value in values:
        value = value.lstrip("~").rstrip(".").lower()
        if re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value) \
                and ".." not in value and len(value) <= 253:
            result.add(value)
    return sorted(result)


def network_context(settings, run=subprocess.run, read=lambda path: Path(path).read_text().strip()):
    interface = settings['wireless']
    if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', interface):
        raise ValueError('invalid split DNS interface')
    epoch = read('/run/dns-policy/split-epoch')
    result = run([settings['nmcli'], '--escape', 'no', '-g',
                  'GENERAL.CON-UUID,GENERAL.CON-PATH', 'device', 'show', interface],
                 capture_output=True, text=True, timeout=3, check=True)
    fields = result.stdout.splitlines()
    if not fields or fields[0] not in settings['split_profiles']:
        return None
    if len(fields) != 2 or not fields[1].startswith('/org/freedesktop/NetworkManager/ActiveConnection/'):
        raise ValueError('active split DNS connection unavailable')
    base = '/sys/class/net/' + interface + '/'
    if read(base + 'carrier') != '1' or read('/run/dns-policy/split-epoch') != epoch:
        raise ValueError('split DNS network changed')
    return {'interface': interface, 'uuid': fields[0], 'connection': fields[1],
            'ifindex': int(read(base + 'ifindex')), 'carrier_down_count': int(read(base + 'carrier_down_count')),
            'boot': read('/proc/sys/kernel/random/boot_id'), 'epoch': epoch}


def split_dns(settings, run=subprocess.run, context=None, clock=lambda: time.clock_gettime(time.CLOCK_BOOTTIME)):
    """Only explicit DNS + suffixes on an active, operator-approved profile."""
    def command(args):
        value = run([settings["nmcli"], "--escape", "no", "-g", *args],
                    capture_output=True, text=True, timeout=6, check=True)
        return value.stdout

    empty = {'entries': [], 'context': None, 'observed_at': clock()}
    if not settings["split_profiles"]:
        return empty
    observe = context or (lambda: network_context(settings, run))
    before = observe()
    if before is None:
        return empty
    observed_at = clock()
    values = command(["ipv4.dns,ipv6.dns,ipv4.dns-search,ipv6.dns-search",
                      "connection", "show", "uuid", before['uuid']]).splitlines()
    if len(values) > 4:
        raise ValueError("unexpected split DNS field count")
    values += [""] * (4 - len(values))
    servers = addresses(word for value in values[:2] for word in value.split(",") if word)
    suffixes = domains(word for value in values[2:] for word in value.split(",") if word)
    allowed = [ipaddress.ip_network(value) for value in settings['split_cidrs']]
    if any(not any(ipaddress.ip_address(server) in network for network in allowed) for server in servers):
        raise ValueError('split DNS requires an excluded private upstream')
    if observe() != before:
        raise ValueError('split DNS connection changed while reading profile')
    return {'entries': [{"domain": suffix, "server": server} for suffix in suffixes for server in servers],
            'context': before, 'observed_at': observed_at}


def transaction(request, policy, now, context=None):
    names = ["dns_plaintext4", "dns_split4", "dns_split6"]
    lines = ["flush set inet lf_filter " + name for name in names]
    if request.get("policy_version") != policy.get("policy_version", policy.get("version")) \
            or not 0 <= now - request.get("monotonic_seconds", -1000) <= 120:
        return "\n".join(lines) + "\n"
    if policy["mode"] not in {"direct", "proxy"}:
        return "\n".join(lines) + "\n"
    leases = {}
    if policy["mode"] == "direct":
        ttl = min(120, int(request.get("plaintext_until", 0) - now))
        if ttl > 0:
            leases["dns_plaintext4"] = (ttl, [a for a in addresses(request.get("plaintext", []))
                                               if ipaddress.ip_address(a).version == 4])
    split_request = request.get('split') or {}
    split = []
    age = now - split_request.get('observed_at', -1000) if isinstance(split_request, dict) else 1000
    remaining = int(120 - age) if 0 <= age < 120 else 0
    if isinstance(split_request, dict) and context is not None and split_request.get('context') == context \
            and 0 < remaining <= 120:
        split = addresses(entry['server'] for entry in split_request.get('entries', [])
                          if domains([entry['domain']]))
    for family in [4, 6]:
        leases["dns_split" + str(family)] = (remaining, [a for a in split if ipaddress.ip_address(a).version == family])
    for name, (ttl, values) in leases.items():
        if values:
            prefix = json.dumps(context['interface']) + ' . ' if name.startswith('dns_split') else ''
            elements = ", ".join(prefix + a + " timeout " + str(ttl) + "s" for a in values)
            lines.append("add element inet lf_filter " + name + " { " + elements + " }")
    return "\n".join(lines) + "\n"


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")) + "\n")
    temporary.replace(path)


def main(settings):
    if "--split" in sys.argv[2:]:
        print(json.dumps(split_dns(settings)))
        return
    runtime = Path(settings["runtime"])
    with open("/run/proxy-mode/lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        policy = json.loads(Path("/run/proxy-mode/state.json").read_text())
        if '--revoke-split' in sys.argv[2:]:
            # Invalidate the old observation before flushing, including when
            # nft fails. A retry must never regrant the old connection's lease.
            atomic_json(Path('/run/dns-policy/split-epoch'), uuid.uuid4().hex)
            subprocess.run([settings['nft'], '-f', '-'], input='flush set inet lf_filter dns_split4\nflush set inet lf_filter dns_split6\n',
                           text=True, capture_output=True, timeout=5, check=True)
            atomic_json(Path('/run/dns-policy/state.json'), {
                'policy_version': policy.get('policy_version', policy['version']),
                'monotonic_seconds': time.clock_gettime(time.CLOCK_BOOTTIME), 'status': 'revoked',
            })
            return
        try:
            request = json.loads((runtime / "policy.json").read_text())
        except (OSError, ValueError):
            request = {}
        sleeping = Path("/run/dns-policy/sleeping").exists()
        if sleeping:
            request = {}
        context = None
        split = request.get('split') or {}
        if isinstance(split, dict) and split.get('entries'):
            try:
                context = network_context(settings)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass  # Still flush the old leases on an observation failure.
        now = time.clock_gettime(time.CLOCK_BOOTTIME)
        value = transaction(request, policy, now, context)
        subprocess.run([settings["nft"], "-f", "-"], input=value, text=True,
                       capture_output=True, timeout=5, check=True)
        stale = isinstance(split, dict) and bool(split.get('entries')) and (
            context is None or split.get('context') != context
            or not 0 <= now - split.get('observed_at', -1000) < 120)
        atomic_json(Path("/run/dns-policy/state.json"), {
            "policy_version": policy.get("policy_version", policy["version"]),
            "monotonic_seconds": now, "status": "suspended" if sleeping else ('stale-network' if stale else 'applied'),
        })


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as stream:
            main(json.load(stream))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print("dns-policy: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
