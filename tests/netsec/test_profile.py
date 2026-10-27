"""Parse rendered Merge templates, including optional TUN field branches."""

import json
from pathlib import Path
import sys

import yaml


with open(sys.argv[1]) as stream:
    cases = json.load(stream)
for case in cases:
    data = yaml.safe_load(Path(case["merge"]).read_text())
    expected = case["expected"]
    tun = data["tun"]
    assert tun["enable"] == expected["tun"], case["name"]
    assert data["mixed-port"] == expected["mixed"]
    assert data["tproxy-port"] == expected["tproxy"]
    assert data["ipv6"] == expected["ipv6"]
    assert data["mode"] == "rule"
    assert not data["allow-lan"]
    for key in ("port", "socks-port", "redir-port"):
        assert data[key] == 0
    for key in ("inet6-address", "congestion-controller", "route-exclude-address"):
        assert key not in data, f"{case['name']}: tun.{key} escaped to the YAML root"
    assert data["dns"]["enhanced-mode"] == ("fake-ip" if expected["tun"] else "redir-host")
    if expected["tun"]:
        assert tun["stack"] == expected["stack"]
        assert tun["mtu"] == expected["mtu"]
        assert tun["route-exclude-address"]
        assert tun["auto-route"] and tun["auto-redirect"] and tun["strict-route"]
        assert tun["dns-hijack"] == ["any:53", "tcp://any:53"]
        # The managed policy pins tun.dns-mode, and RawTun.DNSMode has no
        # omitempty: a Merge that drops this key parses to "" and the core
        # refuses to start, so the coupling is asserted here first.
        assert tun["dns-mode"] == "hijack"
    if expected["tun"] and expected["ipv6"]:
        assert tun["inet6-address"] == [expected["address"]]
    else:
        assert "inet6-address" not in tun
    if expected["tun"] and expected["stack"] == "mips":
        assert tun["congestion-controller"] == expected["congestion"]
    else:
        assert "congestion-controller" not in tun
    print(f"{case['name']}: rendered Merge YAML passed")
