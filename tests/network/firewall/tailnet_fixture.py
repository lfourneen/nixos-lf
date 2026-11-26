"""Use real conntrack entries before and after replacing the host rules."""

from contextlib import ExitStack, contextmanager
import os
import socket
import subprocess


HOST, PEER = '100.111.0.1', '100.111.0.2'
NAMESPACE = 'tailnet-fixture'
PORTS = {socket.SOCK_STREAM: (47984, 47989), socket.SOCK_DGRAM: (47998, 47999)}


def run(*args):
    subprocess.run(args, check=True)


@contextmanager
def peer_namespace():
    with open('/proc/self/ns/net', 'rb') as original, open(f'/run/netns/{NAMESPACE}', 'rb') as peer:
        os.setns(peer.fileno(), os.CLONE_NEWNET)
        try:
            yield
        finally:
            os.setns(original.fileno(), os.CLONE_NEWNET)


def pair(stack, kind, port, *, host_initiates=False):
    with peer_namespace():
        peer = stack.enter_context(socket.socket(socket.AF_INET, kind))
        peer.settimeout(3)
    host = stack.enter_context(socket.socket(socket.AF_INET, kind))
    host.settimeout(3)
    listener, client = (peer, host) if host_initiates else (host, peer)
    server_address, client_address = (PEER, HOST) if host_initiates else (HOST, PEER)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((server_address, port))
    client.bind((client_address, 0))
    if kind == socket.SOCK_STREAM:
        listener.listen()
        client.connect((server_address, port))
        connected, _ = listener.accept()
        with listener:
            listener = stack.enter_context(connected)
        listener.settimeout(3)
    else:
        client.connect((server_address, port))
        listener.connect(client.getsockname())
    transfer(client, listener, allowed=True)
    transfer(listener, client, allowed=True)
    return (listener, client) if host_initiates else (client, listener)


def transfer(sender, receiver, *, allowed):
    receiver.settimeout(2 if allowed else 0.25)
    try:
        sender.sendall(b'x')
        data = receiver.recv(1)
    except PermissionError:
        assert not allowed, 'local firewall refused an authorized exchange'
    except TimeoutError:
        assert not allowed, 'authorized payload was dropped'
    else:
        assert allowed and data == b'x', 'payload crossed a closed tailnet service port'
    finally:
        receiver.settimeout(3)


def exchange(flow, *, allowed):
    # Check server pushes independently of receiving another request.
    peer, host = flow
    transfer(host, peer, allowed=allowed)
    transfer(peer, host, allowed=allowed)


def check():
    variants = {
        'no-tcp': (False, True),
        'no-udp': (True, False),
        'no-ports': (False, False),
        'disabled': (False, False),
    }
    for variant, (tcp_allowed, udp_allowed) in variants.items():
        with ExitStack() as stack:
            tcp = pair(stack, socket.SOCK_STREAM, PORTS[socket.SOCK_STREAM][0])
            udp = pair(stack, socket.SOCK_DGRAM, PORTS[socket.SOCK_DGRAM][0])
            local_tcp = pair(stack, socket.SOCK_STREAM, 18123, host_initiates=True)
            local_udp = pair(stack, socket.SOCK_DGRAM, 18124, host_initiates=True)
            run('nft', '-f', f'/etc/tailnet-{variant}.nft')
            exchange(tcp, allowed=tcp_allowed)
            exchange(udp, allowed=udp_allowed)
            exchange(local_tcp, allowed=True)
            exchange(local_udp, allowed=True)
        run('systemctl', 'reload', 'nftables')

    with ExitStack() as stack:
        removed = [pair(stack, kind, ports[0]) for kind, ports in PORTS.items()]
        retained = [pair(stack, kind, ports[1]) for kind, ports in PORTS.items()]
        run('nft', '-f', '/etc/tailnet-remove-first.nft')
        for flow in removed:
            exchange(flow, allowed=False)
        for flow in retained:
            exchange(flow, allowed=True)
        run('nft', '-f', '/etc/tailnet-renamed.nft')
        for flow in retained:
            exchange(flow, allowed=False)
    run('ip', 'link', 'set', 'tailscale0', 'down')
    run('ip', 'link', 'set', 'tailscale0', 'name', 'tailnet-new0')
    run('ip', 'link', 'set', 'tailnet-new0', 'up')
    with ExitStack() as stack:
        for kind, ports in PORTS.items():
            exchange(pair(stack, kind, ports[0]), allowed=True)


def main():
    run('ip', 'netns', 'add', NAMESPACE)
    try:
        run('ip', 'link', 'add', 'tailscale0', 'type', 'veth', 'peer', 'name', 'tn-peer', 'netns', NAMESPACE)
        run('ip', 'addr', 'add', HOST + '/24', 'dev', 'tailscale0')
        run('ip', '-n', NAMESPACE, 'addr', 'add', PEER + '/24', 'dev', 'tn-peer')
        run('ip', 'link', 'set', 'tailscale0', 'up')
        run('ip', '-n', NAMESPACE, 'link', 'set', 'tn-peer', 'up')
        check()
    finally:
        run('ip', 'netns', 'delete', NAMESPACE)
        run('systemctl', 'reload', 'nftables')


if __name__ == '__main__':
    main()
