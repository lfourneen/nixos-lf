"""Authenticate passive TCP/UDP listeners; never connect to a redirect port."""

import argparse
import ipaddress
import json
import re
import subprocess
import sys


def snapshot(ss, protocols, run=subprocess.run):
    sockets = []
    for protocol in sorted(protocols):
        result = run([ss, '-H', '-lne' + ('t' if protocol == 'tcp' else 'u')],
                     text=True, capture_output=True, timeout=2, check=True)
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 5:
                raise ValueError('malformed socket observation')
            address, separator, port = fields[3].rpartition(':')
            if not separator or not port.isdigit():
                raise ValueError('malformed listener endpoint')
            address = address.strip('[]')
            try:
                address = str(ipaddress.ip_address(address))
            except ValueError:
                pass  # Keep wildcards visible: they cannot pass as loopback.
            uid = re.search(r'\buid:(\d+)\b', line)
            cgroup = re.search(r'\bcgroup:(\S+)', line)
            inode = re.search(r'\bino:(\d+)\b', line)
            sockets.append({'protocol': protocol, 'address': address, 'port': int(port),
                            # ss -e omits uid:0. Missing extended observations
                            # still fail verification; plain ss output cannot pass.
                            'uid': int(uid[1]) if uid else 0,
                            'cgroup': cgroup[1] if cgroup else None,
                            'extended': inode is not None})
    return sockets


def verify(expected, sockets):
    for item in expected:
        found = [s for s in sockets if s['protocol'] == item['protocol']
                 and s['port'] == item['port'] and s['address'] == item['address']]
        allowed = {e['address'] for e in expected
                   if (e['protocol'], e['port']) == (item['protocol'], item['port'])}
        extra = any(s['protocol'] == item['protocol'] and s['port'] == item['port']
                    and s['address'] not in allowed for s in sockets)
        if extra or len(found) != 1:
            raise ValueError(f"{item['protocol']} {item['address']}:{item['port']}: missing or duplicate listener")
        sock = found[0]
        if not sock['extended'] or sock['uid'] not in item['uids'] or sock['cgroup'] != item['cgroup']:
            raise ValueError(f"{item['protocol']} {item['address']}:{item['port']}: listener identity mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('settings')
    parser.add_argument('groups', nargs='+')
    args = parser.parse_args()
    try:
        with open(args.settings) as stream:
            settings = json.load(stream)
        expected = [item for group in args.groups for item in settings['groups'][group]]
        verify(expected, snapshot(settings['ss'], {e['protocol'] for e in expected}))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('listeners: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
