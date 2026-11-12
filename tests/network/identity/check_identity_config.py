"""Check actual Nix policy variants and the selected nmcli's offline parser."""
import configparser
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys

manifest, script, nmcli = sys.argv[1:]
data = json.loads(Path(manifest).read_text())
spec = importlib.util.spec_from_file_location('identity', script)
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)
settings = json.loads(Path(data['settings']).read_text())
identity.validate(settings)
assert data['base']['wifi'] == 'stable-ssid' and data['base']['wired'] == 'permanent'
assert data['base']['persist'] is False
assert shlex.split(data['base']['dhcpService']['ExecStart'])[-1] == 'ens1'
assert shlex.split(data['base']['dhcpService']['ExecReload'])[-1] == 'ens1'
assert data['base']['dhcpService']['PIDFile'] == '/run/dhcpcd/ens1.pid'
assert data['base']['dhcpService']['User'] == 'dhcpcd'
base_dirs = {entry['directory']: entry for entry in data['base']['directories']}
fixed_dirs = {entry['directory']: entry for entry in data['fixed']['directories']}
assert '/var/lib/dhcpcd' not in base_dirs and '/var/lib/dhcpcd' in fixed_dirs
assert base_dirs['/var/lib/network-identity']['mode'] == '0700'
assert 'PermanentMACAddress' in data['base']['link']['matchConfig']
assert 'MACAddress' not in data['base']['link']['matchConfig']
assert data['random'].strip().endswith('anonymous') and 'randomise_hwaddr' in data['random']
assert data['fixed']['dhcp'].strip().endswith('anonymous')
assert data['fixed']['link']['linkConfig']['MACAddress'] == '02:11:22:33:44:55'
assert 'randomise_hwaddr' not in data['fixed']['dhcp']

for uuid, profile in settings['profiles'].items():
    source = f'''[connection]
id=identity-fixture
uuid={uuid}
type=wifi
autoconnect=false
[wifi]
ssid=identity-fixture
mode=infrastructure
[ipv4]
method=auto
[ipv6]
method=auto
'''
    values = identity.desired(uuid, profile)
    arguments = [value for item in values.items() for value in item]
    result = subprocess.run([nmcli, '--offline', 'connection', 'modify', *arguments],
                            input=source, text=True, capture_output=True, check=True)
    parsed = configparser.ConfigParser(interpolation=None)
    parsed.read_string(result.stdout)
    assert parsed['wifi']['cloned-mac-address'] == values['802-11-wireless.cloned-mac-address']
    assert parsed['connection']['stable-id'] == values['connection.stable-id']
    assert parsed['ipv4']['dhcp-client-id'] == 'stable'
    assert parsed['ipv6']['dhcp-duid'] == 'stable-uuid'
    assert parsed['ipv6']['addr-gen-mode'] == 'stable-privacy'
    for family in ('ipv4', 'ipv6'):
        assert parsed[family]['dhcp-send-hostname'] == '0'
        assert parsed[family]['dhcp-send-hostname-deprecated'] == 'false'
    # nmcli synchronizes the old boolean when setting the new ternary.
    # Restoring both must recover the original default hostname behavior.
    restored = subprocess.run([nmcli, '--offline', 'connection', 'modify',
        'ipv4.dhcp-send-hostname-deprecated', 'yes', 'ipv6.dhcp-send-hostname-deprecated', 'yes',
        'ipv4.dhcp-send-hostname', '-1', 'ipv6.dhcp-send-hostname', '-1'],
        input=result.stdout, text=True, capture_output=True, check=True)
    restored_config = configparser.ConfigParser(interpolation=None)
    restored_config.read_string(restored.stdout)
    for family in ('ipv4', 'ipv6'):
        assert restored_config[family].get('dhcp-send-hostname', '-1') == '-1'
        assert restored_config[family].get('dhcp-send-hostname-deprecated', 'true') == 'true'
print('Identity defaults, wired variants and actual nmcli policy parsing: OK')
