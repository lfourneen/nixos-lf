"""Exercise a real WireGuard peer and an independently observed dual-stack target."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import sys


spec = importlib.util.spec_from_file_location('egress_fixture', '/etc/netsec-egress.py')
egress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(egress)
run, wait = egress.run, egress.wait
EXITS = egress.EXITS[2]
TARGETS = ('9.9.9.99', '2606:4700:1111::99')
egress.TARGETS = TARGETS
EVIDENCE = Path('/tmp/ip-tunnel-evidence')
APP = ('setpriv', '--reuid=1000', '--clear-groups')
PEER = ('ip', 'netns', 'exec', 'outside')
TARGET = ('ip', 'netns', 'exec', 'ip-target')
RELAY = 'ip-tunnel-relay.service'
ASSOCIATION_OVERRIDE = Path('/run/ip-tunnel-association-override')
BENCHMARK = '/etc/network-benchmark.py'


def peer_routes():
    run(*PEER, 'ip', 'route', 'replace', '172.31.77.2/32', 'dev', 'wg-fixture')
    run(*PEER, 'ip', '-6', 'route', 'replace', 'fd42:77::2/128', 'dev', 'wg-fixture')


def prepare():
    keys = Path('/run/wireguard-test')
    keys.mkdir(mode=0o700)
    for role in ('client', 'peer'):
        path = keys / (role + '.key')
        path.touch(mode=0o600)
        path.write_bytes(base64.b64encode(hashlib.sha256(('netsec-ip-tunnel-fixture-' + role).encode()).digest()) + b'\n')
    run('ip', 'netns', 'add', 'ip-target')
    run(*PEER, 'ip', 'link', 'add', 'peer-target', 'type', 'veth', 'peer', 'name', 'target0', 'netns', 'ip-target')
    run(*TARGET, 'ip', 'link', 'set', 'lo', 'up')
    for namespace, device, addrs in ((PEER, 'peer-target', EXITS), (TARGET, 'target0', TARGETS)):
        run(*namespace, 'ip', 'address', 'add', addrs[0] + '/24', 'dev', device)
        run(*namespace, 'ip', '-6', 'address', 'add', addrs[1] + '/64', 'dev', device, 'nodad')
        run(*namespace, 'ip', 'link', 'set', device, 'mtu', '1280', 'up')
    for family, target, source, prefix in zip((4, 6), TARGETS, EXITS, (32, 128)):
        run(*PEER, 'ip', f'-{family}', 'route', 'replace', f'{target}/{prefix}', 'dev', 'peer-target', 'src', source)
    run(*PEER, 'sysctl', '-w', 'net.ipv4.ip_forward=1', 'net.ipv6.conf.all.forwarding=1')
    run(*PEER, 'ip', 'link', 'add', 'wg-fixture', 'type', 'wireguard')
    run(*PEER, 'wg', 'set', 'wg-fixture', 'listen-port', '51820', 'private-key', keys / 'peer.key',
        'peer', 'sDBurnUHLq/yWtpNcnfOhMCUeXNsn8Gyz9FxzHAYeFw=', 'allowed-ips', '172.31.77.2/32,fd42:77::2/128')
    run(*PEER, 'ip', 'link', 'set', 'wg-fixture', 'mtu', '1380', 'up')
    peer_routes()
    run(*PEER, 'nft', '-f', '-', input='''
table inet ip_tunnel_test {
  chain relay {
    type filter hook input priority filter; policy accept;
    iifname != "wg-fixture" meta l4proto { tcp, udp } th dport 18081 drop
  }
  chain source {
    type nat hook postrouting priority srcnat; policy accept;
    iifname "wg-fixture" oifname "peer-target" ip saddr 172.31.77.2 snat ip to 9.9.9.12
    iifname "wg-fixture" oifname "peer-target" ip6 saddr fd42:77::2 snat ip6 to 2606:4700:1111::12
  }
}
''')
    run('systemd-run', '--unit=ip-tunnel-target', *TARGET, sys.executable, __file__, 'origin')
    run('systemd-run', '--unit=ip-tunnel-udp-health', *TARGET, sys.executable, __file__, 'udp-health')
    wait(lambda: ':18091' in run(*TARGET, 'ss', '-uln').stdout, timeout=10)
    config = Path('/run/wireguard-test/relay.yaml')
    config.write_text('''mode: rule
ipv6: true
geo-auto-update: false
listeners:
  - name: wg-exit
    type: socks
    listen: 1.1.1.1
    port: 18081
    udp: true
    users: []
rules:
  - MATCH,DIRECT
''')
    start_relay(shutil.which('mihomo'), '-d', '/run/wireguard-test', '-f', config)


def start_relay(*command):
    run('systemd-run', '--collect', '--unit=' + RELAY, *PEER, *command)
    wait(lambda: ':18081' in run(*PEER, 'ss', '-lnt').stdout)


def capture():
    tcpdump = shutil.which('tcpdump')
    assert tcpdump
    for label, namespace, device in (('uplink', (), 'ens1'), ('peer', PEER, 'wg-fixture'), ('target', TARGET, 'target0')):
        path = EVIDENCE / (label + '.pcap')
        run('systemd-run', f'--unit=ip-tunnel-capture-{label}', *namespace, tcpdump,
            '-n', '-U', '-s', '0', '-i', device, '-w', path, 'ip or ip6')
        wait(lambda: path.exists() and path.stat().st_size >= 24, timeout=10)


def ping(target, *args, required=True):
    return run(*APP, 'ping', '-n', '-c', '1', '-W', '2', *args, target, required=required)


def application(protocol, target, required=True):
    return run(*APP, sys.executable, __file__, 'probe', protocol, target, required=required)


def echo_stage(stage):
    return egress.status().get('ip_tunnel_health', {}).get('stage') == stage


def inlet_probe():
    for host in ('127.0.0.1', '::1', '::ffff:127.0.0.1', '100.67.0.2', '2001:db8:dead::2'):
        for port in (33332, 33333, 7896, 7897, 7898):
            try:
                with socket.create_connection((host, port), timeout=.2):
                    raise AssertionError(('ordinary application reached proxy listener', host, port))
            except OSError:
                pass
        with socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, socket.SOCK_DGRAM) as stream:
            try:
                stream.sendto(b'protected-inlet', (host, 7897))
            except PermissionError:
                pass
    with socket.socket(socket.AF_UNIX) as stream:
        try:
            stream.connect('/run/clash-verge-rev/core/mihomo.sock')
        except (PermissionError, FileNotFoundError):
            return
        raise AssertionError('ordinary application reached primary controller')


def check_inlets(mode):
    assert egress.status()['mode'] == mode
    def dropped():
        entries = json.loads(run('nft', '-j', 'list', 'chain', 'inet', 'lf_filter', 'lf_proxy_inlet_guard').stdout)
        return sum(expr.get('counter', {}).get('packets', 0) for entry in entries['nftables']
                   for expr in entry.get('rule', {}).get('expr', []))
    before = dropped()
    run(*APP, sys.executable, __file__, 'inlets')
    # The loopback redirect guard rejects three TCP attempts before conntrack.
    assert dropped() >= before + 27, (before, dropped())
    print(f'{mode}: protected TCP/UDP inlets and primary Unix controller refused', flush=True)


def layer_checks():
    ca = json.loads(Path('/etc/netsec-egress.json').read_text())['tls'] + '/ca.pem'
    name = 'tunnel.egress.fixture.example'

    def sample(label, address, port, hostname=name, *, explicit=False, count=1, timeout=2, system_dns=False):
        actor = () if explicit else APP
        proxy = ('--proxy', 'http://127.0.0.1:33332') if explicit else ()
        resolution = ('--system-resolution', '--no-echo') if system_dns else ()
        result = run(*actor, sys.executable, BENCHMARK, 'layers', '--name', hostname,
                     '--address', address, '--port', port, '--ca-file', ca,
                     '--samples', count, '--timeout', timeout, *proxy, *resolution, timeout=45)
        (EVIDENCE / (label + '.json')).write_text(result.stdout)
        value = json.loads(result.stdout)
        assert all(item['https']['elapsed_ms'] <= timeout * 1000 + 200 for item in value['samples']), value
        return value['samples']

    for version, address in zip((4, 6), TARGETS):
        for item in sample(f'layers-v{version}', address, 18443, count=5):
            assert item['dns']['outcome'] == 'answer' and item['dns']['answers'][0]['address'] == address, item
            assert item['echo']['outcome'] == 'echo-reply' and item['https']['outcome'] == 'ok', item
            assert list(item['https']['stages']) == ['tcp', 'tls', 'http'], item
        item, = sample(f'hostname-v{version}', address, 18443, system_dns=True, timeout=4)
        assert 'dns' not in item and 'echo' not in item, item
        assert item['https']['outcome'] == 'ok' and list(item['https']['stages']) == ['system_dns', 'tcp', 'tls', 'http'], item
    item, = sample('layers-connect', TARGETS[0], 18443, explicit=True, count=1, timeout=4)
    assert item['https']['outcome'] == 'ok' and item['https']['stages']['connect']['status'] == 200, item
    item, = sample('tls-certificate', TARGETS[0], 18443, 'valid.signed.fixture.example')
    assert item['https']['outcome'] == 'certificate', item
    for label, hostname in (('NXDOMAIN', 'absent'), ('NODATA', 'service'), ('SERVFAIL', 'bogus')):
        item, = sample(label, TARGETS[0], 18443, f'{hostname}.signed.fixture.example', timeout=4)
        assert item['dns']['outcome'] == label and not item['dns']['answers'], item
        assert item['https']['outcome'] != 'ok', item

    run(*TARGET, 'nft', '-f', '-', input='''
table inet connection_fault {
  chain input {
    type filter hook input priority filter; policy accept;
    tcp dport 18444 counter drop
  }
}
''')
    for label, address, port in (('port-rst', TARGETS[0], 18445), ('blackhole', TARGETS[0], 18444),
                                 ('no-host', '9.9.9.98', 18443), ('no-mapping', '198.18.255.254', 18443)):
        item, = sample(label, address, port)
        assert item['https']['outcome'] != 'ok' and 'http' not in item['https']['stages'], item
        assert item['echo']['outcome'] == ('echo-reply' if address == TARGETS[0] else 'no-echo'), item
    print('Recorded DNS outcomes, native and CONNECT stages, TLS failure, RST, blackhole and unreachable targets')


def check_udp_health(result):
    observation = Path('/run/proxy-mode/udp-health.json')
    previous = observation.stat().st_mtime_ns if observation.exists() else None
    def observed():
        run('systemctl', 'reset-failed', 'proxy-udp-health.service', required=False)
        run('systemctl', 'start', 'proxy-udp-health.service')
        return observation.exists() and observation.stat().st_mtime_ns != previous
    wait(observed, timeout=20)
    verdict = egress.status()
    assert verdict['ready'] and verdict['capabilities']['udp'] == result, (verdict, observation.read_text())


def udp_failures():
    run(*PEER, 'nft', 'insert', 'rule', 'inet', 'ip_tunnel_test', 'relay',
        'iifname', 'wg-fixture', 'udp', 'dport', '18081', 'drop')
    for target, source in zip(TARGETS, EXITS):
        assert application('udp', target, required=False).returncode
        assert json.loads(application('tcp', target).stdout)['peer'] == source
    check_udp_health('no-reply')
    rules = run(*PEER, 'nft', '-a', 'list', 'chain', 'inet', 'ip_tunnel_test', 'relay').stdout
    handle = next(line.rsplit(' ', 1)[1] for line in rules.splitlines() if 'udp dport 18081 drop' in line)
    run(*PEER, 'nft', 'delete', 'rule', 'inet', 'ip_tunnel_test', 'relay', 'handle', handle)

    run('systemctl', 'stop', RELAY)
    start_relay(sys.executable, __file__, 'faulty-relay')
    for bind in ('1.0.0.1 18081', '1.1.1.1 18082'):
        ASSOCIATION_OVERRIDE.write_text(bind)
        assert application('udp', TARGETS[0], required=False).returncode
        assert ping(TARGETS[0]).returncode == 0
        assert json.loads(application('tcp', TARGETS[0]).stdout)['peer'] == EXITS[0]
    assert egress.status()['capabilities']['udp'] == 'no-reply'
    ASSOCIATION_OVERRIDE.unlink()
    run('systemctl', 'stop', RELAY)
    start_relay(shutil.which('mihomo'), '-d', '/run/wireguard-test', '-f', '/run/wireguard-test/relay.yaml')
    wait(lambda: egress.status()['ready'])
    assert json.loads(application('udp', TARGETS[0]).stdout)['peer'] == EXITS[0]
    check_udp_health('verified')


def check():
    EVIDENCE.mkdir()
    prepare()
    capture()
    source = Path('/var/lib/clash-source/selected').resolve()
    wait(lambda: (source / 'approved/rules/settled.json').exists())
    status, data = egress.api('GET', '/proxies')
    assert status == 200 and 'TunnelRelay' not in json.loads(data)['proxies'], data
    choices = json.loads(run('proxy-control', 'egress', 'list').stdout)
    assert [node['name'] for node in choices] == ['TunnelRelay'], choices
    check_inlets('proxy')
    run(*APP, 'curl', '--noproxy', '*', '-fsS', '--max-time', '5', 'http://tun.fixture.example:8080/')
    run('proxy-control', 'routing', 'proxy-only', timeout=110)
    verdict = egress.status()
    status, data = egress.api('GET', '/proxies')
    assert status == 200 and 'TunnelRelay' in json.loads(data)['proxies'], data
    assert verdict['capabilities']['icmp'] == 'verified-dual-stack', verdict
    assert verdict['capabilities']['udp_declared'] is True and verdict['capabilities']['udp'] == 'not-probed', verdict
    check_udp_health('verified')
    for target, source_address in zip(TARGETS, EXITS):
        ping(target)
        for protocol in ('tcp', 'udp'):
            assert json.loads(application(protocol, target).stdout)['peer'] == source_address
        assert json.loads(application('udp', target).stdout)['peer'] == source_address
    status, data = egress.api('GET', f'http://{TARGETS[0]}:18090/browser', inlet='/run/clash-strict/proxy.sock')
    assert status == 200 and json.loads(data)['peer'] == EXITS[0], (status, data)
    check_inlets('proxy')
    layer_checks()
    real = run('dig', '+short', '@127.0.0.1', '-p', '1054', 'tun.fixture.example', 'A').stdout.split()
    assert real == ['203.0.113.10'], real
    ping('tun.fixture.example')
    fake = run('dig', '+short', '@127.0.0.1', '-p', '1053', 'tun.fixture.example', 'A').stdout.strip()
    assert fake.startswith('198.18.') and ping(fake, required=False).returncode
    assert ping('9.9.9.98', required=False).returncode
    assert 'time to live exceeded' in ping(TARGETS[0], '-t', '1', required=False).stdout.lower()
    for target in TARGETS:
        result = ping(target, '-M', 'do', '-s', '1300', required=False)
        assert result.returncode and ('1280' in result.stdout or '1280' in result.stderr), result

    run('systemctl', 'kill', '--kill-whom=main', '-s', 'STOP', 'proxy-mode.service')
    try:
        run('nft', 'flush', 'set', 'inet', 'lf_filter', 'b_authorization')
        for target in TARGETS:
            assert ping(target, required=False).returncode
    finally:
        run('systemctl', 'kill', '--kill-whom=main', '-s', 'CONT', 'proxy-mode.service')
    wait(lambda: ping(TARGETS[0], required=False).returncode == 0)

    udp_failures()
    run(*PEER, 'nft', 'add', 'table', 'inet', 'echo_fault')
    run(*PEER, 'nft', 'add', 'chain', 'inet', 'echo_fault', 'forward', '{ type filter hook forward priority 0; policy accept; }')
    run(*PEER, 'nft', 'add', 'rule', 'inet', 'echo_fault', 'forward', 'icmpv6', 'type', 'echo-request', 'drop')
    wait(lambda: echo_stage('echo6') and egress.status()['mode'] == 'blocked')
    assert egress.status()['ip_tunnel_health']['echo'] == {'ipv4': True, 'ipv6': False}
    run(*PEER, 'nft', 'delete', 'table', 'inet', 'echo_fault')
    wait(lambda: egress.status()['ready'])

    run('ip', '-6', 'route', 'add', TARGETS[1] + '/128', 'dev', 'lo', 'table', '763')
    wait(lambda: echo_stage('route6') and egress.status()['mode'] == 'blocked')
    run('ip', '-6', 'route', 'del', TARGETS[1] + '/128', 'dev', 'lo', 'table', '763')
    wait(lambda: egress.status()['ready'])

    run(*PEER, 'ip', 'link', 'set', 'wg-fixture', 'down')
    wait(lambda: echo_stage('echo-both') and egress.status()['mode'] == 'blocked')
    check_inlets('blocked')
    assert egress.status()['ip_tunnel_health']['handshake'] in ('recent', 'none-recent')
    for target in TARGETS:
        assert ping(target, required=False).returncode
        assert application('tcp', target, required=False).returncode
        assert application('udp', target, required=False).returncode
    run(*PEER, 'ip', 'link', 'set', 'wg-fixture', 'up')
    peer_routes()
    wait(lambda: egress.status()['ready'], timeout=95)
    for target in TARGETS:
        ping(target)
    run('proxy-control', 'off')
    assert run('ip', 'link', 'show', 'lf-wg-exit', required=False).returncode
    wait(lambda: egress.status()['mode'] == 'direct')
    check_inlets('direct')

    captures = {label: run('tcpdump', '-nn', '-r', EVIDENCE / (label + '.pcap')).stdout
                for label in ('uplink', 'peer', 'target')}
    for target, source_address in zip(TARGETS, EXITS):
        assert f'{source_address} > {target}: ICMP' in captures['target'], captures['target']
        assert f'{source_address}.' in captures['target'] and f'> {target}.18090:' in captures['target']
        assert target not in captures['uplink'], captures['uplink']
        assert target in captures['peer']
    assert '1.1.1.1.51820' in captures['uplink'] and '1.1.1.1.18081' not in captures['uplink']
    assert '1.0.0.1' not in captures['uplink'] and '1.0.0.1' not in captures['peer']
    assert '1.1.1.1.18082' not in captures['peer']
    assert fake not in captures['peer']
    print('Captured real echo, TCP and UDP through the reference relay in both families')


def migrate():
    base = Path('/run/current-system').resolve()
    run('proxy-control', 'routing', 'proxy-only', timeout=110)

    def switch(generation):
        previous = egress.status()
        run(generation / 'bin/switch-to-configuration', 'test', timeout=140)
        wait(lambda: egress.status()['mode'] == 'blocked')
        result = run('proxy-control', 'routing', 'proxy-only', timeout=45, required=False)
        assert result.returncode and 'run clash-rules' in result.stderr, result
        assert application('tcp', TARGETS[0], required=False).returncode
        run('proxy-control', 'routing', 'rules', timeout=110)
        current = egress.status()
        assert current['routing_epoch'] > previous['routing_epoch'] and current['egress_requested'] is None, current
        assert run('ip', 'link', 'show', 'lf-wg-exit', required=False).returncode
        for family in (4, 6):
            assert not run('ip', f'-{family}', 'rule', 'show', 'pref', '4900').stdout

    switch(base / 'specialisation/rotated-peer')
    run(*PEER, 'wg', 'set', 'wg-fixture', 'listen-port', '51822')
    run('proxy-control', 'routing', 'proxy-only', timeout=110)
    assert ':51822' in run('wg', 'show', 'lf-wg-exit', 'endpoints').stdout
    for target in TARGETS:
        ping(target)

    switch(base / 'specialisation/subscription-only')
    choices = json.loads(run('proxy-control', 'egress', 'list').stdout)
    ordinary = next(item['id'] for item in choices if item['name'] == 'Fixture')
    run('proxy-control', 'egress', ordinary, timeout=110)
    verdict = egress.status()
    assert verdict['capabilities']['icmp'] == 'blocked' and verdict['capabilities']['udp_declared'] is True, verdict
    assert ping(TARGETS[0], required=False).returncode

    switch(base)
    run(*PEER, 'wg', 'set', 'wg-fixture', 'listen-port', '51820')
    run('proxy-control', 'routing', 'proxy-only', timeout=110)
    assert egress.status()['capabilities']['icmp'] == 'verified-dual-stack'
    print('Replaced and removed the tunnel, recovered subscription-only routing, and rolled back the generation')


if sys.argv[1] == 'check':
    check()
elif sys.argv[1] == 'origin':
    egress.origin()
elif sys.argv[1] == 'udp-health':
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind((TARGETS[0], 18091))
        while True:
            payload, peer = listener.recvfrom(64)
            if peer[0] == EXITS[0]:
                listener.sendto(payload, peer)
elif sys.argv[1] == 'probe':
    egress.probe(*sys.argv[2:])
elif sys.argv[1] == 'faulty-relay':
    egress.node(2, ASSOCIATION_OVERRIDE)
elif sys.argv[1] == 'migrate':
    migrate()
elif sys.argv[1] == 'inlets':
    inlet_probe()
