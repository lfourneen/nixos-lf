"""Validate routed and host-socket guest authorization before system builds."""

import ipaddress
import json
from pathlib import Path
import re
import sys


def network(text):
    value = ipaddress.ip_network(text, strict=True)
    if value.version == 6 and value.network_address.ipv4_mapped is not None:
        raise ValueError("mapped IPv4 prefixes are not guest scopes")
    return value


def contained(value, scopes):
    return any(value.version == scope.version and value.subnet_of(scope) for scope in scopes)


def ports(entry, keys):
    values = [port for key in keys for port in entry[key]]
    if any(type(port) is not int or not 1 <= port <= 65535 for port in values):
        raise ValueError("guest ports must be integers in 1..65535")
    return values


def validate(policy):
    interfaces = policy["interfaces"]
    if len(set(interfaces)) != len(interfaces) or not set(policy["known"]).issubset(interfaces):
        raise ValueError("every built-in guest must have one interface role")
    if not set(policy.get("declaredInterfaces", [])).issubset(interfaces):
        raise ValueError("declared guest policy has no ingress interface")
    if any(not re.fullmatch(r"[a-zA-Z0-9_.-]{1,15}", i) or i in policy["forbiddenInterfaces"] for i in interfaces):
        raise ValueError("guest interface collides with a host interface")
    private = [network(cidr) for cidr in policy["privateScopes"]]
    sources = [network(cidr) for cidr in policy["sourceScopes"]]
    unsafe = [network(cidr) for cidr in policy["unsafe4"] + policy["unsafe6"]]
    seen = []
    roles = {}
    for role in policy["networks"]:
        iface = role["interface"]
        if iface not in interfaces or iface in roles or role["trust"] not in ("trusted", "restricted"):
            raise ValueError("invalid guest trust role")
        prefixes = [network(cidr) for cidr in role["sourceCidrs"]]
        if not prefixes or any(not contained(value, sources) for value in prefixes):
            raise ValueError("guest sources require canonical private/ULA subnets")
        for prefix in prefixes:
            for other_iface, other in seen:
                if prefix.version == other.version and prefix.overlaps(other):
                    raise ValueError(f"guest source overlap: {iface} and {other_iface}")
            seen.append((iface, prefix))
        for text in role["gateways"]:
            address = ipaddress.ip_address(text)
            if getattr(address, "scope_id", None) or not any(address.version == n.version and address in n for n in prefixes):
                raise ValueError("guest gateway must be inside its source subnet")
            if contained(network(f"{address}/{address.max_prefixlen}"), unsafe):
                raise ValueError("unsafe guest gateway")
        if ports(role, ("hostTcpPorts", "hostUdpPorts")) and not role["gateways"]:
            raise ValueError("guest host service grants require a gateway address")
        roles[iface] = role
    if set(roles) != set(interfaces):
        raise ValueError("guest interfaces and roles differ")

    def destination(entry):
        prefix = network(entry["cidr"])
        if not contained(prefix, private) or contained(prefix, unsafe):
            raise ValueError("guest destination must be a canonical safe private CIDR")
        values = ports(entry, ("tcpPorts", "udpPorts"))
        if not values:
            raise ValueError("guest grants require destination ports")
        return values

    for entry in policy["exceptions"]:
        if entry["interface"] not in roles:
            raise ValueError("guest grant has no guest role")
        destination(entry)

    uid_map = policy["users"]
    protected = {uid_map[name] for name in policy["protectedUsers"] if name in uid_map}
    names, uids = set(), set()
    all_ranges = []
    for name, ranges in policy.get("subUidRanges", {}).items():
        for value in ranges:
            first, count = value["startUid"], value["count"]
            if type(first) is not int or type(count) is not int or first < 65536 or count < 1 or first + count > 4294967295:
                raise ValueError("invalid delegated UID range")
            all_ranges.append((name, first, first+count-1))
    for actor in policy["socketUsers"]:
        name, uid = actor["name"], actor["uid"]
        if name in names or type(uid) is not int or not 1000 <= uid < 65534 or uid in protected or uid in uids:
            raise ValueError("socket guests require distinct dedicated fixed identities")
        if list(uid_map.values()).count(uid) != 1:
            raise ValueError("socket guest UID is shared with another account")
        for value in actor.get("uidRanges", []):
            first, count = value["startUid"], value["count"]
            last = first+count-1
            if (name, first, last) not in all_ranges:
                raise ValueError("socket guest has an undeclared UID range")
            if any(type(other) is int and first <= other <= last for other in uid_map.values()):
                raise ValueError("delegated guest UID overlaps a host account")
            if any(other_name != name and first <= end and start <= last for other_name, start, end in all_ranges):
                raise ValueError("delegated guest UID range is shared with another account")
        names.add(name)
        uids.add(uid)
    for entry in policy["socketExceptions"]:
        if entry["user"] not in names or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,15}", entry["interface"]):
            raise ValueError("socket grant requires a dedicated actor and exact interface")
        if entry["interface"] in ("lo", policy["tunInterface"]):
            raise ValueError("socket grant cannot use loopback or the proxy TUN")
        if set(destination(entry)) & set(policy["reservedPorts"]):
            raise ValueError("socket grant cannot open a proxy or DNS listener")


if __name__ == "__main__":
    try:
        validate(json.loads(Path(sys.argv[1]).read_text()))
    except (OSError, ValueError, KeyError) as error:
        sys.exit(f"Guest network policy: {error}")
