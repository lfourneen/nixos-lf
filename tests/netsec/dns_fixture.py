"""Local-only VM traffic fixtures; never used by the installed system."""
import argparse
import json
import os
from pathlib import Path
import socket
import struct
import time

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=["server", "query", "lease"])
parser.add_argument("host")
parser.add_argument("port", nargs="?", type=int, default=53)
parser.add_argument("--uid", type=int)
parser.add_argument("--mark", type=int)
parser.add_argument('--dns-answer')
parser.add_argument('--rebind', action='store_true')
args = parser.parse_args()

if args.mode == "lease":
    state = json.loads(Path("/run/proxy-mode/state.json").read_text())
    now = time.clock_gettime(time.CLOCK_BOOTTIME)
    Path("/run/dns-upstream/policy.json").write_text(json.dumps({
        "policy_version": state["version"], "monotonic_seconds": now,
        "plaintext_until": now + args.port, "plaintext": [args.host], "split": [],
    }))
elif args.mode == "server":
    stream = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    stream.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    stream.bind(("::", int(args.host)))
    while True:
        data, peer = stream.recvfrom(1024)
        if args.dns_answer and data != b'netsec-fixture':
            end, labels = 12, []
            while data[end]:
                labels.append(data[end + 1:end + 1 + data[end]].decode('ascii'))
                end += data[end] + 1
            end += 5
            name = '.'.join(labels).lower()
            answers = [args.dns_answer]
            if args.rebind and name in ('private.core.fixture.example', 'allowed.home.arpa'):
                answers = ['100.67.0.1']
            if args.rebind and name == 'mixed.core.fixture.example':
                answers = [args.dns_answer, '100.67.0.1']
            data = data[:2] + struct.pack('!5H', 0x8180, 1, len(answers), 0, 0) + data[12:end] + b''.join(
                b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 0, 4) + socket.inet_aton(address) for address in answers)
        stream.sendto(data, peer)
else:
    if args.uid is not None:
        os.setuid(args.uid)
    stream = socket.socket(socket.AF_INET6 if ":" in args.host else socket.AF_INET, socket.SOCK_DGRAM)
    if args.mark is not None:
        stream.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, args.mark)
    stream.settimeout(1)
    stream.sendto(b"netsec-fixture", (args.host, args.port))
    assert stream.recv(1024) == b"netsec-fixture"
