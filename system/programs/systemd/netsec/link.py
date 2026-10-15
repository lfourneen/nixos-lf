"""Read DHCP DNS for the physical default uplink, without consulting the TUN."""

import hashlib
import ipaddress
import json
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
            if key not in {"domain_name_servers", "new_domain_name_servers"}:
                continue
        for word in line.strip("'\"").split():
            try:
                address = ipaddress.ip_address(word)
            except ValueError:
                continue
            if address.version == 4 and not (address.is_loopback or address.is_link_local
                    or address.is_multicast or address.is_unspecified or str(address) == "255.255.255.255"):
                result.add(str(address))
    return sorted(result)


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
        servers = resolvers(command([settings["nmcli"], "-g", "IP4.DNS", "device", "show", interface]))
    addresses = command([settings["ip"], "-j", "-4", "addr", "show", "dev", interface])
    addresses = json.loads(addresses or "[]")
    local = sorted(a["local"] for link in addresses for a in link.get("addr_info", []) if a.get("scope") == "global")
    identity = hashlib.sha256(json.dumps([interface, route.get("gateway"), local, servers]).encode()).hexdigest()
    print(json.dumps({"identity": identity, "interface": interface, "servers": servers}))


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as stream:
            main(json.load(stream))
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"dns-upstream: link discovery failed: {error}", file=sys.stderr)
        sys.exit(1)
