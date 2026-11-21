"""Inject local-link NDP frames and observe INPUT delivery in the isolated VM."""
import ipaddress
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time


PREFIX = ipaddress.IPv6Network("2001:db8:478::/64")


def send(mac, source, kind, hop, code):
    source = ipaddress.IPv6Address(source).packed
    destination = ipaddress.IPv6Address("fe80::2").packed
    body = struct.pack("!BBH", int(kind), int(code), 0) + bytes(12 if int(kind) == 134 else 4)
    if int(kind) == 134:
        body += struct.pack("!BBBBIII16s", 3, 4, PREFIX.prefixlen, 0x40, 120, 60, 0, PREFIX.network_address.packed)
    pseudo = source + destination + struct.pack("!I3xB", len(body), 58)
    total = sum(struct.unpack("!" + "H" * ((len(pseudo) + len(body)) // 2), pseudo + body))
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    body = body[:2] + struct.pack("!H", (~total) & 0xffff) + body[4:]
    header = struct.pack("!IHBB16s16s", 6 << 28, len(body), 58, int(hop), source, destination)
    frame = bytes.fromhex(mac.replace(":", "")) + bytes.fromhex("02000000000a86dd") + header + body
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as sender:
        sender.bind(("peer0", 0))
        sender.send(frame)


def check():
    mac = Path("/sys/class/net/ens1/address").read_text().strip()
    cases = [("fe80::a", 134, 255, 0, True),
             ("::", 134, 255, 0, False),
             ("2001:db8::a", 134, 255, 0, False),
             ("fe80::a", 134, 64, 0, False),
             ("fe80::a", 134, 255, 1, False),
             ("::", 133, 255, 0, True)]
    for source, kind, hop, code, expected in cases:
        with socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_ICMPV6) as receiver:
            receiver.settimeout(0.4)
            subprocess.run(["ip", "netns", "exec", "outside", sys.executable, __file__, "send",
                            mac, source, str(kind), str(hop), str(code)], check=True)
            received = False
            try:
                while True:
                    data, address = receiver.recvfrom(65535)
                    if data[0] == kind and ipaddress.IPv6Address(address[0]) == ipaddress.IPv6Address(source):
                        received = True
                        break
            except TimeoutError:
                pass
            assert received == expected, (source, kind, hop, code, received, expected)

    def autoconfigured():
        addresses = json.loads(subprocess.check_output(["ip", "-6", "-j", "addr", "show", "dev", "ens1"]))
        return [address for address in addresses[0]["addr_info"]
                if ipaddress.IPv6Address(address["local"]) in PREFIX]

    deadline = time.monotonic() + 6
    try:
        while not any(not address.get("tentative") and not address.get("dadfailed")
                      for address in autoconfigured()):
            assert time.monotonic() < deadline, "valid RA did not produce a usable SLAAC address"
            time.sleep(.1)
    finally:
        for address in autoconfigured():
            subprocess.run(["ip", "-6", "addr", "del", f"{address['local']}/{address['prefixlen']}", "dev", "ens1"],
                           check=True)
    print("NDP INPUT: SLAAC and unspecified-source RS allowed; malformed RA refused")


if __name__ == "__main__":
    if sys.argv[1:] and sys.argv[1] == "send":
        send(*sys.argv[2:])
    else:
        check()
