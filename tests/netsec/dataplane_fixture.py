"""VM-only adversarial listeners and receive-only real-core UDP associations."""
import errno
import json
import os
from pathlib import Path
import socket
import struct
import sys
import threading
import time

PORTS = (7897, 7896, 33332, 33333, 1053, 1054, 1055, 1056)


def binding():
    for family, address in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            for port in PORTS:
                with socket.socket(family, kind) as stream:
                    try:
                        stream.bind((address, port))
                    except OSError as error:
                        assert error.errno in (errno.EPERM, errno.EACCES), (family, kind, port, error)
                    else:
                        raise AssertionError((family, kind, port, 'bind allowed'))
            with socket.socket(family, kind) as stream:
                stream.bind((address, 18091))  # ordinary development ports work
    print('reserved binds denied for both protocols and address families', flush=True)


def autobind(expected):
    # The VM temporarily narrows the ephemeral range to the two Gost ports.
    # bind(0) passes the cgroup bind hook; the allocator must reserve the ports.
    for family, address in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            with socket.socket(family, kind) as stream:
                try:
                    stream.bind((address, 0))
                except OSError as error:
                    assert expected == 'denied' and error.errno == errno.EADDRINUSE, (family, kind, error)
                else:
                    port = stream.getsockname()[1]
                    assert expected != 'denied', (family, kind, port, 'reserved auto-bind allowed')
                    assert (port in PORTS) == (expected == 'allowed'), (family, kind, port)
    print('automatic allocation: ' + expected, flush=True)


def impostor():
    def serve(family, address, port, kind):
        with socket.socket(family, kind) as stream:
            stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            stream.bind((address, port))
            if kind == socket.SOCK_DGRAM:
                while True:
                    data, peer = stream.recvfrom(4096)
                    try:
                        stream.sendto(b'impostor:' + data, peer)
                    except PermissionError:
                        pass  # output hook refused the reply; keep listening
            else:
                stream.listen()
                while True:
                    conn, _ = stream.accept()
                    with conn:
                        conn.settimeout(2)
                        data = conn.recv(4096)
                        Path('/tmp/impostor-received').write_bytes(data)
                        conn.sendall(b'impostor:' + data)
    for family, address in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
        for port in (7897, 7896, 33332, 33333):
            for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                threading.Thread(target=serve, args=(family, address, port, kind), daemon=True).start()
    time.sleep(1)
    Path('/tmp/impostor-ready').touch()
    threading.Event().wait()


def exchange(address, port, protocol):
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM if protocol == 'tcp' else socket.SOCK_DGRAM) as stream:
        stream.settimeout(1)
        stream.connect((address, int(port)))
        stream.sendall(b'private-client-payload')
        assert stream.recv(4096) == b'impostor:private-client-payload'


def push():
    def send(stream, peer, label):
        for sequence in range(36000):
            try:
                stream.sendto(label + b':' + str(sequence).encode(), peer)
            except OSError:
                pass
            time.sleep(.05)
    def serve(family, address):
        with socket.socket(family, socket.SOCK_DGRAM) as stream:
            stream.bind((address, 18090))
            while True:
                label, peer = stream.recvfrom(4096)
                threading.Thread(target=send, args=(stream, peer, label), daemon=True).start()
    for family, address in ((socket.AF_INET, '203.0.113.10'),
                            (socket.AF_INET6, '2001:db8:ffff::10')):
        threading.Thread(target=serve, args=(family, address), daemon=True).start()
    threading.Event().wait()


def receive(label, address, socks='no'):
    def read(stream, size):
        result = b''
        while len(result) < size:
            part = stream.recv(size - len(result))
            assert part
            result += part
        return result
    control = None
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    payload = label.encode()
    peer = (address, 18090)
    if socks == 'yes':
        control = socket.create_connection(('127.0.0.1', 7897), timeout=3)
        control.sendall(b'\x05\x01\x00')
        assert read(control, 2) == b'\x05\x00'
        control.sendall(b'\x05\x03\x00\x01' + b'\0' * 6)
        header = read(control, 4)
        assert header[:2] == b'\x05\x00'
        relay_family = socket.AF_INET if header[3] == 1 else socket.AF_INET6
        relay = socket.inet_ntop(relay_family, read(control, 4 if header[3] == 1 else 16))
        port = struct.unpack('!H', read(control, 2))[0]
        assert port == 7897, ('unexpected managed UDP inlet', port)
        peer = (('127.0.0.1' if relay_family == socket.AF_INET else '::1')
                if relay in ('0.0.0.0', '::') else relay, port)
        payload = (b'\0\0\0' + bytes([1 if family == socket.AF_INET else 4])
                   + socket.inet_pton(family, address) + struct.pack('!H', 18090) + payload)
        family = relay_family
    with socket.socket(family, socket.SOCK_DGRAM) as stream:
        stream.settimeout(.5)
        stream.connect(peer)
        stream.send(payload)  # exactly one request; never send a heartbeat
        count = 0
        path = Path('/tmp/receive-' + label + '.json')
        while True:
            try:
                data = stream.recv(4096)
                assert label.encode() + b':' in data
                count += 1
                temporary = path.with_suffix('.tmp')
                temporary.write_text(json.dumps({'count': count, 'at': time.monotonic()}))
                temporary.replace(path)
            except socket.timeout:
                pass


{'bind': binding, 'autobind': autobind, 'impostor': impostor, 'exchange': exchange,
 'push': push, 'receive': receive}[sys.argv[1]](*sys.argv[2:])
