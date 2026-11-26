#!/usr/bin/env python3
"""Measure controlled network workloads and application-visible latency."""
import argparse
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import resource
import re
import signal
import socket
import ssl
import statistics
import subprocess
import threading
import tempfile
import time
from urllib.parse import urlsplit


def distribution(values):
    ordered = sorted(values)
    return {'samples': len(values), 'p50_ms': round(statistics.median(values) * 1000, 3),
            'p95_ms': round(ordered[math.ceil(len(ordered) * .95) - 1] * 1000, 3),
            'max_ms': round(max(values) * 1000, 3),
            'stdev_ms': round(statistics.pstdev(values) * 1000, 3)}


def serve(address, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                count = int(self.path.strip('/') or 1024)
                if not 1 <= count <= 64 * 1024 * 1024:
                    raise ValueError()
            except ValueError:
                self.send_error(400)
                return
            self.send_response(200)
            self.send_header('Content-Length', str(count))
            self.end_headers()
            block = b'x' * 65536
            while count:
                size = min(count, len(block))
                self.wfile.write(block[:size])
                count -= size
        def log_message(self, *args):
            pass
    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ':' in address else socket.AF_INET
    server = Server((address, port), Handler)
    server.daemon_threads = True
    server.serve_forever()


def measure(url, proxy, samples):
    origin = urlsplit(url)
    transport = urlsplit(proxy) if proxy else origin
    if origin.scheme != 'http' or transport.scheme != 'http':
        raise ValueError('the controlled baseline requires HTTP endpoints')
    def request(size):
        started = time.monotonic()
        connection = http.client.HTTPConnection(transport.hostname, transport.port or 80, timeout=15)
        target = url.rstrip('/') + '/' + str(size) if proxy else origin.path.rstrip('/') + '/' + str(size)
        connection.request('GET', target, headers={'Host': origin.netloc})
        with connection.getresponse() as response:
            assert response.status == 200
            response.read(1)
            first = time.monotonic() - started
            count = 1
            while chunk := response.read(65536):
                count += len(chunk)
        connection.close()
        assert count == size
        return first, time.monotonic() - started
    start = time.monotonic()
    cpu = time.process_time()
    short = [request(1024) for _ in range(samples)]
    bulk_size = 8 * 1024 * 1024
    bulk = request(bulk_size)
    stop = threading.Event()
    def load():
        while not stop.is_set():
            request(bulk_size)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(load)
        try:
            loaded = [request(1024) for _ in range(samples)]
        finally:
            stop.set()
        worker.result()
    return {'schema': 1, 'path': 'explicit-proxy' if proxy else 'native-socket',
            'short_ttfb': distribution([v[0] for v in short]),
            'short_total': distribution([v[1] for v in short]),
            'loaded_ttfb': distribution([v[0] for v in loaded]),
            'short_connections_per_second': round(samples / sum(v[1] for v in short), 3),
            'bulk_megabits_per_second': round(bulk_size * 8 / bulk[1] / 1e6, 3),
            'client_cpu_seconds': round(time.process_time() - cpu, 4),
            'client_max_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'elapsed_seconds': round(time.monotonic() - start, 3)}


def run_probe(command, timeout):
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout,
                          env={**os.environ, 'LC_ALL': 'C'})


def dns_probe(name, record, timeout, resolver=None, port=53):
    started = time.monotonic()
    answers = []
    try:
        query = ['dig', '-r', '+noall', '+comments', '+answer', '+tries=1',
                 f'+time={timeout}', '-p', str(port), '-q', name, '-t', record]
        if resolver:
            query.append('@' + resolver)
        response = run_probe(query, timeout + 1)
        status = re.search(r'status: ([A-Z0-9]+),', response.stdout)
        for line in response.stdout.splitlines():
            fields = line.split()
            if len(fields) == 5 and fields[2:4] == ['IN', record]:
                answers.append({'address': str(ipaddress.ip_address(fields[4])), 'ttl': int(fields[1])})
        outcome = status[1] if status else 'no-response'
        if outcome == 'NOERROR':
            outcome = 'answer' if answers else 'NODATA'
    except subprocess.TimeoutExpired:
        outcome = 'timeout'
    return {'outcome': outcome, 'elapsed_ms': elapsed_ms(started), 'answers': answers}


def elapsed_ms(started):
    return round((time.monotonic() - started) * 1000, 3)


def echo_probe(address, timeout):
    started = time.monotonic()
    try:
        response = run_probe(['ping', '-n', '-c', '1', '-w', str(timeout), str(address)], timeout + 1)
    except subprocess.TimeoutExpired:
        return {'outcome': 'timeout', 'elapsed_ms': elapsed_ms(started)}
    rtt = re.search(r'time([=<])([\d.]+) ms', response.stdout)
    result = {'outcome': 'echo-reply' if response.returncode == 0 else
              'no-echo' if response.returncode == 1 else 'error', 'elapsed_ms': elapsed_ms(started)}
    if rtt:
        result.update(rtt_ms=float(rtt[2]), rtt_upper_bound=rtt[1] == '<')
    return result


def deadline_expired(*_):
    raise TimeoutError('HTTPS sample deadline')


def authority(address, port):
    host = f'[{address}]' if ':' in str(address) else str(address)
    return f'{host}:{port}'


def https_probe(name, address, port, context, timeout, proxy=None, system_resolution=False):
    stages = {}
    current = None
    started = time.monotonic()

    @contextmanager
    def stage(name):
        nonlocal current
        current = name
        entered = time.monotonic()
        stages[name] = {'outcome': 'ok'}
        try:
            yield stages[name]
        finally:
            stages[name]['elapsed_ms'] = elapsed_ms(entered)

    previous = signal.signal(signal.SIGALRM, deadline_expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, timeout)
        with ExitStack() as stack:
            if system_resolution:
                with stage('system_dns'):
                    family = socket.AF_INET if address.version == 4 else socket.AF_INET6
                    answers = socket.getaddrinfo(name, port, family, socket.SOCK_STREAM)
                    address = ipaddress.ip_address(answers[0][4][0])
            with stage('tcp'):
                endpoint = (proxy.hostname, proxy.port or 80) if proxy else (str(address), port)
                stream = stack.enter_context(socket.create_connection(endpoint, timeout=timeout))
            if proxy:
                with stage('connect') as result:
                    target = authority(address, port)
                    stream.sendall(f'CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n'.encode('ascii'))
                    with http.client.HTTPResponse(stream, method='HEAD') as reply:
                        reply.begin()
                        result['status'] = reply.status
                        if not 200 <= reply.status < 300:
                            result['outcome'] = 'proxy-rejected'
                            return {'outcome': result['outcome'], 'elapsed_ms': elapsed_ms(started), 'stages': stages}
            with stage('tls') as result:
                tls = stack.enter_context(context.wrap_socket(stream, server_hostname=name))
                result['version'] = tls.version()
            with stage('http') as result:
                host = authority(name, port)
                tls.sendall(f'GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n'.encode('ascii'))
                with http.client.HTTPResponse(tls) as reply:
                    reply.begin()
                    result['status'] = reply.status
                    if reply.status != 200:
                        result['outcome'] = 'unexpected-status'
                    elif not reply.read(1):
                        result['outcome'] = 'empty-body'
    except (OSError, http.client.HTTPException) as error:
        outcomes = {TimeoutError: 'timeout', ConnectionRefusedError: 'refused',
                    ConnectionResetError: 'reset', BrokenPipeError: 'closed',
                    http.client.RemoteDisconnected: 'closed',
                    ssl.SSLCertVerificationError: 'certificate', ssl.SSLEOFError: 'closed'}
        stages[current]['outcome'] = outcomes.get(type(error), 'tls-error' if isinstance(error, ssl.SSLError) else 'error')
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    return {'outcome': stages[current]['outcome'], 'elapsed_ms': elapsed_ms(started), 'stages': stages}


def measure_layers(args):
    name = args.name.rstrip('.').encode('idna').decode('ascii')
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', name):
        raise ValueError('expected a DNS hostname')
    proxy = urlsplit(args.proxy) if args.proxy else None
    if proxy and (proxy.scheme != 'http' or not proxy.hostname or proxy.username is not None
                  or proxy.path not in ('', '/') or proxy.query or proxy.fragment):
        raise ValueError('expected an HTTP proxy without credentials')
    if args.system_resolution and (proxy or args.resolver or args.dns_port != 53):
        raise ValueError('system resolution requires the native socket path and the system resolver')
    context = ssl.create_default_context(cafile=args.ca_file)
    record = 'A' if args.address.version == 4 else 'AAAA'
    samples = []
    timings = {}

    def collect(phase, observation, field='elapsed_ms'):
        if field in observation:
            timings.setdefault(phase, {}).setdefault(observation['outcome'], []).append(observation[field] / 1000)

    for _ in range(args.samples):
        sample = {}
        if not args.system_resolution:
            sample['dns'] = dns_probe(name, record, args.timeout, args.resolver, args.dns_port)
            collect('dns', sample['dns'])
        if not args.no_echo:
            sample['echo'] = echo_probe(args.address, args.timeout)
            collect('echo_wait', sample['echo'])
            collect('echo_rtt', sample['echo'], 'rtt_ms')
        https = https_probe(name, args.address, args.port, context, args.timeout, proxy, args.system_resolution)
        samples.append(dict(sample, https=https))
        collect('https_total', https)
        for phase, observation in https['stages'].items():
            collect(phase, observation)
    return {'schema': 1, 'name': name, 'address': str(args.address), 'port': args.port,
            'path': 'http-connect' if proxy else 'native-socket', 'timeout_seconds': args.timeout,
            **({'hostname_resolution': 'system'} if args.system_resolution else {}),
            'samples': samples, 'timings': {
                phase: {outcome: distribution(values) for outcome, values in groups.items()}
                for phase, groups in timings.items()}}


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    server = commands.add_parser('serve')
    server.add_argument('--bind', default='127.0.0.1')
    server.add_argument('--port', type=int, default=18094)
    run = commands.add_parser('measure')
    run.add_argument('--url', required=True)
    run.add_argument('--proxy')
    run.add_argument('--samples', type=int, choices=range(5, 101), default=20)
    layers = commands.add_parser('layers')
    layers.add_argument('--name', required=True, help='DNS query, TLS identity and HTTP Host')
    layers.add_argument('--address', required=True, type=ipaddress.ip_address, help='fixed HTTPS and echo target')
    layers.add_argument('--port', type=int, default=443)
    layers.add_argument('--proxy', help='HTTP CONNECT endpoint')
    layers.add_argument('--ca-file')
    layers.add_argument('--resolver', type=ipaddress.ip_address)
    layers.add_argument('--dns-port', type=int, default=53)
    layers.add_argument('--system-resolution', action='store_true',
                        help='Resolve the HTTP hostname through the application resolver, using the address family of --address')
    layers.add_argument('--no-echo', action='store_true', help='Skip the ICMP comparison')
    layers.add_argument('--samples', type=int, choices=range(1, 101), default=5)
    layers.add_argument('--timeout', type=int, choices=range(1, 31), default=5)
    snapshot = commands.add_parser('snapshot')
    snapshot.add_argument('--tool', required=True)
    blocklist = commands.add_parser('blocklist')
    blocklist.add_argument('--data', type=Path, default=Path('local-data/dns-blocklist.txt.xz'))
    args = parser.parse_args()
    if args.command == 'serve':
        return serve(args.bind, args.port)
    if args.command == 'layers':
        print(json.dumps(measure_layers(args), indent=2))
        return
    if args.command == 'blocklist':
        import hashlib
        import lzma
        payload = args.data.read_bytes()
        timings = []
        count = 0
        for _ in range(5):
            started = time.monotonic()
            names = lzma.decompress(payload).decode('ascii').splitlines()
            count = len(set(names))
            timings.append(time.monotonic() - started)
        print(json.dumps({'schema': 1, 'workload': 'decompress-and-python-domain-set',
                          'input_sha256': hashlib.sha256(payload).hexdigest(),
                          'domains': count, 'timings': distribution(timings),
                          'max_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, indent=2))
        return
    if args.command == 'snapshot':
        spec = importlib.util.spec_from_file_location('snapshot', args.tool)
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        with tempfile.TemporaryDirectory(prefix='snapshot-baseline-') as temporary:
            root = Path(temporary)
            data = root / 'snapshot'
            tool.private_dir(data, os.getegid())
            tool.write_private(data / 'clash-verge.yaml', b'benchmark: synthetic\n', os.getegid())
            tool.write_private(data / 'GeoSite.dat', b'x' * (32 * 1024 * 1024), os.getegid())
            digest = tool.snapshot_digest(data)
            authority = root / 'authorized-config'
            digest_file = root / 'config-digest'
            for path in (authority, digest_file):
                path.write_text(digest)
            tool.write_private(root / 'boot-id', b'00000000-0000-4000-8000-000000000001\n', os.getegid())
            measured = {}
            for fast in (False, True):
                start, cpu = time.monotonic(), time.process_time()
                timings = []
                for _ in range(20):
                    started = time.monotonic()
                    tool.verify(data, digest_file, authority, fast=fast)
                    timings.append(time.monotonic() - started)
                measured['fast' if fast else 'full'] = {
                    'seconds': time.monotonic() - start, 'cpu_seconds': time.process_time() - cpu,
                    'iterations': 20, 'synthetic_asset_bytes': 32 * 1024 * 1024,
                    'timings': distribution(timings)}
            print(json.dumps(measured, indent=2))
        return
    print(json.dumps(measure(args.url, args.proxy, args.samples), indent=2))


if __name__ == '__main__':
    main()
