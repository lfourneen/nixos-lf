"""Reconcile administrator-declared IPv4 neighbours under the policy lock."""

import fcntl
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid


STATE = Path("/run/proxy-mode")
SET_NAMES = ("observe", "enforce", "gateways", "bindings")
ERRORS = (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError)


def validate(settings):
    for name in (*settings["interfaces"].values(), *settings["managedInterfaces"]):
        if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.:-]{0,14}", name):
            raise ValueError("invalid ARP interface name")
    for profile in settings["wifi"]:
        if str(uuid.UUID(profile)) != profile:
            raise ValueError("Wi-Fi bindings require canonical connection UUIDs")
    bindings = list(settings["wifi"].values())
    if settings["wired"] is not None:
        bindings.append(settings["wired"])
    for binding in bindings:
        network = ipaddress.IPv4Network(binding["subnet"])
        gateway = ipaddress.IPv4Address(binding["gateway"])
        if (str(network) != binding["subnet"] or not 0 < network.prefixlen < 32
                or gateway not in network or gateway.is_multicast
                or gateway.is_unspecified or gateway.is_loopback
                or (network.prefixlen < 31 and gateway in (network.network_address, network.broadcast_address))):
            raise ValueError("ARP gateway must be a usable on-link IPv4 address")
        mac = binding["mac"]
        if (not re.fullmatch(r"(?:[a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}", mac)
                or int(mac[:2], 16) & 1 or mac == "00:00:00:00:00:00"):
            raise ValueError("ARP gateway MAC must be nonzero unicast Ethernet")


def run(*args, input=None):
    return subprocess.run(args, input=input, text=True, capture_output=True, check=True, timeout=3).stdout


def ip(settings, *args):
    return json.loads(run(settings["ip"], "-j", "-4", *args))


def link_addresses(settings):
    # An address dump restricted to IPv4 omits the link type and hardware address.
    return {link["ifname"]: link for link in json.loads(run(settings["ip"], "-j", "address", "show"))}


def read(path, fallback):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return fallback


def write(path, value, mode):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temp:
        draft = Path(temp.name)
        try:
            json.dump(value, temp, sort_keys=True)
            temp.write("\n")
            temp.close()
            draft.chmod(mode)
            draft.replace(path)
        finally:
            draft.unlink(missing_ok=True)


def wifi_profile(settings, iface):
    active = run(settings["nmcli"], "--terse", "--escape", "no", "--fields", "UUID,DEVICE",
                 "connection", "show", "--active")
    profiles = [profile for line in active.splitlines()
                for profile, _, device in [line.partition(":")] if device == iface]
    if len(profiles) > 1:
        raise ValueError("ambiguous active Wi-Fi profile")
    return profiles[0] if profiles else None


def next_hops(routes, iface):
    return [(route["dst"], hop.get("gateway"), route.get("metric", 0))
            for route in routes for hop in route.get("nexthops", [route])
            if hop.get("dev") == iface
            and (route["dst"] == "default" or "gateway" in hop or "via" in hop)]


def observe(settings):
    interfaces = settings["interfaces"]
    links = link_addresses(settings)
    routes = ip(settings, "route", "show", "table", "main")
    statuses, pins, identity = {}, {}, {}
    filters = {name: set() for name in SET_NAMES}
    for iface, mode in settings["managedInterfaces"].items():
        filters[mode].add((iface,))
        statuses[iface] = {"state": "frame-validation", "filter": mode}
    for kind, iface in interfaces.items():
        link = links.get(iface, {})
        if not {"UP", "LOWER_UP"} <= set(link.get("flags", [])):
            statuses[iface] = {"state": "disconnected"}
            continue
        hops = next_hops(routes, iface)
        profile = wifi_profile(settings, iface) if kind == "wifi" and settings["wifi"] else None
        binding = settings["wired"] if kind == "wired" else settings["wifi"].get(profile)
        if binding is None:
            state = "profile-unavailable" if kind == "wifi" and settings["wifi"] and not profile and hops else "dynamic"
            statuses[iface] = {"state": state}
            identity[iface] = [link["ifindex"], link.get("address"), profile, sorted(hops, key=str)]
            continue
        gateway, mac = binding["gateway"], binding["mac"].lower()
        mode = binding["filter"]
        filters[mode].add((iface,))
        filters["gateways"].add((iface, gateway))
        filters["bindings"].add((iface, gateway, mac))
        network = ipaddress.IPv4Network(binding["subnet"])
        addresses = sorted(info["local"] for info in link.get("addr_info", [])
                           if info["family"] == "inet" and info["prefixlen"] == network.prefixlen
                           and ipaddress.IPv4Address(info["local"]) in network and info["local"] != gateway)
        on_link = any(route.get("dev") == iface and route["dst"] == str(network)
                      and route.get("scope") == "link" and "gateway" not in route for route in routes)
        status = {"filter": mode}
        statuses[iface] = status
        if link.get("link_type") != "ether" or not addresses or not on_link:
            status["state"] = "subnet-mismatch"
        elif not any(dst == "default" for dst, _, _ in hops) or any(via != gateway for _, via, _ in hops):
            status["state"] = "gateway-mismatch"
        else:
            pin = [link["ifindex"], gateway, mac]
            pins[iface] = pin
            identity[iface] = [pin, link.get("address"), profile, addresses, sorted(hops, key=str)]
            status["state"] = "pinned"
    return links, statuses, pins, filters, identity


def sync_filters(settings, filters):
    document = json.loads(run(settings["nft"], "-j", "list", "table", "arp", "lf_arp"))
    current = {}
    for item in document["nftables"]:
        obj = item.get("set")
        if obj and obj["name"] in SET_NAMES:
            current[obj["name"]] = {tuple(e["concat"]) if isinstance(e, dict) else (e,)
                                    for e in obj.get("elem", [])}
    if set(current) != set(SET_NAMES):
        raise ValueError("ARP filter sets are missing")
    changes = []
    for name, entries in filters.items():
        if current[name] == entries:
            continue
        changes.append(f"flush set arp lf_arp {name}")
        if entries:
            elements = [" . ".join((json.dumps(iface), *parts)) for iface, *parts in sorted(entries)]
            changes.append(f"add element arp lf_arp {name} {{ {', '.join(elements)} }}")
    if changes:
        run(settings["nft"], "-f", "-", input="\n".join(changes) + "\n")


def permanent(settings, iface, gateway):
    neighbours = ip(settings, "neigh", "show", "to", gateway, "dev", iface)
    return next((item.get("lladdr") for item in neighbours
                 if item["dst"] == gateway and "PERMANENT" in item["state"]), None)


def remove_stale(settings, journal, owned, links, desired):
    for iface, pin in list(owned.items()):
        if desired.get(iface) == pin:
            continue
        ifindex, gateway, mac = pin
        if links.get(iface, {}).get("ifindex") == ifindex and permanent(settings, iface, gateway) == mac:
            run(settings["ip"], "-4", "neigh", "del", gateway, "dev", iface)
        del owned[iface]
        write(journal, owned, 0o600)


def reconcile(settings):
    links, statuses, desired, filters, identity = observe(settings)
    journal = STATE / "arp-owned.json"
    owned = read(journal, {})
    remove_stale(settings, journal, owned, links, desired)
    sync_filters(settings, filters)
    for iface, pin in desired.items():
        _, gateway, mac = pin
        actual = permanent(settings, iface, gateway)
        if actual is not None and actual != mac:
            statuses[iface]["state"] = "permanent-conflict"
            continue
        if actual is None:
            owned[iface] = pin
            write(journal, owned, 0o600)
            run(settings["ip"], "-4", "neigh", "replace", gateway, "lladdr", mac, "nud", "permanent", "dev", iface)
        if permanent(settings, iface, gateway) != mac:
            statuses[iface]["state"] = "pin-unverified"
    # Confirm that no link, profile or next hop changed during the writes.
    _, observed, _, observed_filters, observed_identity = observe(settings)
    ok = (identity == observed_identity and filters == observed_filters
          and all(status["state"] in {"dynamic", "disconnected", "pinned", "frame-validation"}
                  for status in (*statuses.values(), *observed.values())))
    write(STATE / "arp-status.json", {"enabled": True, "ok": ok, "interfaces": statuses}, 0o644)
    if ok:
        print(hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest())
    return ok


def cleanup(settings):
    with (STATE / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        journal = STATE / "arp-owned.json"
        owned = read(journal, {})
        links = link_addresses(settings)
        remove_stale(settings, journal, owned, links, {})
        run(settings["nft"], "destroy", "table", "arp", "lf_arp")
        (STATE / "arp-status.json").unlink(missing_ok=True)


def watch(settings):
    interfaces = set(settings["interfaces"].values()) | set(settings["managedInterfaces"])
    names = "|".join(re.escape(name) for name in sorted(interfaces))
    relevant = re.compile(rf"(?:^|\s)dev (?:{names})(?:\s|$)|^(?:Deleted )?\d+: (?:{names})(?=[:@\s])")
    with subprocess.Popen([settings["ip"], "-4", "-o", "monitor", "link", "address", "route"],
                          stdout=subprocess.PIPE, text=True) as monitor:
        wake = [settings["systemctl"], "start", "--no-block", "proxy-net-wake.service"]
        try:
            subprocess.run(wake, check=False, timeout=3)
            for event in monitor.stdout:
                if relevant.search(event):
                    subprocess.run(wake, check=False, timeout=3)
        finally:
            monitor.terminate()
    return 1


def main():
    settings = json.loads(Path(sys.argv[1]).read_text())
    action = sys.argv[2]
    if action == "status":
        status = (read(STATE / "arp-status.json", {"enabled": True, "ok": False})
                  if settings["enabled"] else {"enabled": False, "mode": "dynamic"})
        print(json.dumps(status, indent=2))
        return 0
    if action == "validate":
        validate(settings)
    elif action == "sync":
        return 0 if reconcile(settings) else 1
    elif action == "watch":
        return watch(settings)
    elif action == "cleanup":
        cleanup(settings)
    else:
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ERRORS as error:
        if sys.argv[2] == "sync":
            write(STATE / "arp-status.json", {"enabled": True, "ok": False, "reason": "observation-failed"}, 0o644)
        print(f"network-arp: {error}", file=sys.stderr)
        sys.exit(1)
