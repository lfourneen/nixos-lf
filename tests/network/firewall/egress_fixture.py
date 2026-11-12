"""Isolated direct-egress peers; reply from the queried IP, including IPv6."""

import argparse
import json
from pathlib import Path
import selectors
import socket
import struct


def peer(gateway=False):
    selector = selectors.DefaultSelector()
    addresses = ("203.0.113.10", "203.0.113.11", "101.226.4.6",
                 "10.44.0.1", "10.44.0.3", "2001:db8:ffff::10",
                 "2001:db8:ffff::11", "fd42:44::1", "fd42:44::3",
                 "100.100.100.100", "100.64.0.1")
    if gateway:
        addresses = ("127.0.0.1", "::1")
    for address in addresses:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        for kind, ports in ((socket.SOCK_STREAM, (42424, 42425, 53, 853, 4460)),
                            (socket.SOCK_DGRAM, (42424, 42425, 53, 123, 853))):
            if gateway:
                ports = (1054,)
            for port in ports:
                if not gateway and kind == socket.SOCK_DGRAM and port == 42424 and address in ("203.0.113.10", "2001:db8:ffff::10"):
                    continue  # The existing dual-stack Tailscale peer owns it.
                stream = socket.socket(family, kind)
                if family == socket.AF_INET6:
                    stream.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                stream.bind((address, port))
                if kind == socket.SOCK_STREAM:
                    stream.listen()
                selector.register(stream, selectors.EVENT_READ, "listen" if kind == socket.SOCK_STREAM else "udp")
    while True:
        for key, _ in selector.select():
            stream = key.fileobj
            if key.data == "listen":
                connection, _ = stream.accept()
                connection.setblocking(False)
                selector.register(connection, selectors.EVENT_READ, "tcp")
            elif key.data == "tcp":
                packet = stream.recv(4096)
                if packet:
                    stream.sendall((b"managed:" if gateway else b"") + packet)
                else:
                    selector.unregister(stream)
                    stream.close()
            else:
                packet, address = stream.recvfrom(4096)
                stream.sendto((b"managed:" if gateway else b"") + packet, address)


def multicast():
    selector = selectors.DefaultSelector()
    for family, group in ((socket.AF_INET, "239.255.42.1"),
                          (socket.AF_INET6, "ff02::4242")):
        for port in (53530, 53531):
            stream = socket.socket(family, socket.SOCK_DGRAM)
            if family == socket.AF_INET6:
                stream.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            stream.bind(("0.0.0.0" if family == socket.AF_INET else "::", port))
            for interface, address in (("peer0", "198.51.100.1"), ("peer1", "192.0.2.1")):
                if family == socket.AF_INET:
                    stream.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                      socket.inet_aton(group) + socket.inet_aton(address))
                else:
                    stream.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP,
                                      socket.inet_pton(family, group) + struct.pack("=I", socket.if_nametoindex(interface)))
            selector.register(stream, selectors.EVENT_READ)
    Path("/run/egress-multicast.ready").touch()
    with open("/run/egress-multicast.jsonl", "a", buffering=1) as output:
        while True:
            for key, _ in selector.select():
                packet, address = key.fileobj.recvfrom(4096)
                output.write(json.dumps({"packet": packet.decode(), "peer": address}) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("peer", "gateway", "multicast"))
    args = parser.parse_args()
    if args.mode == "gateway":
        peer(gateway=True)
    else:
        (peer if args.mode == "peer" else multicast)()
