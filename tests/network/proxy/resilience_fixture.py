"""VM-only bounded pressure and UDP outage diagnostics using production paths."""

import argparse
import errno
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import time


LABELS = {'native4', 'native6', 'fake4', 'socks4', 'socks6'}


def run(*args, timeout=20, required=True, input=None):
    result = subprocess.run(args, input=input, capture_output=True, text=True, timeout=timeout)
    if required and result.returncode:
        raise AssertionError((args, result.returncode, result.stdout, result.stderr))
    return result


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def wait(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('fixture observation deadline exceeded')
        time.sleep(.1)


def mode():
    value = json.loads(Path('/run/proxy-mode/state.json').read_text())['mode']
    assert value in {'proxy', 'blocked'}, ('unexpected direct fallback', value)
    assert Path('/run/proxy-mode/intent').read_text().strip() == 'proxy'
    return value


def health(required=True):
    result = run(sys.executable, '/etc/netsec-health.py', '/etc/netsec-health.json',
                 timeout=16, required=False)
    value = json.loads(result.stdout)
    assert value['scope']['udp_data_plane'] == 'not-probed'
    assert value['scope']['requests'] == ['mixed-tcp', 'fake-ip-tcp']
    if required:
        assert result.returncode == 0 and value['ok'], value
    return value


def nft(table, rules):
    run('ip', 'netns', 'exec', 'outside', 'nft', '-f', '-', input=rules)


def records():
    result = {label: json.loads(Path(f'/tmp/receive-{label}.json').read_text()) for label in LABELS}
    assert all(time.monotonic() - value['at'] < 2 for value in result.values()), result
    assert all(result[label]['control_open'] for label in ('socks4', 'socks6')), result
    return result


def outages():
    # Keep the existing receive-only associations: no reconnect or heartbeat can
    # disguise the loss of their replies. The existing C/B test reuses them too.
    report = []
    run('systemd-run', '--unit=udp-fault-http6', 'ip', 'netns', 'exec', 'outside',
        sys.executable, '/etc/netsec-integration.py', 'http6')
    table = 'udp_outage_fixture'
    nft(table, f'add table inet {table}\n'
        f'add chain inet {table} input {{ type filter hook input priority -10; policy accept; }}\n'
        f'add chain inet {table} output {{ type filter hook output priority -10; policy accept; }}\n')
    # Drop at the receiving side. A local OUTPUT drop can report an error to
    # the sending proxy and tear down its mapping, rather than model wire loss.
    run('nft', '-f', '-', input=f'add table inet {table}\n'
        f'add chain inet {table} input {{ type filter hook input priority -203; policy accept; }}\n')

    def round_trip():
        value = health()
        run('setpriv', '--reuid=1000', '--clear-groups', 'curl', '--noproxy', '*',
            '-fsS', '--max-time', '4', 'http://[2001:db8:ffff::10]:8080/')
        assert mode() == 'proxy'
        return value

    def observe(expected):
        time.sleep(1.3)  # drain in-flight replies and allow explicit timeout state
        before = records()
        proof = round_trip()
        time.sleep(2)
        after = records()
        blocked = {label for label in LABELS if after[label]['count'] == before[label]['count']}
        assert blocked == expected, (expected, before, after)
        assert all(after[label]['udp_status'] == 'udp-timeout' for label in blocked), after
        classification = ('udp-available' if not blocked else 'udp-unavailable' if blocked == LABELS
            else 'ipv6-udp-unavailable' if blocked == {'native6', 'socks6'}
            else 'socks-udp-reply-unavailable' if blocked == {'socks4', 'socks6'} else 'unexpected')
        result = {'classification': classification, 'tcp_health': proof, 'mode': mode(),
                  'blocked': sorted(blocked), 'streams': after}
        report.append(result)
        print(json.dumps({'classification': classification, 'blocked': sorted(blocked)}), flush=True)

    def clear():
        nft(table, f'flush chain inet {table} input\nflush chain inet {table} output\n')
        run('nft', 'flush', 'chain', 'inet', table, 'input')
        before = records()
        try:
            wait(lambda: all(records()[label]['count'] > before[label]['count'] for label in LABELS))
        except AssertionError:
            raise AssertionError(('UDP recovery failed', before, records())) from None
        round_trip()

    try:
        wait(lambda: run('ip', 'netns', 'exec', 'outside', 'ss', '-6Hlnt',
                         'sport = :8080').stdout.strip() != '')
        observe(set())
        # SOCKS node UDP transport is IPv4 even for IPv6 application targets.
        nft(table, f'add rule inet {table} input udp dport 18080 counter drop\n')
        run('nft', '-f', '-', input=f'add rule inet {table} input iifname "ens1" udp sport 18080 counter drop\n')
        observe(LABELS)
        clear()
        nft(table, f'add rule inet {table} input ip6 daddr 2001:db8:ffff::10 udp dport 18090 counter drop\n'
            f'add rule inet {table} input ip6 saddr 2001:db8:ffff::10 udp sport 18090 counter drop\n')
        observe({'native6', 'socks6'})
        clear()
        # Cut only the exact origin reply sockets used by the SOCKS clients;
        # their TCP association controls and native UDP streams stay live.
        rules = []
        for label, address in (('socks4', '203.0.113.10'), ('socks6', '2001:db8:ffff::10')):
            peer = json.loads(Path(f'/tmp/push-peer-{label}.json').read_text())
            family = 'ip6' if ':' in address else 'ip'
            rules.append(f'add rule inet {table} input {family} saddr {address} '
                         f'udp sport 18090 udp dport {peer["port"]} counter drop')
        nft(table, '\n'.join(rules) + '\n')
        observe({'socks4', 'socks6'})
        clear()
        save('/tmp/udp-diagnostics.json', report)
    finally:
        nft(table, f'delete table inet {table}\n')
        run('nft', 'delete', 'table', 'inet', table)
        run('systemctl', 'stop', 'udp-fault-http6.service')


def load(kind, count, seconds):
    assert os.getuid() == 1001, 'load must run as the separate unprivileged VM account'
    assert 1 <= count <= 256 and 1 <= seconds <= 20
    sockets, responders = [], set()
    stats = {'kind': kind, 'uid': os.getuid(), 'opened': 0, 'sent': 0,
             'received': 0, 'refused': 0, 'send_errors': 0, 'responders': 0}
    path = Path('/tmp/load-' + kind + '.json')
    try:
        for _ in range(count):
            if kind == 'slow':
                stream = socket.create_connection(('127.0.0.1', 33332), timeout=2)
                stream.sendall(b'CONNECT node.fixture.example:8080 HTTP/1.1\r\nHost:')
            elif kind == 'syn':
                stream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                stream.setblocking(False)
                error = stream.connect_ex(('100.67.0.250', 18112))
                assert error in (0, errno.EINPROGRESS, errno.EALREADY, errno.EWOULDBLOCK), error
            else:
                stream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                stream.connect(('203.0.113.10', 18096) if kind == 'udp' else
                               ('127.0.0.1', 1054) if kind == 'dns' else ('100.67.0.250', 18111))
            stream.setblocking(False)
            sockets.append(stream)
        stats['opened'] = len(sockets)
        save(path, stats)
        deadline, next_send, next_record, sequence = time.monotonic() + seconds, 0, 0, 0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if kind not in {'slow', 'syn'} and now >= next_send:
                for index, stream in enumerate(sockets):
                    if kind == 'dns':
                        name = f'load-{index}-{sequence}.fixture.example'
                        payload = struct.pack('!6H', sequence % 65536, 0x100, 1, 0, 0, 0)
                        payload += b''.join(bytes([len(part)]) + part.encode() for part in name.split('.'))
                        payload += b'\0\0\1\0\1'
                    else:
                        payload = b'bounded-load'
                    try:
                        stream.send(payload)
                        stats['sent'] += 1
                    except (BlockingIOError, OSError):
                        stats['send_errors'] += 1
                sequence += 1
                next_send = now + .5
            if kind in {'udp', 'dns'}:
                for stream in select.select(sockets, [], [], .05)[0]:
                    try:
                        data = stream.recv(4096)
                    except OSError:
                        continue
                    if kind == 'udp':
                        assert data == b'bounded-load'
                    else:
                        assert len(data) >= 12 and data[2] & 0x80
                        stats['refused'] += int(data[3] & 15 != 0)
                    stats['received'] += 1
                    responders.add(stream.fileno())
            else:
                time.sleep(.05)
            if now >= next_record:
                stats['responders'] = len(responders)
                save(path, stats)
                next_record = now + .5
    finally:
        for stream in sockets:
            stream.close()
        save(path, stats)


def resources():
    result = {'conntrack_count': int(Path('/proc/sys/net/netfilter/nf_conntrack_count').read_text()),
              'conntrack_max': int(Path('/proc/sys/net/netfilter/nf_conntrack_max').read_text())}
    for unit in ('clash-verge', 'gost-relay'):
        group = run('systemctl', 'show', unit, '--property=ControlGroup', '--value').stdout.strip()
        path = Path('/sys/fs/cgroup' + group)
        fds = 0
        for pid in (path / 'cgroup.procs').read_text().split():
            try:
                fds += len(list(Path('/proc', pid, 'fd').iterdir()))
            except FileNotFoundError:
                pass
        result[unit] = {'fds': fds, 'memory_bytes': int((path / 'memory.current').read_text()),
                        'tasks': int((path / 'pids.current').read_text())}
    result['core_log_bytes'] = sum(p.stat().st_size for p in Path('/var/lib/clash-verge-service/logs').glob('*.log'))
    stats = {}
    for line in run('conntrack', '-S').stdout.splitlines():
        for field in line.split():
            key, value = field.split('=')
            if key != 'cpu':
                stats[key] = stats.get(key, 0) + int(value)
    result['conntrack_stats'] = stats
    return result


def pressure():
    table = 'pressure_fixture'
    maximum = Path('/proc/sys/net/netfilter/nf_conntrack_max')
    original_max = maximum.read_text()
    reports = []
    run('ip', '-n', 'outside', 'addr', 'add', '100.67.0.250/32', 'dev', 'peer0')
    run('systemd-run', '--unit=pressure-echo', 'ip', 'netns', 'exec', 'outside',
        sys.executable, '/etc/netsec-dataplane.py', 'echo')
    nft(table, f'add table inet {table}\n'
        f'add chain inet {table} input {{ type filter hook input priority -10; policy accept; }}\n'
        f'add rule inet {table} input ip daddr 100.67.0.250 udp dport 18111 drop\n'
        f'add rule inet {table} input ip daddr 100.67.0.250 tcp dport 18112 drop\n'
        f'add rule inet {table} input ip saddr 100.67.0.2 ip daddr 203.0.113.10 '
        f'meta l4proto {{ tcp, udp }} th dport {{ 18094, 18096 }} counter\n')

    def start(kind, count):
        path = Path('/tmp/load-' + kind + '.json')
        path.unlink(missing_ok=True)
        run('systemd-run', '--unit=pressure-' + kind, '--uid=1001',
            '--property=MemoryMax=64M', '--property=TasksMax=16', '--property=LimitNOFILE=512',
            '--property=RuntimeMaxSec=25', sys.executable, __file__, 'load', kind, str(count), '20')
        wait(lambda: path.exists())
        assert json.loads(path.read_text())['opened'] == count
        return path

    def normal(kind):
        value = health()
        for proxy in ([], ['-x', 'http://127.0.0.1:33332']):
            run('setpriv', '--reuid=1000', '--clear-groups', 'curl', '--noproxy', '' if proxy else '*',
                '-fsS', '--max-time', '5', *proxy, 'http://203.0.113.10:18094/1024')
        run('setpriv', '--reuid=1000', '--clear-groups', sys.executable, '/etc/netsec-dns.py',
            'query', '203.0.113.10', '18096')
        response = run('setpriv', '--reuid=1000', '--clear-groups', 'dig', '+time=2', '+tries=1',
                       '@127.0.0.1', '-p', '1054', f'normal-{kind}.fixture.example', 'A').stdout
        assert 'status: NOERROR' in response and '198.18.' in response, response
        assert mode() == 'proxy'
        return value

    try:
        wait(lambda: run('ip', 'netns', 'exec', 'outside', 'ss', '-Huln',
                         'sport = :18096').stdout.strip() != '')
        baseline = resources()
        for kind, count in (('syn', 96), ('slow', 96), ('udp', 128), ('dns', 64)):
            before = resources()
            path = start(kind, count)
            try:
                if kind == 'syn':
                    assert len(run('ss', '-Hnt', 'state', 'syn-sent', 'dst', '100.67.0.250').stdout.splitlines()) >= 64
                samples = []
                for _ in range(2):
                    proof = normal(kind)
                    samples.append({'resources': resources(), 'health': proof})
                    time.sleep(2)
                work = json.loads(path.read_text())
                if kind in {'udp', 'dns'}:
                    assert work['responders'] == count, work
                if kind == 'slow':
                    assert max(s['resources']['gost-relay']['fds'] for s in samples) >= before['gost-relay']['fds'] + 64
                reports.append({'kind': kind, 'before': before, 'samples': samples, 'workload': work})
            finally:
                run('systemctl', 'stop', 'pressure-' + kind)
        # Exhaust only this VM's remaining conntrack headroom. Normal trials
        # above retain the production limit; this is an explicit fault injection.
        before = resources()
        limit = before['conntrack_count'] + 32
        assert limit < int(original_max)
        maximum.write_text(str(limit))
        path = start('conntrack', 256)
        try:
            def exhausted():
                now = resources()['conntrack_stats']
                return any(now.get(key, 0) > before['conntrack_stats'].get(key, 0)
                           for key in ('drop', 'early_drop', 'insert_failed'))
            wait(exhausted)
            samples = []
            for _ in range(2):
                samples.append({'resources': resources(), 'health': health(required=False), 'mode': mode()})
                time.sleep(1)
            reports.append({'kind': 'conntrack-exhaustion', 'injected_max': limit, 'before': before,
                            'samples': samples, 'workload': json.loads(path.read_text())})
        finally:
            maximum.write_text(original_max)
            run('systemctl', 'stop', 'pressure-conntrack')
            run('conntrack', '-D', '-p', 'udp', '--orig-dst', '100.67.0.250', required=False)
        wait(lambda: mode() == 'proxy', timeout=45)
        normal('recovered')
        counters = json.loads(run('ip', 'netns', 'exec', 'outside', 'nft', '-j', 'list',
                                 'chain', 'inet', table, 'input').stdout)['nftables']
        assert sum(expr['counter']['packets'] for entry in counters
                   for expr in entry.get('rule', {}).get('expr', []) if 'counter' in expr) == 0, 'public direct leak'
        after = resources()
        assert after['core_log_bytes'] - baseline['core_log_bytes'] < 8 * 1024 * 1024
        assert all(sample['resources']['clash-verge']['memory_bytes'] < baseline['clash-verge']['memory_bytes'] + 256 * 1024 * 1024
                   for report in reports for sample in report['samples'])
        save('/tmp/pressure-report.json', {'scope': 'bounded single-UID VM workloads; not a general DoS guarantee',
                                         'reports': reports, 'after': after})
        print(json.dumps({'pressure_summary': [
            {'kind': r['kind'], 'opened': r['workload']['opened'],
             'conntrack_peak': max(s['resources']['conntrack_count'] for s in r['samples']),
             'core_fds_peak': max(s['resources']['clash-verge']['fds'] for s in r['samples']),
             'gost_fds_peak': max(s['resources']['gost-relay']['fds'] for s in r['samples'])}
            for r in reports], 'core_log_growth_bytes': after['core_log_bytes'] - baseline['core_log_bytes']}), flush=True)
        print('Bounded SYN/slow/UDP/DNS pressure, conntrack exhaustion, no-direct and recovery passed', flush=True)
    finally:
        maximum.write_text(original_max)
        for kind in ('syn', 'slow', 'udp', 'dns', 'conntrack'):
            run('systemctl', 'stop', 'pressure-' + kind, required=False)
        nft(table, f'delete table inet {table}\n')
        run('systemctl', 'stop', 'pressure-echo')
        run('ip', '-n', 'outside', 'addr', 'del', '100.67.0.250/32', 'dev', 'peer0')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('load', 'outages', 'pressure'))
    parser.add_argument('load_args', nargs='*')
    args = parser.parse_args()
    if args.mode == 'load':
        kind, count, seconds = args.load_args
        assert kind in {'syn', 'slow', 'udp', 'dns', 'conntrack'}
        load(kind, int(count), int(seconds))
    else:
        assert os.geteuid() == 0 and Path('/etc/netsec-control.py').exists(), 'isolated control VM only'
        {'outages': outages, 'pressure': pressure}[args.mode]()
