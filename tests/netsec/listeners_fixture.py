"""Real socket/cgroup/UID fault injection, run only in the NixOS test VM."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

if not Path('/etc/netsec-mode-b.nft').exists():
    raise SystemExit('VM fixture only')

parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=['serve', 'query', 'check'])
parser.add_argument('protocol', nargs='?', default='both')
parser.add_argument('--mixed', action='store_true')
parser.add_argument('--uid', type=int)
args = parser.parse_args()


def query(protocol, uid=None):
    command = [sys.executable, __file__, 'query', protocol]
    if uid is not None:
        command += ['--uid', str(uid)]
    # Include interpreter startup/scheduling time; the socket operation itself
    # retains its strict 350 ms deadline below. A process timeout remains a test failure.
    return subprocess.run(command, capture_output=True, timeout=8).returncode == 0


if args.mode == 'serve':
    def serve(protocol, port):
        stream = socket.socket(socket.AF_INET, socket.SOCK_STREAM if protocol == 'tcp' else socket.SOCK_DGRAM)
        stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        stream.bind(('127.0.0.1', port))
        if protocol == 'tcp':
            stream.listen()
        def loop():
            while True:
                if protocol == 'udp':
                    data, peer = stream.recvfrom(4096)
                    stream.sendto(data, peer)
                else:
                    peer, _ = stream.accept()
                    with peer:
                        peer.settimeout(1)
                        peer.sendall(peer.recv(4096))
        threading.Thread(target=loop, daemon=True).start()
    if args.protocol in ('tcp', 'both'):
        serve('tcp', 1053)
    if args.protocol in ('udp', 'both'):
        serve('udp', 1053)
    if args.mixed:
        serve('tcp', 7897)
    while True:
        time.sleep(1)
elif args.mode == 'query':
    if args.uid is not None:
        os.setuid(args.uid)
    stream = socket.socket(socket.AF_INET, socket.SOCK_STREAM if args.protocol == 'tcp' else socket.SOCK_DGRAM)
    stream.settimeout(0.35)
    stream.connect(('127.0.0.1', 1053))
    stream.sendall(b'listener-fixture')
    assert stream.recv(4096) == b'listener-fixture'
else:
    check = '/etc/netsec-listener-check'
    def run(argv):
        result = subprocess.run(argv, capture_output=True, text=True)
        assert result.returncode == 0, (argv, result.stdout, result.stderr)
    def stop():
        subprocess.run(['systemctl', 'stop', 'clash-verge.service', 'netsec-listener-impostor.service'], capture_output=True)
    def ready():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            tcp = subprocess.check_output(['ss', '-Hlnt', 'sport = :1053'], text=True)
            udp = subprocess.check_output(['ss', '-Hlnu', 'sport = :1053'], text=True)
            if tcp.strip() and udp.strip():
                return
            time.sleep(0.05)
        raise AssertionError('fixture listeners missing')
    try:
        # Repeat after valid ownership to cover port recreation, not just boot.
        for bad in ('udp', 'tcp', 'both', None, 'udp', None):
            stop()
            good = {'udp': 'tcp', 'tcp': 'udp', 'both': 'none', None: 'both'}[bad]
            Path('/run/listener-good-protocol').write_text(good)
            run(['systemctl', 'start', 'clash-verge.service'])
            if bad:
                Path('/run/listener-bad-protocol').write_text(bad)
                run(['systemctl', 'start', 'netsec-listener-impostor.service'])
            ready()
            result = subprocess.run([check, 'core'], capture_output=True)
            assert (result.returncode == 0) == (bad is None), result.stderr
            for protocol in ('tcp', 'udp'):
                assert query(protocol) == (bad not in (protocol, 'both')), (bad, protocol)
                assert not query(protocol, 1000), (bad, protocol, 'ordinary UID reached upstream')
                if bad is None:
                    assert query(protocol, 985), (protocol, 'dnsmasq UID refused')
            if bad is None:
                run(['setpriv', '--reuid=987', '--regid=gost', '--clear-groups', check, 'core-mixed'])
        print('Real TCP/UDP ownership, UID response guards and recreation checks passed')
    finally:
        stop()
