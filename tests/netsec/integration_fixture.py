"""Local profiles for the real-core/supervisor VM test; no internet endpoints."""

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import struct
import sys


def prepare():
    policy = json.loads(Path('/etc/netsec-integration-policy.json').read_text())
    profile = {}
    for path, value in policy.items():
        parts = path.split('.')
        target = profile
        for key in parts[:-1]:
            target = target.setdefault(key, {})
        target[parts[-1]] = value
    profile.update({
        'log-level': 'info', 'mode': 'rule',
        'proxies': [{'name': 'Fixture', 'type': 'socks5', 'server': '100.67.0.1',
                     'port': 18080, 'udp': True}],
        'proxy-groups': [{'name': 'Choice', 'type': 'load-balance',
                          'strategy': 'consistent-hashing', 'proxies': ['Fixture']}],
        'rules': ['DOMAIN,tun.fixture.example,DIRECT', 'DOMAIN,allowed.home.arpa,DIRECT', 'MATCH,Choice'],
    })
    profile['tun']['auto-detect-interface'] = True
    # Boot an older generated profile without relying on GUI regeneration.
    for key in ('iproute2-rule-index', 'auto-redirect-input-mark',
                'auto-redirect-output-mark', 'auto-redirect-iproute2-fallback-rule-index'):
        profile['tun'].pop(key)
    node = {
        'mixed-port': 18080, 'allow-lan': True, 'bind-address': '100.67.0.1',
        'ipv6': True, 'log-level': 'info', 'mode': 'rule', 'rules': ['MATCH,DIRECT'],
        'hosts': {'node.fixture.example': '203.0.113.10',
                  'tun.fixture.example': '203.0.113.10',
                  'private.core.fixture.example': '100.67.0.1',
                  'mixed.core.fixture.example': '100.67.0.1',
                  'allowed.home.arpa': '100.67.0.1'},
    }
    for directory in ('/run/netsec-real-core', '/run/netsec-node', '/run/user/1000/clash-verge-rev'):
        Path(directory).mkdir(parents=True, exist_ok=True)
    Path('/run/netsec-node.json').write_text(json.dumps(node))
    Path('/run/netsec-real-profile.json').write_text(json.dumps(profile))


def check_node_log():
    log = Path('/tmp/netsec-node.log').read_text()
    for address, port in [('203.0.113.10', '8080'), ('203.0.113.10', '15353'),
                          ('2001:db8:ffff::10', '8080'), ('2001:db8:ffff::10', '15353')]:
        assert any(address in line and ':' + port in line and 'using DIRECT' in line
                   for line in log.splitlines()), (address, port, log)


def http6():
    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6

        def server_bind(self):
            # The earlier IPv4 packet tests already own 0.0.0.0:8080.
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            super().server_bind()
    Server(('::', 8080), SimpleHTTPRequestHandler).serve_forever()


def connect_test():
    for target in ('[2001:db8:ffff::10]:8080', '2001:db8:ffff::10:8080'):
        with socket.create_connection(('127.0.0.1', 33332), timeout=4) as connection:
            connection.settimeout(4)
            stream = connection.makefile('rb')
            connection.sendall(f'CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n'.encode())
            assert b' 200 ' in stream.readline(), target
            while stream.readline() not in (b'\r\n', b'\n', b''):
                pass
            connection.sendall(b'GET / HTTP/1.1\r\nHost: fixture\r\nConnection: close\r\n\r\n')
            assert b' 200 ' in stream.readline(), target
    print('Gost bracketed and legacy IPv6 CONNECT carried real HTTP responses')


def guarded_udp():
    def receive(stream, length):
        result = b''
        while len(result) < length:
            part = stream.recv(length - len(result))
            assert part, 'SOCKS control socket closed'
            result += part
        return result
    with socket.create_connection(('127.0.0.1', 7897), timeout=3) as control:
        control.sendall(b'\x05\x01\x00')
        assert receive(control, 2) == b'\x05\x00'
        control.sendall(b'\x05\x03\x00\x01' + b'\0' * 6)
        header = receive(control, 4)
        assert header[:2] == b'\x05\x00' and header[3] in (1, 4), header
        family = socket.AF_INET if header[3] == 1 else socket.AF_INET6
        address = socket.inet_ntop(family, receive(control, 4 if family == socket.AF_INET else 16))
        port = struct.unpack('!H', receive(control, 2))[0]
        if address in ('0.0.0.0', '::'):
            address = '127.0.0.1' if family == socket.AF_INET else '::1'
        with socket.socket(family, socket.SOCK_DGRAM) as stream:
            stream.settimeout(2)
            for name, allowed in [('allowed.home.arpa', True), ('private.core.fixture.example', False),
                                  ('mixed.core.fixture.example', False), ('allowed.home.arpa', True)]:
                payload = b'netsec-fixture'
                packet = b'\0\0\0\x03' + bytes([len(name)]) + name.encode() + struct.pack('!H', 15353) + payload
                stream.sendto(packet, (address, port))
                try:
                    response = stream.recv(65535)
                except TimeoutError:
                    assert not allowed, name
                else:
                    assert allowed and response.endswith(payload), (name, response)
    print('Real SOCKS UDP private/mixed targets denied; internal exception and liveness passed')


if __name__ == '__main__':
    {'prepare': prepare, 'check-node-log': check_node_log, 'http6': http6,
     'connect-test': connect_test, 'guarded-udp': guarded_udp}[sys.argv[1]]()
