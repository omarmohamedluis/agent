"""Agent-facing service lifecycle, bounded child IPC and editor ownership."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from omi.models import PresetConfig, change_requires_restart
from .processes import process_options, terminate_process
from .satellite import SatelliteSupervisor

LOGGER = logging.getLogger("omi.services")


class ServiceHost:
    def __init__(self, data_dir: Path, simulated: bool = False, satellite_dir: Path | None = None):
        self.data_dir = Path(data_dir)
        self.simulated = simulated
        self.satellite_dir = Path(satellite_dir or os.environ.get("OMI_SATELLITE_DIR", "/opt/omi/satellite/current"))
        self._process = None
        self._satellite = None
        self._monitor = None
        self._stderr = None
        self._config: PresetConfig | None = None
        self._capture = False
        self._editor: str | None = None
        self._editor_config: PresetConfig | None = None
        self._learn = None
        self._learn_armed = False
        self._state = {"health": "standby", "detail": "", "ready": False}
        self._lifecycle = asyncio.Lock()
        self._ipc = asyncio.Lock()

    async def start(self, config: PresetConfig) -> dict:
        async with self._lifecycle:
            await self._stop()
            try:
                if config.service == "midi":
                    await self._start_midi(config, capture=False)
                else:
                    self._satellite = SatelliteSupervisor(self.data_dir / "runtime", self.satellite_dir, simulated=self.simulated)
                    await self._satellite.start(config.satellite)
                self._config = config.model_copy(deep=True)
                LOGGER.info("Service %s ready", config.service)
                return self.status()
            except BaseException as exc:
                await self._stop()
                self._state = {"health": "error", "detail": str(exc), "ready": False}
                raise

    async def _start_midi(self, config: PresetConfig, *, capture: bool):
        self._capture = capture
        self._process = await asyncio.create_subprocess_exec(sys.executable, "-m", "omi.services.worker",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=64 * 1024, **process_options())
        self._stderr = asyncio.create_task(self._read_stderr(self._process.stderr), name="midi-stderr")
        initialization = {"config": config.midi.model_dump(), "simulated": self.simulated,
                          "capture": capture, "muted": self._editor is not None}
        self._process.stdin.write(json.dumps(initialization).encode() + b"\n")
        await asyncio.wait_for(self._process.stdin.drain(), 1)
        await self._read_reply(timeout=7)
        if self._editor is not None and self._learn_armed:
            await self._request("learn")
        self._monitor = asyncio.create_task(self._watch_midi(), name="midi-supervision")

    async def _read_stderr(self, stream):
        while chunk := await stream.read(2048):
            LOGGER.warning("MIDI worker: %s", chunk.decode("utf-8", "replace").strip())

    async def _read_reply(self, *, timeout=2):
        line = await asyncio.wait_for(self._process.stdout.readline(), timeout)
        if not line:
            raise RuntimeError("MIDI worker closed its control channel")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "MIDI worker failed"))
        self._state = reply["status"]
        if self._state.get("learn") is not None:
            # Captured preview belongs to the editor session, which can outlive
            # the worker during a coordinated network/input restart.
            self._learn = self._state["learn"]
            self._learn_armed = False
        return self._state

    async def _request(self, action: str, **payload):
        async with self._ipc:
            if self._process is None or self._process.returncode is not None:
                raise RuntimeError("MIDI worker is not running")
            try:
                self._process.stdin.write(json.dumps({"action": action, **payload}).encode() + b"\n")
                await asyncio.wait_for(self._process.stdin.drain(), 1)
                return await self._read_reply()
            except (asyncio.TimeoutError, BrokenPipeError, ConnectionError, ValueError) as exc:
                # A timed-out stream cannot be reused: a late reply could be
                # mistaken for the next command's acknowledgment.
                self._state = {"health": "error", "detail": f"MIDI control channel failed: {exc}", "ready": False}
                await terminate_process(self._process)
                raise RuntimeError(self._state["detail"]) from exc

    async def _watch_midi(self):
        try:
            while True:
                await asyncio.sleep(0.25)
                await self._request("status")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._state = {"health": "error", "detail": f"MIDI worker failed: {exc}", "ready": False}
            LOGGER.error("%s", self._state["detail"])
            if self._process:
                await terminate_process(self._process)

    async def stop(self) -> None:
        async with self._lifecycle:
            await self._stop()

    async def _stop(self):
        if self._monitor:
            self._monitor.cancel()
            await asyncio.gather(self._monitor, return_exceptions=True)
            self._monitor = None
        if self._process:
            process, self._process = self._process, None
            if process.returncode is None:
                try:
                    process.stdin.write(b'{"action":"stop"}\n')
                    await asyncio.wait_for(process.stdin.drain(), 0.25)
                    process.stdin.close()
                    await asyncio.wait_for(process.wait(), 1)
                except (OSError, ConnectionError, asyncio.TimeoutError):
                    pass
            await terminate_process(process)
        if self._stderr:
            self._stderr.cancel()
            await asyncio.gather(self._stderr, return_exceptions=True)
            self._stderr = None
        if self._satellite:
            await self._satellite.stop()
            self._satellite = None
        self._config = None
        self._capture = False
        if self._editor is None:
            self._learn = None
            self._learn_armed = False
        self._state = {"health": "standby", "detail": "", "ready": False}

    async def apply(self, config: PresetConfig) -> None:
        async with self._lifecycle:
            if self._config is None or self._capture:
                raise RuntimeError("No operational service is running")
            if change_requires_restart(self._config, config):
                raise ValueError("This configuration change requires a coordinated restart")
            if config.service == "midi":
                await self._request("apply", config=config.midi.model_dump())
            self._config = config.model_copy(deep=True)

    async def inputs(self) -> list[str]:
        if self.simulated:
            return ["Simulated MIDI"]
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "omi.services.worker", "--inputs",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, **process_options())
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), 3)
            result = json.loads(stdout)
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "MIDI enumeration failed"))
            return result["inputs"]
        finally:
            await terminate_process(process)

    async def open_editor(self, session_id: str, config: PresetConfig) -> None:
        async with self._lifecycle:
            if not session_id:
                raise ValueError("Editor session ID is required")
            if self._editor is not None:
                if self._editor == session_id:
                    return
                raise RuntimeError("Another route editor is already open")
            if config.service != "midi" or self._config is not None and self._config.service != "midi":
                raise ValueError("Route capture is only available for MIDI")
            self._editor = session_id
            self._editor_config = config.model_copy(deep=True)
            self._learn = None
            self._learn_armed = False
            try:
                if self._process:
                    await self._request("editor", opened=True)
                else:
                    await self._start_midi(config, capture=True)
            except BaseException:
                self._editor = self._editor_config = None
                await self._stop()
                raise

    async def close_editor(self, session_id: str) -> None:
        async with self._lifecycle:
            if self._editor != session_id:
                return
            try:
                if self._capture:
                    await self._stop()
                elif self._process and self._process.returncode is None:
                    await self._request("editor", opened=False)
            finally:
                self._editor = self._editor_config = None
                self._learn = None
                self._learn_armed = False

    async def arm_learn(self, session_id: str) -> None:
        async with self._lifecycle:
            if self._editor != session_id:
                raise ValueError("Route editor session is not open")
            await self._request("learn")
            self._learn = None
            self._learn_armed = True

    def learn_result(self, session_id: str) -> dict | None:
        return dict(self._learn) if self._editor == session_id and self._learn is not None else None

    async def inject_midi(self, message: dict) -> None:
        if not self.simulated:
            raise ValueError("MIDI injection is only available in simulation")
        await self._request("inject", message=message, received_ns=time.monotonic_ns())

    def status(self) -> dict:
        state = self._satellite.status() if self._satellite else dict(self._state)
        state.update({"service": self._config.service if self._config else None,
                      "editor": self._editor, "capture_only": self._capture,
                      "simulated": self.simulated})
        state.pop("learn", None)
        return state
