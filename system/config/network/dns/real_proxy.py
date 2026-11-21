"""Bounded loopback DNS gateway and fixed Unix bridge to the core validator."""
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import threading
import time


def receive(stream, size, deadline):
    data = bytearray()
    while len(data) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('DNS deadline')
        stream.settimeout(remaining)
        part = stream.recv(size - len(data))
        if not part:
            raise OSError('DNS stream ended')
        data.extend(part)
    return bytes(data)


def exchange(stream, packet, deadline):
    stream.settimeout(max(.001, deadline - time.monotonic()))
    stream.sendall(struct.pack('!H', len(packet)) + packet)
    size = struct.unpack('!H', receive(stream, 2, deadline))[0]
    answer = receive(stream, size, deadline)
    if len(answer) < 12 or answer[:2] != packet[:2] or not answer[2] & 0x80:
        raise ValueError('invalid DNS reply')
    return answer


def error_reply(packet):
    if len(packet) < 12:
        return b''
    return packet[:2] + struct.pack('!H', 0x8002 | (struct.unpack('!H', packet[2:4])[0] & 0x100)) \
        + packet[4:6] + b'\0' * 4 + packet[10:]


def query(settings, packet, backend=False, deadline=None, *, infrastructure=False):
    if not 12 <= len(packet) <= 65535 or packet[2] & 0xf8 or packet[4:6] != b'\0\1' or packet[6:10] != b'\0' * 4:
        raise ValueError('invalid DNS query')
    deadline = time.monotonic() + 4 if deadline is None else deadline
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('DNS deadline')
    strict = False
    if not backend:
        state = Path(settings['state'])
        intent = (state / 'intent').read_text().strip() if (state / 'intent').exists() else 'direct'
        if intent == 'proxy':
            info = (state / 'routing.json').lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('unsafe routing state')
            record = json.loads((state / 'routing.json').read_text())
            if record.get('boot_id') != (state / 'boot-id').read_text().strip() \
                    or record.get('routing_requested') not in ('rules', 'proxy-only'):
                raise ValueError('unknown DNS route')
            strict = record['routing_requested'] == 'proxy-only'
    if strict:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.settimeout(remaining)
            stream.connect('/run/clash-real-dns/validator.sock')
            _, uid, _ = struct.unpack('3i', stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != 0:
                raise ValueError('untrusted validator bridge')
            return exchange(stream, packet, deadline)
    if backend:
        port = settings['validator_port']
    elif infrastructure:
        port = settings['infrastructure_validator_port']
    else:
        # A stale application target must not inherit the infrastructure
        # gateway's permission to query the direct resolver.
        raise ValueError('managed real-address application DNS unavailable')
    with socket.create_connection(('127.0.0.1', port), timeout=remaining) as stream:
        return exchange(stream, packet, deadline)


def serve(settings, backend=False):
    slots = threading.BoundedSemaphore(24)
    def handle(stream, infrastructure, peer=None, packet=None):
        try:
            deadline = time.monotonic() + 4
            if packet is None:
                size = struct.unpack('!H', receive(stream, 2, deadline))[0]
                packet = receive(stream, size, deadline)
            try:
                answer = query(settings, packet, backend, deadline, infrastructure=infrastructure)
            except (OSError, ValueError, KeyError):
                answer = error_reply(packet)
            if peer is None:
                stream.settimeout(1)
                stream.sendall(struct.pack('!H', len(answer)) + answer)
            else:
                stream.sendto(answer, peer)
        except (OSError, ValueError):
            pass
        finally:
            if peer is None:
                stream.close()
            slots.release()

    def tcp(listener, infrastructure):
        while True:
            stream, _ = listener.accept()
            if not slots.acquire(blocking=False):
                stream.close()
                continue
            threading.Thread(target=handle, args=(stream, infrastructure), daemon=True).start()

    listeners = []
    if backend:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        path = Path('/run/clash-real-dns/validator.sock')
        path.unlink(missing_ok=True)
        listener.bind(str(path))
        path.chmod(0o660)
        listener.listen(24)
        tcp(listener, False)
        return

    def udp(stream, infrastructure):
        while True:
            packet, peer = stream.recvfrom(65535)
            if slots.acquire(blocking=False):
                threading.Thread(target=handle, args=(stream, infrastructure, peer, packet), daemon=True).start()
            else:
                stream.sendto(error_reply(packet), peer)

    addresses = ('127.0.0.1', '::1') if settings.get('ipv6', True) else ('127.0.0.1',)
    for port, infrastructure in ((settings['real_dns_port'], False), (settings['infrastructure_dns_port'], True)):
        for address in addresses:
            family = socket.AF_INET6 if ':' in address else socket.AF_INET
            for kind, serve_socket in ((socket.SOCK_STREAM, tcp), (socket.SOCK_DGRAM, udp)):
                listener = socket.socket(family, kind)
                if family == socket.AF_INET6:
                    listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                listener.bind((address, port))
                listeners.append(listener)
                if kind == socket.SOCK_STREAM:
                    listener.listen(24)
                threading.Thread(target=serve_socket, args=(listener, infrastructure), daemon=True).start()
    threading.Event().wait()


if __name__ == '__main__':
    serve(json.loads(Path(sys.argv[1]).read_text()), backend='--backend' in sys.argv[2:])
