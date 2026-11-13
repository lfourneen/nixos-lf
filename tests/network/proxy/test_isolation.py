"""Real byte streams: losing authorization aborts a live connection."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import os
import unittest

spec = importlib.util.spec_from_file_location('relay', sys.argv.pop(1))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_stream_is_cut_and_no_buffer_is_replayed(self):
        received, active = [], set()
        async def peer(reader, writer):
            try:
                while data := await reader.read(64):
                    received.append(data)
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
        origin = await asyncio.start_server(peer, '127.0.0.1', 0)
        address = origin.sockets[0].getsockname()
        async def client(reader, writer):
            task = asyncio.current_task()
            active.add(task)
            try:
                await r.bridge(reader, writer, lambda: asyncio.open_connection(*address))
            finally:
                active.discard(task)
        server = await asyncio.start_server(client, '127.0.0.1', 0)
        try:
            reader, writer = await asyncio.open_connection(*server.sockets[0].getsockname())
            writer.write(b'before')
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(6), 2), b'before')
            for task in tuple(active):
                task.cancel()
            await asyncio.gather(*tuple(active), return_exceptions=True)
            self.assertEqual(await asyncio.wait_for(reader.read(1), 2), b'')
            self.assertEqual(received, [b'before'])
            writer.close()
            await writer.wait_closed()
        finally:
            server.close(); origin.close()
            await server.wait_closed(); await origin.wait_closed()

    async def test_kernel_peer_identity_precedes_forwarding(self):
        connected = asyncio.Event()
        async def server(reader, writer):
            connected.set()
            await reader.read()
            writer.close()
        listener = await asyncio.start_server(server, '127.0.0.1', 0)
        reader, writer = await asyncio.open_connection(*listener.sockets[0].getsockname())
        try:
            await connected.wait()
            self.assertTrue(r.peer_identity(writer, os.getuid()))
            self.assertFalse(r.peer_identity(writer, os.getuid()+1))
        finally:
            writer.close()
            await writer.wait_closed()
            listener.close()
            await listener.wait_closed()

    async def test_non_ready_request_does_not_connect_upstream(self):
        async def connect():
            self.fail('upstream dial in blocked mode')
        accepted = asyncio.Event()
        async def client(reader, writer):
            await r.bridge(reader, writer, connect, lambda: False)
            accepted.set()
        server = await asyncio.start_server(client, '127.0.0.1', 0)
        try:
            reader, writer = await asyncio.open_connection(*server.sockets[0].getsockname())
            await asyncio.wait_for(accepted.wait(), 2)
            self.assertEqual(await reader.read(1), b'')
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()


if __name__ == '__main__':
    unittest.main()
