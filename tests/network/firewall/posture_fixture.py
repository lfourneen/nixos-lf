"""Local-only TCP echoes for passive listener and ingress policy tests."""

import argparse
import selectors
import socket


def server():
    with selectors.DefaultSelector() as selector:
        endpoints = [
            ("0.0.0.0", 22), ("::", 22),
            ("0.0.0.0", 18080), ("::", 18080),
            ("127.0.0.1", 8788), ("127.0.0.1", 33332), ("0.0.0.0", 33333),
        ]
        for address, port in endpoints:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            listener = socket.socket(family, socket.SOCK_STREAM)
            if family == socket.AF_INET6:
                listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((address, port))
            listener.listen()
            selector.register(listener, selectors.EVENT_READ)
        while True:
            for key, _ in selector.select():
                stream, _ = key.fileobj.accept()
                with stream:
                    stream.settimeout(2)
                    stream.sendall(stream.recv(32))


def client(address, port):
    with socket.create_connection((address, port), timeout=1.5) as stream:
        stream.sendall(b"fixture")
        assert stream.recv(32) == b"fixture"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("server")
    probe = commands.add_parser("client")
    probe.add_argument("address")
    probe.add_argument("port", type=int)
    args = parser.parse_args()
    if args.command == "server":
        server()
    else:
        client(args.address, args.port)
