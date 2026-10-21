"""Compare our nft tables structurally; rule order is part of the policy."""

import argparse
import ipaddress
import json
import sys


TABLES = {"lf_filter", "lf_nat", "lf_mangle", "lf_proxymode_nat"}
DYNAMIC_CHAINS = {"proxymode_drops", "proxymode_tail", "proxymode_forward",
                  "lf_blocked_guard", "lf_guest_guard", "proxymode_tproxy"}

# `nft -e` echoes the applied object with the protocol as its number, while
# `nft -j list ruleset` prints the name. Canonicalise both to the number so the
# build-time expected JSON compares equal to the live snapshot.
PROTO_NUMBERS = {
    "ip": 0, "icmp": 1, "igmp": 2, "ipip": 4, "tcp": 6, "egp": 8, "udp": 17,
    "ipv6": 41, "ipv6-route": 43, "ipv6-frag": 44, "gre": 47, "esp": 50,
    "ah": 51, "icmpv6": 58, "ipv6-icmp": 58, "ipv6-nonxt": 59, "ipv6-opts": 60,
    "ospf": 89, "pim": 103, "vrrp": 112, "sctp": 132, "udplite": 136,
}


def canonical_proto(value):
    if isinstance(value, str):
        return PROTO_NUMBERS.get(value.lower(), value)
    if isinstance(value, dict) and set(value) == {"set"} and isinstance(value["set"], list):
        return {"set": sorted((canonical_proto(x) for x in value["set"]),
                              key=lambda x: json.dumps(x, sort_keys=True))}
    return value


def is_protocol_field(left):
    if isinstance(left, dict):
        meta = left.get("meta")
        if isinstance(meta, dict) and meta.get("key") == "l4proto":
            return True
        payload = left.get("payload")
        if isinstance(payload, dict) and payload.get("field") == "protocol":
            return True
    return False


def normalize(value):
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"match"} and isinstance(value["match"], dict):
        match = value["match"]
        left = normalize(match.get("left"))
        right = normalize(match.get("right"))
        if is_protocol_field(left):
            right = canonical_proto(right)
        return {"match": {"op": match.get("op"), "left": left, "right": right}}
    result = {}
    for key, item in value.items():
        if key in {"handle", "index", "position"}:
            continue
        if key == "counter":
            result[key] = None
        elif key == "set" and isinstance(item, list):
            result[key] = sorted((normalize(x) for x in item), key=lambda x: json.dumps(x, sort_keys=True))
        else:
            result[key] = normalize(item)
    return result


def policy(document):
    tables = {}
    for entry in document["nftables"]:
        if len(entry) != 1:
            raise ValueError("invalid nft entry")
        kind, obj = next(iter(entry.items()))
        if kind == "metainfo":
            continue
        table = obj.get("name") if kind == "table" else obj.get("table")
        if table not in TABLES:
            continue
        key = (obj["family"], table)
        bucket = tables.setdefault(key, {"objects": {}, "rules": {}})
        if kind == "rule":
            bucket["rules"].setdefault(obj["chain"], []).append(normalize(obj))
        else:
            bucket["objects"][(kind, obj.get("name", ""))] = normalize(obj)
    if ("inet", "lf_filter") not in tables:
        raise ValueError("inet lf_filter is missing")
    return tables


def static_policy(document):
    """Ignore mode rules, while still checking their chain/hook declarations."""
    result = policy(document)
    for key in list(result):
        if key[1] == "lf_proxymode_nat":
            del result[key]
            continue
        result[key]["rules"] = {chain: rules for chain, rules in result[key]["rules"].items()
                                if chain not in DYNAMIC_CHAINS}
    return result


def compare(expected, actual, static=False):
    extract = static_policy if static else policy
    exp, live = extract(expected), extract(actual)
    if any(e.get("table", {}).get("name") == "lf_bootstrap" for e in actual["nftables"]):
        raise ValueError("bootstrap policy still active beside the full policy")
    if exp.keys() != live.keys():
        raise ValueError(f"table mismatch: expected {sorted(exp)}, found {sorted(live)}")
    for table in exp:
        for kind in ("objects", "rules"):
            if exp[table][kind] != live[table][kind]:
                raise ValueError(f"{table[0]} {table[1]}: {kind} differ (including rule order/hook/policy)")


def unwrap(document):
    """Turn `nft -e -j` (echo of applied commands) into `list ruleset` shape.

    `-e` wraps each applied object as {"add": {"rule": {...}}}; the live dump
    emits the bare {"rule": {...}}. Unwrapping makes the build-time expected
    JSON comparable with the runtime `nft -j list ruleset` output. Non-applying
    operations (flush/destroy of a fresh table) are not echoed and need no
    handling.
    """
    result = []
    for entry in document.get("nftables", []):
        if len(entry) == 1:
            operation, inner = next(iter(entry.items()))
            if operation in {"add", "insert", "replace"} \
                    and isinstance(inner, dict) and len(inner) == 1:
                key, value = next(iter(inner.items()))
                result.append({key: value})
                continue
        result.append(entry)
    return {"nftables": result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("compare", "compare-static", "address", "unwrap"))
    parser.add_argument("arguments", nargs="+")
    args = parser.parse_args()
    try:
        if args.command in {"compare", "compare-static"}:
            with open(args.arguments[0]) as stream:
                expected = json.load(stream)
            with open(args.arguments[1]) as stream:
                actual = json.load(stream)
            compare(expected, actual, static=args.command == "compare-static")
        elif args.command == "unwrap":
            with open(args.arguments[0]) as stream:
                document = json.load(stream)
            with open(args.arguments[1], "w") as stream:
                json.dump(unwrap(document), stream, sort_keys=True, separators=(",", ":"))
        else:
            network = ipaddress.ip_network(args.arguments[0], strict=False)
            for line in sys.stdin:
                try:
                    address = ipaddress.ip_address(line.strip())
                except ValueError:
                    continue
                if address in network:
                    print(address)
                    return 0
            return 1
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"netsec: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
