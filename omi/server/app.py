"""Persistent central control plane. Heartbeats never carry queued commands."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

import httpx
from omi import __version__
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from omi.library import Library
from omi.models import Command, Heartbeat, HeartbeatReply, Operation, SavePreset
from omi.store import Store
from omi.web import mount_web

log = logging.getLogger(__name__)
TERMINAL = {"succeeded", "failed", "interrupted"}


class Onboard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=120)
    import_presets: bool = False


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: int | None = Field(default=None, ge=1, le=999999)
    name: str | None = Field(default=None, min_length=1, max_length=120)


class GroupCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serials: list[str] = Field(min_length=1, max_length=100)
    command: Command


class ServerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    offline_after: float = Field(default=8, ge=.5, le=300, strict=True)
    request_timeout: float = Field(default=10, ge=1, le=120, strict=True)
    advertise: str = Field(default="", max_length=2048)
    beacon_interval: float = Field(default=2, ge=.2, le=30, strict=True)

    @field_validator("advertise")
    @classmethod
    def valid_address(cls, value):
        from omi.discovery import validate_url
        if any(character.isspace() or ord(character) < 32 or character == "\\" for character in value):
            raise ValueError("La dirección anunciada no admite espacios ni caracteres de control")
        return validate_url(value) if value else ""


class Central:
    def __init__(self, directory: Path, transport=None, *, offline_after=None,
                 request_timeout=None, clock: Callable[[], float] = time.time,
                 advertise="", beacon_interval=None, beacon_enabled=False,
                 beacon_port=18080, http_port=8080):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.directory / "omi.sqlite3")
        self.library = Library(self.store)
        self.lock = asyncio.Lock()
        self.clock = clock
        overrides = {key: value for key, value in {"offline_after": offline_after,
                     "request_timeout": request_timeout, "beacon_interval": beacon_interval}.items() if value is not None}
        if advertise:
            overrides["advertise"] = advertise
        try:
            self.saved_settings = ServerSettings.model_validate(self.store.get("settings", {}))
            self.defaults = ServerSettings.model_validate(overrides)
            self.settings = ServerSettings.model_validate({**self.saved_settings.model_dump(), **overrides})
        except ValueError:
            self.store.close()
            raise
        self.explicit_advertise = self.defaults.advertise if advertise else ""
        self.offline_after = self.settings.offline_after
        self.client = httpx.AsyncClient(transport=transport, timeout=self.settings.request_timeout,
                                        trust_env=False)
        self.beacon_enabled = beacon_enabled
        self.beacon_port, self.http_port = beacon_port, http_port
        self.broadcaster = None
        self.started = False
        self.agents: dict[str, dict] = self.store.get("server.agents", {})
        for agent in self.agents.values():
            pending = agent.get("pending")
            if pending and pending.get("status") == "sending":
                pending["status"] = "unknown"
                pending["error"] = "El servidor se reinició durante el envío; se consultará el resultado sin reenviar la orden"
                self.store.put(f"server.command.{agent['serial']}.{pending['id']}", pending)
        self.persist()
        # A persisted timestamp cannot certify connectivity after server restart.
        self.live: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        self.events: list[str] = []
        self.generation = 0

    async def _restart_beacon(self, settings: ServerSettings):
        if not self.beacon_enabled:
            return
        from omi.discovery import BeaconBroadcaster, local_ipv4
        url = settings.advertise or f"http://{local_ipv4()}:{self.http_port}"
        replacement = BeaconBroadcaster(url, port=self.beacon_port, interval=settings.beacon_interval)
        try:
            await replacement.start()
        except BaseException:
            await replacement.close()
            raise
        previous, self.broadcaster = self.broadcaster, replacement
        if previous:
            await previous.close()

    async def start(self):
        await self._restart_beacon(self.settings)
        self.started = True

    async def _apply_settings(self, settings: ServerSettings):
        if self.started and (settings.advertise != self.settings.advertise
                             or settings.beacon_interval != self.settings.beacon_interval):
            await self._restart_beacon(settings)
        self.settings = settings
        self.offline_after = settings.offline_after
        self.client.timeout = httpx.Timeout(settings.request_timeout)

    async def update_settings(self, patch: dict):
        # Validate the complete candidate before changing timers or beacon resources.
        saved = ServerSettings.model_validate({**self.saved_settings.model_dump(), **patch})
        effective = ServerSettings.model_validate({**self.settings.model_dump(), **patch})
        if self.explicit_advertise and effective.advertise != self.explicit_advertise:
            raise HTTPException(409, "La dirección fijada por --advertise/OMI_ADVERTISE prevalece; retire ese ajuste para cambiarla desde la web")
        await self._apply_settings(effective)
        self.store.put("settings", saved.model_dump())
        self.saved_settings = saved
        return self.settings.model_dump()

    async def reset_settings(self):
        await self._apply_settings(self.defaults)
        self.saved_settings = ServerSettings()

    def event(self, message: str):
        log.info(message)
        self.events.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')} INFO {message}")
        del self.events[:-300]

    def persist(self):
        self.store.put("server.agents", self.agents)

    def background(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(self._task_done)

    def _task_done(self, task):
        if not task.cancelled() and task.exception():
            log.warning("Central background task: %s", task.exception())

    def get_agent(self, serial: str, onboarded=True) -> dict:
        agent = self.agents.get(serial)
        if not agent or (onboarded and not agent.get("onboarded")):
            raise HTTPException(404, "Pi no incorporada")
        return agent

    def online(self, agent: dict) -> bool:
        return agent["serial"] in self.live and self.clock() - agent.get("last_seen", 0) < self.offline_after

    def busy(self, agent: dict) -> bool:
        pending = agent.get("pending")
        observed = agent.get("state", {}).get("operation")
        return bool(agent.get("state", {}).get("busy") or (pending and pending.get("status") not in TERMINAL)
                    or (observed and observed.get("status") not in TERMINAL))

    def view(self, agent: dict) -> dict:
        result = copy.deepcopy(agent)
        result["online"] = self.online(agent)
        result["busy"] = self.busy(agent)
        target = agent.get("state", {}).get("target")
        result["observed"] = target
        result["discrepancy"] = bool(agent.get("onboarded") and target != agent.get("expected"))
        result["connection"] = "offline" if not result["online"] else "busy" if result["busy"] else "online"
        result["import_available"] = not self.library.export() and bool(agent.get("library_digest"))
        return result

    async def remote(self, agent: dict, method: str, path: str, *, body=None, params=None):
        if not self.online(agent):
            raise HTTPException(503, "Pi offline; no se guardan órdenes pendientes")
        url = agent["endpoint"].rstrip("/") + "/api/" + path.lstrip("/")
        try:
            response = await self.client.request(method, url, json=body, params=params,
                                                 headers={"X-OMI-Control": "central"})
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"Sin respuesta de la Pi: {exc.__class__.__name__}") from exc
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise HTTPException(response.status_code, detail)
        return response

    async def replicate(self, agent: dict):
        generation = self.generation
        response = await self.remote(agent, "POST", "sync", body={"revisions": self.library.export()})
        incoming = response.json().get("revisions", [])
        # An intentionally blank central library never erases or implicitly imports a Pi.
        if generation == self.generation and (self.library.export() or agent.get("import_presets")):
            self.merge_revisions(incoming)

    async def distribute(self):
        agents = [a for a in self.agents.values() if a.get("onboarded") and self.online(a)]
        outcomes = await asyncio.gather(*(self.replicate(a) for a in agents), return_exceptions=True)
        for agent, outcome in zip(agents, outcomes):
            if isinstance(outcome, Exception):
                self.event(f"Biblioteca pendiente de sincronizar con {agent.get('name', agent['serial'])}: {outcome}")

    def _ack(self, agent: dict, record: dict, operation: dict):
        if record.get("status") in TERMINAL and operation["status"] not in TERMINAL:
            return
        record["status"] = operation["status"]
        record["operation"] = operation
        record["acknowledged"] = True
        command = record["command"]
        if command["action"] in ("apply", "standby"):
            agent["expected"] = command.get("target") if command["action"] == "apply" else None
        if operation["status"] in TERMINAL:
            record["finished_at"] = self.clock()
            if (agent.get("pending") or {}).get("id") == record["id"]:
                agent["pending"] = None
        self.store.put(f"server.command.{agent['serial']}.{record['id']}", record)

    async def recover_command(self, serial: str, command_id: str):
        agent = self.get_agent(serial)
        try:
            response = await self.remote(agent, "GET", f"operations/{quote(command_id, safe='')}")
            operation = Operation.model_validate(response.json()).model_dump(mode="json")
        except HTTPException as exc:
            if exc.status_code != 404:
                return
            async with self.lock:
                pending = agent.get("pending")
                # An explicit 404 is evidence that the agent retained no such command.
                if pending and pending["id"] == command_id:
                    pending["status"] = "interrupted"
                    pending["error"] = "La Pi no conserva esta operación; no se reenvía automáticamente"
                    self.store.put(f"server.command.{serial}.{command_id}", pending)
                    agent["last_command"] = copy.deepcopy(pending)
                    agent["pending"] = None
                    self.persist()
            return
        async with self.lock:
            pending = agent.get("pending")
            if pending and pending["id"] == command_id:
                self._ack(agent, pending, operation)
                agent["last_command"] = copy.deepcopy(pending)
                self.persist()

    async def heartbeat(self, heartbeat: Heartbeat) -> HeartbeatReply:
        data = heartbeat.model_dump(mode="json")
        parts = urlsplit(heartbeat.endpoint)
        try:
            _ = parts.port
        except ValueError as exc:
            raise HTTPException(422, "Puerto de agente inválido") from exc
        if parts.scheme != "http" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
            raise HTTPException(422, "Endpoint de agente inválido: se requiere http://host:puerto")
        async with self.lock:
            agent = self.agents.setdefault(heartbeat.serial, {"serial": heartbeat.serial, "onboarded": False,
                                                            "device_id": None, "name": "", "expected": None,
                                                            "pending": None})
            agent.update(data)
            agent["last_seen"] = self.clock()
            self.live.add(heartbeat.serial)
            pending = agent.get("pending")
            operation = data["state"].get("operation")
            if pending and operation and operation["id"] == pending["id"]:
                self._ack(agent, pending, operation)
                agent["last_command"] = copy.deepcopy(pending)
            elif pending and pending.get("status") != "sending" and agent.get("onboarded"):
                self.background(self.recover_command(heartbeat.serial, pending["id"]))
            self.persist()
            view = self.view(agent)
            return HeartbeatReply(onboarded=agent["onboarded"], device_id=agent["device_id"],
                                  name=agent["name"], expected=agent["expected"],
                                  discrepancy=view["discrepancy"], library_digest=self.library.digest())

    async def command(self, serial: str, command: Command) -> dict:
        async with self.lock:
            agent = self.get_agent(serial)
            key = f"server.command.{serial}.{command.id}"
            previous = self.store.get(key)
            payload = command.model_dump(mode="json")
            if previous:
                if {k:v for k,v in previous["command"].items() if k != "expires_at"} != {k:v for k,v in payload.items() if k != "expires_at"}:
                    raise HTTPException(409, "El ID de orden ya existe con otro contenido")
                return previous
            if not self.online(agent):
                raise HTTPException(503, "Pi offline; la orden no se ha guardado ni se ejecutará al reconectar")
            if self.busy(agent):
                raise HTTPException(409, "Pi ocupada; consulte el resultado de la operación actual")
            if command.expires_at <= self.clock():
                raise HTTPException(422, "La orden ha caducado")
            if command.action == "apply":
                if command.target is None:
                    raise HTTPException(422, "Aplicar requiere preset y revisión")
                revision = self.library.get(command.target.revision)
                if revision.deleted or revision.preset_id != command.target.preset_id:
                    raise HTTPException(422, "Revisión de preset inválida")
            record = {"id": command.id, "command": payload, "status": "sending", "sent_at": self.clock(),
                      "acknowledged": False, "operation": None, "error": None}
            agent["pending"] = record
            self.store.put(key, record)
            self.persist()
        dispatched = False
        try:
            if command.action == "apply":
                await self.replicate(agent)
            dispatched = True
            response = await self.remote(agent, "POST", "commands", body=payload)
            operation = Operation.model_validate(response.json()).model_dump(mode="json")
            if operation["id"] != command.id:
                raise ValueError("La confirmación contiene otro ID de operación")
            async with self.lock:
                self._ack(agent, record, operation)
                agent["last_command"] = copy.deepcopy(record)
                self.persist()
            self.event(f"{serial}: {command.action} [{command.id}] {record['status']}")
            return record
        except (HTTPException, ValueError) as exc:
            # A lost response may follow acceptance. Keep the lock until an explicit result.
            uncertain = dispatched and (not isinstance(exc, HTTPException) or exc.status_code >= 500)
            async with self.lock:
                if record.get("acknowledged"):
                    return record
                record["status"] = "unknown" if uncertain else "failed"
                record["error"] = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                if not uncertain:
                    agent["pending"] = None
                self.store.put(key, record)
                agent["last_command"] = copy.deepcopy(record)
                self.persist()
            self.event(f"{serial}: {command.action} [{command.id}] {record['status']}")
            return record

    def used_by(self, preset_id: str) -> list[str]:
        used = []
        for agent in self.agents.values():
            targets = [agent.get("expected"), agent.get("state", {}).get("target"), agent.get("restoring")]
            pending = agent.get("pending")
            if pending:
                targets.append(pending["command"].get("target"))
            if any(t and t.get("preset_id") == preset_id for t in targets):
                used.append(f"{agent.get('device_id') or '?'} · {agent.get('name') or agent['serial']}")
        return used

    def merge_revisions(self, revisions: list[dict]) -> dict:
        # Imported/synchronized tombstones obey the same installation-wide protection
        # as the Delete button. Validate before merge so rejection is atomic.
        known = {revision["id"] for revision in self.library.export()}
        from omi.models import Revision
        parsed = [Revision.model_validate(revision) for revision in revisions]
        for revision in parsed:
            if revision.deleted and revision.id not in known:
                used = self.used_by(revision.preset_id)
                if used:
                    raise RuntimeError("No se puede incorporar el borrado: preset activo o asignado a " + ", ".join(used))
        return self.library.merge([revision.model_dump(mode="json") for revision in parsed])

    async def close(self):
        if self.broadcaster:
            await self.broadcaster.close()
            self.broadcaster = None
        self.started = False
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.client.aclose()
        self.store.close()


def create_app(data_dir: Path, *, transport=None, offline_after=None, request_timeout=None,
               clock: Callable[[], float] = time.time, simulated=False, advertise="",
               beacon_enabled=False, beacon_port=18080, beacon_interval=None, http_port=8080, **_kwargs) -> FastAPI:
    central = Central(data_dir, transport, offline_after=offline_after,
                      request_timeout=request_timeout, clock=clock, advertise=advertise,
                      beacon_interval=beacon_interval, beacon_enabled=beacon_enabled,
                      beacon_port=beacon_port, http_port=http_port)

    @asynccontextmanager
    async def lifespan(app):
        try:
            await central.start()
            yield
        finally:
            await central.close()

    app = FastAPI(title="OMI Central", version=__version__, lifespan=lifespan)
    app.state.central = central
    app.state.store = central.store
    app.state.library = central.library

    @app.exception_handler(KeyError)
    async def missing(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(RuntimeError)
    async def conflict(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/api/status")
    async def status():
        from omi import code_revision
        try:
            update = json.loads((central.directory / "update-status.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            update = None
        return {"role": "server", "version": __version__, "simulated": simulated,
                "agents": len([a for a in central.agents.values() if a.get("onboarded")]),
                "online": sum(central.online(a) for a in central.agents.values()),
                "library_digest": central.library.digest(), "update": update, "code_revision": code_revision(),
                "beacon": {"enabled": central.beacon_enabled,
                           "url": central.broadcaster.url if central.broadcaster else "",
                           "interval": central.settings.beacon_interval}}

    @app.post("/api/heartbeat", response_model=HeartbeatReply)
    async def heartbeat(body: Heartbeat):
        return await central.heartbeat(body)

    @app.get("/api/agents")
    async def agents():
        return sorted((central.view(a) for a in central.agents.values()),
                      key=lambda a: (not a["onboarded"], a.get("device_id") or 999999, a["serial"]))

    @app.post("/api/agents/{serial}/onboard")
    async def onboard(serial: str, body: Onboard = Body(default=Onboard())):
        async with central.lock:
            agent = central.get_agent(serial, onboarded=False)
            if not agent.get("onboarded"):
                used = {a.get("device_id") for a in central.agents.values() if a.get("onboarded")}
                device_id = next(i for i in range(1, len(used) + 2) if i not in used)
                agent.update(onboarded=True, device_id=device_id,
                             name=(body.name or agent["hostname"]).strip(),
                             expected=copy.deepcopy(agent["state"].get("target")),
                             import_presets=body.import_presets)
                central.persist()
        if body.import_presets:
            response = await central.remote(agent, "GET", "presets/export")
            exported = response.json()
            central.merge_revisions(exported if isinstance(exported, list) else exported["revisions"])
        central.background(central.distribute())
        central.event(f"Pi incorporada: {serial} · {agent['device_id']} · {agent['name']}")
        return central.view(agent)

    @app.patch("/api/agents/{serial}")
    async def identity(serial: str, body: Identity):
        async with central.lock:
            agent = central.get_agent(serial)
            if body.device_id is not None and body.device_id != agent["device_id"]:
                other = next((a for a in central.agents.values() if a.get("onboarded")
                              and a.get("device_id") == body.device_id), None)
                if other:
                    other["device_id"] = agent["device_id"]
                agent["device_id"] = body.device_id
            if body.name is not None:
                if not body.name.strip() or any(ord(c) < 32 for c in body.name):
                    raise HTTPException(422, "Nombre no válido")
                agent["name"] = body.name.strip()
            central.persist()
            return central.view(agent)

    @app.delete("/api/agents/{serial}")
    async def forget(serial: str):
        async with central.lock:
            agent = central.get_agent(serial, onboarded=False)
            if central.busy(agent):
                raise HTTPException(409, "No se puede retirar una Pi con una operación sin resolver")
            del central.agents[serial]
            central.live.discard(serial)
            central.persist()
        return {"removed": serial}

    @app.post("/api/agents/{serial}/accept")
    async def accept(serial: str):
        async with central.lock:
            agent = central.get_agent(serial)
            if not central.online(agent):
                raise HTTPException(503, "No se puede aceptar un estado offline")
            if central.busy(agent):
                raise HTTPException(409, "Espere a que termine la operación")
            agent["expected"] = copy.deepcopy(agent["state"].get("target"))
            central.persist()
            return central.view(agent)

    @app.post("/api/agents/{serial}/synchronize")
    async def synchronize(serial: str):
        agent = central.get_agent(serial)
        command = Command(action="apply" if agent.get("expected") else "standby", target=agent.get("expected"))
        return await central.command(serial, command)

    @app.post("/api/agents/{serial}/commands")
    async def command(serial: str, body: Command):
        return await central.command(serial, body)

    @app.get("/api/agents/{serial}/operations/{operation_id}")
    async def operation(serial: str, operation_id: str):
        agent = central.get_agent(serial)
        await central.recover_command(serial, operation_id)
        result = central.store.get(f"server.command.{serial}.{operation_id}")
        if result:
            return result
        return (await central.remote(agent, "GET", f"operations/{quote(operation_id, safe='')}")).json()

    @app.post("/api/groups/commands")
    async def group(body: GroupCommand):
        async def one(serial):
            try:
                return {"serial": serial, "result": await central.command(serial, body.command)}
            except HTTPException as exc:
                return {"serial": serial, "skipped": True, "status_code": exc.status_code, "error": exc.detail}
        return {"results": await asyncio.gather(*(one(s) for s in dict.fromkeys(body.serials)))}

    @app.post("/api/sync")
    async def sync(body: dict):
        serial = body.get("serial", "")
        agent = central.get_agent(serial)
        if central.library.export() or agent.get("import_presets"):
            central.merge_revisions(body.get("revisions", []))
        return {"revisions": central.library.export(), "digest": central.library.digest()}

    @app.get("/api/presets")
    async def presets():
        return central.library.list()

    @app.get("/api/presets/export")
    async def export():
        return {"revisions": central.library.export(), "schema_version": 1}

    @app.get("/api/presets/revisions/{revision_id}")
    async def revision(revision_id: str):
        return central.library.get(revision_id)

    @app.post("/api/presets/import")
    async def import_presets(body: dict):
        result = central.merge_revisions(body.get("revisions", []))
        central.background(central.distribute())
        return result

    @app.post("/api/presets")
    async def save(body: SavePreset):
        revision = central.library.save(body, author="central")
        central.background(central.distribute())
        return revision

    @app.post("/api/presets/{preset_id}/resolve")
    async def resolve(preset_id: str, body: dict):
        chosen = central.library.get(body.get("revision", ""))
        if chosen.deleted and central.used_by(preset_id):
            raise RuntimeError("No se puede resolver usando el borrado: preset activo o asignado a " + ", ".join(central.used_by(preset_id)))
        revision = central.library.resolve(preset_id, body.get("revision", ""), author="central")
        central.background(central.distribute())
        return revision

    @app.delete("/api/presets/{preset_id}")
    async def delete(preset_id: str):
        revision = central.library.delete(preset_id, central.used_by(preset_id))
        central.background(central.distribute())
        return revision

    @app.get("/api/settings")
    async def get_settings():
        return central.settings.model_dump()

    @app.put("/api/settings")
    async def put_settings(body: dict):
        async with central.lock:
            return await central.update_settings(body)

    @app.get("/api/logs")
    async def logs(level: str = "", limit: int = Query(default=200, ge=1, le=1000)):
        lines = list(central.events)
        log_directory = central.directory / "logs"
        if log_directory.exists():
            for path in sorted(log_directory.glob("*.log"))[-3:]:
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - 512 * 1024))
                    lines.extend(stream.read().decode("utf-8", errors="replace").splitlines())
        if level:
            lines = [line for line in lines if level.upper() in line]
        return {"lines": lines[-limit:]}

    @app.get("/api/logs/download")
    async def log_download():
        return PlainTextResponse("\n".join((await logs(limit=1000))["lines"]),
                                 headers={"Content-Disposition": 'attachment; filename="omi-central.log"'})

    @app.get("/api/backup")
    async def backup():
        from omi.maintenance import backup as create_backup
        path = create_backup(central.store, central.directory / "backups")
        return FileResponse(path, filename=path.name, media_type="application/vnd.sqlite3")

    @app.post("/api/maintenance/{action}")
    async def maintenance(action: str, body: dict = Body(default={})):
        if action == "clear-logs":
            central.events.clear()
            directory = central.directory / "logs"
            if directory.exists():
                for path in directory.glob("*.log*"):
                    if path.is_file() and path.resolve().is_relative_to(directory.resolve()):
                        path.write_text("", encoding="utf-8")
            return {"ok": True, "action": action}
        if action == "update":
            if simulated:
                return {"status": "succeeded", "simulated": True, "detail": "Simulación: no se ha modificado la instalación"}
            from omi.maintenance import run_update
            return await run_update("server", body)
        if action not in {"reset-state", "reset-installation", "factory-reset"}:
            raise HTTPException(404, "Acción de mantenimiento desconocida")
        if body.get("confirm") not in (True, action):
            raise HTTPException(422, f"Confirme escribiendo {action}")
        async with central.lock:
            if any(central.busy(a) for a in central.agents.values()):
                raise HTTPException(409, "Hay operaciones sin resolver; recupere su resultado antes de restablecer")
            backup_file = None
            if body.get("backup", True):
                directory = central.directory / "backups"
                directory.mkdir(exist_ok=True)
                from omi.maintenance import backup as create_backup
                backup_file = create_backup(central.store, directory)
            if action == "reset-state":
                for key in central.store.items("server.command."):
                    central.store.delete(key)
                for agent in central.agents.values():
                    agent.pop("last_command", None)
                    agent["pending"] = None
                central.persist()
            else:
                central.generation += 1
                if action == "factory-reset":
                    await central.reset_settings()
                    with central.store.transaction() as connection:
                        connection.execute("DELETE FROM kv")
                        connection.execute("DELETE FROM revisions")
                    central.library = Library(central.store)
                    app.state.library = central.library
                else:
                    for key in central.store.items("server."):
                        central.store.delete(key)
                central.agents.clear()
                central.live.clear()
                central.persist()
            central.event(f"Mantenimiento: {action}")
            return {"ok": True, "action": action, "backup": str(backup_file) if backup_file else None}

    # Whitelist forwarding; do not expose arbitrary requests to discovered endpoints.
    proxy_roots = {"midi", "editors", "logs", "presets", "settings", "maintenance", "backup", "simulate"}

    @app.api_route("/api/agents/{serial}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def proxy(serial: str, path: str, request: Request):
        if path.split("/", 1)[0] not in proxy_roots or ".." in path.split("/"):
            raise HTTPException(404, "Control remoto desconocido")
        agent = central.get_agent(serial)
        raw = await request.body()
        body = json.loads(raw) if raw else None
        context_target = None
        if request.method == "POST" and path == "presets":
            # Decide the semantics from the context in which Save was accepted.
            # A restart can legitimately clear observed target before POST returns.
            before = await central.remote(agent, "GET", "status")
            context_target = before.json().get("state", {}).get("target")
        if request.method == "POST" and path in {"editors", "presets"}:
            await central.replicate(agent)
        response = await central.remote(agent, request.method, path, body=body, params=request.query_params)
        if path == "presets" and request.method == "POST":
            # Context save applies only on this Pi; mirror its resulting local assignment.
            revision = response.json()
            central.merge_revisions([revision])
            if context_target and context_target["preset_id"] == revision["preset_id"]:
                agent["expected"] = {"preset_id": revision["preset_id"], "revision": revision["id"]}
            central.persist()
            try:
                state_response = await central.remote(agent, "GET", "status")
                agent["state"] = state_response.json().get("state", agent["state"])
                central.persist()
            except HTTPException as exc:
                # Save was already acknowledged; a lost follow-up status must not
                # turn it into a failed save that the UI retries as a new revision.
                central.event(f"{serial}: preset guardado; estado posterior pendiente ({exc.detail})")
            central.background(central.distribute())
        if "application/json" in response.headers.get("content-type", ""):
            return JSONResponse(response.json(), status_code=response.status_code)
        return Response(response.content, media_type=response.headers.get("content-type", "application/octet-stream"),
                        headers={key: value for key, value in response.headers.items() if key == "content-disposition"})

    mount_web(app)
    return app
