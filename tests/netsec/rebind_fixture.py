"""VM-only recursive DNS fixture for Unbound's upstream answer filtering."""

import ipaddress
import socketserver
import struct
import threading


ZONE = "rebind.fixture.example"
ANSWERS = {
    "public": "203.0.113.42",
    "private": "192.168.1.1",
    "cgnat": "100.64.0.1",
    "loopback": "127.0.0.1",
    "unspecified": "0.0.0.0",
    "linklocal": "169.254.169.254",
    "public6": "2001:db8::42",
    "ula": "fd12::1",
    "loopback6": "::1",
    "unspecified6": "::",
    "linklocal6": "fe80::1",
    "mapped": "::ffff:192.168.1.1",
    "mapped-cgnat": "::ffff:100.64.0.1",
    "mapped-public": "::ffff:203.0.113.42",
    "allowed": "192.168.1.2",
}


def name_wire(name):
    return b"".join(bytes([len(label)]) + label.encode("ascii") for label in name.split(".")) + b"\0"


def answer(data):
    end, labels = 12, []
    while data[end]:
        size = data[end]
        labels.append(data[end + 1:end + 1 + size].decode("ascii"))
        end += size + 1
    end += 1
    qtype, _ = struct.unpack("!HH", data[end:end + 4])
    question = data[12:end + 4]
    name = ".".join(labels).lower()
    label = name.removesuffix("." + ZONE)
    records = []
    if name.endswith("." + ZONE):
        if label == "alias":
            target = name_wire("private." + ZONE)
            records.append(b"\xc0\x0c" + struct.pack("!HHIH", 5, 1, 0, len(target)) + target)
            label = "private"
            owner = target
        else:
            owner = b"\xc0\x0c"
        if label in ANSWERS:
            address = ipaddress.ip_address(ANSWERS[label])
            record_type = 1 if address.version == 4 else 28
            if qtype == record_type:
                records.append(owner + struct.pack("!HHIH", record_type, 1, 0, len(address.packed))
                               + address.packed)
    return data[:2] + struct.pack("!HHHHH", 0x8180, 1, len(records), 0, 0) + question + b"".join(records)


class UDP(socketserver.BaseRequestHandler):
    def handle(self):
        packet, stream = self.request
        stream.sendto(answer(packet), self.client_address)


def receive(stream, length):
    data = b""
    while len(data) < length:
        part = stream.recv(length - len(data))
        if not part:
            return b""
        data += part
    return data


class TCP(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        while header := receive(self.request, 2):
            packet = receive(self.request, struct.unpack("!H", header)[0])
            if not packet:
                return
            response = answer(packet)
            self.request.sendall(struct.pack("!H", len(response)) + response)


if __name__ == "__main__":
    with socketserver.ThreadingTCPServer(("127.0.0.1", 15355), TCP) as tcp:
        tcp.daemon_threads = True
        threading.Thread(target=tcp.serve_forever, daemon=True).start()
        with socketserver.ThreadingUDPServer(("127.0.0.1", 15355), UDP) as udp:
            udp.daemon_threads = True
            udp.serve_forever()
