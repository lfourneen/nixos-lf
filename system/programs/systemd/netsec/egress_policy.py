"""Validate direct-egress CIDRs without silently broadening host-bit prefixes."""

import ipaddress
import json
from pathlib import Path
import sys


def validate(policy):
    scopes = [ipaddress.ip_network(cidr, strict=True) for cidr in policy["scopes"]]
    for entry in policy["exceptions"]:
        network = ipaddress.ip_network(entry["cidr"], strict=True)
        if not any(network.version == scope.version and network.subnet_of(scope) for scope in scopes):
            raise ValueError(f"host direct CIDR is outside the private/multicast scope: {network}")
        if not entry["tcpPorts"] and not entry["udpPorts"]:
            raise ValueError("host direct grants require explicit ports")
    for cidr in policy["timeServerCidrs"]:
        network = ipaddress.ip_network(cidr, strict=True)
        if network.prefixlen == 0 or network.is_multicast:
            raise ValueError(f"time-server restriction requires a bounded unicast CIDR: {network}")


if __name__ == "__main__":
    try:
        validate(json.loads(Path(sys.argv[1]).read_text()))
    except (OSError, ValueError) as error:
        sys.exit(f"Direct-egress policy: {error}")
