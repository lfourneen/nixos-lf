"""Production topology confinement and localhost compatibility in the VM."""
import json
import os
from pathlib import Path
import pwd
import socket
import struct
import subprocess
import sys
import threading
import time


def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), text=True, capture_output=True,
                          check=True, timeout=20, **kwargs)


def attacker(fd, unrelated_port):
    import ctypes
    assert os.getuid() == pwd.getpwnam('clash-core').pw_uid
    assert os.stat('/proc/self/ns/net').st_ino != os.fstat(fd).st_ino
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.setns(fd, 0x40000000) == -1 and ctypes.get_errno() == 1
    os.close(fd)
    # Identically named objects are confined to the private namespace.
    run('nft', 'add', 'table', 'inet', 'lf_filter')
    run('nft', 'flush', 'table', 'inet', 'lf_filter')
    run('ip', 'route', 'add', '192.0.2.88/32', 'dev', 'lo')
    for family, target, source in [(socket.AF_INET, '169.254.169.254', '10.255.253.2'),
                                    (socket.AF_INET6, 'fd00:ec2::254', 'fd46:6c66:636f::2')]:
        for mark in (6666, 0x80000, 0x233):
            with socket.socket(family, socket.SOCK_DGRAM) as stream:
                stream.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, mark)
                stream.sendto(b'forged-mark', (target, 18097))
        udp = struct.pack('!HHHH', 18096, 18097, 12, 0) + b'raw!'
        if family == socket.AF_INET:
            header = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 20+len(udp), 1, 0, 64, 17, 0,
                                 socket.inet_aton(source), socket.inet_aton(target))
        else:
            header = struct.pack('!IHBB16s16s', 6 << 28, len(udp), 17, 64,
                                 socket.inet_pton(family, source), socket.inet_pton(family, target))
        with socket.socket(family, socket.SOCK_RAW, socket.IPPROTO_RAW) as stream:
            stream.sendto(header + udp, (target, 0))
    run('ip', 'route', 'del', '192.0.2.88/32', 'dev', 'lo')
    run('nft', 'delete', 'table', 'inet', 'lf_filter')
    payload = b'forged-reply'
    udp = struct.pack('!HHHH', 18099, unrelated_port, 8+len(payload), 0) + payload
    header = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 20+len(udp), 2, 0, 64, 17, 0,
                         socket.inet_aton('100.67.0.1'), socket.inet_aton('100.67.0.2'))
    with socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW) as stream:
        stream.sendto(header + udp, ('100.67.0.2', 0))


def packets(chain='lf_core_egress'):
    value = json.loads(run('nft', '-j', 'list', 'chain', 'inet', 'lf_filter', chain).stdout)
    return sum(expression.get('counter', {}).get('packets', 0)
               for entry in value['nftables'] for expression in entry.get('rule', {}).get('expr', []))


def confinement():
    assert pwd.getpwnam('clash-loopback').pw_uid != pwd.getpwnam('clash-core').pw_uid
    run('systemctl', 'stop', 'proxy-mode.service')
    before = {family: run('ip', family, '-j', 'route', 'show', 'table', 'all').stdout for family in ('-4', '-6')}
    rules = {family: run('ip', family, '-j', 'rule', 'show').stdout for family in ('-4', '-6')}
    base = run('nft', '-s', '-j', 'list', 'chain', 'inet', 'lf_filter', 'lf_core_egress').stdout
    count = packets()
    run('systemd-run', '--unit=namespace-unrelated', 'ip', 'netns', 'exec', 'outside',
        sys.executable, __file__, 'unrelated-server')
    deadline = time.monotonic() + 5
    while not Path('/run/namespace-unrelated-ready').exists():
        assert time.monotonic() < deadline
        time.sleep(.05)
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(2)
    client.connect(('100.67.0.1', 18099))
    client.send(b'genuine')
    assert client.recv(4096) == b'genuine'
    fd = os.open('/proc/1/ns/net', os.O_RDONLY)
    try:
        run('ip', 'netns', 'exec', 'lf-clash-core', 'setpriv', '--reuid=clash-core',
            '--regid=clash-core', '--clear-groups', '--bounding-set=-all,+net_admin,+net_raw',
            '--inh-caps=+net_admin,+net_raw', '--ambient-caps=+net_admin,+net_raw', '--no-new-privs',
            sys.executable, __file__, 'attacker', str(fd), str(client.getsockname()[1]), pass_fds=(fd,))
        client.settimeout(.5)
        try:
            data = client.recv(4096)
        except socket.timeout:
            data = b''
        assert not data, ('namespace injected a reply into an unrelated host connection', data)
        for family in ('-4', '-6'):
            assert before[family] == run('ip', family, '-j', 'route', 'show', 'table', 'all').stdout
            assert rules[family] == run('ip', family, '-j', 'rule', 'show').stdout
        assert base == run('nft', '-s', '-j', 'list', 'chain', 'inet', 'lf_filter', 'lf_core_egress').stdout
        after = packets()
        assert after >= count + 8, (count, after)
    finally:
        os.close(fd)
        client.close()
        run('systemctl', 'stop', 'namespace-unrelated.service')
        run('systemctl', 'start', 'proxy-mode.service')
    print('Private NET_ADMIN/RAW, injected host netns FD denial, mark/raw guards and unchanged host policy passed')


def localhost_servers():
    # Every echo is received through a real host loopback-bound socket.
    def server(family, address, kind, port):
        with socket.socket(family, kind) as stream:
            stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                stream.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            stream.bind((address, port))
            if kind == socket.SOCK_STREAM:
                stream.listen()
            while True:
                if kind == socket.SOCK_DGRAM:
                    data, peer = stream.recvfrom(4096)
                    stream.sendto(struct.pack('!H', port) + data, peer)
                else:
                    client, _ = stream.accept()
                    with client:
                        client.sendall(struct.pack('!H', port) + client.recv(4096))
    for family, address in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            for port in (18098, 18100):
                threading.Thread(target=server, args=(family, address, kind, port), daemon=True).start()
    threading.Event().wait()


def localhost_probe():
    from contextlib import ExitStack
    for family, address in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            with ExitStack() as stack:
                streams = []
                # Keep distinct destinations and clients alive together; a
                # constant-port or shared-association relay must not pass.
                for number, port in enumerate((18098, 18100, 18098)):
                    stream = stack.enter_context(socket.socket(family, kind))
                    stream.settimeout(3)
                    stream.connect((address, port))
                    data = f'host-localhost-{number}'.encode()
                    stream.sendall(data)
                    streams.append((stream, port, data))
                for stream, port, data in streams:
                    assert stream.recv(4096) == struct.pack('!H', port) + data, (family, kind, port)
                    if kind == socket.SOCK_DGRAM:
                        stream.send(b'')
                        assert stream.recv(4096) == struct.pack('!H', port)
    print('Dual-stack multi-port TCP/UDP, concurrent UDP clients and empty datagrams passed')


def broker_denials():
    # Directory/group access alone must not authorize another UID. An allowed
    # core UID also cannot select the reserved proxy/DNS endpoints.
    for uid, ports in ((1000, (18098,)), (pwd.getpwnam('clash-core').pw_uid, (7897, 7898, 1053, 1054, 1055))):
        for port in ports:
            run('setpriv', f'--reuid={uid}', '--regid=clash-core', '--clear-groups',
                sys.executable, __file__, 'broker-denied-client', str(port))


def broker_denied_client(port):
    for protocol, kind in (('tcp', socket.SOCK_STREAM), ('udp', socket.SOCK_SEQPACKET)):
        for version in (4, 6):
            with socket.socket(socket.AF_UNIX, kind) as stream:
                stream.settimeout(3)
                stream.connect('/run/clash-loopback/' + protocol + '.sock')
                try:
                    stream.sendall(struct.pack('!BH', version, port))
                    assert stream.recv(4096) == b''
                except (BrokenPipeError, ConnectionResetError):
                    pass


def lost_peer():
    # A private NET_ADMIN may delete its own veth endpoint. That is a proxy
    # outage, never permission to send the loopback inlet onto a physical LAN.
    run('systemctl', 'stop', 'clash-core-uplink-watch.service')
    run('ip', '-n', 'lf-clash-core', 'link', 'delete', 'lf-host')
    try:
        run('ip', '-n', 'outside', 'addr', 'add', '10.255.253.2/32', 'dev', 'peer0')
        run('ip', 'route', 'add', '10.255.253.2/32', 'via', '100.67.0.1', 'dev', 'ens1')
        run('systemd-run', '--unit=namespace-inlet-spy', 'ip', 'netns', 'exec', 'outside',
            sys.executable, __file__, 'inlet-spy')
        deadline = time.monotonic() + 5
        while not Path('/run/namespace-spy-ready').exists():
            assert time.monotonic() < deadline
            time.sleep(.05)
        assert json.loads(run('ip', '-j', 'route', 'get', '10.255.253.2').stdout)[0]['dev'] == 'ens1'
        before = packets('lf_core_route_guard')
        result = subprocess.run(['curl', '--noproxy', '', '-x', 'http://127.0.0.1:7897',
            '--interface', '100.67.0.2', '--max-time', '2', '-fsS',
            'http://namespace-leak.fixture.example/'], capture_output=True)
        assert result.returncode != 0
        assert packets('lf_core_route_guard') > before
        assert not Path('/run/namespace-inlet-leak').exists()
    finally:
        run('systemctl', 'stop', 'namespace-inlet-spy.service')
        run('ip', 'route', 'del', '10.255.253.2/32')
        run('ip', '-n', 'outside', 'addr', 'del', '10.255.253.2/32', 'dev', 'peer0')
        run('systemctl', 'start', 'clash-core-uplink-watch.service')


def diagnose():
    for namespace in ([], ['ip', 'netns', 'exec', 'lf-clash-core']):
        for command in (['ip', 'address'], ['ip', 'rule'], ['ip', 'route', 'show', 'table', 'all'],
                        ['ip', 'neigh'], ['ss', '-tn']):
            result = subprocess.run(namespace + command, capture_output=True, text=True)
            print(namespace + command, result.returncode, result.stdout, result.stderr, flush=True)
    for chain in ('lf_core_scrub', 'lf_core_path', 'lf_core_input', 'lf_core_forward_guard',
                  'lf_core_egress', 'lf_core_return', 'forward'):
        print(run('nft', 'list', 'chain', 'inet', 'lf_filter', chain).stdout, flush=True)
    print(run('nft', 'list', 'chain', 'ip', 'lf_nat', 'lf_core_snat').stdout, flush=True)
    run('nft', '-f', '-', input='''table inet namespace_debug {
      chain pre { type filter hook prerouting priority -311; policy accept;
        iifname "Mihomo" meta l4proto tcp meta nftrace set 1
        iifname "ens1" tcp sport 18080 meta nftrace set 1
      }
      chain out { type filter hook output priority -311; policy accept;
        tcp dport 7897 meta nftrace set 1
      }
    }''')
    run('systemd-run', '--unit=namespace-trace', '--property=StandardOutput=append:/tmp/namespace-trace.log',
        'nft', 'monitor', 'trace')
    subprocess.run(['curl', '-v', '--noproxy', '', '-x', 'http://127.0.0.1:7897', '--max-time', '3',
                    'http://node.fixture.example:8080/'], capture_output=True)
    time.sleep(1)
    print(Path('/tmp/namespace-trace.log').read_text(), flush=True)
    run('systemctl', 'stop', 'namespace-trace.service')
    run('nft', 'delete', 'table', 'inet', 'namespace_debug')


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'attacker':
        attacker(int(sys.argv[2]), int(sys.argv[3]))
    elif action == 'confinement':
        confinement()
    elif action == 'localhost-server':
        localhost_servers()
    elif action == 'localhost-probe':
        localhost_probe()
    elif action == 'broker-denials':
        broker_denials()
    elif action == 'broker-denied-client':
        broker_denied_client(int(sys.argv[2]))
    elif action == 'unrelated-server':
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
            stream.bind(('100.67.0.1', 18099))
            Path('/run/namespace-unrelated-ready').touch()
            while True:
                data, peer = stream.recvfrom(4096)
                stream.sendto(data, peer)
    elif action == 'lost-peer':
        lost_peer()
    elif action == 'inlet-spy':
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
            stream.bind(('10.255.253.2', 7897))
            stream.listen()
            Path('/run/namespace-spy-ready').touch()
            while True:
                client, _ = stream.accept()
                with client:
                    if client.recv(4096):
                        Path('/run/namespace-inlet-leak').touch()
    elif action == 'diagnose':
        diagnose()
