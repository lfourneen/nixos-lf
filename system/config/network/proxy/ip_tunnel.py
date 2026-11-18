"""Root-owned WireGuard path shared by the strict relay and native ICMP."""
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time


def command(settings, tool, *args, **kwargs):
    return subprocess.run([settings[tool], *map(str, args)], capture_output=True,
                          text=True, check=True, timeout=4, **kwargs).stdout.strip()


def addresses(policy):
    values = {key: ipaddress.ip_address(policy[key]) for key in
              ('endpointAddress', 'relayAddress', 'clientIpv4', 'clientIpv6', 'probeIpv4', 'probeIpv6')}
    for key, address in values.items():
        if str(address) != policy[key] or address.is_multicast or address.is_unspecified or address.is_loopback:
            raise ValueError('non-canonical tunnel address')
        if key.endswith(('4', '6')) and address.version != int(key[-1]):
            raise ValueError('incorrect tunnel address family')
        if not key.startswith('client') and not address.is_global:
            raise ValueError('remote tunnel addresses must be public')
    distinct = [address for key, address in values.items()
                if key != 'relayAddress' or address != values['endpointAddress']]
    if len(set(distinct)) != len(distinct):
        raise ValueError('tunnel addresses must be distinct')
    return values


def endpoint(policy, address):
    host = f'[{address}]' if address.version == 6 else str(address)
    return f'{host}:{policy["endpointPort"]}'


def rules(policy, values, family):
    relay = values['relayAddress']
    icmp = 'icmp' if family == 4 else 'ipv6-icmp'
    candidates = [
        (['fwmark', policy['outerMark']], ['lookup', 'main']),
        (['fwmark', policy['outerMark']], ['unreachable']),
        (['fwmark', policy['echoMark']], ['lookup', policy['table']]),
        (['fwmark', policy['echoMark']], ['unreachable']),
        (['to', str(relay)], ['lookup', policy['table']]) if relay.version == family else None,
        (['to', str(relay)], ['unreachable']) if relay.version == family else None,
    ] + [
        (['oif', uplink, 'ipproto', icmp], ['lookup', 'main']) for uplink in policy['uplinks']
    ] + [
        (['iif', 'lo', 'ipproto', icmp], ['lookup', policy['table']]),
        (['iif', 'lo', 'ipproto', icmp], ['unreachable']),
    ]
    return [(policy['priority'] + index, item[0] + item[1])
            for index, item in enumerate(candidates) if item is not None]


def stop(settings):
    owner = Path(settings['ownership'])
    if not owner.exists():
        return
    policy = json.loads(owner.read_text())
    device = policy['device']
    # Exact selectors avoid deleting another RPDB user's rule on a collision.
    values = addresses(policy)
    for family in (4, 6):
        for priority, arguments in rules(policy, values, family):
            existing = json.loads(command(settings, 'ip', '-j', f'-{family}', 'rule', 'show', 'pref', priority))
            if existing:
                command(settings, 'ip', f'-{family}', 'rule', 'del', 'pref', priority, *arguments)
    if Path('/sys/class/net', device).exists():
        links = json.loads(command(settings, 'ip', '-j', '-d', 'link', 'show', 'dev', device))
        if (links[0].get('ifalias') not in (None, 'lf-managed-ip-tunnel')
                or links[0]['linkinfo']['info_kind'] != 'wireguard'):
            raise ValueError('tunnel interface is already in use')
        command(settings, 'ip', 'link', 'del', device)
    owner.unlink()


def start(settings):
    policy = settings['policy']
    values = addresses(policy)
    stop(settings)
    device, table = policy['device'], policy['table']
    if Path('/sys/class/net', device).exists():
        raise ValueError('tunnel interface is already in use')
    for family in (4, 6):
        existing = json.loads(command(settings, 'ip', '-j', f'-{family}', 'route', 'show', 'table', 'all'))
        if any(str(route.get('table')) == str(table) for route in existing):
            raise ValueError('tunnel route table is already in use')
        for priority, _ in rules(policy, values, family):
            if json.loads(command(settings, 'ip', '-j', f'-{family}', 'rule', 'show', 'pref', priority)):
                raise ValueError('tunnel routing priority is already in use')
    key = os.open(settings['privateKeyFile'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(key, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError('unsafe WireGuard private key')
        Path(settings['ownership']).write_text(json.dumps(policy))
        command(settings, 'ip', 'link', 'add', device, 'type', 'wireguard')
        command(settings, 'ip', 'link', 'set', device, 'alias', 'lf-managed-ip-tunnel')
        command(settings, 'wg', 'set', device, 'private-key', '/proc/self/fd/0',
                'listen-port', policy['listenPort'], 'fwmark', policy['outerMark'], 'peer', policy['publicKey'],
                'endpoint', endpoint(policy, values['endpointAddress']),
                'allowed-ips', '0.0.0.0/0,::/0', 'persistent-keepalive', 25, stdin=stream)
    command(settings, 'ip', 'link', 'set', device, 'mtu', policy['mtu'], 'up')
    for family in (4, 6):
        client = f'{values[f"clientIpv{family}"]}/{32 if family == 4 else 128}'
        command(settings, 'ip', f'-{family}', 'address', 'add', client, 'dev', device,
                *(['nodad'] if family == 6 else []))
        command(settings, 'ip', f'-{family}', 'route', 'add', 'default', 'dev', device,
                'src', values[f'clientIpv{family}'], 'table', table)
        for priority, arguments in rules(policy, values, family):
            command(settings, 'ip', f'-{family}', 'rule', 'add', 'pref', priority, *arguments)


class PathFailure(ValueError):
    pass


def verify(settings):
    policy = settings['policy']
    device, peer = policy['device'], policy['publicKey']
    try:
        values = addresses(policy)
        if (command(settings, 'wg', 'show', device, 'peers') != peer
                or command(settings, 'wg', 'show', device, 'listen-port') != str(policy['listenPort'])
                or command(settings, 'wg', 'show', device, 'fwmark') != policy['outerMark']
                or command(settings, 'wg', 'show', device, 'endpoints').split() !=
                [peer, endpoint(policy, values['endpointAddress'])]
                or set(command(settings, 'wg', 'show', device, 'allowed-ips').split()) !=
                {peer, '0.0.0.0/0', '::/0'}):
            raise ValueError
    except (OSError, ValueError, subprocess.SubprocessError):
        raise PathFailure('peer-config') from None
    for family in (4, 6):
        protocol = 'icmp' if family == 4 else 'ipv6-icmp'
        try:
            route = json.loads(command(settings, 'ip', '-j', f'-{family}', 'route', 'get',
                                       values[f'probeIpv{family}'], 'ipproto', protocol))[0]
            if route.get('dev') != device or route.get('prefsrc') != str(values[f'clientIpv{family}']):
                raise ValueError
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            raise PathFailure(f'route{family}') from None


def probe(settings, fast):
    policy = settings['policy']

    def echo(family):
        for seconds in (2, 1):
            try:
                command(settings, 'ping', f'-{family}', '-n', '-c', '1', '-w', seconds,
                        '-m', str(int(policy['probeMark'], 16)), '-I', policy[f'clientIpv{family}'], policy[f'probeIpv{family}'])
                return True
            except subprocess.CalledProcessError as error:
                if error.returncode != 1:  # ping(8): only no reply merits a retry
                    break
            except (OSError, subprocess.TimeoutExpired):
                break
        return False

    result = {'stage': 'path-verified'}
    try:
        verify(settings)
        if fast:
            return result
        with ThreadPoolExecutor(max_workers=2) as workers:
            echo4, echo6 = workers.map(echo, (4, 6))
        result['echo'] = {'ipv4': echo4, 'ipv6': echo6}
        verify(settings)
        if echo4 and echo6:
            result['stage'] = 'echo-verified'
        elif echo4 or echo6:
            result['stage'] = 'echo6' if echo4 else 'echo4'
        else:
            result['stage'] = 'echo-both'
            try:
                _, stamp = command(settings, 'wg', 'show', policy['device'], 'latest-handshakes').split()
                age = time.time() - int(stamp)
                result['handshake'] = 'recent' if 0 <= age <= 180 else 'none-recent'
            except (OSError, ValueError, subprocess.SubprocessError):
                result['handshake'] = 'unavailable'
    except PathFailure as error:
        result['stage'] = str(error)
    return result


if __name__ == '__main__':
    print(json.dumps(probe(json.load(sys.stdin), fast=sys.argv[1] == 'verify')))
