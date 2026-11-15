"""Actual registered-source and strict-browser paths across the core netns."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import ssl
import subprocess
import sys
import time

SETTINGS = json.loads(Path('/etc/netsec-managed.json').read_text())
GUI = Path('/home/lfour/.local/share/io.github.clash-verge-rev.clash-verge-rev')


def run(*args, **kwargs):
    result = subprocess.run(list(map(str, args)), text=True, capture_output=True,
                            timeout=70, **kwargs)
    if result.returncode:
        raise AssertionError((args, result.returncode, result.stdout, result.stderr))
    return result


def wait(check, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.1)
    raise AssertionError('managed namespace fixture timed out')


def source():
    data = {'proxies': [{'name': 'Fixture', 'type': 'socks5', 'server': 'source-node.fixture.example',
                        'port': 18080, 'udp': True}],
            'proxy-groups': [{'name': 'Choice', 'type': 'select', 'proxies': ['Fixture']}],
            'rules': ['MATCH,Choice']}
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_GET(self):
            if self.path.startswith('/echo?'):
                body = self.path.split('?', 1)[1].encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path not in ('/one', '/two'):
                self.send_error(404)
                return
            if Path('/run/managed-source-unavailable').exists():
                self.send_error(503)
                return
            candidate = json.loads(json.dumps(data))
            if self.path == '/one' and Path('/run/managed-source-change').exists():
                candidate['proxies'][0]['server'] = 'source-node-updated.fixture.example'
                if Path('/run/managed-source-block').exists():
                    candidate['hosts'] = {'blocked.fixture.example': '1.1.1.1'}
            body = json.dumps(candidate).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    # Bootstrap resolves to 1.1.1.1 while B's application resolver uses
    # 203.0.113.10. Both addresses belong to this isolated fixture namespace.
    server = ThreadingHTTPServer(('0.0.0.0', 443), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(Path(SETTINGS['tls'])/'trusted.pem', Path(SETTINGS['tls'])/'trusted.key')
    server.socket = context.wrap_socket(server.socket, server_side=True)
    Path('/run/managed-source-ready').touch()
    server.serve_forever()


def setup():
    node = Path('/run/netsec-node.json')
    node.write_text(json.dumps(dict(json.loads(node.read_text()), **{'bind-address': '0.0.0.0'})))
    registry = Path('/run/managed-source-definition.json')
    for identity in ('one', 'two'):
        registry.write_text(json.dumps({'id': identity, 'gui-profile': 'uid-' + identity,
            'subscription': {'url': 'https://source.fixture.example/' + identity}}))
        registry.chmod(0o600)
        review = json.loads(run('clash-source', 'review', registry).stdout)
        run('clash-source', 'register', registry, '--digest', review['digest'], '--previous', 'none')
    run('clash-source', 'refresh')
    assert Path('/var/lib/clash-source/selected').resolve().name == 'one'


def state_is(mode):
    return json.loads(Path('/run/proxy-mode/state.json').read_text())['mode'] == mode


def ready():
    return json.loads(run('proxy-control', 'status', 'proxy').stdout)['ready'] is True


def refresh():
    for host in ('source.fixture.example', 'source-node.fixture.example'):
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        assert addresses and all(ipaddress.ip_address(ip) in ipaddress.ip_network('198.18.0.0/16')
                                 for ip in addresses), (host, addresses)
    denied = subprocess.run(['setpriv', '--reuid=1000', '--clear-groups', 'dig',
                             '+time=1', '+tries=1', '@127.0.0.1', '-p', '1056',
                             'source.fixture.example', 'A'], capture_output=True, timeout=5)
    assert denied.returncode != 0, 'desktop borrowed source-worker DNS access'
    run('systemctl', 'start', 'clash-source-refresh.service')
    wait(ready, 90)
    assert Path('/var/lib/clash-source/selected').resolve().name == 'one'
    print('B fake-IP answers cannot poison source fetch or node pinning; source DNS stays UID-scoped')


def browser_request(allowed=True):
    connection = http.client.HTTPConnection('localhost', timeout=6)
    connection.sock = socket.socket(socket.AF_UNIX)
    connection.sock.settimeout(6)
    connection.sock.connect('/run/clash-strict/proxy.sock')
    try:
        connection.request('GET', 'http://node.fixture.example:8080/')
        result = connection.getresponse()
        body = result.read()
        assert (result.status == 200 and bool(body)) == allowed, (result.status, body)
    except (OSError, http.client.HTTPException):
        assert not allowed
    finally:
        connection.close()


def recover():
    source = Path('/var/lib/clash-source/selected')
    settled = source / 'approved/settled.json'
    wait(settled.exists, 90)
    state = Path('/run/proxy-mode')
    def accepted_and_healthy():
        health = json.loads((state / 'health.json').read_text())
        policy = json.loads((state / 'state.json').read_text())
        return (not (state / 'profile-refresh-verifying.json').exists()
                and health.get('ok') is True and health.get('policy_version') == policy.get('policy_version')
                and ready())
    wait(accepted_and_healthy, 90)
    baseline = json.loads(settled.read_text())['digest']
    marker = Path('/run/managed-source-change')
    spec = importlib.util.spec_from_file_location('refresh_stage', SETTINGS['stage'])
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
    stage_settings = json.loads(Path(SETTINGS['stageSettings']).read_text())
    before_pid = stage.service_request(stage_settings, 'GET', '/status')['core_pid']
    before_version = json.loads((state / 'state.json').read_text())['policy_version']
    before_authority = (state / 'authorized-config').read_bytes()
    context = ssl.create_default_context(cafile=str(Path(SETTINGS['tls']) / 'ca.pem'))
    stream = http.client.HTTPSConnection('127.0.0.1', 7897, timeout=6, context=context)
    stream.set_tunnel('source.fixture.example', 443)
    stream.request('GET', '/echo?before-refresh')
    assert stream.getresponse().read() == b'before-refresh'
    held_socket = stream.sock
    assert held_socket is not None
    marker.touch()
    blocked = Path('/run/managed-source-block')
    blocked.touch()
    try:
        run('systemctl', 'start', 'clash-source-refresh.service')
        wait(ready, 90)
        rejection = json.loads((state / 'profile-refresh.json').read_text())
        assert rejection['result'] == 'fallback' and rejection['audit']['deny']['node_identity'] == 1
        assert rejection['audit']['deny']['hosts_override'] == 1
        assert stage.service_request(stage_settings, 'GET', '/status')['core_pid'] == before_pid
        assert json.loads((state / 'state.json').read_text())['policy_version'] == before_version
        assert (state / 'authorized-config').read_bytes() == before_authority
        stream.request('GET', '/echo?after-refresh')
        assert stream.getresponse().read() == b'after-refresh'
        assert stream.sock is held_socket
        stream.close()
        print('Rejected source kept the same core, B generation, authority and established TLS stream')
        run('proxy-control', 'off')
        wait(lambda: state_is('direct'))
        assert not (state / 'authorized-config').exists()
        assert not (state / 'config-digest').exists()
        assert Path('/run/clash-verge-rev/effective').is_symlink()
        for family, target in ((4, '1.1.1.1'), (6, '2606:4700:4700::1111')):
            for uid in (0, 1000):
                route = json.loads(run('ip', f'-{family}', '-j', 'route', 'get', target,
                                       'uid', uid, 'mark', '0').stdout)
                assert len(route) == 1 and route[0]['dev'] == 'ens1', route
        # Public TLS from the desktop must work without the stopped core.
        run('setpriv', '--reuid=1000', '--clear-groups', 'curl', '--noproxy', '*',
            '--fail', '--silent', '--max-time', '8', '--cacert', Path(SETTINGS['tls'])/'ca.pem',
            '--resolve', 'source.fixture.example:443:1.1.1.1', 'https://source.fixture.example/one')
        wait(lambda: '1.1.1.1' in run('dig', '+short', '+time=1', '+tries=1',
             '@127.0.0.1', '-p', '1054', 'source.fixture.example', 'A').stdout, 60)
        unavailable = Path('/run/managed-source-unavailable')
        unavailable.touch()
        run('proxy-control', 'on')
        wait(ready, 30)
        wait(lambda: json.loads((state / 'source-fetch.json').read_text()).get('result') == 'failed', 15)
        restored = json.loads((state / 'profile-refresh.json').read_text())
        assert restored['result'] == 'restored' and restored['digest'] == baseline, restored
        assert not (state / 'profile-refresh-verifying.json').exists()
        download = json.loads((state / 'source-fetch.json').read_text())
        assert download['reason'] == 'http rejected' and download['http_status'] == 503, download
        status = run('setpriv', '--reuid=1000', '--clear-groups', 'proxy-status', '--plain').stdout
        assert 'restored accepted snapshot' in status and 'HTTP 503' in status
        print('Explicit on restored accepted source while cached input was rejected and background download returned HTTP 503')
        unavailable.unlink()
        run('systemctl', 'stop', 'clash-source-refresh.service')
        run('systemctl', 'reset-failed', 'clash-source-refresh.service')
        run('systemctl', 'start', 'clash-source-refresh.service')
        wait(ready, 90)
        assert (state / 'authorized-config').read_text().strip() == baseline
        rejection = json.loads((state / 'profile-refresh.json').read_text())
        assert rejection['result'] == 'fallback' and rejection['audit']['deny']['node_identity'] == 1
        assert json.loads(settled.read_text())['digest'] == baseline
        print('Off restores dual-stack main routing and direct TLS/DNS; on retains the settled baseline after audit denial')
        reviewed = json.loads(run('clash-diff', '--json').stdout)
        assert reviewed['audit_current'] and reviewed['audit']['deny'] == {'node_identity': 1, 'hosts_override': 1}
        assert reviewed['nodes'][0]['changes'] == [{'field': 'logical-server', 'hidden': False,
            'before': 'source-node.fixture.example', 'after': 'source-node-updated.fixture.example'}]
        assert not (source / 'approval.json').exists(), 'comparison approved the update'
        assert not (source / 'review.json').exists(), 'read-only comparison wrote a review'
        assert (state / 'authorized-config').read_text().strip() == baseline
        blocked.unlink()
        run('systemctl', 'start', 'clash-source-refresh.service')
        wait(ready, 90)
        wait(accepted_and_healthy, 90)
        imported = json.loads((state / 'profile-refresh.json').read_text())
        assert imported['result'] == 'accepted', imported
        assert imported['audit']['deny'] == {}, imported
        assert imported['last_auto_approval']['categories'] == {'node_identity': 1}
        assert imported['last_auto_approval']['nodes'] == 1
        assert (state / 'authorized-config').read_text().strip() != baseline
        assert not (source / 'approval.json').exists() and not (source / 'review.json').exists()
        status = run('setpriv', '--reuid=1000', '--clear-groups', 'proxy-status', '--plain').stdout
        assert 'Last auto approval:' in status and 'node_identity=1' in status
        browser_request()
        print('Root refresh auto-imported node rotation through normal audit and health gates; read-only diff retained authority')
    except Exception:
        # This VM contains synthetic source data only.
        for name in ('profile-refresh.json', 'source-validator.log'):
            path = state / name
            if path.exists():
                print(name, path.read_text()[-8192:], flush=True)
        raise
    finally:
        stream.close()
        marker.unlink(missing_ok=True)
        Path('/run/managed-source-unavailable').unlink(missing_ok=True)


def receive_queue():
    owner = str(pwd.getpwnam('gost').pw_uid)
    queues = []
    for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) > 9 and fields[2].endswith(f':{7898:04X}') and fields[3] == '01' and fields[7] == owner:
            queues.append(int(fields[4].split(':')[1], 16))
    assert len(queues) == 1, queues
    return queues[0]


def stream_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(('203.0.113.10', 18099))
        server.listen()
        Path('/run/managed-stream-ready').touch()
        stream, _ = server.accept()
        with stream:
            assert stream.recv(4096) == b'hold'
            stream.sendall(b'ready')
            wait(lambda: Path('/run/managed-stream-release').exists(), 120)
            stream.sendall(b'post-C-data')
            time.sleep(15)


def strict():
    browser_request()
    run('systemd-run', '--unit=managed-stream', 'ip', 'netns', 'exec', 'outside',
        sys.executable, __file__, 'stream')
    wait(lambda: Path('/run/managed-stream-ready').exists())
    client = socket.socket(socket.AF_UNIX)
    client.settimeout(6)
    client.connect('/run/clash-strict/proxy.sock')
    client.sendall(b'CONNECT 203.0.113.10:18099 HTTP/1.1\r\nHost: 203.0.113.10:18099\r\n\r\n')
    header = b''
    while b'\r\n\r\n' not in header:
        header += client.recv(4096)
    assert b' 200 ' in header, header
    client.sendall(b'hold')
    assert client.recv(4096) == b'ready'
    pid = int(run('systemctl', 'show', '-p', 'MainPID', '--value', 'clash-strict.service').stdout)
    assert pid > 0 and receive_queue() == 0
    original = run('nft', '-s', 'list', 'chain', 'inet', 'lf_filter', 'lf_strict_reply_guard').stdout
    os.kill(pid, signal.SIGSTOP)
    coordinator = None
    try:
        run('systemctl', 'stop', 'control-http-8080.service')
        wait(lambda: state_is('blocked'), 90)
        pending = Path('/run/proxy-mode/source-refresh-pending')
        assert not pending.exists()
        # The guard acquires the policy lock. Exercise it before freezing a
        # coordinator that might otherwise be stopped while holding that lock.
        run('systemctl', 'start', 'clash-source-guard.service')
        assert not pending.exists(), 'an update activation guard must not enqueue another download'
        coordinator = int(run('systemctl', 'show', '-p', 'MainPID', '--value', 'proxy-mode.service').stdout)
        os.kill(coordinator, signal.SIGSTOP)
        source_status = Path('/var/lib/clash-source/selected/status.json')
        before_download = source_status.read_bytes()
        before_authority = Path('/run/proxy-mode/authorized-config').read_bytes()
        run('systemctl', 'start', 'clash-source-refresh.service')
        download = json.loads(Path('/run/proxy-mode/source-fetch.json').read_text())
        assert download['result'] == 'deferred' and download['reason'] == 'network protection active', download
        assert source_status.read_bytes() == before_download
        assert Path('/run/proxy-mode/authorized-config').read_bytes() == before_authority
        assert pending.exists()
        # This subtest holds C to inspect receive revocation. Startup's B
        # trigger is covered by recover(); cancel this separate test request.
        pending.unlink()
        print('C deferred automatic source download without changing candidate or runtime authority', flush=True)
        Path('/run/managed-stream-release').touch()
        time.sleep(.8)
        assert receive_queue() == 0, 'strict core replies entered the stopped host relay in C'
        # Demonstrate a real receive-side loss: with only this guard removed,
        # the same server push enters the same live TCP socket in C.
        run('nft', 'flush', 'chain', 'inet', 'lf_filter', 'lf_strict_reply_guard')
        wait(lambda: receive_queue() >= len(b'post-C-data'), 10)
    finally:
        run('nft', 'flush', 'chain', 'inet', 'lf_filter', 'lf_strict_reply_guard')
        run('nft', '-f', '-', input=original)
        if coordinator is not None:
            os.kill(coordinator, signal.SIGCONT)
        os.kill(pid, signal.SIGKILL)
        client.close()
        run('systemctl', 'stop', 'managed-stream.service')
    browser_request(False)
    run('systemd-run', '--unit=control-http-8080', 'ip', 'netns', 'exec', 'outside',
        sys.executable, '-m', 'http.server', '8080', '--bind', '0.0.0.0')
    wait(ready, 90)
    browser_request()
    print('Production strict inlet identity, host socket UID, kernel C receive revocation and B recovery passed')


def switch():
    # Readiness precedes the asynchronous health-acceptance commit. A refresh
    # deliberately defers while that worker owns an import or rollback.
    state = Path('/run/proxy-mode')
    wait(lambda: not any((state / name).exists() for name in (
        'profile-refresh-verifying.json', 'profile-refresh-restart.json',
        'profile-refresh-commit.json')), 90)
    original = json.loads(Path('/run/clash-verge-rev/effective/source-manifest.json').read_text())
    assert original['source_id'] == 'one'
    index = GUI/'profiles.yaml'
    index.write_text('current: uid-two\nitems: [{uid: uid-two, url: https://unregistered.invalid/}]\n')
    os.chown(index, 1000, pwd.getpwnam('lfour').pw_gid)
    run('clash-source', 'gui-select')
    assert Path('/var/lib/clash-source/selected').resolve().name == 'two'
    run('systemctl', 'start', 'clash-source-refresh.service')
    wait(lambda: json.loads(Path('/run/clash-verge-rev/effective/source-manifest.json').read_text())['source_id'] == 'two')
    wait(ready, 90)
    browser_request()
    assert Path('/var/lib/clash-source/selected').resolve().name == 'two'
    print('Actual TLS registration, source worker, approved GUI source switch and core restart passed')


if __name__ == '__main__':
    {'source': source, 'setup': setup, 'refresh': refresh, 'recover': recover, 'strict': strict,
     'stream': stream_server, 'switch': switch}[sys.argv[1]]()
