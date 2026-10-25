"""Short-lived DNS permissions. Only this small broker needs NET_ADMIN."""

import fcntl
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys
import time


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


def split_dns(settings, run=subprocess.run):
    """Only explicit DNS + suffixes on an active, operator-approved profile."""
    def command(args):
        value = run([settings["nmcli"], "--escape", "no", "-g", *args],
                    capture_output=True, text=True, timeout=6, check=True)
        return value.stdout.strip()

    if not settings["split_profiles"]:
        return []
    uuid = command(["GENERAL.CON-UUID", "device", "show", settings["wireless"]])
    if uuid not in settings["split_profiles"]:
        return []
    values = command(["ipv4.dns,ipv6.dns,ipv4.dns-search,ipv6.dns-search",
                      "connection", "show", "uuid", uuid]).split("\n")
    values += [""] * (4 - len(values))
    servers = addresses(word for value in values[:2] for word in value.split(",") if word)
    suffixes = domains(word for value in values[2:] for word in value.split(",") if word)
    return [{"domain": suffix, "server": server} for suffix in suffixes for server in servers]


def transaction(request, policy, now):
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
    split = addresses(entry["server"] for entry in request.get("split", [])
                      if domains([entry["domain"]]))
    for family in [4, 6]:
        leases["dns_split" + str(family)] = (120, [a for a in split if ipaddress.ip_address(a).version == family])
    for name, (ttl, values) in leases.items():
        if values:
            elements = ", ".join(a + " timeout " + str(ttl) + "s" for a in values)
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
        try:
            request = json.loads((runtime / "policy.json").read_text())
        except (OSError, ValueError):
            request = {}
        now = time.clock_gettime(time.CLOCK_BOOTTIME)
        if Path("/run/dns-policy/sleeping").exists():
            request = {}
        value = transaction(request, policy, now)
        subprocess.run([settings["nft"], "-f", "-"], input=value, text=True,
                       capture_output=True, timeout=5, check=True)
        atomic_json(Path("/run/dns-policy/state.json"), {
            "policy_version": policy.get("policy_version", policy["version"]),
            "monotonic_seconds": now, "status": "applied",
        })


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as stream:
            main(json.load(stream))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print("dns-policy: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
