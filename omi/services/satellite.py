"""Prepared Companion Satellite 3.8.0 integration; never installs at runtime.

Upstream pinned config/API: bitfocus/companion-satellite tag v3.8.0,
satellite/src/{main,config,rest,apiTypes}.ts. Launcher comes from our installer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sys
import urllib.request
from collections import deque
from pathlib import Path

from omi.models import SatelliteConfig
from .processes import process_options, terminate_process

SATELLITE_VERSION = "3.8.0"
SATELLITE_BUILD = "804-8831637"
LOGGER = logging.getLogger("omi.services.satellite")


def runtime_config(config: SatelliteConfig, previous: dict | None = None) -> dict:
    previous = previous or {}
    return {"remoteProtocol": "tcp", "remoteIp": config.host, "remotePort": config.port,
            "restEnabled": True, "restPort": config.rest_port, "mdnsEnabled": False,
            "detailedUsageEnabled": False,
            "installationName": previous.get("installationName", f"OMI {socket.gethostname()}"),
            **({"machineId": previous["machineId"]} if previous.get("machineId") else {}),
            "surfacePluginsEnabled": {"elgato-stream-deck": True, "loupedeck": False,
                                      "idisplay-infinitton": False, "mirabox-stream-dock": False}}


def prepared_command(directory: Path, config_path: Path) -> list[str]:
    try:
        manifest = json.loads((directory / "omi-satellite.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Satellite is not prepared; run the OMI client installer") from exc
    if manifest.get("version") != SATELLITE_VERSION or manifest.get("build") != SATELLITE_BUILD:
        raise RuntimeError(f"Satellite must be pinned to {SATELLITE_VERSION} ({SATELLITE_BUILD})")
    executable = (directory / manifest.get("executable", "companion-satellite")).resolve()
    if executable.parent != directory.resolve() or not executable.is_file():
        raise RuntimeError("Satellite launcher missing or outside its prepared directory")
    return [str(executable), str(config_path.resolve())]


def probe(config: SatelliteConfig) -> dict:
    # Ignore proxy env variables; this is exclusively a local child REST probe.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def read(path):
        with opener.open(f"http://127.0.0.1:{config.rest_port}/api/{path}", timeout=0.5) as reply:
            return json.loads(reply.read(64 * 1024))
    actual, status = read("config"), read("status")
    if actual.get("host") != config.host or actual.get("port") != config.port or actual.get("protocol") != "tcp":
        raise RuntimeError("Satellite REST configuration differs from the requested preset")
    if not isinstance(status.get("connected"), bool):
        raise RuntimeError("Satellite returned an invalid connection status")
    connected = status["connected"]
    unsupported = status.get("companionUnsupportedApi", False)
    return {"health": "error" if unsupported else "ready" if connected else "waiting", "ready": True,
            "detail": "Companion API version is unsupported" if unsupported else "Companion connected" if connected else "Waiting for Companion",
            "companion_connected": connected, "companion_version": status.get("companionVersion"),
            "companion_unsupported": unsupported,
            "version": SATELLITE_VERSION, "build": SATELLITE_BUILD}


class SatelliteSupervisor:
    def __init__(self, data_dir: Path, directory: Path, *, simulated=False):
        self.data_dir, self.directory, self.simulated = data_dir, directory, simulated
        self.process = None
        self.task = None
        self.reader = None
        self.config = None
        self.restarts = 0
        self.logs = deque(maxlen=50)
        self.state = {"health": "standby", "detail": "", "ready": False}
        self.stopping = False

    async def start(self, config: SatelliteConfig):
        self.config = config
        self.stopping = False
        self.restarts = 0
        if self.simulated:
            self.state = {"health": "ready", "detail": "Simulated Satellite", "ready": True,
                          "companion_connected": True, "version": SATELLITE_VERSION, "simulated": True}
            return self.status()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / "satellite.json"
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(runtime_config(config, previous), indent=2), encoding="utf-8")
        os.replace(temporary, path)
        self.command = prepared_command(self.directory, path)
        # A stale/foreign REST listener must never be mistaken for this process.
        with socket.socket() as port_check:
            try:
                if sys.platform.startswith("linux"):
                    # Node also uses REUSEADDR. A previous REST connection in
                    # TIME_WAIT must not block an ordinary service restart.
                    port_check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                port_check.bind(("127.0.0.1", config.rest_port))
                port_check.listen(1)
            except OSError as exc:
                raise RuntimeError(f"Satellite REST port {config.rest_port} is already in use") from exc
        try:
            await self._spawn()
            await self._readiness()
        except BaseException:
            await self._cleanup_process()
            raise
        self.task = asyncio.create_task(self._supervise(), name="satellite-supervision")
        return self.status()

    async def _spawn(self):
        self.state = {"health": "waiting", "detail": "Starting Satellite", "ready": False}
        self.process = await asyncio.create_subprocess_exec(sys.executable, "-m", "omi.services.guard", *self.command, cwd=self.directory,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                        limit=16 * 1024, **process_options())
        self.reader = asyncio.create_task(self._logs(self.process.stdout), name="satellite-output")

    async def _logs(self, stream):
        pending = b""
        while chunk := await stream.read(4096):
            # Bounded fragments protect against a child writing unbounded lines.
            pending += chunk
            while b"\n" in pending or len(pending) >= 4096:
                line, delimiter, tail = pending.partition(b"\n")
                if not delimiter:
                    line, tail = pending[:4096], pending[4096:]
                pending = tail
                text = line[:4096].decode("utf-8", "replace").strip()
                if text:
                    self.logs.append(text)
                    LOGGER.info("%s", text)

    async def _readiness(self):
        deadline = asyncio.get_running_loop().time() + 15
        last = "REST API not ready"
        while asyncio.get_running_loop().time() < deadline:
            if self.process.returncode is not None:
                raise RuntimeError(f"Satellite exited during startup ({self.process.returncode})")
            try:
                self.state = await asyncio.to_thread(probe, self.config)
                if self.state["health"] == "error":
                    raise RuntimeError(self.state["detail"])
                return
            except (OSError, ValueError, RuntimeError) as exc:
                if self.state.get("companion_unsupported"):
                    raise
                last = str(exc)
            await asyncio.sleep(0.15)
        raise TimeoutError(f"Satellite readiness timed out: {last}")

    async def _supervise(self):
        while not self.stopping:
            await asyncio.sleep(0.5)
            if self.process.returncode is not None:
                code = self.process.returncode
                await self._cleanup_process()
                if self.restarts >= 3:
                    self.state = {"health": "error", "detail": f"Satellite exited ({code}); recovery limit reached", "ready": False}
                    return
                self.restarts += 1
                self.state = {"health": "waiting", "detail": f"Restarting Satellite ({self.restarts}/3)", "ready": False}
                await asyncio.sleep(0.5 * self.restarts)
                try:
                    await self._spawn()
                    await self._readiness()
                except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                    self.state = {"health": "error", "detail": str(exc), "ready": False}
                    # A failed spawn has no process to watch; use the bounded
                    # budget immediately rather than orphaning supervision.
                    await self._cleanup_process()
                    return
            else:
                try:
                    self.state = await asyncio.to_thread(probe, self.config)
                except (OSError, ValueError, RuntimeError) as exc:
                    # Missing Companion/REST never restarts a living Satellite.
                    self.state = {"health": "waiting", "detail": f"Satellite health unavailable: {exc}", "ready": True,
                                  "companion_connected": None, "version": SATELLITE_VERSION}

    def status(self):
        return {**self.state, "restarts": self.restarts,
                "process_alive": self.simulated and not self.stopping or self.process is not None and self.process.returncode is None,
                "recent_logs": list(self.logs)}

    async def _cleanup_process(self):
        if self.process:
            await terminate_process(self.process, grace=4.5)
        if self.reader:
            try:
                # Drain EOF before cancelling the reader, especially for the
                # Windows Proactor transport when startup fails immediately.
                await asyncio.wait_for(asyncio.shield(self.reader), 0.5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
            self.reader = None
        self.process = None

    async def stop(self):
        self.stopping = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        await self._cleanup_process()
        self.state = {"health": "standby", "detail": "", "ready": False}
