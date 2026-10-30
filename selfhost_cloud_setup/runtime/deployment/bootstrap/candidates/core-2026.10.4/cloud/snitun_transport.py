"""Carry unchanged encrypted SniTun peer bytes over authenticated HTTPS/WSS."""
import asyncio
import logging

from aiohttp import WSMsgType, WSServerHandshakeError, ServerDisconnectedError
from snitun.client.client_peer import ClientPeer
from snitun.utils.aiohttp_client import SniTunClientAioHttp


async def bridge(reader, writer, ws):
    async def upstream():
        # Stay below the WebSocket receiver's exclusive 64 KiB boundary.
        while data := await reader.read(32768):
            await ws.send_bytes(data)

    async def downstream():
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                writer.write(msg.data)
                await writer.drain()
            elif msg.type == WSMsgType.ERROR:
                raise ConnectionError("SniTun WebSocket transport failed")
            elif msg.type == WSMsgType.TEXT:
                raise ConnectionError("Unexpected text in SniTun transport")

    tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        async def finish_cleanup():
            await asyncio.gather(*tasks, return_exceptions=True)
            writer.close()
            await ws.close()

        cleanup = asyncio.create_task(finish_cleanup())
        cancellation = None
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as error:
                cancellation = error
        cleanup.result()
        if cancellation is not None:
            raise cancellation


def client_factory(cloud, url):
    class WebSocketSniTunClient(SniTunClientAioHttp):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._transport_server = None
            self._transport_tasks = set()
            self._stopping = False

        async def _transport(self, reader, writer):
            if self._stopping:
                writer.close()
                return
            task = asyncio.current_task()
            self._transport_tasks.add(task)
            try:
                async with cloud.websession.ws_connect(url, max_msg_size=65536, compress=0,
                                                       headers={"Authorization": "Bearer " + cloud.id_token}) as ws:
                    await bridge(reader, writer, ws)
            except WSServerHandshakeError as error:
                logging.getLogger(__name__).warning("SniTun WebSocket handshake rejected (HTTP %s)", error.status)
            except (OSError, ConnectionError, ServerDisconnectedError):
                pass
            finally:
                writer.close()
                self._transport_tasks.discard(task)

        async def start(self, access_list=None):
            await super().start(access_list)
            self._stopping = False
            self._transport_server = await asyncio.start_server(self._transport, "127.0.0.1", 0)
            port = self._transport_server.sockets[0].getsockname()[1]
            self._client = ClientPeer("127.0.0.1", port)

        async def stop(self, **kwargs):
            self._stopping = True
            try:
                await super().stop(**kwargs)
            finally:
                if self._transport_server:
                    self._transport_server.close()
                tasks = list(self._transport_tasks)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if self._transport_server:
                    await self._transport_server.wait_closed()

    return WebSocketSniTunClient
