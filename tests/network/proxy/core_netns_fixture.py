"""VM-only core netns prototype; never imported by the production launcher.

Host root owns the veth, routes and atomic authorization. The core owns its
TUN/routes and sockets inside lf-core. No host network FD enters the service.
The fixture controls mode transitions; it is not a replacement supervisor.
"""
import ipaddress
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time

SELF = '/etc/core-netns.py'
CORE4, CORE6 = '10.255.0.2', 'fd42:ffff::2'
ORIGIN4, ORIGIN6 = '203.0.113.10', '2001:db8:42::10'
NODE4, NODE6 = '203.0.113.20', '2001:db8:42::20'
MARK = '0x4c460002'


def run(*args, okay=True, **kwargs):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=20, **kwargs)
    if okay:
        assert result.returncode == 0, (args, result.stdout, result.stderr)
    return result


def ns(*args):
    return run('ip', 'netns', 'exec', 'lf-core', *args)


def wait(check, seconds=20):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.1)
    raise TimeoutError('core netns fixture timed out')


def policy(mode, authorized=True):
    # Drop verdicts precede established accepts in both directions. Untrusted
    # marks are cleared before host routing/conntrack and grant evaluation.
    marking = ('meta skuid 1000 ip daddr { 203.0.113.10, 198.18.0.0/16 } meta mark set 0x123\n'
               'meta skuid 1000 ip6 daddr 2001:db8:42::10 meta mark set 0x123') if mode == 'B' else ''
    blocked = ('meta skuid 1000 oifname != "lo" counter drop') if mode == 'C' else ''
    inlet = '' if mode == 'B' else f'meta skuid 1000 ip daddr {{ {CORE4}, 198.18.0.0/16 }} counter drop\nmeta skuid 1000 ip6 daddr {CORE6} counter drop'
    replies = f'ct mark {MARK} ct direction reply counter drop' if mode == 'C' else ''
    allowed4 = f'ip daddr {NODE4} meta l4proto {{ tcp, udp }} th dport 18080 return\nip daddr {NODE4} udp dport 15353 return' if authorized else ''
    allowed6 = f'ip6 daddr {NODE6} meta l4proto {{ tcp, udp }} th dport 18080 return' if authorized else ''
    result = f'''
destroy table inet lf_core_prototype
table inet lf_core_prototype {{
 chain scrub {{ type filter hook prerouting priority -310; policy accept;
  iifname "lf-core-host" meta mark set 0
 }}
 chain output {{ type route hook output priority -150; policy accept;
  {blocked}
  {inlet}
  meta skuid 1000 oifname != "lo" ct mark set {MARK}
  {marking}
 }}
 chain input {{ type filter hook input priority -10; policy accept;
  {replies}
  iifname "lf-core-host" ip6 hoplimit 255 icmpv6 type {{ nd-neighbor-solicit, nd-neighbor-advert }} accept
  iifname "lf-core-host" ct direction reply ct mark {MARK} accept
  iifname "lf-core-host" counter drop
 }}
 chain node {{ {allowed4}
  {allowed6}
  counter drop
 }}
 chain node_reply {{
  {f'ct original ip daddr {NODE4} ct protocol {{ tcp, udp }} ct original proto-dst 18080 return' if authorized else ''}
  {f'ct original ip6 daddr {NODE6} ct protocol {{ tcp, udp }} ct original proto-dst 18080 return' if authorized else ''}
  {f'ct original ip daddr {NODE4} ct protocol udp ct original proto-dst 15353 return' if authorized else ''}
  counter drop
 }}
 chain forward {{ type filter hook forward priority -10; policy accept;
  iifname "lf-core-host" jump node
  oifname "lf-core-host" ct direction reply jump node_reply
  oifname "lf-core-host" ct direction original counter drop
 }}
}}
'''
    run('nft', '-f', '-', input=result)


def setup():
    run('ip', 'netns', 'add', 'outside')
    run('ip', 'link', 'add', 'outside-host', 'type', 'veth', 'peer', 'name', 'outside-peer')
    run('ip', 'link', 'set', 'outside-peer', 'netns', 'outside')
    run('ip', 'addr', 'add', '192.0.2.1/30', 'dev', 'outside-host')
    run('ip', '-6', 'addr', 'add', '2001:db8:ff::1/64', 'dev', 'outside-host', 'nodad')
    run('ip', 'link', 'set', 'outside-host', 'up')
    for cmd in [('link', 'set', 'lo', 'up'), ('link', 'set', 'outside-peer', 'up'),
                ('addr', 'add', '192.0.2.2/30', 'dev', 'outside-peer'),
                ('-6', 'addr', 'add', '2001:db8:ff::2/64', 'dev', 'outside-peer', 'nodad')]:
        run('ip', '-n', 'outside', *cmd)
    for address in (ORIGIN4, ORIGIN6, NODE4, NODE6):
        run('ip', '-n', 'outside', 'addr', 'add', address+('/128' if ':' in address else '/32'), 'dev', 'lo', *(['nodad'] if ':' in address else []))
    run('ip', 'route', 'add', '203.0.113.0/24', 'via', '192.0.2.2')
    run('ip', '-6', 'route', 'add', '2001:db8:42::/64', 'via', '2001:db8:ff::2')
    run('ip', '-n', 'outside', 'route', 'add', 'default', 'via', '192.0.2.1')
    run('ip', '-n', 'outside', '-6', 'route', 'add', 'default', 'via', '2001:db8:ff::1')
    run('ip', 'netns', 'add', 'lf-core')
    run('ip', 'link', 'add', 'lf-core-host', 'type', 'veth', 'peer', 'name', 'uplink0')
    run('ip', 'link', 'set', 'uplink0', 'netns', 'lf-core')
    for namespace, device, address4, address6 in [(None, 'lf-core-host', '10.255.0.1', 'fd42:ffff::1'), ('lf-core', 'uplink0', CORE4, CORE6)]:
        cmd = ['ip'] + (['-n', namespace] if namespace else [])
        run(*cmd, 'addr', 'add', address4+'/30', 'dev', device)
        run(*cmd, '-6', 'addr', 'add', address6+'/64', 'dev', device, 'nodad')
        run(*cmd, 'link', 'set', device, 'up')
    ns('sysctl', '-w', 'net.ipv4.ip_forward=1', 'net.ipv6.conf.all.forwarding=1')
    run('ip', '-n', 'lf-core', 'link', 'set', 'lo', 'up')
    run('ip', '-n', 'lf-core', 'route', 'add', 'default', 'via', '10.255.0.1')
    run('ip', '-n', 'lf-core', '-6', 'route', 'add', 'default', 'via', 'fd42:ffff::1')
    # The first route lookup precedes the output marking hook. Give synthetic
    # destinations a route; A/C refuse them independently in the output guard.
    run('ip', 'route', 'add', '198.18.0.0/16', 'via', CORE4, 'dev', 'lf-core-host')
    for family, gateway in [('-4', CORE4), ('-6', CORE6)]:
        run('ip', family, 'route', 'add', 'table', '100', 'default', 'via', gateway, 'dev', 'lf-core-host')
        run('ip', family, 'rule', 'add', 'pref', '100', 'fwmark', '0x123', 'lookup', '100')
    policy('C')


def origin():
    def push(label, send):
        wait(lambda: Path('/run/core-holds', label).exists(), 120)
        try:
            send(b'server-push')
        except OSError:
            pass
    def respond(connection):
        with connection:
            while data := connection.recv(8192):
                if data.startswith(b'GET '):
                    connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 6\r\nConnection: close\r\n\r\norigin')
                    return
                if data.startswith(b'hold:'):
                    label = data[5:].decode()
                    connection.sendall(b'ready')
                    push(label, connection.sendall)
                    # A post-revocation client packet would get a response if
                    # the original direction had accidentally stayed open.
                    if connection.recv(8192):
                        Path('/run/core-holds', label+'.leaked').touch()
                    return
                connection.sendall(data)
    def tcp(address):
        with socket.socket(socket.AF_INET6 if ':' in address else socket.AF_INET, socket.SOCK_STREAM) as stream:
            stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            stream.bind((address, 18081)); stream.listen()
            while True:
                client, _ = stream.accept()
                threading.Thread(target=respond, args=(client,), daemon=True).start()
    def udp(address):
        with socket.socket(socket.AF_INET6 if ':' in address else socket.AF_INET, socket.SOCK_DGRAM) as stream:
            stream.bind((address, 18082))
            while True:
                data, peer = stream.recvfrom(8192)
                if data.startswith(b'hold:'):
                    label = data[5:].decode()
                    stream.sendto(b'ready', peer)
                    threading.Thread(target=push, args=(label, lambda packet, peer=peer: stream.sendto(packet, peer)), daemon=True).start()
                else:
                    if data.startswith(b'after:'):
                        Path('/run/core-holds', data[6:].decode()+'.leaked').touch()
                    stream.sendto(data, peer)
    for address in ((ORIGIN4, ORIGIN6) if len(sys.argv) < 3 else ('10.255.0.1', 'fd42:ffff::1')):
        for serve in (tcp, udp):
            threading.Thread(target=serve, args=(address,), daemon=True).start()
    threading.Event().wait()


def dns_origin():
    # Bind the actual authorized address so a multihomed fixture cannot reply
    # from its transit-link address and accidentally test a different tuple.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
        stream.bind((NODE4, 15353))
        while True:
            query, peer = stream.recvfrom(4096)
            end = 12
            while query[end]:
                end += query[end]+1
            end += 5
            kind = struct.unpack('!H', query[end-4:end-2])[0]
            payload = (socket.inet_aton(ORIGIN4) if kind == 1 else
                       socket.inet_pton(socket.AF_INET6, ORIGIN6) if kind == 28 else b'')
            reply = query[:2]+struct.pack('!5H', 0x8180, 1, bool(payload), 0, 0)+query[12:end]
            if payload:
                reply += b'\xc0\x0c'+struct.pack('!HHIH', kind, 1, 30, len(payload))+payload
            stream.sendto(reply, peer)


def hold(label, address, transport):
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM if transport == 'tcp' else socket.SOCK_DGRAM) as stream:
        stream.settimeout(8)
        stream.connect((address, 18081 if transport == 'tcp' else 18082))
        stream.sendall(b'hold:'+label.encode())
        assert stream.recv(4096) == b'ready'
        print('ready', flush=True)
        assert sys.stdin.readline().strip() == 'revoked'
        stream.settimeout(.7)
        try:
            data = stream.recv(4096)
        except OSError:
            data = b''
        assert not data, ('revoked server push', label, data)
        try:
            stream.sendall(b'after:'+label.encode())
            stream.recv(4096)
        except OSError:
            pass
        print('blocked', flush=True)


def probe(address, transport, allowed):
    if transport == 'socks-udp':
        reply = b''
        try:
            inlet = CORE6 if ':' in address else CORE4
            family = socket.AF_INET6 if ':' in inlet else socket.AF_INET
            with socket.socket(family, socket.SOCK_STREAM) as control:
                control.settimeout(2)
                control.connect((inlet, 7897))
                def receive(size):
                    value = b''
                    while len(value) < size:
                        part = control.recv(size-len(value))
                        if not part:
                            raise ConnectionError('SOCKS control stream closed')
                        value += part
                    return value
                control.sendall(b'\x05\x01\x00')
                assert receive(2) == b'\x05\x00'
                control.sendall(b'\x05\x03\x00\x01'+b'\0'*6)
                answer = receive(4)
                assert answer[:3] == b'\x05\x00\x00' and answer[3] in (1, 4), answer
                length = 4 if answer[3] == 1 else 16
                answer += receive(length+2)
                relay = socket.inet_ntop(socket.AF_INET if length == 4 else socket.AF_INET6, answer[4:4+length])
                if ipaddress.ip_address(relay).is_unspecified:
                    relay = inlet
                port = struct.unpack('!H', answer[4+length:6+length])[0]
                af = socket.AF_INET6 if ':' in relay else socket.AF_INET
                with socket.socket(af, socket.SOCK_DGRAM) as stream:
                    stream.settimeout(2)
                    destination = socket.inet_pton(socket.AF_INET6 if ':' in address else socket.AF_INET, address)
                    stream.sendto(b'\0\0\0'+bytes([4 if ':' in address else 1])+destination+struct.pack('!H', 18082)+b'netns-probe', (relay, port))
                    reply = stream.recv(4096)
        except OSError:
            pass
        assert reply.endswith(b'netns-probe') == allowed, (address, transport, allowed, reply)
        return
    data = b''
    try:
        with socket.socket(socket.AF_INET6 if ':' in address else socket.AF_INET,
                           socket.SOCK_STREAM if transport == 'tcp' else socket.SOCK_DGRAM) as stream:
            stream.settimeout(2)
            stream.connect((address, 18081 if transport == 'tcp' else 18082))
            stream.sendall(b'netns-probe'); data = stream.recv(4096)
    except OSError:
        pass
    assert (data == b'netns-probe') == allowed, (address, transport, allowed, data)


def controller(*args):
    return run('runuser', '-u', 'client', '--', 'curl', '--silent', '--show-error', '--fail', '--unix-socket', '/run/core-netns-gui/controller.sock', *args)


def tun_routes():
    for family, destination in [('-4', ORIGIN4+'/32'), ('-4', '198.18.0.0/16'), ('-6', ORIGIN6+'/128')]:
        run('ip', '-n', 'lf-core', family, 'route', 'replace', destination, 'dev', 'Mihomo')


def client(*args, allowed=True):
    return run('runuser', '-u', 'client', '--', 'python3', SELF, 'probe', *args, str(int(allowed)))


def attack():
    # This process has the same UID, netns and network caps as the core.
    assert os.getuid() == 982
    assert not [p for p in Path('/proc/self/fd').iterdir() if p.exists() and os.readlink(p).startswith('socket:')], 'inherited network FD'
    assert run('nsenter', '-t', '1', '-n', 'true', okay=False).returncode
    assert run('ip', 'link', 'set', 'outside-host', 'down', okay=False).returncode
    run('nft', 'add', 'table', 'inet', 'attacker')  # Its own namespace only.
    run('ip', 'route', 'replace', ORIGIN4+'/32', 'via', '10.255.0.1', 'dev', 'uplink0')
    run('ip', '-6', 'route', 'replace', ORIGIN6+'/128', 'via', 'fd42:ffff::1', 'dev', 'uplink0')
    for address in (ORIGIN4, ORIGIN6, '10.255.0.1', 'fd42:ffff::1'):
        for transport in ('tcp', 'udp'):
            for mark in (0, 6666, 0x80000, 0x123):
                with socket.socket(socket.AF_INET6 if ':' in address else socket.AF_INET,
                                   socket.SOCK_STREAM if transport == 'tcp' else socket.SOCK_DGRAM) as stream:
                    stream.settimeout(.2)
                    stream.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, mark)
                    data = b''
                    try:
                        stream.connect((address, 18081 if transport == 'tcp' else 18082))
                        stream.sendall(b'forged-mark'); data = stream.recv(1024)
                    except OSError:
                        pass
                    assert not data, (address, transport, mark, data)
    # Raw IPv4/IPv6 packets traverse the same independently owned host guard.
    for address in (ORIGIN4, ORIGIN6):
        with socket.socket(socket.AF_INET6 if ':' in address else socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_UDP) as stream:
            stream.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, 6666)
            if ':' in address:
                stream.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_CHECKSUM, 6)
            stream.sendto(struct.pack('!HHHH', 49152, 18082, 12, 0)+b'raw!', (address, 0))
    print('Namespace mutations, setns refusal, no inherited socket, forged marks and raw packets checked', flush=True)


def check():
    binary = Path('/etc/core-netns-bin').read_text()
    for index, address in enumerate((NODE4, NODE6)):
        profile = f'/run/netns-node{index}.json'
        bind = f'[{address}]' if ':' in address else address
        Path(profile).write_text(json.dumps({'mixed-port': 18080, 'allow-lan': True, 'bind-address': bind, 'ipv6': True, 'rules': ['MATCH,DIRECT']}))
        run('systemd-run', '--unit=netns-node'+str(index), '--setenv=SKIP_SYSTEM_IPV6_CHECK=1', 'ip', 'netns', 'exec', 'outside', binary, '-d', '/run/netns-node'+str(index), '-f', profile)
    run('systemd-run', '--unit=netns-origin', 'ip', 'netns', 'exec', 'outside', sys.executable, SELF, 'origin')
    run('systemd-run', '--unit=netns-host-trap', sys.executable, SELF, 'origin', 'host')
    Path('/run/core-holds').mkdir()
    run('systemd-run', '--unit=netns-dns', 'ip', 'netns', 'exec', 'outside', sys.executable, SELF, 'dns-origin')
    wait(lambda: run('ip', 'netns', 'exec', 'outside', 'ss', '-lnt', okay=False).stdout.count(':18081') == 2)
    wait(lambda: all(address in run('ip', 'netns', 'exec', 'outside', 'ss', '-lnt').stdout for address in (NODE4, NODE6)))
    policy('A')
    for address in (ORIGIN4, ORIGIN6):
        for transport in ('tcp', 'udp'):
            client(address, transport)
    print('A direct TCP/UDP and dual stack passed', flush=True)
    run('systemctl', 'start', 'core-netns')
    wait(lambda: Path('/run/core-netns/controller.sock').is_socket())
    wait(lambda: run('ip', '-n', 'lf-core', 'link', 'show', 'Mihomo', okay=False).returncode == 0)
    tun_routes()
    pid = int(run('systemctl', 'show', '-p', 'MainPID', '--value', 'core-netns').stdout)
    assert os.readlink(f'/proc/{pid}/ns/net') != os.readlink('/proc/1/ns/net')
    caps = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines() if ':' in line)
    assert int(caps['CapEff'].strip(), 16) == (1 << 12) | (1 << 13), caps['CapEff']
    assert caps['Uid'].split() == ['982'] * 4
    policy('B')
    for node in ('node4', 'node6'):
        controller('-X', 'PUT', '-H', 'Content-Type: application/json', '-d', json.dumps({'name': node}), 'http://localhost/proxies/P')
        for address in (ORIGIN4, ORIGIN6):
            for transport in ('tcp', 'udp', 'socks-udp'):
                client(address, transport)
        for inlet in (CORE4, f'[{CORE6}]'):
            output = run('runuser', '-u', 'client', '--', 'curl', '--noproxy', '', '--max-time', '5', '--fail', '--silent', '-x', f'http://{inlet}:7897', f'http://{ORIGIN4}:18081/').stdout
            assert output == 'origin', output
    for inlet in (CORE4, CORE6):
        for tcp in (False, True):
            answer = run('runuser', '-u', 'client', '--', 'dig', '+short', '+time=2', '+tries=1', *(['+tcp'] if tcp else []), '@'+inlet, '-p', '1053', 'fixture.example', 'A').stdout.strip()
            assert ipaddress.ip_address(answer) in ipaddress.ip_network('198.18.0.0/16'), answer
            client(answer, 'tcp')
    rejected = run('curl', '--silent', '--unix-socket', '/run/core-netns/controller.sock', '-X', 'PATCH', '-H', 'Content-Type: application/json', '-d', '{"mode":"direct"}', '-o', '/dev/null', '-w', '%{http_code}', 'http://localhost/configs').stdout
    assert rejected == '403', rejected
    print('B real core TUN, TCP/UDP, IPv4/IPv6 nodes, HTTP inlets, fake-IP and DNS TCP/UDP passed', flush=True)
    before = run('ip', '-j', 'route', 'show', 'table', 'all').stdout
    host_rules = run('nft', '-j', 'list', 'table', 'inet', 'lf_core_prototype').stdout
    run('systemd-run', '--wait', '--pipe', '--unit=core-netns-attacker', '-p', 'User=clash-core', '-p', 'NetworkNamespacePath=/run/netns/lf-core',
        '-p', 'CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW', '-p', 'AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW',
        '-p', 'NoNewPrivileges=yes', '-p', 'RestrictNamespaces=yes', '--setenv=PATH=/run/current-system/sw/bin', sys.executable, SELF, 'attack')
    assert run('ip', '-j', 'route', 'show', 'table', 'all').stdout == before
    assert run('nft', '-j', 'list', 'table', 'inet', 'lf_core_prototype').stdout != '', 'host guard disappeared'
    # Dynamic packet counters differ; compare rules without their values.
    def stable(value):
        for entry in value['nftables']:
            for expression in entry.get('rule', {}).get('expr', []):
                if 'counter' in expression:
                    expression['counter'] = {}
        return value
    assert stable(json.loads(host_rules)) == stable(json.loads(run('nft', '-j', 'list', 'table', 'inet', 'lf_core_prototype').stdout))
    tun_routes()
    drops = json.loads(run('nft', '-j', 'list', 'chain', 'inet', 'lf_core_prototype', 'node').stdout)
    assert sum(expression['counter']['packets'] for item in drops['nftables'] for expression in item.get('rule', {}).get('expr', []) if 'counter' in expression) >= 18, drops
    # Atomic rejection retains the installed policy.
    result = run('nft', '-f', '-', input='destroy table inet lf_core_prototype\nadd rule inet missing missing accept\n', okay=False)
    assert result.returncode
    assert stable(json.loads(run('nft', '-j', 'list', 'table', 'inet', 'lf_core_prototype').stdout)) == stable(json.loads(host_rules))
    def revoke(mode, authorized):
        policy('B')
        processes = []
        for index, (address, transport) in enumerate((a, t) for a in (ORIGIN4, ORIGIN6) for t in ('tcp', 'udp')):
            label = mode+str(index)
            process = subprocess.Popen(['runuser', '-u', 'client', '--', 'python3', SELF, 'hold', label, address, transport],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert process.stdout.readline().strip() == 'ready', process.stderr.read() if process.poll() is not None else label
            processes.append((label, process))
        policy(mode, authorized)
        for label, process in processes:
            Path('/run/core-holds', label).touch()
        time.sleep(.2)
        for label, process in processes:
            output, error = process.communicate('revoked\n', timeout=15)
            assert process.returncode == 0 and 'blocked' in output, (label, output, error)
            assert not Path('/run/core-holds', label+'.leaked').exists(), label
        print('Established native TCP/UDP both directions revoked:', mode, authorized, flush=True)
    revoke('B', False)
    for address in (ORIGIN4, ORIGIN6):
        for transport in ('tcp', 'udp', 'socks-udp'):
            client(address, transport, allowed=False)
    revoke('C', True)
    for address in (ORIGIN4, ORIGIN6):
        for transport in ('tcp', 'udp', 'socks-udp'):
            client(address, transport, allowed=False)
    run('systemctl', 'stop', 'core-netns')
    assert run('ip', '-n', 'lf-core', 'link', 'show', 'Mihomo', okay=False).returncode
    # Restart recreates its own TUN, without changing host routes or a host TUN.
    run('systemctl', 'start', 'core-netns')
    wait(lambda: Path('/run/core-netns/controller.sock').is_socket())
    wait(lambda: run('ip', '-n', 'lf-core', 'link', 'show', 'Mihomo', okay=False).returncode == 0)
    tun_routes(); policy('B')
    wait(lambda: run('runuser', '-u', 'client', '--', sys.executable, SELF, 'probe', ORIGIN4, 'tcp', '1', okay=False).returncode == 0)
    client(ORIGIN6, 'udp')
    assert run('ip', 'link', 'show', 'Mihomo', okay=False).returncode
    print('Node revocation, C blocking, rejected transaction, helper restart/TUN recreation and B recovery passed', flush=True)


if __name__ == '__main__':
    if sys.argv[1] == 'probe':
        probe(sys.argv[2], sys.argv[3], bool(int(sys.argv[4])))
    elif sys.argv[1] == 'hold':
        hold(*sys.argv[2:])
    else:
        {'setup': setup, 'check': check, 'origin': origin, 'dns-origin': dns_origin, 'attack': attack}[sys.argv[1]]()
