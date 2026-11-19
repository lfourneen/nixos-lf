"""Real registered-source routing switches and outer-wire evidence in the VM."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time


def run(*args, timeout=100, required=True, **kwargs):
    result = subprocess.run(list(map(str, args)), text=True, capture_output=True, timeout=timeout, **kwargs)
    if required and result.returncode:
        raise AssertionError((args, result.returncode, result.stdout, result.stderr))
    return result


def wait(check, timeout=100):
    until = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < until, 'routing observation deadline'
        time.sleep(.2)


def status():
    return json.loads(run('proxy-status', '--json').stdout)


def request(name, allowed=True, uid=1000):
    path = ['--noproxy', '*', '--resolve', f'{name}:18082:203.0.113.10'] if uid in (1100, 600001) else [
        '--noproxy', '', '-x', 'http://127.0.0.1:33332']
    result = run('setpriv', f'--reuid={uid}', '--clear-groups', 'curl', *path, '-fsS', '--max-time', '4',
                 f'http://{name}:18082/', required=False)
    assert (result.returncode == 0) == allowed, (name, uid, result.stdout, result.stderr)


def route(target):
    result = run('proxy-control', 'routing', target, timeout=160)
    assert target in result.stdout and 'Network status' not in result.stdout, result.stdout
    current = status()
    assert current['ready'] and current['mode'] == 'proxy', current
    assert current['routing_requested'] == current['routing_applied'] == target, current
    return current


def old_peer():
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('100.67.0.1', 18081))
    listener.listen()
    while True:
        stream, _ = listener.accept()
        def push(connection=stream):
            try:
                while True:
                    connection.sendall(b'push\n')
                    time.sleep(.1)
            except OSError:
                connection.close()
        threading.Thread(target=push, daemon=True).start()


def old_client():
    stream = socket.create_connection(('100.67.0.1', 18081), timeout=4)
    stream.settimeout(.5)
    count = 0
    while True:
        try:
            data = stream.recv(4096)
            if not data:
                break
            count += len(data)
            Path('/tmp/routing-old-replies').write_text(str(count))
        except TimeoutError:
            pass


def check():
    if Path('/etc/netsec-gui-webdriver.json').exists():
        current = status()
        assert current['routing_applied'] == 'rules', current
        for target in ('rules', 'proxy-only'):
            if target != 'rules':
                route(target)
            run(sys.executable, '/etc/netsec-control.py', 'check_gui_controls')
            print(run(sys.executable, '/etc/netsec-gui-webdriver.py', 'check', timeout=180).stdout,
                  end='', flush=True)
        route('rules')
        run(sys.executable, '/etc/netsec-control.py', 'check_gui_controls')
        print('GUI controls preserved system ownership in both submodes; root restored rules', flush=True)
        return

    run('systemd-run', '--unit=routing-http', 'ip', 'netns', 'exec', 'outside',
        sys.executable, '-m', 'http.server', '18082', '--bind', '0.0.0.0')
    run('systemd-run', '--unit=routing-old-peer', 'ip', 'netns', 'exec', 'outside', sys.executable, __file__, 'peer')
    wait(lambda: run('ip', 'netns', 'exec', 'outside', 'curl', '--noproxy', '*',
                     '-fsS', 'http://203.0.113.10:18082/', required=False).returncode == 0)
    current = status()
    assert current['routing_applied'] == 'rules', current
    run(sys.executable, '/etc/netsec-control.py', 'check_gui_controls')
    request('domestic.fixture.example')
    request('denied.fixture.example', False)
    source = Path('/var/lib/clash-source/selected').resolve()
    wait(lambda: (source / 'approved/rules/settled.json').exists(), 100)
    rule_snapshot = json.loads((source / 'approved/rules/settled.json').read_text())['digest']
    original = (source / 'approved/rules' / rule_snapshot / 'source-data.json').read_bytes()
    run('systemd-run', '--unit=routing-old-client', 'setpriv', '--reuid=1000', '--clear-groups',
        sys.executable, __file__, 'client')
    wait(lambda: Path('/tmp/routing-old-replies').exists())

    strict = route('proxy-only')
    run(sys.executable, '/etc/netsec-control.py', 'check_gui_controls')
    assert strict['capabilities']['icmp'] == 'blocked'
    assert strict['capabilities']['udp'] == 'not-probed'
    assert strict['capabilities']['dns'] == 'validated-via-proxy'
    request('domestic.fixture.example')
    request('denied.fixture.example')
    request('denied.fixture.example', uid=1100)
    request('denied.fixture.example', uid=600001)
    time.sleep(.7)
    before = Path('/tmp/routing-old-replies').read_text()
    time.sleep(1)
    assert Path('/tmp/routing-old-replies').read_text() == before, 'old LAN pushes crossed the strict boundary'
    run('systemctl', 'stop', 'routing-old-client.service', 'routing-old-peer.service')
    wait(lambda: (source / 'approved/proxy-only/settled.json').exists(), 100)
    strict_snapshot = json.loads((source / 'approved/proxy-only/settled.json').read_text())['digest']
    assert (source / 'approved/proxy-only' / strict_snapshot / 'source-data.json').read_bytes() == original

    # Observe the physical peer independently of host/core policy verdicts.
    run('ip', 'netns', 'exec', 'outside', 'nft', '-f', '-', input='''
table inet routing_wire {
 chain incoming { type filter hook prerouting priority -300; policy accept;
  iifname "peer0" ip daddr 1.1.1.1 meta l4proto { tcp, udp } th dport 18080 counter return comment "node"
  iifname "peer0" meta l4proto udp th dport { 67, 68, 546, 547 } return
  iifname "peer0" meta l4proto { tcp, udp } counter comment "unexpected"
 }
}
''')
    for uid in (0, 1000, 1100, 600001):
        for family, target in (('-4', '100.67.0.1'), ('-6', '[fd42:96::1]')):
            denied = run('setpriv', f'--reuid={uid}', '--clear-groups', 'curl', family, '--noproxy', '*',
                         '-fsS', '--max-time', '1', f'http://{target}:18082/', required=False)
            assert denied.returncode != 0, (uid, target)
        for address in ('203.0.113.53', '100.67.0.1'):
            run('setpriv', f'--reuid={uid}', '--clear-groups', 'dig', '+time=1', '+tries=1',
                '@' + address, '-p', '853', 'unsigned.fixture.example', required=False)
    # Configured transparent IPv4 guests work; other routed traffic is refused.
    for interface, subnet, allowed in (('virbr0', '122', False), ('waydroid0', '240', True)):
        namespace, peer = 'routing-guest-' + subnet, 'routing-peer' + subnet
        run('ip', 'netns', 'add', namespace)
        run('ip', 'link', 'add', interface, 'type', 'veth', 'peer', 'name', peer)
        run('ip', 'link', 'set', peer, 'netns', namespace)
        run('ip', 'addr', 'add', f'192.168.{subnet}.1/24', 'dev', interface)
        run('ip', '-6', 'addr', 'add', f'fd42:{subnet}::1/64', 'dev', interface, 'nodad')
        run('ip', 'link', 'set', interface, 'up')
        run('ip', '-n', namespace, 'addr', 'add', f'192.168.{subnet}.2/24', 'dev', peer)
        run('ip', '-n', namespace, '-6', 'addr', 'add', f'fd42:{subnet}::2/64', 'dev', peer, 'nodad')
        run('ip', '-n', namespace, 'link', 'set', peer, 'up')
        run('ip', '-n', namespace, 'link', 'set', 'lo', 'up')
        run('ip', '-n', namespace, 'route', 'add', 'default', 'via', f'192.168.{subnet}.1')
        run('ip', '-n', namespace, '-6', 'route', 'add', 'default', 'via', f'fd42:{subnet}::1')
        for target in ('203.0.113.10', '[2001:db8:dead::1]', '100.67.0.1'):
            reached = run('ip', 'netns', 'exec', namespace, 'curl', '--noproxy', '*', '-fsS',
                          '--max-time', '3', f'http://{target}:18082/', required=False).returncode == 0
            assert reached == (allowed and target == '203.0.113.10'), (interface, target, reached)
    for uid in (1100, 600001):
        answer = run('setpriv', f'--reuid={uid}', '--clear-groups', 'dig', '+short', '+time=3', '+tries=1',
                     '@127.0.0.1', 'unsigned.fixture.example', 'A').stdout
        assert '203.0.113.42' in answer and '198.18.' not in answer, answer
    request('denied.fixture.example')
    raw = run('dig', '+time=3', '+tries=1', '+short', '@127.0.0.1', '-p', '1056',
              'unsigned.fixture.example', 'A').stdout
    assert '203.0.113.42' in raw and '198.18.' not in raw, raw
    run('python3', '/etc/netsec-assurance.py', 'dns', timeout=100)
    # A refresh must resolve real source/node addresses and download over the proxy.
    run('systemctl', 'start', 'clash-source-refresh.service', timeout=160)
    wait(lambda: status()['ready'])
    wire = json.loads(run('ip', 'netns', 'exec', 'outside', 'nft', '-j', 'list', 'table', 'inet', 'routing_wire').stdout)
    counters = {}
    for entry in wire['nftables']:
        rule = entry.get('rule', {})
        for expression in rule.get('expr', []):
            if 'counter' in expression and rule.get('comment'):
                counters[rule['comment']] = expression['counter']['packets']
    assert counters.get('node', 0) > 0 and counters.get('unexpected') == 0, counters
    node_log = run('journalctl', '-u', 'control-node.service', '--no-pager').stdout
    assert '203.0.113.10:18082' in node_log, 'the remote node did not receive the application target'
    print('Wire evidence: application TCP, source refresh and real DNS used only pinned node transports', counters, flush=True)

    # Repeating a ready selection must preserve the core and policy generation.
    stage_settings = json.loads(Path('/etc/netsec-managed.json').read_text())
    spec = importlib.util.spec_from_file_location('routing_stage', stage_settings['stage'])
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
    settings = json.loads(Path(stage_settings['stageSettings']).read_text())
    pid = stage.service_request(settings, 'GET', '/status')['core_pid']
    epoch = status()['routing_epoch']
    route('proxy-only')
    assert stage.service_request(settings, 'GET', '/status')['core_pid'] == pid
    assert status()['routing_epoch'] == epoch
    invalid = run('proxy-control', 'routing', '../rules', required=False)
    assert invalid.returncode and status()['routing_epoch'] == epoch

    run('proxy-control', 'off')
    wait(lambda: json.loads(Path('/run/proxy-mode/state.json').read_text())['mode'] == 'direct')
    assert json.loads(Path('/run/proxy-mode/routing.json').read_text())['routing_requested'] == 'proxy-only'
    run('proxy-control', 'on', timeout=160)
    assert status()['routing_applied'] == 'proxy-only'

    # Emulate an activation whose new routing artifacts no longer match the
    # previous boot-scoped selection, with direct intent already committed.
    run('proxy-control', 'off')
    runtime = Path('/run/proxy-mode')
    active = runtime / 'routing-active'
    epoch_before_reload = json.loads((runtime / 'routing.json').read_text())['routing_epoch']
    active.unlink()
    active.symlink_to('/etc')
    run('systemctl', 'reload', 'nftables.service', timeout=20)
    run('systemctl', 'restart', 'proxy-mode.service', timeout=20)
    assert (runtime / 'intent').read_text().strip() == 'direct'
    assert json.loads((runtime / 'state.json').read_text())['mode'] == 'direct'
    direct = json.loads((runtime / 'routing.json').read_text())
    assert direct['routing_requested'] == 'rules' and direct['routing_epoch'] > epoch_before_reload
    assert not (runtime / 'authorized-config').exists()
    assert run('systemctl', 'is-active', 'clash-verge.service', required=False).returncode != 0
    route('proxy-only')

    # Missing complete evidence remains C and cannot reuse a prior epoch proof.
    baseline = source / 'approved/rules' / rule_snapshot / 'source-data.json'
    corrupt = baseline.read_bytes()
    baseline.write_bytes(b'{}')
    try:
        failed = run('proxy-control', 'routing', 'rules', required=False, timeout=160)
        assert failed.returncode and 'prepare' in failed.stdout, failed
        wait(lambda: json.loads(Path('/run/proxy-mode/state.json').read_text())['mode'] == 'blocked')
        assert json.loads(Path('/run/proxy-mode/routing.json').read_text())['routing_requested'] == 'rules'
        assert run('proxy-control', 'status', 'proxy', '--check', required=False).returncode != 0
    finally:
        baseline.write_bytes(corrupt)
    restored = route('rules')
    assert restored['routing_epoch'] > epoch
    request('domestic.fixture.example')
    request('denied.fixture.example', False)

    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs = [workers.submit(run, 'proxy-control', 'routing', mode, required=False, timeout=160)
                for mode in ('proxy-only', 'rules')]
        results = [job.result() for job in jobs]
    assert any(result.returncode == 0 for result in results), results
    wait(lambda: status()['ready'])
    last = status()
    assert last['routing_applied'] == last['routing_requested']
    route('proxy-only')
    run('python3', '/etc/netsec-assurance.py', 'lease', timeout=150)
    wait(lambda: status()['ready'])
    print('Routing: bidirectional switches, idempotence, off/on memory, corruption failure, concurrent selection and kernel expiry passed', flush=True)


if __name__ == '__main__':
    {'check': check, 'peer': old_peer, 'client': old_client}[sys.argv[1]]()
