"""VM-only UDP/STUN peer and LocalAPI DERP-map fixture; never uses the internet."""

import argparse
from http.server import BaseHTTPRequestHandler
import ipaddress
import json
import os
from pathlib import Path
import secrets
import selectors
import socket
from socketserver import UnixStreamServer
import struct
import time


def server():
    selector = selectors.DefaultSelector()
    for family, address in ((socket.AF_INET, "203.0.113.10"),
                            (socket.AF_INET6, "2001:db8:ffff::10")):
        for port in (3478, 42424):
            stream = socket.socket(family, socket.SOCK_DGRAM)
            if family == socket.AF_INET6:
                stream.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            stream.bind((address, port))
            selector.register(stream, selectors.EVENT_READ)
    while True:
        for key, _ in selector.select():
            stream = key.fileobj
            packet, peer = stream.recvfrom(4096)
            if len(packet) >= 20 and packet[:2] == b"\x00\x01" and packet[4:8] == b"\x21\x12\xa4\x42":
                # RFC 5389 XOR-MAPPED-ADDRESS, with the client's transaction ID.
                ip = ipaddress.ip_address(peer[0])
                mask = packet[4:20]
                address = bytes(a ^ b for a, b in zip(ip.packed, mask))
                value = struct.pack("!BBH", 0, 1 if ip.version == 4 else 2, peer[1] ^ 0x2112) + address
                attribute = struct.pack("!HH", 0x0020, len(value)) + value
                packet = struct.pack("!HH", 0x0101, len(attribute)) + packet[4:20] + attribute
            stream.sendto(packet, peer)


def api():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/localapi/v0/derpmap":
                self.send_error(404)
                return
            data = json.dumps({"Regions": {"1": {
                "RegionID": 1, "RegionCode": "fixture", "RegionName": "Isolated STUN",
                "Nodes": [{"Name": "fixture", "RegionID": 1,
                           "HostName": "stun.fixture.test", "IPv4": "203.0.113.10",
                           "IPv6": "2001:db8:ffff::10", "STUNPort": 3478, "STUNOnly": True}],
            }}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    with UnixStreamServer("/run/netsec-tailapi.sock", Handler) as service:
        service.serve_forever()


def host_server():
    selector = selectors.DefaultSelector()
    for kind, ports in ((socket.SOCK_STREAM, (47984, 47985, 47989, 47990, 48010)),
                        (socket.SOCK_DGRAM, range(47998, 48011))):
        for port in ports:
            stream = socket.socket(socket.AF_INET, kind)
            stream.bind(("0.0.0.0", port))
            if kind == socket.SOCK_STREAM:
                stream.listen()
            selector.register(stream, selectors.EVENT_READ, kind)
    while True:
        for key, _ in selector.select():
            stream = key.fileobj
            if key.data == socket.SOCK_STREAM:
                connection, _ = stream.accept()
                with connection:
                    connection.settimeout(2)
                    connection.sendall(connection.recv(4096))
            else:
                packet, peer = stream.recvfrom(4096)
                stream.sendto(packet, peer)


def wait_file(path):
    deadline = time.monotonic() + 45
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(path)
        time.sleep(0.05)


def client(args):
    family = socket.AF_INET6 if ":" in args.address else socket.AF_INET
    # Create the socket under the UID being tested. Merely dropping UID after
    # creation would leave sk_uid=0 and would not test the firewall's UID check.
    os.seteuid(args.uid)
    stream = socket.socket(family, socket.SOCK_STREAM if args.tcp else socket.SOCK_DGRAM)
    os.seteuid(0)
    with stream:
        stream.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, args.mark)
        if args.interface:
            stream.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, args.interface.encode() + b"\0")
        if args.sport:
            stream.bind(("::" if family == socket.AF_INET6 else "0.0.0.0", args.sport))
        os.setuid(args.uid)
        stream.settimeout(1)
        if args.tcp:
            stream.connect((args.address, args.port))

        def exchange():
            packet = args.token.encode() if args.token else secrets.token_bytes(32)
            if args.tcp:
                stream.sendall(packet)
                response = stream.recv(4096)
            else:
                destination = ((args.address, args.port, 0, socket.if_nametoindex(args.interface))
                               if family == socket.AF_INET6 and args.interface
                               else (args.address, args.port))
                stream.sendto(packet, destination)
                if args.send_only:
                    return
                response, _ = stream.recvfrom(4096)
            assert response == args.expect_prefix.encode() + packet, response

        exchange()
        if args.persistent:
            state = Path(args.persistent)
            state.with_suffix(".ready").touch()
            wait_file(state.with_suffix(".blocked"))
            try:
                exchange()
            except (TimeoutError, OSError):
                state.with_suffix(".denied").touch()
            else:
                raise AssertionError("an established Tailscale-marked socket escaped C")
            wait_file(state.with_suffix(".restore"))
            exchange()
            state.with_suffix(".recovered").touch()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("server")
    subparsers.add_parser("api")
    subparsers.add_parser("host-server")
    probe = subparsers.add_parser("client")
    probe.add_argument("address")
    probe.add_argument("port", type=int)
    probe.add_argument("--mark", type=lambda v: int(v, 0), default=0)
    probe.add_argument("--uid", type=int, default=0)
    probe.add_argument("--sport", type=int, default=0)
    probe.add_argument("--persistent")
    probe.add_argument("--tcp", action="store_true")
    probe.add_argument("--interface")
    probe.add_argument("--send-only", action="store_true")
    probe.add_argument("--token")
    probe.add_argument("--expect-prefix", default="")
    args = parser.parse_args()
    if args.command == "server":
        server()
    elif args.command == "api":
        api()
    elif args.command == "host-server":
        host_server()
    else:
        client(args)
