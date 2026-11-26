"""Issue short-lived, link-bound leases for a root-requested source recovery."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
import fcntl
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time


SETS = {4: 'source_recovery4', 6: 'source_recovery6'}
FLUSH = ''.join(f'flush set inet lf_filter {name}\n' for name in SETS.values())
STATE = Path('/run/proxy-mode')
ACTIVE = STATE / 'source-recovery'
SERVICE = 'clash-source-recovery-dns.service'


def clock():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


@contextmanager
def policy_lock():
    with (STATE / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def nft(settings, content):
    subprocess.run([settings['nft'], '-f', '-'], input=content, text=True,
                   check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def route(settings, address, port):
    target = ipaddress.ip_address(address)
    if target.version == 6 and not settings['ipv6']:
        return None
    if target.version == 6 and target.ipv4_mapped:
        return None
    uid = settings['dns_uid'] if port == settings['dot_port'] else settings['uid']
    result = subprocess.run([settings['ip'], '-j', f'-{target.version}', 'route', 'get',
                             str(target), 'uid', str(uid)],
                            capture_output=True, check=False, timeout=2)
    if result.returncode:
        return None
    [entry] = json.loads(result.stdout)
    device = entry.get('dev')
    if device not in settings['uplinks'] or not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', device):
        return None
    source = ipaddress.ip_address(entry['prefsrc'])
    return target.version, f'{json.dumps(device)} . {source} . {target} . {int(port)}'


def install(settings, endpoints, deadline):
    leases = {family: set() for family in SETS}
    for address, port in set(endpoints):
        if clock() >= deadline:
            raise TimeoutError('recovery lease expired')
        item = route(settings, address, port)
        if item is not None:
            family, value = item
            leases[family].add(value)
    remaining = int((deadline - clock()) * 1000)
    if remaining <= 0:
        raise TimeoutError('recovery lease expired')
    if not any(leases.values()) or any(len(values) > 256 for values in leases.values()):
        raise ValueError('source recovery needs bounded routes through physical uplinks')
    commands = [FLUSH]
    for family, values in leases.items():
        if values:
            elements = ', '.join(f'{value} timeout {remaining}ms' for value in sorted(values))
            commands.append(f'add element inet lf_filter {SETS[family]} {{ {elements} }}\n')
    nft(settings, ''.join(commands))


def pinned_addresses(worker, registry, resolver, deadline, translation):
    specs = [registry['subscription'], *registry.get('providers', {}).values()]
    hosts = {worker.endpoint(url)[0] for spec in specs for url in (spec['url'], *spec.get('redirects', []))}
    if len(hosts) > 256:
        raise ValueError('too many source recovery hosts')

    def lookup(host):
        if clock() >= deadline:
            return host, []
        try:
            return host, worker.public_addresses(resolver, host, 443, translation)
        except (socket.gaierror, worker.Refused):
            # Unused redirect hosts need not resolve. A fetch of an unavailable
            # or invalid answer fails before opening a socket.
            return host, []

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(lookup, sorted(hosts)))


def control(settings, verb):
    subprocess.run([settings['systemctl'], verb, SERVICE], timeout=5, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def revoke(settings):
    with policy_lock():
        if ACTIVE.exists():
            # Invalidate the broker's version immediately. Keep the marker
            # visible to the link hooks until the kernel confirms the flush.
            ACTIVE.write_text('revoked\n')
        nft(settings, FLUSH)
        ACTIVE.unlink(missing_ok=True)


@contextmanager
def open_recovery(settings, worker, registry, nat64=()):
    if not settings:
        raise ValueError('source recovery is unavailable')
    with ExitStack() as cleanup:
        with policy_lock():
            if ((STATE / 'intent').read_text().strip() != 'proxy'
                    or (STATE / 'status').read_text().strip() != 'blocked'
                    or (STATE.parent / 'dns-policy/sleeping').exists()):
                raise ValueError('source recovery requires blocked proxy intent')
            version = (STATE / 'version').read_text()
            deadline = clock() + 55
            dot = [(address, settings['dot_port']) for address in settings['dot_addresses']]
            cleanup.callback(control, settings, 'stop')
            cleanup.callback(revoke, settings)
            ACTIVE.write_text(version)
            install(settings, dot, deadline)
        control(settings, 'start')
        resolver = worker.real_resolver(settings['resolver'])
        pinned = pinned_addresses(worker, registry, resolver, deadline, worker.translation_prefixes(nat64))
        https = [(address, 443) for addresses in pinned.values() for address in addresses]
        with policy_lock():
            if ACTIVE.read_text() != version or (STATE / 'version').read_text() != version:
                raise ValueError('source recovery policy changed')
            install(settings, dot + https, deadline)
        yield pinned
        with policy_lock():
            if clock() >= deadline or ACTIVE.read_text() != version or (STATE / 'version').read_text() != version:
                raise ValueError('source recovery expired or was revoked')


if __name__ == '__main__':
    revoke(json.loads(Path(sys.argv[1]).read_text())['recovery'])
