"""Authorized localhost TCP/UDP over credential-checked Unix channels.

Only the capless host broker opens host INET sockets. The private receiver
holds private-netns sockets and Unix clients; file descriptors never cross.
"""
import ipaddress
import json
import os
from pathlib import Path
import selectors
import socket
import stat
import struct
import sys
import threading
import time


def original(data, family):
    if family == socket.AF_INET:
        return socket.inet_ntop(family, data[4:8]), struct.unpack('!H', data[2:4])[0]
    return socket.inet_ntop(family, data[8:24]), struct.unpack('!H', data[2:4])[0]


def target(s, version, port):
    p = s['ports']
    address = '127.0.0.1' if version == 4 else '::1'
    if version not in (4, 6) or (version == 6 and not s['ipv6']) or not 1 <= port <= 65535:
        raise ValueError('invalid localhost selector')
    if port == 53 and not s['strictDns']:
        return address, p['dnsmasq']
    reserved = {53, p['mihomoMixed'], p['mihomoStrict'], p['mihomoDns'], p['mihomoTproxy'],
                p['gostHttp'], p['gostRedirect'], p['dnsmasq'], p['unbound'],
                p['socketGuestDns'], p['coreLoopback']}
    if port in reserved or not any(ipaddress.ip_address(address) in ipaddress.ip_network(cidr)
                                   for cidr in s['localTargets']):
        raise ValueError('localhost selector not authorized')
    return address, port


def credentials(stream):
    return struct.unpack('3i', stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


def channel(s, protocol, family, port):
    path = Path(s['loopbackDirectory']) / (protocol + '.sock')
    info, parent = path.lstat(), path.parent.lstat()
    if (info.st_uid != s['loopbackUid'] or not stat.S_ISSOCK(info.st_mode)
            or parent.st_uid != s['loopbackUid'] or parent.st_mode & 0o022):
        raise ValueError('unsafe localhost broker')
    stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM if protocol == 'tcp' else socket.SOCK_SEQPACKET)
    try:
        stream.settimeout(3)
        stream.connect(str(path))
        if credentials(stream) != s['loopbackUid']:
            raise ValueError('localhost broker identity mismatch')
        stream.sendall(struct.pack('!BH', 4 if family == socket.AF_INET else 6, port))
        return stream
    except BaseException:
        stream.close()
        raise


def transfer(client, remote):
    with selectors.DefaultSelector() as poll:
        client.settimeout(3)
        remote.settimeout(3)
        poll.register(client, selectors.EVENT_READ, remote)
        poll.register(remote, selectors.EVENT_READ, client)
        while poll.get_map():
            events = poll.select(60)
            if not events:
                return
            for key, _ in events:
                data = key.fileobj.recv(65536)
                if data:
                    key.data.sendall(data)
                else:
                    poll.unregister(key.fileobj)
                    key.data.shutdown(socket.SHUT_WR)


def serve_host(s):
    limiter = threading.BoundedSemaphore(128)

    def handle(client, protocol):
        try:
            with client:
                if credentials(client) != s['coreUid']:
                    return
                client.settimeout(3)
                header = client.recv(3)
                while protocol == 'tcp' and len(header) < 3:
                    part = client.recv(3 - len(header))
                    if not part:
                        return
                    header += part
                if len(header) != 3:
                    return
                version, port = struct.unpack('!BH', header)
                address, port = target(s, version, port)
                if protocol == 'tcp':
                    with socket.create_connection((address, port), timeout=3) as remote:
                        transfer(client, remote)
                else:
                    with socket.socket(socket.AF_INET if version == 4 else socket.AF_INET6, socket.SOCK_DGRAM) as remote:
                        remote.connect((address, port))
                        remote.settimeout(3)
                        with selectors.DefaultSelector() as poll:
                            poll.register(client, selectors.EVENT_READ)
                            poll.register(remote, selectors.EVENT_READ)
                            while events := poll.select(60):
                                for key, _ in events:
                                    data = key.fileobj.recv(65536)
                                    if key.fileobj is client:
                                        if not data or data[:1] != b'\0' or len(data) > 65506:
                                            return
                                        remote.send(data[1:])
                                    else:
                                        client.sendall(b'\0' + data)
        except (OSError, ValueError):
            pass
        finally:
            limiter.release()

    with selectors.DefaultSelector() as poll:
        for protocol, kind in (('tcp', socket.SOCK_STREAM), ('udp', socket.SOCK_SEQPACKET)):
            path = Path(s['loopbackDirectory']) / (protocol + '.sock')
            path.unlink(missing_ok=True)
            server = socket.socket(socket.AF_UNIX, kind)
            server.bind(str(path))
            os.chmod(path, 0o660)
            server.listen(32)
            poll.register(server, selectors.EVENT_READ, protocol)
        while True:
            for key, _ in poll.select():
                client, _ = key.fileobj.accept()
                if limiter.acquire(False):
                    threading.Thread(target=handle, args=(client, key.data), daemon=True).start()
                else:
                    client.close()


def serve_namespace(s):
    p, associations = s['ports'], {}
    limiter = threading.BoundedSemaphore(128)

    def tcp(client, family, port):
        try:
            with client, channel(s, 'tcp', family, port) as remote:
                transfer(client, remote)
        except (OSError, ValueError):
            pass
        finally:
            limiter.release()

    def close(identity, poll):
        remote, _, response = associations.pop(identity)
        poll.unregister(remote)
        remote.close()
        response.close()

    with selectors.DefaultSelector() as poll:
        for family, address in ([(socket.AF_INET, '127.0.0.1')]
                                + ([(socket.AF_INET6, '::1')] if s['ipv6'] else [])):
            for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                server = socket.socket(family, kind)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if family == socket.AF_INET6:
                    server.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                if kind == socket.SOCK_DGRAM:
                    level = socket.SOL_IP if family == socket.AF_INET else socket.IPPROTO_IPV6
                    server.setsockopt(level, 19 if family == socket.AF_INET else 75, 1)
                    server.setsockopt(level, 20 if family == socket.AF_INET else 74, 1)
                server.bind((address, p['coreLoopback']))
                if kind == socket.SOCK_STREAM:
                    server.listen(32)
                poll.register(server, selectors.EVENT_READ, (kind, address))
        while True:
            for key, _ in poll.select(1):
                stream = key.fileobj
                kind, address = key.data
                if kind == 'reply':
                    try:
                        data = stream.recv(65536)
                        if not data or data[:1] != b'\0':
                            close(address, poll)
                            continue
                        _, _, response = associations[address]
                        response.sendto(data[1:], address[1])
                        associations[address][1] = time.monotonic()
                    except OSError:
                        close(address, poll)
                    continue
                if kind == socket.SOCK_STREAM:
                    client, peer = stream.accept()
                    try:
                        endpoint = original(client.getsockopt(
                            socket.SOL_IP if stream.family == socket.AF_INET else socket.IPPROTO_IPV6,
                            80, 16 if stream.family == socket.AF_INET else 28), stream.family)
                        target(s, 4 if stream.family == socket.AF_INET else 6, endpoint[1])
                        peers = {address, s['core4'] if stream.family == socket.AF_INET else s['core6']}
                        gateway = endpoint[1] == 53 and not s['strictDns']
                        if peer[0] not in peers or (endpoint[0] != address and not gateway) or not limiter.acquire(False):
                            client.close()
                            continue
                        threading.Thread(target=tcp, args=(client, stream.family, endpoint[1]), daemon=True).start()
                    except (OSError, ValueError):
                        client.close()
                else:
                    try:
                        data, ancillary, _, peer = stream.recvmsg(65535, 128)
                        endpoint = next((original(value, stream.family) for level, option, value in ancillary
                            if (level, option) == ((socket.SOL_IP, 20) if stream.family == socket.AF_INET
                                                 else (socket.IPPROTO_IPV6, 74))), None)
                        peers = {address, s['core4'] if stream.family == socket.AF_INET else s['core6']}
                        if not endpoint or endpoint[0] != address or peer[0] not in peers or len(data) > 65505:
                            continue
                        target(s, 4 if stream.family == socket.AF_INET else 6, endpoint[1])
                        identity = (stream.fileno(), peer, endpoint[1])
                        if identity not in associations:
                            if len(associations) >= 128:
                                continue
                            response = socket.socket(stream.family, socket.SOCK_DGRAM)
                            try:
                                response.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                                response.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, s['loopbackReplyMark'])
                                response.setsockopt(socket.SOL_IP if stream.family == socket.AF_INET else socket.IPPROTO_IPV6,
                                                   19 if stream.family == socket.AF_INET else 75, 1)
                                response.bind(endpoint)
                                remote = channel(s, 'udp', stream.family, endpoint[1])
                            except BaseException:
                                response.close()
                                raise
                            remote.setblocking(False)
                            associations[identity] = [remote, time.monotonic(), response]
                            poll.register(remote, selectors.EVENT_READ, ('reply', identity))
                        associations[identity][0].send(b'\0' + data)
                        associations[identity][1] = time.monotonic()
                    except (OSError, ValueError):
                        pass
            for identity, (_, last, _) in list(associations.items()):
                if time.monotonic() - last > 60:
                    close(identity, poll)


if __name__ == '__main__':
    settings = json.loads(Path(sys.argv[1]).read_text())
    (serve_namespace if len(sys.argv) == 3 and sys.argv[2] == 'namespace' else serve_host)(settings)
