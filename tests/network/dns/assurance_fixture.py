"""Isolated VM checks using the real validators, core and kernel policy."""
import json
import importlib.util
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

import dns.flags
import dns.exception
import dns.message
import dns.query
import dns.rcode
import dns.rdatatype


def run(*argv, required=True, timeout=20):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
    if required:
        assert result.returncode == 0, (argv, result.stdout, result.stderr)
    return result


def wait(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'observation deadline'
        time.sleep(.1)


def query(name, port, kind='A', tcp=True, cd=False, timeout=4):
    message = dns.message.make_query(name, kind, want_dnssec=True)
    if cd:
        message.flags |= dns.flags.CD
    return (dns.query.tcp if tcp else dns.query.udp)(message, '127.0.0.1', port=port, timeout=timeout)


def cache_checks():
    spec = importlib.util.spec_from_file_location('health', '/etc/netsec-health.py')
    health = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(health)
    settings = json.loads(Path('/etc/netsec-health.json').read_text())
    name = 'cache.signed.fixture.example'
    settings['tun_probe_names'] = [name]

    def upstream_queries():
        log = Path('/run/netsec-doh.jsonl')
        return sum(json.loads(line)['name'] == name + '.' for line in log.read_text().splitlines()) if log.exists() else 0

    before = upstream_queries()
    cold = health.Probe(settings)
    cold.timed('application_dns', cold.application_dns)
    after_cold = upstream_queries()
    assert after_cold > before, 'cold lookup did not reach DoH'
    warm = health.Probe(settings)
    warm.timed('application_dns', warm.application_dns)
    assert warm.fake_ips[name] == cold.fake_ips[name]
    assert upstream_queries() == after_cold, 'warm lookup reached DoH'

    answer = run('ip', 'netns', 'exec', 'lf-clash-core', 'dig', '+tcp', '+dnssec',
                 '+noall', '+answer', '@127.0.0.1', '-p', '1055', 'short.signed.fixture.example', 'A')
    records = [line.split() for line in answer.stdout.splitlines()]
    assert any(fields[3:] == ['A', '203.0.113.43'] and int(fields[1]) <= 1
               for fields in records), answer.stdout
    print(json.dumps({'application_dns_cold_ms': cold.timings['application_dns'],
                      'application_dns_warm_ms': warm.timings['application_dns'],
                      'additional_warm_doh_queries': 0}), flush=True)


def dns_checks():
    cache_checks()
    for exchange in (dns.query.tcp, dns.query.udp):
        request = dns.message.make_query('bogus.signed.fixture.example', 'A')
        request.question.append(request.question[0])
        response = exchange(request, '127.0.0.1', port=1053, timeout=4)
        assert response.rcode() == dns.rcode.FORMERR and not response.answer, response
    run('curl', '--noproxy', '', '--socks5-hostname', '127.0.0.1:7897',
        '-fsS', '--max-time', '5', 'http://valid.signed.fixture.example:8080/')
    for port in (1055, 1056, 1057, 1058):
        with socket.create_connection(('127.0.0.1', 7897), timeout=3) as stream:
            stream.sendall(b'\x05\x01\x00')
            assert stream.recv(2) == b'\x05\x00'
            stream.sendall(b'\x05\x01\x00\x01' + socket.inet_aton('127.0.0.1') + struct.pack('!H', port))
            reply = stream.recv(10, socket.MSG_WAITALL)
            assert len(reply) == 10
            if reply[1] == 0:
                # The inlet acknowledges SOCKS before its guarded outbound dial.
                # It must not provide a usable DNS stream to the application.
                packet = dns.message.make_query('valid.signed.fixture.example', 'A').to_wire()
                try:
                    stream.sendall(struct.pack('!H', len(packet)) + packet)
                    assert stream.recv(2) == b'', 'application reached a local validator'
                except (ConnectionResetError, BrokenPipeError, TimeoutError):
                    pass
    for tcp in (False, True):
        for port in (1053, 1054):
            valid = query('valid.signed.fixture.example', port, tcp=tcp)
            assert valid.rcode() == dns.rcode.NOERROR and '198.18.' in valid.to_text(), valid
            assert not valid.flags & dns.flags.AD, 'synthetic addresses cannot claim DNSSEC AD'
            for cd in (False, True):
                bogus = query('bogus.signed.fixture.example', port, tcp=tcp, cd=cd)
                assert bogus.rcode() == dns.rcode.SERVFAIL and not bogus.answer, bogus
            negative = query('absent.signed.fixture.example', port, tcp=tcp)
            assert negative.rcode() == dns.rcode.NXDOMAIN, negative
            nodata = query('service.signed.fixture.example', port, tcp=tcp)
            assert nodata.rcode() == dns.rcode.NOERROR and not nodata.answer, nodata
            service = query('service.signed.fixture.example', port, 'HTTPS', tcp=tcp)
            assert service.rcode() == dns.rcode.NOERROR and 'alpn="h2"' in service.to_text(), service
            unsigned = query('node.fixture.example', port, tcp=tcp)
            assert unsigned.rcode() == dns.rcode.NOERROR and not unsigned.flags & dns.flags.AD, unsigned
    # A subscription hosts entry or an existing fake-IP cannot rescue a bogus
    # target at the final dial boundary.
    result = run('curl', '--noproxy', '', '--socks5-hostname', '127.0.0.1:7897',
                 '-fsS', '--max-time', '5', 'http://bogus.signed.fixture.example:8080/', required=False)
    assert result.returncode != 0
    # The local validator must retain the encrypted proxy upstream even when
    # Mode A's authenticated resolver returns a wrong unsigned address.
    strict = json.loads(Path('/run/proxy-mode/state.json').read_text()).get('routing_applied') == 'proxy-only'
    host_answer = query('unsigned.fixture.example', 1056 if strict else 1061)
    assert ('203.0.113.42' if strict else '203.0.113.66') in host_answer.to_text(), host_answer
    run('curl', '--noproxy', '', '--socks5-hostname', '127.0.0.1:7897',
        '-fsS', '--max-time', '5', 'http://unsigned.fixture.example:8080/')
    queries = [json.loads(line) for line in Path('/run/netsec-doh.jsonl').read_text().splitlines()]
    assert any(item['name'] == 'unsigned.fixture.example.' and item['client'] != '100.67.0.2'
               for item in queries), queries
    validator = int(run('systemctl', 'show', '-p', 'MainPID', '--value', 'clash-dns-application.service').stdout)
    os.kill(validator, signal.SIGSTOP)
    try:
        failed = query('valid.signed.fixture.example', 1053)
        assert failed.rcode() == dns.rcode.SERVFAIL, failed
        assert Path('/run/proxy-mode/intent').read_text().strip() == 'proxy'
        assert 'accept' not in run('nft', 'list', 'chain', 'inet', 'lf_filter', 'dns_plaintext_mode').stdout
    finally:
        os.kill(validator, signal.SIGCONT)
    print('DNSSEC: valid, bogus/CD, signed denial, unsigned, HTTPS and validator outage passed', flush=True)


def direct():
    for tcp in (False, True):
        valid = query('valid.signed.fixture.example', 1055, tcp=tcp)
        assert valid.rcode() == dns.rcode.NOERROR and valid.flags & dns.flags.AD, valid
        for cd in (False, True):
            bogus = query('bogus.signed.fixture.example', 1054, tcp=tcp, cd=cd)
            assert bogus.rcode() == dns.rcode.SERVFAIL, bogus
    print('A local DNSSEC: valid and bogus/CD over TCP/UDP passed', flush=True)


def observed(name, transport):
    logfile = Path(f'/run/netsec-{transport}.jsonl')
    return logfile.exists() and any(json.loads(line)['name'] == name + '.'
                                    for line in logfile.read_text().splitlines())


def dns_handoff_start():
    name = 'pending.dns-path.fixture.example'
    wait(lambda: json.loads(Path('/run/dns-upstream/state.json').read_text())['mode'] == 'dot', 25)
    Path('/run/netsec-dot-hold').touch()
    run('systemd-run', '--unit=netsec-a-dns-query.service', '--uid=1000',
        '--property=StandardOutput=append:/tmp/netsec-a-dns-answer',
        'dig', '+tcp', '+time=70', '+tries=1', '+short', '@127.0.0.1', '-p', '1054', name, 'A')
    wait(lambda: observed(name, 'dot'))
    run('systemctl', 'freeze', 'dns-upstream.service')


def dns_handoff_finish():
    assert 'server=127.0.0.1#1055' in Path('/run/dns-upstream/servers.conf').read_text()
    assert Path('/run/proxy-mode/status').read_text().strip() == 'proxy'
    Path('/run/netsec-dot-hold').unlink()
    name = 'stale.dns-path.fixture.example'
    # The DNS coordinator is frozen with the old A forwarding file active.
    # The independent kernel policy must prevent a new upstream request.
    response = run('setpriv', '--reuid=1000', '--clear-groups', 'dig', '+time=2', '+tries=1', '+short',
                   '@127.0.0.1', '-p', '1054', name, 'A', required=False)
    assert '203.0.113.66' not in response.stdout and not observed(name, 'dot'), response
    answer = Path('/tmp/netsec-a-dns-answer')
    assert not answer.exists() or '203.0.113.66' not in answer.read_text()
    run('systemctl', 'stop', 'netsec-a-dns-query.service', required=False)
    print('DNS handoff: stale A forwarding and its pending reply stayed isolated in B', flush=True)


def dns_degrade():
    status = Path('/run/dns-upstream/state.json')

    def await_mode(mode):
        deadline = time.monotonic() + 28
        while (state := json.loads(status.read_text()))['mode'] != mode:
            assert time.monotonic() < deadline, (
                mode, state, run('nft', 'list', 'table', 'inet', 'dns_path_fixture', required=False).stdout)
            # Wake both rounds even if the healthy loop had already backed off.
            run('systemctl', 'kill', '-s', 'WINCH', '--kill-whom=main', 'dns-upstream.service')
            time.sleep(2)

    # Fail only the DNS coordinator's UDP probes, keeping core health and the
    # listener available to ordinary clients.
    run('nft', 'add', 'table', 'inet', 'dns_path_fixture')
    try:
        run('nft', 'add chain inet dns_path_fixture output { type filter hook output priority -200; policy accept; }')
        run('nft', 'add rule inet dns_path_fixture output socket cgroupv2 level 2 '
                   '"system.slice/dns-upstream.service" udp dport 1053 counter reject with icmpx type port-unreachable')
        await_mode('degraded-proxy')
        assert Path('/run/proxy-mode/status').read_text().strip() == 'proxy'
        assert 'server=127.0.0.1#1053' in Path('/run/dns-upstream/servers.conf').read_text()
        name = 'degraded.dns-path.fixture.example'
        answer = run('setpriv', '--reuid=1000', '--clear-groups', 'dig', '+time=4', '+tries=1',
                     '+short', '@127.0.0.1', '-p', '1054', name, 'A').stdout
        assert answer.startswith('198.18.') and observed(name, 'doh') and not observed(name, 'dot'), answer
    finally:
        run('nft', 'delete', 'table', 'inet', 'dns_path_fixture')
    await_mode('proxy')
    print('DNS degradation: B stayed on the managed upstream and recovered after probes resumed', flush=True)


def dns_roles():
    selected = json.loads(Path('/run/proxy-mode/state.json').read_text())['routing_applied']
    via_proxy = selected == 'proxy-only'
    for role, uid, resolver, port in (
        ('source', run('id', '-u', 'clash-fetch').stdout.strip(), '127.0.0.1', '1056'),
        ('guest', '1100', '127.0.0.53', '53'),
    ):
        name = f'{role}-{selected}.dns-path.fixture.example'
        answer = run('setpriv', f'--reuid={uid}', '--clear-groups', 'dig', '+time=4', '+tries=1',
                     '+short', '@' + resolver, '-p', port, name, 'A').stdout
        assert ('203.0.113.42' if via_proxy else '203.0.113.66') in answer, answer
        assert observed(name, 'doh' if via_proxy else 'dot')
        if via_proxy:
            assert not observed(name, 'dot')
    if not via_proxy:
        name = 'stale-real-address.dns-path.fixture.example'
        answer = query(name, 1059)
        assert answer.rcode() == dns.rcode.SERVFAIL and not observed(name, 'dot'), answer
    print(f'DNS roles: {selected} preserved filtered source and guest resolution', flush=True)


def configure():
    path = Path('/run/netsec-real-profile.json')
    value = json.loads(path.read_text())
    value['rules'].insert(0, 'IP-CIDR,127.0.0.0/8,DIRECT,no-resolve')
    path.write_text(json.dumps(value))


def http():
    class Handler(SimpleHTTPRequestHandler):
        def end_headers(self):
            if Path('/run/netsec-health-hold').exists():
                time.sleep(.4)
            super().end_headers()
    ThreadingHTTPServer(('0.0.0.0', int(sys.argv[2])), Handler).serve_forever()


def health_checks():
    spec = importlib.util.spec_from_file_location('health', '/etc/netsec-health.py')
    health = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(health)
    settings = json.loads(Path('/etc/netsec-health.json').read_text())
    original_line = health.route_line
    probe = health.Probe(settings)
    probe.run()
    assert all(probe.diagnostics.values()), probe.diagnostics

    # Exercise delayed delivery with actual core logs and no table fallback.
    seen = {}
    def delayed(*args):
        key = args[1:]
        first = seen.setdefault(key, time.monotonic())
        return original_line(*args) if time.monotonic() - first > 1.1 else None
    health.route_line = delayed
    probe = health.Probe(settings)
    original_request = probe.request
    def logs_only(*args):
        output, _records = original_request(*args)
        return output, []
    probe.request = logs_only
    probe.run()
    assert set(probe.route_sources.values()) == {'log'}, probe.route_sources
    assert probe.timings['mixed_route'] >= 1000 and probe.timings['tun_route'] >= 1000, probe.timings

    # Hold real HTTP replies long enough to read both live controller records.
    health.route_line = lambda *_args: None
    hold = Path('/run/netsec-health-hold')
    hold.touch()
    try:
        probe = health.Probe(settings)
        probe.run()
        assert probe.route_sources == {'mixed': 'connections', 'TUN': 'connections'}, probe.route_sources
    finally:
        hold.unlink()
        health.route_line = original_line

    # A cold timeout retries the actual DNS path within the same full probe.
    probe = health.Probe(settings)
    command = probe.command
    attempts = set()
    def cold(argv, maximum):
        if argv[0] == settings['dig'] and '@127.0.0.1' in argv and argv[-2] not in attempts:
            attempts.add(argv[-2])
            time.sleep(3)
            return ''
        return command(argv, maximum)
    probe.command = cold
    probe.run()
    assert attempts == set(settings['tun_probe_names']), attempts
    assert all(probe.diagnostics.values()), probe.diagnostics

    # Partial mixed proof cannot complete the health contract on DNS failure.
    probe = health.Probe(settings)
    command = probe.command
    probe.command = lambda argv, maximum: '' if argv[0] == settings['dig'] else command(argv, maximum)
    try:
        probe.run()
        raise AssertionError('DNS outage accepted')
    except health.ProbeFailure as error:
        assert error.layer == 'dns', error
        assert probe.diagnostics['data_plane_ok'] and probe.diagnostics['route_ok'], probe.diagnostics
        assert not probe.diagnostics['dns_ok'], probe.diagnostics
    print('Health: delayed logs, live /connections, all-name cold DNS retry and DNS-outage refusal passed', flush=True)


def pressure():
    # Measure baseline work through both actual request lists, then occupy the
    # application request list with actual delayed queries. The infrastructure
    # inlet must retain its independent descriptors and service capacity.
    baseline = []
    for port in (1055, 1056):
        start = time.monotonic()
        for _ in range(10):
            assert query('valid.signed.fixture.example', port).rcode() == dns.rcode.NOERROR
        baseline.append(round((time.monotonic() - start) * 1000 / 10, 2))
    held = []
    try:
        for index in range(96):
            try:
                stream = socket.create_connection(('127.0.0.1', 1055), timeout=.1)
                packet = dns.message.make_query(f'p{index}.slow.fixture.example', 'A').to_wire()
                stream.sendall(struct.pack('!H', len(packet)) + packet)
                held.append(stream)
            except OSError:
                continue
        refused = 0
        for stream in select.select(held, [], [], .2)[0]:
            try:
                header = stream.recv(2, socket.MSG_PEEK)
                if not header:
                    refused += 1
                else:
                    size = struct.unpack('!H', stream.recv(2, socket.MSG_WAITALL))[0]
                    response = dns.message.from_wire(stream.recv(size, socket.MSG_WAITALL))
                    refused += response.rcode() != dns.rcode.NOERROR
            except (OSError, EOFError):
                refused += 1
        assert refused > 0, 'application overload was not refused'
        for _ in range(10):
            assert query('valid.signed.fixture.example', 1056).rcode() == dns.rcode.NOERROR
    finally:
        for stream in held:
            stream.close()
    released = time.monotonic()
    time.sleep(2.2)
    recovery_codes = []
    while True:
        remaining = released + 10 - time.monotonic()
        assert remaining > 0, recovery_codes
        try:
            recovered = query('recovered.slow.fixture.example', 1055, timeout=min(remaining, 4))
        except dns.exception.Timeout:
            recovery_codes.append('TIMEOUT')
        else:
            recovery_codes.append(dns.rcode.to_text(recovered.rcode()))
            if recovered.rcode() == dns.rcode.NOERROR and recovered.answer:
                break
        time.sleep(.2)
    recovery_ms = round((time.monotonic() - released) * 1000, 2)
    assert recovery_ms <= 10000, (recovery_ms, recovery_codes)
    print(json.dumps(dict(baseline_ms=baseline, held=len(held), refused=refused, overload='refused',
                         infrastructure_queries=10, application_recovered=True,
                         recovery_ms=recovery_ms, recovery_codes=recovery_codes)), flush=True)


def save(path, value):
    temporary = Path(path + '.tmp')
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def lease_endpoints():
    return ((socket.AF_INET, '203.0.113.10', 4), (socket.AF_INET6, '2001:db8:ffff::10', 6)) \
        if os.environ.get('NETSEC_LEASE_PUBLIC') == '1' else \
        ((socket.AF_INET, '100.67.0.1', 4), (socket.AF_INET6, 'fd42:96::1', 6))


def peer():
    counts = dict(tcp4=0, udp4=0, tcp6=0, udp6=0)
    def connection(stream, label):
        with stream:
            stream.settimeout(.2)
            while True:
                try:
                    stream.sendall(b'reply\n')
                    if not stream.recv(4096):
                        return
                    counts[label] += 1
                except socket.timeout:
                    continue
                except OSError:
                    return
    def listener(family, address, kind, label):
        with socket.socket(family, kind) as server:
            server.bind((address, 18096))
            if kind == socket.SOCK_STREAM:
                server.listen()
                while True:
                    stream, _ = server.accept()
                    threading.Thread(target=connection, args=(stream, label), daemon=True).start()
            else:
                clients = set()
                server.settimeout(.1)
                while True:
                    try:
                        _, client = server.recvfrom(4096)
                        clients.add(client)
                        counts[label] += 1
                    except socket.timeout:
                        pass
                    for client in clients:
                        server.sendto(b'reply\n', client)
    for family, address, version in lease_endpoints():
        for kind, transport in ((socket.SOCK_STREAM, 'tcp'), (socket.SOCK_DGRAM, 'udp')):
            threading.Thread(target=listener, args=(family, address, kind, transport + str(version)), daemon=True).start()
    while True:
        save('/tmp/assurance-peer.json', counts)
        time.sleep(.2)


def client():
    streams, counts = [], {}
    for family, address, version in lease_endpoints():
        for kind, transport in ((socket.SOCK_STREAM, 'tcp'), (socket.SOCK_DGRAM, 'udp')):
            stream = socket.socket(family, kind)
            stream.settimeout(2)
            stream.connect((address, 18096))
            stream.setblocking(False)
            streams.append((stream, transport + str(version)))
            counts[transport + str(version)] = 0
    while True:
        for stream, label in streams:
            try:
                stream.send(b'client\n')
                if stream in select.select([stream], [], [], .1)[0]:
                    counts[label] += len(stream.recv(65536))
            except (BlockingIOError, OSError):
                pass
        save('/tmp/assurance-client.json', counts)
        time.sleep(.1)


def fresh_drift_clients():
    for family, address, _ in lease_endpoints():
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            with socket.socket(family, kind) as stream:
                stream.settimeout(.4)
                try:
                    stream.connect((address, 18096))
                    stream.sendall(b'fresh\n')
                    received = stream.recv(4096)
                except OSError:
                    continue
                assert not received, (address, kind, received)


def start_lease_client(environment):
    state = Path('/tmp/assurance-client.json')
    state.unlink(missing_ok=True)
    run('systemd-run', '--collect', *environment, '--unit=assurance-client', 'setpriv',
        '--reuid=1000', '--clear-groups', sys.executable, __file__, 'client')
    wait(lambda: state.exists() and all(value > 0 for value in json.loads(state.read_text()).values()),
         timeout=25)


def check_policy_loss(operation, environment, inbound):
    def counts(side):
        return json.loads(Path(f'/tmp/assurance-{side}.json').read_text())

    def policy_version():
        return json.loads(Path('/run/proxy-mode/state.json').read_text())['policy_version']

    def lease_remaining():
        authority = json.loads(Path('/run/proxy-mode/authorization.json').read_text())
        return authority['until'] - time.clock_gettime(time.CLOCK_BOOTTIME)

    def output_drops():
        chain = json.loads(run('nft', '-j', 'list', 'chain', 'inet', 'lf_policy_guard',
                               'lf_guard_require_output').stdout)
        return sum(statement['counter']['packets']
                   for entry in chain['nftables'] for statement in entry.get('rule', {}).get('expr', [])
                   if 'counter' in statement)

    wait(lambda: lease_remaining() > 8, timeout=35)
    run('systemctl', 'freeze', 'proxy-mode.service')
    try:
        version = policy_version()
        assert lease_remaining() > 3
        drops = output_drops()
        run('nft', *operation)
        time.sleep(.6)
        before = [counts('peer')] + ([counts('client')] if inbound else [])
        run('systemd-run', '--wait', '--pipe', *environment, '--uid=1000',
            sys.executable, __file__, 'fresh-drift-clients')
        after = [counts('peer')] + ([counts('client')] if inbound else [])
        assert before == after and output_drops() > drops, (operation, before, after)
        assert lease_remaining() > 0, 'fault was observed only after the old B lease expired'
        print(f'Independent guard refused wire traffic after {" ".join(operation)} before B lease expiry', flush=True)
    finally:
        run('systemctl', 'thaw', 'proxy-mode.service')
    wait(lambda: policy_version() > version
         and run('proxy-control', 'status', 'proxy', '--check', required=False).returncode == 0,
         timeout=85)
    assert '@b_authorization' in run('nft', 'list', 'chain', 'inet', 'lf_filter', 'lf_lease_output').stdout
    # Establish fresh connections to each peer before the next fault.
    run('systemctl', 'stop', 'assurance-client.service')
    start_lease_client(environment)


def lease():
    public = json.loads(Path('/run/proxy-mode/state.json').read_text()).get('routing_applied') == 'proxy-only'
    environment = ['--setenv=NETSEC_LEASE_PUBLIC=1'] if public else []
    if public:
        run('ip', '-n', 'outside', '-6', 'addr', 'replace', '2001:db8:ffff::10/128', 'dev', 'peer0', 'nodad')
    run('systemd-run', *environment, '--unit=assurance-peer', 'ip', 'netns', 'exec', 'outside', sys.executable, __file__, 'peer')
    time.sleep(1)
    try:
        start_lease_client(environment)
    except AssertionError:
        for side in ('client', 'peer'):
            path = Path('/tmp/assurance-' + side + '.json')
            print(side, path.read_text() if path.exists() else 'missing', flush=True)
        print(run('journalctl', '-u', 'assurance-client', '-u', 'assurance-peer',
                  '--no-pager', '-n', '30').stdout, flush=True)
        raise
    check_policy_loss(('flush', 'chain', 'inet', 'lf_filter', 'lf_lease_output'), environment, False)
    check_policy_loss(('delete', 'table', 'inet', 'lf_filter'), environment, True)
    print('Firewall continuity: fresh and established dual-stack TCP/UDP refused during rule loss', flush=True)
    pid = int(run('systemctl', 'show', '-p', 'MainPID', '--value', 'proxy-mode.service').stdout)
    os.kill(pid, signal.SIGSTOP)
    try:
        wait(lambda: json.loads(Path('/run/proxy-mode/authorization.json').read_text())['until'] <
             time.clock_gettime(time.CLOCK_BOOTTIME), timeout=35)
        time.sleep(1)
        before = [json.loads(Path('/tmp/assurance-' + side + '.json').read_text()) for side in ('client', 'peer')]
        time.sleep(2)
        after = [json.loads(Path('/tmp/assurance-' + side + '.json').read_text()) for side in ('client', 'peer')]
        assert before == after, ('existing TCP/UDP flows were not revoked in both directions', before, after)
        assert run('proxy-control', 'status', 'proxy', '--check', required=False).returncode != 0
        assert Path('/run/proxy-mode/intent').read_text().strip() == 'proxy'
        assert json.loads(Path('/run/proxy-mode/state.json').read_text())['mode'] == 'proxy'
        # The same root diagnostic path used by health retains C recovery.
        run('curl', '--noproxy', '', '-x', 'http://127.0.0.1:7897', '-fsS', '--max-time', '5',
            'http://node.fixture.example:8080/')
        refused = run('setpriv', '--reuid=1000', '--clear-groups', 'curl', '--noproxy', '',
            '-x', 'http://127.0.0.1:33332', '-fsS', '--max-time', '2', 'http://node.fixture.example:8080/', required=False)
        assert refused.returncode != 0
    finally:
        os.kill(pid, signal.SIGCONT)
    wait(lambda: run('proxy-control', 'status', 'proxy', '--check', required=False).returncode == 0, timeout=45)
    wait(lambda: any(json.loads(Path('/tmp/assurance-client.json').read_text())[key] > after[0][key] for key in after[0]))
    run('systemctl', 'stop', 'assurance-client.service', 'assurance-peer.service')
    # A normal stop has the same kernel deadline. Sleep revokes immediately,
    # even if timeout accounting would otherwise pause until resume.
    run('systemctl', 'stop', 'proxy-mode.service')
    wait(lambda: json.loads(Path('/run/proxy-mode/authorization.json').read_text())['until'] <
         time.clock_gettime(time.CLOCK_BOOTTIME), timeout=35)
    assert run('proxy-control', 'status', 'proxy', '--check', required=False).returncode != 0
    run('systemctl', 'start', 'dns-policy-sleep.service')
    assert json.loads(Path('/run/proxy-mode/authorization.json').read_text())['until'] == 0
    assert 'elements' not in run('nft', 'list', 'set', 'inet', 'lf_filter', 'b_authorization').stdout
    run('systemctl', 'stop', 'dns-policy-sleep.service')
    run('systemctl', 'start', 'proxy-mode.service')
    print('Kernel lease: frozen supervisor, TCP/UDP IPv4/IPv6 bidirectional expiry, recovery and fresh regrant passed', flush=True)


if __name__ == '__main__':
    {'configure': configure, 'direct': direct, 'dns': dns_checks, 'pressure': pressure,
     'http': http, 'health': health_checks, 'dns-roles': dns_roles, 'dns-degrade': dns_degrade,
     'dns-handoff-start': dns_handoff_start, 'dns-handoff-finish': dns_handoff_finish,
     'lease': lease, 'peer': peer, 'client': client, 'fresh-drift-clients': fresh_drift_clients}[sys.argv[1]]()
