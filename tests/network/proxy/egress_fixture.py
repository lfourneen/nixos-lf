"""Two controlled SOCKS exits and independent target observations in the VM."""
from concurrent.futures import ThreadPoolExecutor
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import ipaddress
import json
from pathlib import Path
import select
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path('/tmp/egress-evidence')
EXITS = {1: ('9.9.9.11', '2606:4700:1111::11'), 2: ('9.9.9.12', '2606:4700:1111::12')}
TARGETS = ('203.0.113.10', '2001:db8:ffff::10')


def run(*args, timeout=180, required=True, **kwargs):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=timeout, **kwargs)
    if required:
        assert result.returncode == 0, (args, result.stdout, result.stderr)
    return result


def wait(check, timeout=100):
    until = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < until, 'egress observation deadline'
        time.sleep(.2)


def status():
    return json.loads(run('proxy-status', '--json').stdout)


def core_index():
    return json.loads(run('ip', '-j', 'link', 'show', 'dev', 'Mihomo').stdout)[0]['ifindex']


def api(method, path, body=None, inlet='/run/clash-verge-rev/core/mihomo.sock'):
    connection = http.client.HTTPConnection('localhost', timeout=8)
    connection.sock = socket.socket(socket.AF_UNIX)
    connection.sock.settimeout(8)
    connection.sock.connect(inlet)
    try:
        connection.request(method, path, json.dumps(body) if body is not None else None,
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def exact(stream, count):
    data = b''
    while len(data) < count:
        part = stream.recv(count - len(data))
        if not part:
            raise EOFError()
        data += part
    return data


def address(stream, kind):
    if kind == 1:
        return socket.inet_ntop(socket.AF_INET, exact(stream, 4))
    if kind == 4:
        return socket.inet_ntop(socket.AF_INET6, exact(stream, 16))
    if kind == 3:
        return exact(stream, exact(stream, 1)[0]).decode()
    raise ValueError('address type')


def header(host, port):
    ip = ipaddress.ip_address(host)
    return bytes([1 if ip.version == 4 else 4]) + ip.packed + struct.pack('!H', port)


def node(number):
    port = 18079 + number
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(('1.1.1.1', port))
    def relay(packet, peer):
        try:
            if packet[:3] != b'\0\0\0' or Path(f'/tmp/egress-drop-udp-{number}').exists():
                return
            kind = packet[3]
            size = 4 if kind == 1 else 16 if kind == 4 else 0
            assert size, 'node received an unpinned UDP name'
            host = str(ipaddress.ip_address(packet[4:4+size]))
            target_port = struct.unpack('!H', packet[4+size:6+size])[0]
            family = socket.AF_INET if kind == 1 else socket.AF_INET6
            with socket.socket(family, socket.SOCK_DGRAM) as remote:
                remote.settimeout(2)
                remote.bind((EXITS[number][0 if kind == 1 else 1], 0))
                remote.sendto(packet[6+size:], (host, target_port))
                reply, _ = remote.recvfrom(65535)
                udp.sendto(packet[:6+size] + reply, peer)
        except OSError:
            pass
    def packets():
        while True:
            packet, peer = udp.recvfrom(65535)
            threading.Thread(target=relay, args=(packet, peer), daemon=True).start()
    threading.Thread(target=packets, daemon=True).start()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('1.1.1.1', port))
    listener.listen()
    def connection(client):
        remote = None
        try:
            client.settimeout(30)
            assert exact(client, 1) == b'\5'
            exact(client, exact(client, 1)[0])
            client.sendall(b'\5\0')
            version, command, _, kind = exact(client, 4)
            host = address(client, kind)
            target_port = struct.unpack('!H', exact(client, 2))[0]
            assert version == 5
            if command == 3:
                client.sendall(b'\5\0\0' + header('1.1.1.1', port))
                while client.recv(1024):
                    pass
                return
            assert command == 1 and kind in (1, 4), 'node received an unpinned TCP name'
            family = socket.AF_INET if kind == 1 else socket.AF_INET6
            remote = socket.socket(family, socket.SOCK_STREAM)
            remote.settimeout(5)
            remote.bind((EXITS[number][0 if kind == 1 else 1], 0))
            remote.connect((host, target_port))
            client.sendall(b'\5\0\0' + header('1.1.1.1', port))
            client.settimeout(None)
            remote.settimeout(None)
            while True:
                for source in select.select([client, remote], [], [], 30)[0]:
                    data = source.recv(65536)
                    if not data:
                        return
                    (remote if source is client else client).sendall(data)
        except (OSError, EOFError, AssertionError):
            pass
        finally:
            client.close()
            if remote:
                remote.close()
    while True:
        client, _ = listener.accept()
        threading.Thread(target=connection, args=(client,), daemon=True).start()


def origin():
    settings = json.loads(Path('/etc/netsec-egress.json').read_text())
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/push':
                self.send_response(200)
                self.end_headers()
                try:
                    while True:
                        self.wfile.write(json.dumps({'peer': self.client_address[0]}).encode() + b'\n')
                        self.wfile.flush()
                        time.sleep(.1)
                except OSError:
                    return
            body = json.dumps({'peer': self.client_address[0], 'path': self.path}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    def server(family, port):
        class Server(ThreadingHTTPServer):
            address_family = family
            def server_bind(self):
                if family == socket.AF_INET6:
                    self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                super().server_bind()
        http = Server(('0.0.0.0' if family == socket.AF_INET else '::', port), Handler)
        if port == 18443:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(settings['tls'] + '/trusted.pem', settings['tls'] + '/trusted.key')
            http.socket = context.wrap_socket(http.socket, server_side=True)
        http.serve_forever()
    def datagrams(family):
        with socket.socket(family, socket.SOCK_DGRAM) as stream:
            stream.bind((TARGETS[0 if family == socket.AF_INET else 1], 18090))
            while True:
                packet, peer = stream.recvfrom(4096)
                stream.sendto(json.dumps({'peer': peer[0], 'payload': packet.decode()}).encode(), peer)
    for family in (socket.AF_INET, socket.AF_INET6):
        for port in (18090, 18443):
            threading.Thread(target=server, args=(family, port), daemon=True).start()
        threading.Thread(target=datagrams, args=(family,), daemon=True).start()
    threading.Event().wait()


def probe(protocol, host):
    family = socket.AF_INET6 if ':' in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM if protocol == 'udp' else socket.SOCK_STREAM) as stream:
        stream.settimeout(4)
        stream.connect((host, 18090))
        stream.sendall(b'egress' if protocol == 'udp' else b'GET /native HTTP/1.0\r\nHost: fixture\r\n\r\n')
        result = stream.recv(65535)
        if protocol == 'tcp':
            while part := stream.recv(65535):
                result += part
            result = result.split(b'\r\n\r\n', 1)[1]
        print(result.decode(), flush=True)


def flows():
    def tcp():
        try:
            with socket.create_connection((TARGETS[0], 18090), timeout=5) as stream:
                stream.settimeout(None)
                stream.sendall(b'GET /push HTTP/1.0\r\nHost: fixture\r\n\r\n')
                with stream.makefile('rb') as reader:
                    while reader.readline().strip():
                        pass
                    while packet := reader.readline():
                        with Path('/tmp/egress-old-tcp').open('a') as output:
                            output.write(packet.decode())
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            Path('/tmp/egress-old-tcp-closed').touch()
    threading.Thread(target=tcp, daemon=True).start()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
        stream.settimeout(.5)
        stream.connect((TARGETS[0], 18090))
        while True:
            try:
                stream.send(b'flow')
                packet = stream.recv(4096)
                with Path('/tmp/egress-old-udp').open('a') as output:
                    output.write(packet.decode() + '\n')
            except OSError:
                pass
            time.sleep(.1)


def browser():
    item = json.loads(Path('/etc/netsec-egress.json').read_text())
    spec = importlib.util.spec_from_file_location('driver', '/etc/netsec-firefox-driver.py')
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    settings = json.loads(Path(item['browser']['settings']).read_text())
    with tempfile.TemporaryDirectory(prefix='egress-firefox-') as work:
        profile = Path(work) / 'profile'
        profile.mkdir()
        run('certutil', '-N', '--empty-password', '-d', 'sql:' + str(profile))
        run('certutil', '-A', '-n', 'Fixture CA', '-t', 'C,,', '-d', 'sql:' + str(profile), '-i', item['tls'] + '/ca.pem')
        port = 2829
        (profile / 'user.js').write_text(f'user_pref("marionette.port", {port});\n')
        launcher = driver.confined_launcher(item['browser']['package'], settings, work)
        with (Path(work) / 'log').open('w') as log:
            process = subprocess.Popen([str(launcher), '--headless', '--no-remote', '--profile', str(profile),
                '--marionette', '--remote-allow-system-access', 'about:blank'], stdout=log, stderr=log)
            client = None
            try:
                client = driver.Marionette(process, port)
                client.call('WebDriver:NewSession', {'capabilities': {'alwaysMatch': {'acceptInsecureCerts': False}}})
                client.call('WebDriver:Navigate', {'url': 'https://v4.egress.fixture.example:18443/browser'})
                print(client.execute('return document.body.innerText;'), flush=True)
            finally:
                if client:
                    client.socket.close()
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def check():
    ROOT.mkdir(exist_ok=True)
    evidence = dict(json.loads(Path('/etc/netsec-egress.json').read_text())['identity'], observations=[])
    def save():
        (ROOT / 'observations.json').write_text(json.dumps(evidence, indent=2) + '\n')
    save()
    for address in ('1.1.1.1', '203.0.113.10', '203.0.113.53'):
        run('ip', '-n', 'outside', 'addr', 'del', address + '/32', 'dev', 'peer0')
        run('ip', '-n', 'outside', 'addr', 'add', address + '/32', 'dev', 'lo')
    run('ip', '-n', 'outside', '-6', 'addr', 'add', TARGETS[1] + '/128', 'dev', 'lo', 'nodad')
    for v4, v6 in EXITS.values():
        run('ip', '-n', 'outside', 'addr', 'add', v4 + '/32', 'dev', 'lo')
        run('ip', '-n', 'outside', '-6', 'addr', 'add', v6 + '/128', 'dev', 'lo', 'nodad')
    run('systemctl', 'stop', 'control-node.service')
    for number in (1, 2):
        run('systemd-run', f'--unit=egress-node-{number}', 'ip', 'netns', 'exec', 'outside', sys.executable, __file__, 'node', number)
    run('systemd-run', '--unit=egress-origin', 'ip', 'netns', 'exec', 'outside', sys.executable, __file__, 'origin')
    wait(lambda: run('ip', 'netns', 'exec', 'outside', 'curl', '--noproxy', '*', '-fsS',
                     'http://203.0.113.10:18090/', required=False).returncode == 0)
    wait(lambda: status()['ready'])
    source = Path('/var/lib/clash-source/selected').resolve()
    wait(lambda: (source / 'approved/rules/settled.json').exists())
    # Reproduce independent selectors in rules before binding the strict leaf.
    assert api('PUT', '/proxies/Choice', {'name': 'Other'})[0] == 204
    assert api('PUT', '/proxies/lf-remote', {'name': 'Fixture'})[0] == 204
    ordinary = run('curl', '--noproxy', '', '-x', 'http://127.0.0.1:33332', '-fsS',
                   'http://203.0.113.10:18090/rules').stdout
    strict = api('GET', 'http://203.0.113.10:18090/identity', inlet='/run/clash-strict/proxy.sock')
    assert json.loads(ordinary)['peer'] == EXITS[2][0], ordinary
    assert strict[0] == 200 and json.loads(strict[1])['peer'] == EXITS[1][0], strict
    evidence['rules_split'] = [EXITS[2][0], EXITS[1][0]]
    choices = json.loads(run('proxy-control', 'egress', 'list').stdout)
    assert len(choices) == 3, choices
    capture_binary = shutil.which('tcpdump')
    assert capture_binary, 'tcpdump missing from the VM package closure'
    core_indexes = {core_index()}
    for label, namespace, interface in (('host', None, 'any'), ('core', None, 'any'), ('remote', 'outside', 'any')):
        command = ['ip', 'netns', 'exec', namespace] if namespace else []
        filename = 'core-all.pcap' if label == 'core' else label + '.pcap'
        run('systemd-run', f'--unit=egress-capture-{label}', *command, capture_binary, '-U', '-n', '-i', interface,
            '-w', ROOT / filename, 'tcp or udp')
    wait(lambda: all((ROOT / name).exists() and (ROOT / name).stat().st_size >= 24
                    for name in ('host.pcap', 'core-all.pcap', 'remote.pcap')), timeout=10)
    for number, name in ((1, 'Fixture'), (2, 'Other'), (1, 'Fixture')):
        print('Checking fixed exit:', name, flush=True)
        choice = next(item for item in choices if item['name'] == name)
        before = status()
        if number == 2:
            run('systemd-run', '--unit=egress-old-flows', '--uid=1000', sys.executable, __file__, 'flows')
            wait(lambda: Path('/tmp/egress-old-tcp').exists() and Path('/tmp/egress-old-udp').exists())
            Path('/run/managed-source-hold').touch()
            with ThreadPoolExecutor(max_workers=2) as pool:
                refresh = pool.submit(run, 'clash-source', 'refresh', required=False)
                wait(lambda: Path('/run/managed-source-fetching').exists())
                change = pool.submit(run, 'proxy-control', 'egress', choice['id'])
                wait(lambda: json.loads(Path('/run/proxy-mode/state.json').read_text())['mode'] == 'blocked')
                Path('/run/managed-source-hold').unlink()
                change.result()
                assert refresh.result().returncode != 0, 'an interrupted old-epoch download was published'
            evidence['concurrent_download'] = 'wire request started before exit change; interrupted download refused'
            wait(lambda: Path('/tmp/egress-old-tcp-closed').exists())
            time.sleep(.7)
            offset = Path('/tmp/egress-old-udp').stat().st_size
            time.sleep(1)
            received = Path('/tmp/egress-old-udp').read_text()[offset:]
            assert all(json.loads(line)['peer'] == EXITS[2][0] for line in received.splitlines()), received
            run('systemctl', 'stop', 'egress-old-flows.service')
            evidence['old_flows'] = 'TCP closed; UDP old exit stopped across concurrent source refresh'
        else:
            run('proxy-control', 'egress', choice['id'])
        current = status()
        core_indexes.add(core_index())
        assert current['ready'] and current['egress_requested'] == choice['id'], current
        assert current['egress_epoch'] > before.get('egress_epoch', 0)
        assert api('PUT', '/proxies/lf-remote', {'name': 'Other'})[0] == 403
        assert api('DELETE', '/proxies/lf-remote')[0] == 403
        assert len(json.loads(api('GET', '/proxies')[1])['proxies']['lf-remote']['all']) == 1
        for uid in (1000, 1100, 600001):
            for family, target in enumerate(TARGETS):
                for protocol in ('tcp', 'udp'):
                    reply = run('setpriv', f'--reuid={uid}', '--clear-groups', sys.executable, __file__, 'probe', protocol, target)
                    observation = json.loads(reply.stdout)
                    assert observation['peer'] == EXITS[number][family], (uid, protocol, observation)
                    evidence['observations'].append(dict(uid=uid, protocol=protocol, family=(4, 6)[family],
                        peer=observation['peer'], at=time.time(),
                        snapshot=Path('/run/proxy-mode/config-digest').read_text().strip(),
                        **{key: current[key] for key in
                        ('routing_epoch', 'routing_policy_digest', 'egress_requested', 'egress_epoch')}))
        for host in ('v4.egress.fixture.example', 'v6.egress.fixture.example'):
            reply = run('curl', '--noproxy', '', '-x', 'http://127.0.0.1:33332', '-fsS',
                        '--cacert', json.loads(Path('/etc/netsec-egress.json').read_text())['tls'] + '/ca.pem',
                        f'https://{host}:18443/explicit').stdout
            assert json.loads(reply)['peer'] == EXITS[number][int(host.startswith('v6'))], reply
        response = api('GET', 'http://203.0.113.10:18090/identity', inlet='/run/clash-strict/proxy.sock')
        assert response[0] == 200 and json.loads(response[1])['peer'] == EXITS[number][0], response
        # A UDP-only fault does not claim a continuous UDP health guarantee.
        run('ip', 'netns', 'exec', 'outside', 'touch', f'/tmp/egress-drop-udp-{number}')
        failed = run('setpriv', '--reuid=1000', '--clear-groups', sys.executable, __file__, 'probe', 'udp', TARGETS[0], required=False)
        assert failed.returncode and status()['capabilities']['udp'] == 'not-probed'
        run('ip', 'netns', 'exec', 'outside', 'unlink', f'/tmp/egress-drop-udp-{number}')
        evidence.update(boot=Path('/run/proxy-mode/boot-id').read_text().strip(),
                        snapshot=Path('/run/proxy-mode/config-digest').read_text().strip())
        save()
    no_udp = next(item for item in choices if item['name'] == 'NoUDP')
    print('Checking unsupported UDP and Firefox', flush=True)
    run('proxy-control', 'egress', no_udp['id'])
    core_indexes.add(core_index())
    assert status()['capabilities']['udp'] == 'unsupported'
    assert run('setpriv', '--reuid=1000', '--clear-groups', sys.executable, __file__,
               'probe', 'udp', TARGETS[0], required=False).returncode
    run('proxy-control', 'egress', next(item['id'] for item in choices if item['name'] == 'Fixture'))
    core_indexes.add(core_index())
    evidence['unsupported_udp'] = 'unsupported leaf refused datagrams without selecting another node'
    # Use the actual packaged Firefox through its production filesystem sandbox.
    reply = run('setpriv', '--reuid=1000', '--regid=users', '--init-groups', 'env',
        'HOME=/home/lfour', 'XDG_RUNTIME_DIR=/run/user/1000', 'WAYLAND_DISPLAY=wayland-0',
        'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus', 'PULSE_SERVER=unix:/run/user/1000/pulse/native',
        sys.executable, __file__, 'browser', timeout=90)
    assert json.loads(reply.stdout)['peer'] == EXITS[1][0], reply.stdout
    evidence['firefox_peer'] = EXITS[1][0]
    # Literal ICMP cannot be mistaken for a tunnelled echo.
    for target in TARGETS:
        assert run('setpriv', '--reuid=1000', '--clear-groups', 'ping', '-c', '1', '-W', '1', target, required=False).returncode
    fake = run('dig', '+short', '@127.0.0.1', '-p', '1053', 'v4.egress.fixture.example').stdout.strip()
    assert fake.startswith('198.18.') and run('setpriv', '--reuid=1000', '--clear-groups',
        'ping', '-c', '1', '-W', '1', fake, required=False).returncode
    # Change the physical route while retaining the same approved exit.
    print('Checking dual-stack uplink loss and recovery', flush=True)
    run('ip', 'link', 'add', 'wlo1', 'type', 'veth', 'peer', 'name', 'egress-alt')
    run('ip', 'link', 'set', 'egress-alt', 'netns', 'outside')
    run('ip', 'addr', 'add', '100.68.0.2/24', 'dev', 'wlo1')
    run('ip', '-6', 'addr', 'add', '2001:db8:beef::2/64', 'dev', 'wlo1', 'nodad')
    run('ip', 'link', 'set', 'wlo1', 'up')
    run('ip', '-n', 'outside', 'addr', 'add', '100.68.0.1/24', 'dev', 'egress-alt')
    run('ip', '-n', 'outside', '-6', 'addr', 'add', '2001:db8:beef::1/64', 'dev', 'egress-alt', 'nodad')
    run('ip', '-n', 'outside', 'link', 'set', 'egress-alt', 'up')
    run('ip', 'route', 'add', 'default', 'via', '100.68.0.1', 'dev', 'wlo1', 'metric', '2')
    run('ip', '-6', 'route', 'add', 'default', 'via', '2001:db8:beef::1', 'dev', 'wlo1', 'metric', '2')
    previous_proof = json.loads(Path('/run/proxy-mode/health.json').read_text())['monotonic_seconds']
    run('ip', 'link', 'set', 'ens1', 'down')
    wait(lambda: json.loads(Path('/run/proxy-mode/health.json').read_text()).get('monotonic_seconds', 0) > previous_proof
        and status()['ready'] and run('setpriv', '--reuid=1000', '--clear-groups', sys.executable, __file__,
                                     'probe', 'tcp', TARGETS[0], required=False).returncode == 0)
    reply = run('setpriv', '--reuid=1000', '--clear-groups', sys.executable, __file__, 'probe', 'tcp', TARGETS[0])
    assert json.loads(reply.stdout)['peer'] == EXITS[1][0]
    previous_proof = json.loads(Path('/run/proxy-mode/health.json').read_text())['monotonic_seconds']
    run('ip', 'link', 'set', 'ens1', 'up')
    run('ip', '-6', 'addr', 'replace', '2001:db8:dead::2/64', 'dev', 'ens1', 'nodad')
    run('ip', '-6', 'addr', 'replace', 'fd42:96::2/64', 'dev', 'ens1', 'nodad')
    run('ip', 'route', 'replace', 'default', 'via', '100.67.0.1', 'dev', 'ens1', 'metric', '1')
    run('ip', '-6', 'route', 'replace', 'default', 'via', '2001:db8:dead::1', 'dev', 'ens1', 'metric', '1')
    run('ip', 'route', 'del', 'default', 'via', '100.68.0.1', 'dev', 'wlo1')
    run('ip', '-6', 'route', 'del', 'default', 'via', '2001:db8:beef::1', 'dev', 'wlo1')
    run('ip', 'link', 'del', 'wlo1')
    wait(lambda: json.loads(Path('/run/proxy-mode/health.json').read_text()).get('monotonic_seconds', 0) > previous_proof
        and status()['ready'] and run('setpriv', '--reuid=1000', '--clear-groups', sys.executable, __file__,
                                     'probe', 'tcp', TARGETS[0], required=False).returncode == 0)
    evidence['uplink_switch'] = 'ens1 loss -> wlo1 -> ens1 with unchanged exit identity'
    # Failure of the chosen node cannot activate the other approved node.
    print('Checking selected-node failure without fallback', flush=True)
    run('systemctl', 'stop', 'egress-node-1.service')
    wait(lambda: status()['mode'] == 'blocked', 90)
    run('systemd-run', '--unit=egress-node-1', 'ip', 'netns', 'exec', 'outside', sys.executable, __file__, 'node', 1)
    wait(lambda: status()['ready'])
    evidence['node_failure'] = 'C without fallback; fresh recovery'
    evidence['udp_failure'] = 'request refused; continuous scope remains not-probed'
    evidence['icmp'] = 'IPv4/IPv6 and fake-IP echo refused'
    core_indexes.add(core_index())
    for label in ('host', 'core', 'remote'):
        run('systemctl', 'stop', f'egress-capture-{label}.service')
    # SLL2 preserves interface indexes across veth removal and recreation.
    run(capture_binary, '-nn', '-r', ROOT / 'core-all.pcap', '-w', ROOT / 'core.pcap',
        ' or '.join('ifindex ' + str(index) for index in sorted(core_indexes)))
    assert (ROOT / 'core.pcap').stat().st_size > 24, 'core veth capture is empty'
    evidence['core_ifindexes'] = sorted(core_indexes)
    dns_wire = run(capture_binary, '-nn', '-r', ROOT / 'remote.pcap', 'dst host 203.0.113.53 and dst port 443').stdout
    for v4, _ in EXITS.values():
        assert v4 in dns_wire, ('DNS exit missing', v4, dns_wire)
    evidence['dns_peers'] = [pair[0] for pair in EXITS.values()]
    (ROOT / 'dns-wire.txt').write_text(dns_wire)
    save()
    print('Fixed egress evidence:', json.dumps(evidence), flush=True)
    # Restore the original node service for the remaining routing regressions.
    run('proxy-control', 'routing', 'rules')
    assert api('PUT', '/proxies/Choice', {'name': 'Fixture'})[0] == 204
    assert api('PUT', '/proxies/lf-remote', {'name': 'Fixture'})[0] == 204
    run('systemctl', 'stop', 'egress-node-1.service', 'egress-node-2.service')
    run('systemd-run', '--unit=control-node', 'ip', 'netns', 'exec', 'outside',
        Path('/etc/netsec-core').read_text().strip(), '-d', '/run/netsec-node', '-f', '/run/netsec-node.json')
    wait(lambda: status()['ready'])


if __name__ == '__main__':
    {'check': check, 'node': lambda: node(int(sys.argv[2])), 'origin': origin,
     'probe': lambda: probe(*sys.argv[2:]), 'browser': browser, 'flows': flows}[sys.argv[1]]()
