"""Client composition and independent discovery/heartbeat/library tasks."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
import socket
import sys
import time
from urllib.parse import urlparse

import httpx
from pydantic import Field, field_validator

from omi import __version__
from omi.client.coordinator import Coordinator
from omi.discovery import BeaconListener, local_ipv4, validate_url
from omi.library import Library
from omi.maintenance import run_update, wait_update
from omi.models import Heartbeat, HeartbeatReply, Model, Target, new_id
from omi.network import NetworkManager, SimulatedNetwork, run_command
from omi.store import Store

log = logging.getLogger(__name__)


class Settings(Model):
    server_url: str = ""
    interface: str | None = None
    discovery_port: int = Field(default=18080, ge=1, le=65535)
    discovery_wait: float = Field(default=6, ge=0, le=60)
    heartbeat_interval: float = Field(default=2, ge=.1, le=30)
    offline_after: float = Field(default=8, ge=.5, le=120)
    operation_timeout: float = Field(default=100, ge=10, le=300)
    ip_timeout: float = Field(default=35, ge=1, le=120)
    editor_ttl: float = Field(default=30, ge=10, le=120)
    log_budget_mb: int = Field(default=40, ge=5, le=200)
    display: bool = True

    @field_validator("server_url")
    @classmethod
    def server_address(cls, value: str) -> str:
        return validate_url(value) if value else ""


def physical_serial() -> str:
    path = Path("/proc/device-tree/serial-number")
    if path.is_file():
        value = path.read_text().strip("\x00\r\n ")
        if value and value != "0000000000000000":
            return value
    path = Path("/proc/cpuinfo")
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.startswith("Serial"):
                value = line.partition(":")[2].strip()
                if value and value != "0000000000000000":
                    return value
    raise RuntimeError("Raspberry Pi physical serial unavailable; use --simulate for development")


class ClientRuntime:
    def __init__(self, data_dir: Path, *, simulated: bool = False, serial: str | None = None,
                 host=None, network=None, transport=None, endpoint: str | None = None,
                 port: int = 8081, settings: dict | None = None, listener=None, power=None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data_dir / "omi.sqlite3")
        self.library = Library(self.store)
        self.settings = Settings.model_validate({**self.store.get("settings", {}), **(settings or {})})
        self.simulated, self.port = simulated, port
        if serial is not None and not simulated:
            raise ValueError("Serial overrides are allowed only in simulation")
        self.serial = serial or (self.store.get("simulation.serial") or "sim-" + new_id() if simulated else physical_serial())
        if simulated:
            self.store.put("simulation.serial", self.serial)
        self.hostname, self.boot_id = socket.gethostname(), new_id()
        if host is None:
            from omi.services.host import ServiceHost
            host = ServiceHost(self.data_dir, simulated=simulated)
        self.host = host
        self.network = network or (SimulatedNetwork() if simulated else NetworkManager(self.store, self.settings.interface, ip_timeout=self.settings.ip_timeout))
        self.coordinator = Coordinator(self.store, self.library, host, self.network,
                                       operation_timeout=self.settings.operation_timeout, editor_ttl=self.settings.editor_ttl,
                                       power=power or self._power, updater=self._update)
        self.http = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(3), trust_env=False)
        self.listener = listener or BeaconListener(self.settings.discovery_port)
        self.endpoint = endpoint
        self.server_url = self.settings.server_url or None
        self.last_contact = 0.0
        self.server = HeartbeatReply()
        self.booting = True
        self.closed = False
        self.tasks: list[asyncio.Task] = []
        self.display = None

    @property
    def connected(self) -> bool:
        return bool(self.last_contact and time.monotonic() - self.last_contact <= self.settings.offline_after)

    @property
    def local_control(self) -> bool:
        editor = self.coordinator.editor
        return not self.connected or not self.server.onboarded or self.server.discrepancy or bool(editor and editor["local"])

    def status(self) -> dict:
        result = {"role": "client", "serial": self.serial, "hostname": self.hostname,
                  "state": self.coordinator.snapshot().model_dump(), "local_control": self.local_control,
                  "connected": self.connected, "device_id": self.server.device_id, "name": self.server.name,
                  "simulated": self.simulated, "booting": self.booting, "version": __version__,
                  "server_url": self.server_url, "busy": self.coordinator.busy,
                  "library_digest": self.library.digest()}
        try:
            result["update"] = json.loads((self.data_dir / "update-status.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            result["update"] = None
        return result

    async def start(self) -> None:
        try:
            await self.listener.start()
        except OSError as exc:
            log.warning("Discovery listener unavailable; manual server address still works: %s", exc)
        self.tasks = [asyncio.create_task(self._boot(), name="boot"),
                      asyncio.create_task(self._communication(), name="heartbeats"),
                      asyncio.create_task(self._synchronize(), name="library-sync")]
        if self.settings.display and not self.simulated:
            self.tasks.append(asyncio.create_task(self._display_loop(), name="display"))

    async def _boot(self) -> None:
        try:
            if self.server_url:
                await self._heartbeat_once()
            else:
                self.server_url = await self.listener.wait(self.settings.discovery_wait)
                if self.server_url:
                    await self._heartbeat_once()
            # Announce stored intent first, then restore it regardless of server reply.
            await self.coordinator.restore()
        except Exception:
            log.exception("Boot recovery failed")
            self.coordinator.state.health = "error"
            self.coordinator.state.detail = "Boot recovery failed; consult diagnostics"
        finally:
            self.booting = False
            await self._heartbeat_once()

    def _agent_endpoint(self) -> str:
        if self.endpoint:
            return self.endpoint
        if self.simulated:
            return f"http://127.0.0.1:{self.port}"
        hostname = urlparse(self.server_url or "").hostname or "192.0.2.1"
        return f"http://{local_ipv4(hostname)}:{self.port}"

    async def _heartbeat_once(self) -> None:
        if not self.server_url:
            return
        saved = self.store.get("execution", {})
        try:
            heartbeat = Heartbeat(serial=self.serial, hostname=self.hostname, endpoint=self._agent_endpoint(),
                                  state=self.coordinator.snapshot(), boot_id=self.boot_id,
                                  restoring=Target.model_validate(saved["target"]) if self.booting and saved.get("target") else None,
                                  library_digest=self.library.digest(), version=__version__)
            response = await self.http.post(self.server_url + "/api/heartbeat", json=heartbeat.model_dump())
            response.raise_for_status()
            self.server = HeartbeatReply.model_validate(response.json())
            was_connected = self.connected
            self.last_contact = time.monotonic()
            if not was_connected:
                log.info("Connected to central server %s", self.server_url)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            log.debug("Heartbeat unavailable: %s", exc)

    async def _communication(self) -> None:
        while True:
            if self.settings.server_url:
                self.server_url = self.settings.server_url
            elif self.listener.url:
                self.server_url = self.listener.url
            await self._heartbeat_once()
            await asyncio.sleep(self.settings.heartbeat_interval)

    async def _synchronize(self) -> None:
        while True:
            if self.connected and self.server_url and self.server.library_digest != self.library.digest():
                try:
                    response = await self.http.post(self.server_url + "/api/sync", json={"serial": self.serial, "revisions": self.library.export()})
                    response.raise_for_status()
                    payload = response.json()
                    self.library.merge(payload["revisions"])
                except (httpx.HTTPError, ValueError, RuntimeError, KeyError):
                    log.exception("Library synchronization failed; local revisions preserved")
            await asyncio.sleep(max(.5, self.settings.heartbeat_interval))

    async def _display_loop(self) -> None:
        try:
            from omi.display import DisplayManager
            self.display = DisplayManager()
            await asyncio.to_thread(self.display.start)
            while True:
                await asyncio.to_thread(self.display.render, self.status())
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Optional display unavailable; runtime continues")

    async def _power(self, action: str) -> None:
        if self.simulated:
            self.store.put("temporary.simulated_power", action)
            return
        if sys.platform != "linux":
            raise RuntimeError("Power control requires Linux")
        if action not in ("reboot", "poweroff"):
            raise ValueError("Invalid power action")
        if self.display:
            await asyncio.to_thread(self.display.close)
        await self._heartbeat_once()
        await run_command("systemctl", action, timeout=10)

    async def _update(self, body: dict) -> None:
        if self.simulated:
            self.store.put("temporary.simulated_update", body)
            return
        result = await run_update("client", body)
        await wait_update(self.data_dir, result["id"])

    def update_settings(self, value: dict) -> dict:
        if self.coordinator.busy:
            raise RuntimeError("Wait for the current operation")
        updated = Settings.model_validate({**self.settings.model_dump(), **value})
        self.store.put("settings", updated.model_dump())
        self.settings = updated
        self.coordinator.operation_timeout = updated.operation_timeout
        self.coordinator.editor_ttl = updated.editor_ttl
        if isinstance(self.network, NetworkManager):
            self.network.interface = updated.interface
            self.network.ip_timeout = updated.ip_timeout
        return updated.model_dump()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        try:
            await self.coordinator.close()
        finally:
            if self.display:
                await asyncio.to_thread(self.display.close)
            await self.listener.close()
            await self.http.aclose()
            self.store.close()
