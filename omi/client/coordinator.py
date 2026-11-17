"""One owner for process/network changes, regardless of command origin."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from typing import Any, Awaitable, Callable

from omi.library import Library, canonical
from omi.models import (Command, EditorRequest, NetworkProfile, Operation, RuntimeState,
                        SavePreset, Target, change_requires_restart, new_id)
from omi.store import Store

log = logging.getLogger(__name__)


class Coordinator:
    def __init__(self, store: Store, library: Library, host: Any, network: Any, *, operation_timeout: float = 100,
                 editor_ttl: float = 30, power: Callable[[str], Awaitable[None]] | None = None,
                 updater: Callable[[dict], Awaitable[None]] | None = None):
        self.store, self.library, self.host, self.network = store, library, host, network
        self.operation_timeout, self.editor_ttl = operation_timeout, editor_ttl
        self.power, self.updater = power, updater
        saved = store.get("execution", {})
        self.state = RuntimeState(detail=saved.get("error", ""), health="error" if saved.get("error") else "standby")
        self.task: asyncio.Task | None = None
        self.editor: dict | None = None
        self.editor_opening = False
        self.closed = False
        self.quarantined = False
        self._maintenance_task: asyncio.Task | None = None
        for key, record in store.items("command.").items():
            operation = Operation.model_validate(record["operation"])
            if operation.status in ("accepted", "running"):
                operation.status = "interrupted"
                operation.error = "Agent restarted before operation result was recorded; consult current state"
                operation.finished_at = time.time()
                record["operation"] = operation.model_dump()
                store.put(key, record)

    @property
    def busy(self) -> bool:
        return self.quarantined or self.editor_opening or bool(self.task and not self.task.done())

    def snapshot(self) -> RuntimeState:
        state = self.state.model_copy(deep=True)
        info = self.host.status()
        state.service_info = info
        if state.target and not self.busy:
            health = info.get("health")
            if health in ("ready", "waiting", "error"):
                state.health = health
                state.detail = str(info.get("detail", ""))
        return state

    def _persist_execution(self, target: Target | None, error: str = "") -> None:
        self.store.put("execution", {"target": target.model_dump() if target else None, "error": error})

    def _save_operation(self, operation: Operation) -> None:
        record = self.store.get("command." + operation.id, {})
        record["operation"] = operation.model_dump()
        self.store.put("command." + operation.id, record)

    def _phase(self, message: str) -> None:
        operation = self.state.operation
        if operation:
            operation.phase = message
            operation.progress = (operation.progress + [message])[-40:]
            self._save_operation(operation)
            log.info("operation=%s %s", operation.id, message)

    def operation(self, identifier: str) -> Operation:
        record = self.store.get("command." + identifier)
        if record is None:
            raise KeyError(identifier)
        return Operation.model_validate(record["operation"])

    def submit(self, command: Command, *, internal: bool = False) -> Operation:
        fingerprint = hashlib.sha256(canonical(command.model_dump(exclude={"expires_at"})).encode()).hexdigest()
        previous = self.store.get("command." + command.id)
        if previous:
            if previous.get("fingerprint") != fingerprint:
                raise RuntimeError("Command ID already used for different arguments")
            return Operation.model_validate(previous["operation"])
        if self.closed:
            raise RuntimeError("Agent is shutting down")
        if command.expires_at < time.time():
            raise ValueError("Command expired; no action was performed")
        if self.busy:
            raise RuntimeError("An operation is still running; await its actual result")
        if self.editor and not internal and command.arguments.get("session_id") != self.editor["id"]:
            raise RuntimeError("An editor is open; close it before changing service")
        if command.action == "apply":
            if command.target is None:
                raise ValueError("Choose a preset before starting a service")
            revision = self.library.get(command.target.revision)
            if revision.preset_id != command.target.preset_id or revision.deleted:
                raise ValueError("Invalid preset/revision target")
            if all(r.deleted for r in self.library.heads(revision.preset_id)):
                raise ValueError("Preset has been deleted")
            if self.editor and self.editor["preset_id"] != revision.preset_id:
                raise RuntimeError("Cannot change to another preset while editing")
        operation = Operation(id=command.id, action=command.action)
        self.store.put("command." + command.id, {"fingerprint": fingerprint, "operation": operation.model_dump()})
        self.state.operation = operation
        self.task = asyncio.create_task(self._execute(command), name=f"operation-{command.id}")
        return operation.model_copy(deep=True)

    async def _cleanup(self) -> None:
        self._phase("Stopping service and child processes")
        await self.host.stop()
        self._phase("Removing service VLAN")
        await self.network.cleanup()
        self.state.target = None
        self.state.network = {}

    async def _apply(self, target: Target) -> None:
        revision = self.library.get(target.revision)
        current = self.library.get(self.state.target.revision) if self.state.target else None
        if current and current.preset_id == revision.preset_id and not change_requires_restart(current.config, revision.config):
            self._phase("Applying compatible configuration without restarting")
            if current.config != revision.config:
                await self.host.apply(revision.config)
            self.state.target = target
            self._persist_execution(target)
            return
        # A cut during a transition must not resurrect the previous service.
        self._persist_execution(None, "Service transition interrupted before completion")
        self.state.health = "stopping"
        await self._cleanup()
        self.state.health = "starting"
        self._phase("Preparing service network and checking IPv4")
        self.state.network = await self.network.prepare(revision.config.network)
        self._phase("Network ready; starting service")
        info = await self.host.start(revision.config)
        self.state.target = target
        self.state.service_info = info or {}
        self.state.health = self.host.status().get("health", "ready")
        self.state.detail = self.host.status().get("detail", "")
        self._persist_execution(target)
        self._phase("Service ready" if self.state.health == "ready" else "Service started; waiting for external device")

    async def _work(self, command: Command) -> None:
        if command.action == "apply":
            await self._apply(command.target)
        elif command.action == "standby":
            self._persist_execution(None)
            await self._cleanup()
            self.state.health = "standby"
            self.state.detail = ""
            self._phase("Standby")
        elif command.action == "management":
            profile = NetworkProfile.model_validate(command.arguments.get("network", {}))
            self._phase("Applying management network")
            await self.network.configure_management(profile)
        elif command.action in ("reboot", "poweroff"):
            if self.power is None:
                raise RuntimeError("Power adapter unavailable")
            # Keep last execution for restoration after either kind of restart.
            await self._cleanup()
            self._phase("Cleanup complete; requesting system " + command.action)
            await self.power(command.action)
        elif command.action == "update":
            if self.updater is None:
                raise RuntimeError("Update adapter unavailable")
            self._phase("Preparing update while current service remains running")
            await self.updater({**command.arguments, "operation_id": command.id})

    async def _execute(self, command: Command) -> None:
        operation = self.state.operation
        operation.status = "running"
        self._save_operation(operation)
        try:
            # Updates have their own staged deadline; normal transitions bounded here.
            timeout = 1800 if command.action == "update" else self.operation_timeout
            await asyncio.wait_for(self._work(command), timeout)
        except BaseException as exc:
            message = str(exc) or type(exc).__name__
            operation.status = "failed"
            operation.error = message
            if command.action in ("apply", "standby"):
                self._persist_execution(None, message)
                try:
                    await asyncio.wait_for(self._cleanup(), 25)
                except BaseException as cleanup_error:
                    self.quarantined = True
                    message += f"; cleanup incomplete: {cleanup_error}; further operations blocked"
                self.state.health = "error"
                self.state.detail = message
            self._phase("Failed: " + message)
            log.error("operation=%s failed: %s", command.id, message)
        else:
            operation.status = "succeeded"
            if command.action == "apply":
                self.state.health = self.host.status().get("health", "ready")
                self.state.detail = self.host.status().get("detail", "")
        finally:
            operation.finished_at = time.time()
            self._save_operation(operation)

    async def restore(self) -> None:
        saved = self.store.get("execution", {})
        target_data = saved.get("target")
        if target_data:
            try:
                target = Target.model_validate(target_data)
                self.submit(Command(action="apply", target=target), internal=True)
                await self.task
            except (ValueError, KeyError, RuntimeError) as exc:
                self._persist_execution(None, str(exc))
                self.state.health, self.state.detail = "error", str(exc)
        else:
            try:
                await self.network.cleanup()
            except Exception as exc:
                self.quarantined = True
                self.state.health, self.state.detail = "error", str(exc)
        if self._maintenance_task is None:
            self._maintenance_task = asyncio.create_task(self._maintain(), name="runtime-health")

    async def open_editor(self, request: EditorRequest, *, local: bool = True) -> dict:
        if self.busy or self.editor:
            raise RuntimeError("Another operation or editor is active")
        revision = self.library.get(request.revision)
        if revision.preset_id != request.preset_id or revision.deleted:
            raise ValueError("Invalid editor preset revision")
        if self.state.target and self.state.target.preset_id != request.preset_id:
            raise RuntimeError("Only the running preset can be edited while a service is active")
        if request.route_editor and revision.config.service != "midi":
            raise ValueError("Routes and Learn are available for MIDI presets")
        editor = {"id": new_id(), "preset_id": request.preset_id, "revision": request.revision,
                  "route_editor": request.route_editor, "local": local,
                  "expires_at": time.time() + self.editor_ttl, "learn": None}
        self.editor_opening = True
        try:
            if request.route_editor:
                config = revision.config.model_copy(deep=True)
                if request.input is not None:
                    if self.state.target and request.input != config.midi.input:
                        raise RuntimeError("Save a controller change before learning with an active service")
                    config.midi.input = request.input
                await self.host.open_editor(editor["id"], config)
            self.editor = editor
        finally:
            self.editor_opening = False
        return self.editor_status(editor["id"])

    def editor_status(self, identifier: str, *, renew: bool = False) -> dict:
        if self.editor is None or self.editor["id"] != identifier:
            raise KeyError("Editor session expired or closed")
        if renew:
            self.editor["expires_at"] = time.time() + self.editor_ttl
        result = dict(self.editor)
        result["learn"] = self.host.learn_result(identifier) if result["route_editor"] else None
        return result

    async def arm_learn(self, identifier: str) -> dict:
        editor = self.editor_status(identifier, renew=True)
        if not editor["route_editor"]:
            raise ValueError("Open the route editor before learning")
        await self.host.arm_learn(identifier)
        return self.editor_status(identifier)

    async def close_editor(self, identifier: str) -> None:
        editor = self.editor_status(identifier)
        if editor["route_editor"]:
            await self.host.close_editor(identifier)
        self.editor = None

    def save_preset(self, request: SavePreset, author: str) -> Any:
        if self.busy:
            raise RuntimeError("Wait for the current operation before saving")
        if self.editor:
            if request.session_id != self.editor["id"] or request.preset_id != self.editor["preset_id"]:
                raise RuntimeError("This preset belongs to another editing session")
            self.editor_status(request.session_id, renew=True)
        if self.state.target and request.preset_id != self.state.target.preset_id:
            raise RuntimeError("Only the running preset may be edited")
        revision = self.library.save(request, author)
        if self.editor:
            self.editor["revision"] = revision.id
        if self.state.target:
            self.submit(Command(action="apply", target=Target(preset_id=revision.preset_id, revision=revision.id),
                                arguments={"session_id": request.session_id}), internal=False)
        return revision

    async def _maintain(self) -> None:
        while not self.closed:
            try:
                if self.editor and self.editor["expires_at"] < time.time() and not self.busy:
                    await self.close_editor(self.editor["id"])
                if self.state.target and not self.busy and self.host.status().get("health") == "error":
                    message = self.host.status().get("detail", "Service failed")
                    if self.editor:
                        await self.close_editor(self.editor["id"])
                    self.submit(Command(action="standby"), internal=True)
                    await self.task
                    self.state.health, self.state.detail = "error", message
                    self._persist_execution(None, message)
            except Exception:
                log.exception("Runtime health maintenance failed")
            await asyncio.sleep(.25)

    async def close(self) -> None:
        self.closed = True
        if self._maintenance_task:
            self._maintenance_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._maintenance_task
        if self.task and not self.task.done():
            self.task.cancel()
            await self.task
        if self.editor:
            with contextlib.suppress(Exception):
                await self.close_editor(self.editor["id"])
        await asyncio.wait_for(self._cleanup(), 25)
