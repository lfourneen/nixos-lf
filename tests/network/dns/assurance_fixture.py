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


def query(name, port, kind='A', tcp=True, cd=False):
    message = dns.message.make_query(name, kind, want_dnssec=True)
    if cd:
        message.flags |= dns.flags.CD
    return (dns.query.tcp if tcp else dns.query.udp)(message, '127.0.0.1', port=port, timeout=4)


def dns_checks():
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
    host_answer = query('unsigned.fixture.example', 1056 if strict else 1055)
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
        recovered = query('recovered.slow.fixture.example', 1055)
        recovery_codes.append(dns.rcode.to_text(recovered.rcode()))
        if recovered.rcode() == dns.rcode.NOERROR and recovered.answer:
            break
        assert time.monotonic() - released < 10, (recovery_codes, recovered.to_text())
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


def lease():
    public = json.loads(Path('/run/proxy-mode/state.json').read_text()).get('routing_applied') == 'proxy-only'
    environment = ['--setenv=NETSEC_LEASE_PUBLIC=1'] if public else []
    if public:
        run('ip', '-n', 'outside', '-6', 'addr', 'replace', '2001:db8:ffff::10/128', 'dev', 'peer0', 'nodad')
    run('systemd-run', *environment, '--unit=assurance-peer', 'ip', 'netns', 'exec', 'outside', sys.executable, __file__, 'peer')
    time.sleep(1)
    run('systemd-run', *environment, '--unit=assurance-client', 'setpriv', '--reuid=1000', '--clear-groups', sys.executable, __file__, 'client')
    try:
        wait(lambda: Path('/tmp/assurance-client.json').exists() and
             all(value > 0 for value in json.loads(Path('/tmp/assurance-client.json').read_text()).values()))
    except AssertionError:
        for side in ('client', 'peer'):
            path = Path('/tmp/assurance-' + side + '.json')
            print(side, path.read_text() if path.exists() else 'missing', flush=True)
        print(run('journalctl', '-u', 'assurance-client', '-u', 'assurance-peer',
                  '--no-pager', '-n', '30').stdout, flush=True)
        raise
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
     'http': http, 'health': health_checks,
     'lease': lease, 'peer': peer, 'client': client}[sys.argv[1]]()
