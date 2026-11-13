"""HTTP byte relay across the browser's private network namespace.

The host endpoint is socket activated, checks current B readiness, and destroys
all streams when that readiness is lost. It has no direct forwarding mode.
"""
import argparse
import asyncio
import importlib.util
import ipaddress
from urllib.parse import urlsplit
import json
import os
from pathlib import Path
import signal
import socket


async def copy(reader, writer):
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()


async def bridge(reader, writer, connect, allowed=lambda: True):
    remote = None
    tasks = []
    try:
        if not allowed():
            return
        upstream, remote = await asyncio.wait_for(connect(), 3)
        tasks = [asyncio.create_task(copy(reader, remote)), asyncio.create_task(copy(upstream, writer))]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (OSError, ValueError, asyncio.TimeoutError, asyncio.CancelledError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Abort instead of draining queued browser data across a mode change.
        for stream in (writer, remote):
            if stream:
                stream.transport.abort()


def peer_identity(writer, uid, table=Path('/proc/net/tcp')):
    # Authenticate the established server socket before forwarding any browser
    # bytes. A listener lookup alone has a stop/rebind race. Both endpoints are
    # fixed IPv4 loopback; the kernel supplies the peer socket's owning UID.
    local = writer.get_extra_info('sockname')
    peer = writer.get_extra_info('peername')
    if not local or not peer or local[0] != '127.0.0.1' or peer[0] != '127.0.0.1':
        return False
    want_local = f'0100007F:{peer[1]:04X}'
    want_remote = f'0100007F:{local[1]:04X}'
    try:
        for line in table.read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) > 9 and fields[1] == want_local and fields[2] == want_remote and fields[3] == '01':
                return fields[7] == str(uid)
    except OSError:
        pass
    return False


async def host(settings):
    spec = importlib.util.spec_from_file_location('network_state', settings['stateModule'])
    state = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(state)
    active = set()
    ready = False

    async def dial():
        reader, writer = await asyncio.open_connection('127.0.0.1', settings['port'])
        if not peer_identity(writer, settings['coreUid']):
            writer.transport.abort()
            raise ValueError('strict core socket identity mismatch')
        return reader, writer

    async def client(reader, writer):
        task = asyncio.current_task()
        if len(active) >= 128:
            writer.transport.abort()
            return
        active.add(task)
        try:
            await bridge(reader, writer, dial, lambda: ready)
        finally:
            active.discard(task)

    async def monitor():
        nonlocal ready
        while True:
            ready = False
            try:
                verdict = state.readiness(target='proxy', maximum=settings['maxAge'],
                                          verification_maximum=settings['verificationMaxAge'])
                ready = verdict.get('mode') == 'proxy' and verdict.get('ready') is True
            except Exception:
                # Corrupt/unavailable observations must not leave a previously
                # true readiness value alive in a detached failed task.
                ready = False
            if not ready:
                for task in tuple(active):
                    task.cancel()
            await asyncio.sleep(0.1)

    if os.environ.get('LISTEN_FDS') != '1' or os.environ.get('LISTEN_PID') != str(os.getpid()):
        raise ValueError('socket activation required')
    listener = socket.socket(fileno=3)
    listener.setblocking(False)
    server = await asyncio.start_unix_server(client, sock=listener, limit=65536)
    watch = asyncio.create_task(monitor())
    async with server:
        try:
            await server.serve_forever()
        finally:
            watch.cancel()
            for task in tuple(active):
                task.cancel()


async def portal(args):
    # A literal, explicitly selected portal address is the sole remote target.
    ip = ipaddress.ip_address(args.address)
    if ip.is_multicast or ip.is_unspecified or str(ip) in ('169.254.169.254', '100.100.100.200', 'fd00:ec2::254'):
        raise ValueError('unsafe portal address')
    active = set()
    async def client(reader, writer):
        task = asyncio.current_task()
        remote = None
        if len(active) >= 32:
            writer.transport.abort()
            return
        active.add(task)
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
            lines = header.split(b'\r\n')
            method, target, version = lines[0].decode('ascii').split(' ')
            if method == 'CONNECT':
                parsed = urlsplit('https://' + target)
            else:
                parsed = urlsplit(target)
            if parsed.hostname != args.address or parsed.port not in (None, 80, 443):
                raise ValueError('portal target')
            if parsed.username or parsed.password or parsed.scheme not in ('http', 'https'):
                raise ValueError('portal scheme')
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            upstream, remote = await asyncio.wait_for(asyncio.open_connection(args.address, port), 3)
            if method == 'CONNECT':
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                await writer.drain()
            else:
                path = parsed.path or '/'
                if parsed.query:
                    path += '?' + parsed.query
                remote.write((method + ' ' + path + ' ' + version).encode() + b'\r\n' + b'\r\n'.join(lines[1:]))
                await remote.drain()
            tasks = [asyncio.create_task(copy(reader, remote)), asyncio.create_task(copy(upstream, writer))]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    t.result()
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except (ValueError, OSError, UnicodeError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            active.discard(task)
            for stream in (writer, remote):
                if stream:
                    stream.transport.abort()
    server = await asyncio.start_unix_server(client, args.socket, limit=16384)
    async with server:
        await server.serve_forever()


async def browser(args):
    active = set()
    async def client(reader, writer):
        task = asyncio.current_task()
        if len(active) >= 128:
            writer.transport.abort()
            return
        active.add(task)
        try:
            await bridge(reader, writer, lambda: asyncio.open_unix_connection(args.socket))
        finally:
            active.discard(task)
    server = await asyncio.start_server(client, '127.0.0.1', args.port, limit=65536)
    process = await asyncio.create_subprocess_exec(*args.command)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: process.terminate() if process.returncode is None else None)
    try:
        async with server:
            return await process.wait()
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()
        for task in tuple(active):
            task.cancel()


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='mode', required=True)
    p = sub.add_parser('host')
    p.add_argument('settings')
    p = sub.add_parser('portal')
    p.add_argument('--socket', required=True)
    p.add_argument('--address', required=True)
    p = sub.add_parser('browser')
    p.add_argument('--socket', required=True)
    p.add_argument('--port', required=True, type=int)
    p.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.mode == 'portal':
        asyncio.run(portal(args))
        return 0
    if args.mode == 'host':
        asyncio.run(host(json.loads(Path(args.settings).read_text())))
        return 0
    if args.command[:1] == ['--']:
        args.command.pop(0)
    return asyncio.run(browser(args))


if __name__ == '__main__':
    raise SystemExit(main())
