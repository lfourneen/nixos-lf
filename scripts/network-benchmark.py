#!/usr/bin/env python3
"""Controlled HTTP latency/throughput baseline; never changes network policy."""
import argparse
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import math
import os
from pathlib import Path
import resource
import socket
import statistics
import threading
import tempfile
import time
from urllib.parse import urlsplit


def distribution(values):
    ordered = sorted(values)
    return {'samples': len(values), 'p50_ms': round(statistics.median(values) * 1000, 3),
            'p95_ms': round(ordered[math.ceil(len(ordered) * .95) - 1] * 1000, 3)}


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
    snapshot = commands.add_parser('snapshot')
    snapshot.add_argument('--tool', required=True)
    args = parser.parse_args()
    if args.command == 'serve':
        return serve(args.bind, args.port)
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
            measured = {}
            for fast in (False, True):
                start, cpu = time.monotonic(), time.process_time()
                for _ in range(20):
                    tool.verify(data, digest_file, authority, fast=fast)
                measured['fast' if fast else 'full'] = {
                    'seconds': time.monotonic() - start, 'cpu_seconds': time.process_time() - cpu,
                    'iterations': 20, 'synthetic_asset_bytes': 32 * 1024 * 1024}
            print(json.dumps(measured, indent=2))
        return
    print(json.dumps(measure(args.url, args.proxy, args.samples), indent=2))


if __name__ == '__main__':
    main()
