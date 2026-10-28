"""Local profiles for the real-core/supervisor VM test; no internet endpoints."""

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
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
        'rules': ['DOMAIN,tun.fixture.example,DIRECT', 'MATCH,Choice'],
    })
    profile['tun']['auto-detect-interface'] = True
    node = {
        'mixed-port': 18080, 'allow-lan': True, 'bind-address': '100.67.0.1',
        'ipv6': True, 'log-level': 'info', 'mode': 'rule', 'rules': ['MATCH,DIRECT'],
        'hosts': {'node.fixture.example': '203.0.113.10',
                  'tun.fixture.example': '203.0.113.10'},
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


if __name__ == '__main__':
    {'prepare': prepare, 'check-node-log': check_node_log, 'http6': http6}[sys.argv[1]]()
