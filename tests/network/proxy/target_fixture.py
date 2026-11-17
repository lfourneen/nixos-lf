"""Isolated target authorization fixtures for the production helper/core VM."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading


def prepare():
    path = Path('/run/netsec-real-profile.json')
    profile = json.loads(path.read_text())
    profile['hosts'] = {'local.home.arpa': '127.0.0.1', 'remote.home.arpa': '127.0.0.1',
                        'metadata.home.arpa': '169.254.169.254'}
    profile['rules'].insert(0, 'DOMAIN,local.home.arpa,DIRECT')
    path.write_text(json.dumps(profile))


def server(label, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            with open(f'/run/target-{label}.hits', 'a') as stream:
                stream.write(self.path + '\n')
            self.send_response(200)
            self.end_headers()
            self.wfile.write((label + '\n').encode())

    class Server6(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    ipv6 = Server6(('::1', port), Handler)
    threading.Thread(target=ipv6.serve_forever, daemon=True).start()
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


def discard_controls():
    def request(method, path, body=None):
        conn = http.client.HTTPConnection('localhost', timeout=3)
        conn.sock = socket.socket(socket.AF_UNIX)
        conn.sock.connect('/run/clash-verge-rev/core/mihomo.sock')
        conn.request(method, path, None if body is None else json.dumps(body),
                     {'Content-Type': 'application/json'})
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result
    status, body = request('GET', '/configs')
    assert status == 200, (status, body)
    before = json.loads(body)
    for patch in ([{'mode': mode} for mode in ('rule', 'global', 'direct')]
                  + [{'tun': {'enable': value}} for value in (True, False)]):
        assert request('PATCH', '/configs', patch)[0] == 204, patch
    status, body = request('GET', '/configs')
    after = json.loads(body)
    assert status == 200 and after['mode'] == before['mode'] and after['tun'] == before['tun']


def hits(label):
    path = Path(f'/run/target-{label}.hits')
    return path.read_text() if path.exists() else ''


def check():
    def curl(target, proxy, allowed=False, body=None):
        result = subprocess.run(['curl', '--noproxy', '', '-fsS', '--max-time', '3',
                                 *proxy, f'http://{target}/'], capture_output=True, text=True)
        assert (result.returncode == 0) == allowed, (target, proxy, result.stdout, result.stderr)
        if body is not None:
            assert result.stdout == body + '\n', result.stdout
    http = ['-x', 'http://127.0.0.1:33332']
    connect = ['--proxytunnel', *http]
    socks = ['--socks5-hostname', '127.0.0.1:7897']
    before_remote = hits('remote')
    before_granted = hits('granted')
    for proxy in (http, connect, socks):
        curl('local.home.arpa:18081', proxy, True, 'local')
        for target in ('127.0.0.1:18081', '[::1]:18081', '[::ffff:127.0.0.1]:18081',
                       'remote.home.arpa:18081', 'metadata.home.arpa:18081',
                       '169.254.169.254:18081', '[64:ff9b::7f00:1]:18081', '198.18.255.254:18081'):
            curl(target, proxy)
        curl('203.0.113.10:8080', proxy, True)
    for proxy in (http, connect):
        curl('127.0.0.1:18082', proxy)
    assert hits('remote') == before_remote, 'remote loopback trap was reached'
    assert hits('granted') == before_granted, 'wrong inlet used remote grant'
    curl('127.0.0.1:18082', socks, True, 'granted')
    assert len(hits('granted').splitlines()) == len(before_granted.splitlines()) + 1
    discard_controls()
    for proxy in (http, socks):
        curl('local.home.arpa:18081', proxy, True, 'local')
        curl('127.0.0.1:18082', proxy, proxy == socks, 'granted' if proxy == socks else None)
    assert hits('remote') == before_remote
    print('Local portal/DIRECT, remote internal denials and scoped leaf grant survive discarded control writes')


if __name__ == '__main__':
    {'prepare': prepare, 'server': lambda: server(sys.argv[2], int(sys.argv[3])), 'check': check}[sys.argv[1]]()
