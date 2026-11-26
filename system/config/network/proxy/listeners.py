"""Authenticate passive TCP/UDP listeners; never connect to a redirect port."""

import argparse
import importlib.util
import ipaddress
import json
import re
import subprocess
import sys


class ListenerMismatch(ValueError):
    """An observed socket failed the expected endpoint or owner check."""


def snapshot(ss, protocols, run=subprocess.run, *, family=None):
    sockets = []
    for protocol in sorted(protocols):
        result = run([ss, *([f'-{family}'] if family else []),
                      '-H', '-lne' + ('t' if protocol == 'tcp' else 'u')],
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
            if family == 6:
                v6only = re.search(r'\bv6only:([01])\b', line)
                sockets[-1]['ipv6_only'] = v6only[1] == '1' if v6only else None
    return sockets


def verify(expected, sockets):
    for item in expected:
        found = [s for s in sockets if s['protocol'] == item['protocol']
                 and s['port'] == item['port'] and s['address'] == item['address']
                 and s.get('namespace', False) == item.get('namespace', False)]
        allowed = {e['address'] for e in expected
                   if (e['protocol'], e['port']) == (item['protocol'], item['port'])}
        extra = any(s.get('namespace', False) == item.get('namespace', False)
                    and s['protocol'] == item['protocol'] and s['port'] == item['port']
                    and s['address'] not in allowed for s in sockets)
        if extra or len(found) != 1:
            raise ListenerMismatch(f"{item['protocol']} {item['address']}:{item['port']}: missing or duplicate listener")
        sock = found[0]
        if not sock['extended'] or sock['uid'] not in item['uids'] or sock['cgroup'] != item['cgroup']:
            raise ListenerMismatch(f"{item['protocol']} {item['address']}:{item['port']}: listener identity mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('settings')
    parser.add_argument('groups', nargs='+')
    args = parser.parse_args()
    try:
        with open(args.settings) as stream:
            settings = json.load(stream)
        expected = [item for group in args.groups for item in settings['groups'][group]]
        sockets = []
        if any(not item.get('namespace') for item in expected):
            sockets.extend(snapshot(settings['ss'], {e['protocol'] for e in expected if not e.get('namespace')}))
        if any(item.get('namespace') for item in expected):
            spec = importlib.util.spec_from_file_location('core_network', settings['observer_module'])
            observer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(observer)
            sockets.extend(observer.request(settings['observer'], 'listeners')['sockets'])
        verify(expected, sockets)
    except ListenerMismatch as error:
        print('listeners: ' + str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('listeners: ' + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
