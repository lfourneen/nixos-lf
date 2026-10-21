"""Read DHCP DNS for the physical default uplink, without consulting the TUN."""

import hashlib
import ipaddress
import json
from pathlib import Path
import subprocess
import sys


def command(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=4, check=False)
        return result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def resolvers(text):
    result = set()
    for line in text.splitlines():
        if "=" in line:
            key, line = line.split("=", 1)
            if key.strip() not in {"domain_name_servers", "new_domain_name_servers"}:
                continue
        for word in line.strip().strip("'\"").replace(",", " ").split():
            try:
                address = ipaddress.ip_address(word)
            except ValueError:
                continue
            if address.version == 4 and not (address.is_loopback or address.is_link_local
                    or address.is_multicast or address.is_unspecified or str(address) == "255.255.255.255"):
                result.add(str(address))
    return sorted(result)


def kernel_value(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return "unknown"


def session_identity(interface, active_path=""):
    """DHCP-controlled addresses never create a fresh plaintext budget."""
    base = Path("/sys/class/net") / interface
    values = [kernel_value("/proc/sys/kernel/random/boot_id"), interface,
              kernel_value(base / "ifindex"), kernel_value(base / "carrier_down_count"), active_path]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def main(settings):
    raw = command([settings["ip"], "-j", "-4", "route", "show", "table", "main", "default"])
    routes = json.loads(raw or "[]")
    routes = [r for r in routes if r.get("dev") in settings["interfaces"] and "linkdown" not in r.get("flags", [])]
    if not routes:
        print(json.dumps({"identity": "offline", "interface": "", "servers": []}))
        return
    route = min(routes, key=lambda r: (r.get("metric", 0), r["dev"]))
    interface = route["dev"]
    lease = ""
    if interface in settings["dhcp_interfaces"]:
        lease = command([settings["dhcpcd"], "--config", settings["dhcpcd_config"], "-U", "-4", interface])
    servers = resolvers(lease)
    if not servers:
        # IP4.DNS is the applied resolver list, which is deliberately empty once
        # ignore-auto-dns is enabled. DHCP4.OPTION preserves the original lease.
        servers = resolvers(command([settings["nmcli"], "-g", "DHCP4.OPTION", "device", "show", interface]))
    addresses = command([settings["ip"], "-j", "-4", "addr", "show", "dev", interface])
    addresses = json.loads(addresses or "[]")
    local = sorted(a["local"] for link in addresses for a in link.get("addr_info", []) if a.get("scope") == "global")
    active_path = ""
    if interface not in settings["dhcp_interfaces"]:
        active_path = command([settings["nmcli"], "-g", "GENERAL.CON-PATH", "device", "show", interface]).strip()
        if not active_path or active_path == "--":
            raise ValueError("active NetworkManager connection identity unavailable")
    fingerprint = hashlib.sha256(json.dumps([interface, route.get("gateway"), local, servers]).encode()).hexdigest()
    print(json.dumps({"identity": session_identity(interface, active_path), "fingerprint": fingerprint,
                      "interface": interface, "servers": servers}))


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as stream:
            main(json.load(stream))
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"dns-upstream: link discovery failed: {error}", file=sys.stderr)
        sys.exit(1)
