"""Small UDP discovery channel, independent of service operations."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("Use an HTTP address such as http://192.168.1.10:8080")
    _ = parsed.port
    return url.rstrip("/")


class BeaconListener(asyncio.DatagramProtocol):
    def __init__(self, port: int = 18080):
        self.port = port
        self.url: str | None = None
        self.event = asyncio.Event()
        self.transport = None

    def datagram_received(self, data: bytes, addr) -> None:
        if len(data) > 2048:
            return
        try:
            payload = json.loads(data)
            if payload.get("omi") != 3:
                return
            self.url = validate_url(payload["url"])
        except (ValueError, TypeError, KeyError, AttributeError):
            return
        self.event.set()

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=("0.0.0.0", self.port), allow_broadcast=True)

    async def wait(self, timeout: float) -> str | None:
        if self.url:
            return self.url
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.event.wait(), timeout)
        return self.url

    async def close(self) -> None:
        if self.transport:
            self.transport.close()
            self.transport = None


class BeaconBroadcaster:
    def __init__(self, url: str, *, port: int = 18080, interval: float = 2,
                 broadcast: str = "255.255.255.255", server_id: str = "omi"):
        self.url = validate_url(url)
        self.port, self.interval, self.broadcast, self.server_id = port, interval, broadcast, server_id
        self.task: asyncio.Task | None = None
        self.socket: socket.socket | None = None

    async def start(self) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.socket.setblocking(False)
        self.task = asyncio.create_task(self._loop(), name="beacon")

    async def _loop(self) -> None:
        payload = json.dumps({"omi": 3, "url": self.url, "server_id": self.server_id}).encode()
        while True:
            try:
                await asyncio.get_running_loop().sock_sendto(self.socket, payload, (self.broadcast, self.port))
            except OSError as exc:
                log.debug("Beacon unavailable: %s", exc)
            await asyncio.sleep(self.interval)

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        if self.socket:
            self.socket.close()


def local_ipv4(destination: str = "192.0.2.1") -> str:
    """Route lookup using UDP connect, without transmitting a datagram."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((destination, 9))
        return sock.getsockname()[0]
