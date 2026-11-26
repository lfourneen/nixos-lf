"""HTTP byte relay across the browser's private network namespace.

The host endpoint is socket activated, checks current B readiness, and destroys
all streams when that readiness is lost. It has no direct forwarding mode.
"""
import argparse
import asyncio
from collections import Counter
import importlib.util
import ipaddress
from urllib.parse import urlsplit
import json
import os
from pathlib import Path
import signal
import socket
import sys
import time


AUTHORITY_POLL_SECONDS = 0.1


class ObserverUnavailable(Exception):
    pass


class CoreIdentityMismatch(Exception):
    pass


async def copy(reader, writer, allowed=lambda: True, *, half_close=False):
    while data := await reader.read(65536):
        if not allowed():
            return False
        writer.write(data)
        await writer.drain()
    if not allowed():
        return False
    if half_close and writer.can_write_eof():
        # The other direction stays live while this transport sends its FIN.
        writer.write_eof()
    return True


async def close_clients(server, active):
    server.close()
    clients = tuple(active)
    for task in clients:
        task.cancel()
    await asyncio.gather(*clients, return_exceptions=True)


async def bridge(reader, writer, connect, allowed=lambda: True, *, drain_timeout=10):
    remote = None
    tasks = set()
    closed = False
    try:
        if not allowed():
            return 'revoked'
        upstream, remote = await asyncio.wait_for(connect(), 3)
        if not allowed():
            return 'revoked'
        tasks = {asyncio.create_task(copy(reader, remote, allowed, half_close=True)),
                 asyncio.create_task(copy(upstream, writer, allowed, half_close=True))}
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if not allowed() or not all(task.result() for task in done):
            return 'revoked'
        async with asyncio.timeout(drain_timeout):
            if pending and not await next(iter(pending)):
                return 'revoked'
            if not allowed():
                return 'revoked'
            writer.close()
            remote.close()
            await asyncio.gather(writer.wait_closed(), remote.wait_closed())
            closed = True
        return 'completed'
    except ObserverUnavailable:
        return 'observer-unavailable'
    except CoreIdentityMismatch:
        return 'core-identity'
    except TimeoutError:
        return 'drain-timeout' if tasks else 'connect-timeout'
    except (OSError, ValueError):
        return 'stream-error' if tasks else 'connect-error'
    finally:
        # Abort before awaiting task cleanup, including cancellation in a drain.
        if not closed:
            for stream in (writer, remote):
                if stream:
                    stream.transport.abort()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def peer_identity(writer, uid, table=Path('/proc/net/tcp'), *, table_text=None, source_address='127.0.0.1'):
    # Authenticate the established server socket before forwarding any browser
    # bytes. A listener lookup alone has a stop/rebind race. Both endpoints are
    # fixed IPv4 loopback; the kernel supplies the peer socket's owning UID.
    local = writer.get_extra_info('sockname')
    peer = writer.get_extra_info('peername')
    if not local or not peer or local[0] != source_address or peer[0] != '127.0.0.1':
        return False
    want_local = f'0100007F:{peer[1]:04X}'
    source_hex = socket.inet_aton(source_address)[::-1].hex().upper()
    want_remote = f'{source_hex}:{local[1]:04X}'
    try:
        for line in (table.read_text() if table_text is None else table_text).splitlines()[1:]:
            fields = line.split()
            if len(fields) > 9 and fields[1] == want_local and fields[2] == want_remote and fields[3] == '01':
                return fields[7] == str(uid)
    except OSError:
        pass
    return False


def state_module(path):
    spec = importlib.util.spec_from_file_location('network_state', path)
    state = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(state)
    return state


async def host(settings):
    state = state_module(settings['stateModule'])
    active = {}
    outcomes = Counter()
    observed = None
    observe_after = 0
    observer = None
    if settings.get('observer'):
        spec = importlib.util.spec_from_file_location('core_network', settings['observerModule'])
        observer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(observer)

    async def dial():
        source = settings.get('sourceAddress', '127.0.0.1')
        reader, writer = await asyncio.open_connection('127.0.0.1', settings['port'], local_addr=(source, 0))
        try:
            table = None
            if observer:
                try:
                    table = (await asyncio.to_thread(observer.request, settings['observer'], 'strict-tcp4'))['table']
                except OSError as error:
                    raise ObserverUnavailable from error
                except (KeyError, TypeError, ValueError) as error:
                    raise CoreIdentityMismatch from error
                if not isinstance(table, str):
                    raise CoreIdentityMismatch
            if not peer_identity(writer, settings['coreUid'], table_text=table, source_address=source):
                raise CoreIdentityMismatch
        except BaseException:
            writer.transport.abort()
            raise
        return reader, writer

    async def client(reader, writer):
        task = asyncio.current_task()
        if len(active) >= 128:
            writer.transport.abort()
            return
        binding = authority(fresh=True)
        if binding is None:
            outcomes['revoked'] += 1
            writer.transport.abort()
            return
        active[task] = binding
        try:
            result = await bridge(reader, writer, dial, lambda: authority() == binding)
            outcomes[result] += 1
        finally:
            active.pop(task, None)

    def authority(*, fresh=False):
        nonlocal observed, observe_after
        now = time.clock_gettime(time.CLOCK_BOOTTIME)
        if not fresh and now < observe_after:
            return observed
        # Share one readiness observation across active streams. Check again
        # at the old lease deadline even if the next monitor tick is later.
        observed = None
        observe_after = now + AUTHORITY_POLL_SECONDS
        try:
            verdict = state.readiness(target='proxy', maximum=settings['maxAge'],
                                      verification_maximum=settings['verificationMaxAge'])
            if verdict.get('mode') != 'proxy' or verdict.get('ready') is not True:
                return None
            if verdict['authorization_lease']['required']:
                lease = state.read(Path('/run/proxy-mode/authorization.json'))
                until = lease.get('until')
                if (lease.get('policy_version') != verdict['policy_version']
                        or type(until) not in (int, float) or not until > time.clock_gettime(time.CLOCK_BOOTTIME)):
                    return None
                observe_after = min(observe_after, until)
            observed = tuple(verdict.get(key) for key in (
                'policy_version', 'routing_epoch', 'routing_policy_digest', 'egress_epoch', 'egress_requested'))
        except Exception:
            pass
        return observed

    async def monitor():
        loop = asyncio.get_running_loop()
        report_at = loop.time() + 60
        while True:
            if active:
                current = authority(fresh=True)
                for task, binding in tuple(active.items()):
                    if current != binding and not task.cancelling():
                        outcomes['revoked'] += 1
                        task.cancel()
            if loop.time() >= report_at:
                if outcomes:
                    print('strict relay ' + json.dumps(outcomes, sort_keys=True), file=sys.stderr, flush=True)
                    outcomes.clear()
                report_at = loop.time() + 60
            await asyncio.sleep(AUTHORITY_POLL_SECONDS)

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
            await close_clients(server, active)
            await asyncio.gather(watch, return_exceptions=True)


async def portal(args, settings):
    # A literal, explicitly selected portal address is the sole remote target.
    ip = ipaddress.ip_address(args.address)
    if ip.is_multicast or ip.is_unspecified or str(ip) in ('169.254.169.254', '100.100.100.200', 'fd00:ec2::254'):
        raise ValueError('unsafe portal address')
    state = state_module(settings['stateModule'])
    grant = (args.boot_id, args.policy_version)

    def allowed():
        return state.portal_authority(maximum=settings['maxAge'],
                                      verification_maximum=settings['verificationMaxAge']) == grant

    if not allowed():
        raise ValueError('portal authorization is unavailable')
    active = set()
    async def client(reader, writer):
        task = asyncio.current_task()
        remote = None
        if len(active) >= 32 or not allowed():
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
            if not allowed():
                return
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            upstream, remote = await asyncio.wait_for(asyncio.open_connection(args.address, port), 3)
            if not allowed():
                return
            if method == 'CONNECT':
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                await writer.drain()
            else:
                path = parsed.path or '/'
                if parsed.query:
                    path += '?' + parsed.query
                remote.write((method + ' ' + path + ' ' + version).encode() + b'\r\n' + b'\r\n'.join(lines[1:]))
                await remote.drain()
            tasks = [asyncio.create_task(copy(reader, remote, allowed)),
                     asyncio.create_task(copy(upstream, writer, allowed))]
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
        try:
            while allowed():
                await asyncio.sleep(0.1)
        finally:
            await close_clients(server, active)


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
    process = None
    async with server:
        try:
            process = await asyncio.create_subprocess_exec(*args.command)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: process.terminate() if process.returncode is None else None)
            return await process.wait()
        finally:
            await close_clients(server, active)
            if process is not None and process.returncode is None:
                process.terminate()
                await process.wait()


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='mode', required=True)
    p = sub.add_parser('host')
    p.add_argument('settings')
    p = sub.add_parser('portal')
    p.add_argument('settings')
    p.add_argument('--socket', required=True)
    p.add_argument('--address', required=True)
    p.add_argument('--boot-id', required=True)
    p.add_argument('--policy-version', type=int, required=True)
    p = sub.add_parser('browser')
    p.add_argument('--socket', required=True)
    p.add_argument('--port', required=True, type=int)
    p.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.mode == 'portal':
        asyncio.run(portal(args, json.loads(Path(args.settings).read_text())))
        return 0
    if args.mode == 'host':
        asyncio.run(host(json.loads(Path(args.settings).read_text())))
        return 0
    if args.command[:1] == ['--']:
        args.command.pop(0)
    return asyncio.run(browser(args))


if __name__ == '__main__':
    raise SystemExit(main())
