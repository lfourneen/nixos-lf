"""Check that dual-stack endpoint identities reach every DNS consumer."""
import ipaddress
import json
from pathlib import Path
import sys

variants = json.loads(Path(sys.argv[1]).read_text())
for settings in variants:
    tokens = []
    for endpoint in settings['endpoints']:
        ipaddress.ip_address(endpoint['address'])
        tokens.append(f"{endpoint['address']}@{settings['port']}#{endpoint['authName']}")
    zones = settings['zones']
    root = next(zone for zone in zones if zone['name'] == '.')
    assert root['forward-addr'] == tokens
    script = Path(settings['script']).read_text()
    assert all(token in script for token in tokens)
assert len({ipaddress.ip_address(e['address']).version for e in variants[2]['endpoints']}) == 2
assert all(ipaddress.ip_address(e['address']).version == 6 for e in variants[3]['endpoints'])
