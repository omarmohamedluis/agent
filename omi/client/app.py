"""Local human interface and permanent central control share one coordinator."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import ValidationError

from omi.client.runtime import ClientRuntime
from omi.logging import recent_logs
from omi.maintenance import backup, clear_logs, factory_reset, reset_state
from omi.models import Command, EditorRequest, SavePreset


def create_app(runtime: ClientRuntime, *, manage_lifecycle: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        if manage_lifecycle:
            await runtime.start()
        try:
            yield
        finally:
            if manage_lifecycle:
                await runtime.close()

    app = FastAPI(title="OMI Agent v3", lifespan=lifespan)
    app.state.runtime = runtime

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(RuntimeError)
    async def conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    def access(request: Request, *, mutation: bool = False) -> None:
        if request.headers.get("X-OMI-Control") != "central" and not runtime.local_control:
            raise HTTPException(423, "Central control is available; use the server interface")
        if mutation and runtime.booting:
            raise HTTPException(409, "Agent is restoring its local state")

    def idle() -> None:
        if runtime.coordinator.busy or runtime.coordinator.editor:
            raise HTTPException(409, "Close the editor and wait for operations to finish")

    @app.get("/api/status")
    async def status():
        return runtime.status()

    @app.post("/api/commands")
    async def command(value: Command, request: Request):
        access(request, mutation=True)
        return runtime.coordinator.submit(value)

    @app.get("/api/operations/{identifier}")
    async def operation(identifier: str):
        return runtime.coordinator.operation(identifier)

    @app.post("/api/sync")
    async def synchronize(value: dict):
        # Control channel: independent of human UI availability and active service.
        runtime.library.merge(value.get("revisions", []))
        return {"revisions": runtime.library.export(), "digest": runtime.library.digest()}

    @app.get("/api/presets")
    async def presets(request: Request):
        access(request)
        return runtime.library.list()

    @app.get("/api/presets/export")
    async def export(request: Request):
        access(request)
        return {"schema_version": 1, "revisions": runtime.library.export()}

    @app.post("/api/presets/import")
    async def import_presets(value: dict, request: Request):
        access(request, mutation=True)
        return runtime.library.merge(value["revisions"])

    @app.post("/api/presets")
    async def save(value: SavePreset, request: Request):
        access(request, mutation=True)
        return runtime.coordinator.save_preset(value, runtime.serial)

    @app.get("/api/presets/revisions/{revision}")
    async def revision(revision: str, request: Request):
        access(request)
        return runtime.library.get(revision)

    @app.post("/api/presets/{preset_id}/resolve")
    async def resolve(preset_id: str, value: dict, request: Request):
        access(request, mutation=True)
        idle()
        return runtime.library.resolve(preset_id, value["revision"], runtime.serial)

    @app.delete("/api/presets/{preset_id}")
    async def delete(preset_id: str, request: Request):
        access(request, mutation=True)
        idle()
        used_by = []
        current = runtime.coordinator.state.target
        if current and current.preset_id == preset_id:
            used_by.append(runtime.serial + " (active)")
        if runtime.server.expected and runtime.server.expected.preset_id == preset_id:
            used_by.append(runtime.serial + " (assigned by server)")
        return runtime.library.delete(preset_id, used_by)

    @app.get("/api/midi/inputs")
    async def inputs(request: Request):
        access(request)
        return {"inputs": await runtime.host.inputs()}

    @app.post("/api/editors")
    async def open_editor(value: EditorRequest, request: Request):
        access(request, mutation=True)
        return await runtime.coordinator.open_editor(value, local=request.headers.get("X-OMI-Control") != "central")

    @app.get("/api/editors/{identifier}")
    async def editor(identifier: str, request: Request):
        access(request)
        return runtime.coordinator.editor_status(identifier)

    @app.post("/api/editors/{identifier}/renew")
    async def renew(identifier: str, request: Request):
        access(request)
        return runtime.coordinator.editor_status(identifier, renew=True)

    @app.post("/api/editors/{identifier}/learn")
    async def learn(identifier: str, request: Request):
        access(request)
        return await runtime.coordinator.arm_learn(identifier)

    @app.delete("/api/editors/{identifier}")
    async def close_editor(identifier: str, request: Request):
        access(request)
        await runtime.coordinator.close_editor(identifier)
        return {"closed": True}

    @app.post("/api/simulate/midi")
    async def simulate_midi(value: dict):
        if not runtime.simulated:
            raise HTTPException(404, "Simulation is disabled")
        await runtime.host.inject_midi(value)
        return {"accepted": True}

    @app.get("/api/logs")
    async def logs(request: Request, level: str = "", limit: int = 200):
        access(request)
        return {"lines": recent_logs(runtime.data_dir, limit=limit, level=level)}

    @app.get("/api/logs/download")
    async def download_logs(request: Request):
        access(request)
        return PlainTextResponse("\n".join(recent_logs(runtime.data_dir, limit=2000)),
                                 headers={"Content-Disposition": 'attachment; filename="omi-recent.log"'})

    @app.get("/api/settings")
    async def settings(request: Request):
        access(request)
        return runtime.settings.model_dump()

    @app.put("/api/settings")
    async def save_settings(value: dict, request: Request):
        access(request, mutation=True)
        return runtime.update_settings(value)

    @app.get("/api/backup")
    async def export_backup(request: Request):
        access(request)
        path = backup(runtime.store, runtime.data_dir / "backups")
        return FileResponse(path, filename=path.name, media_type="application/vnd.sqlite3")

    @app.post("/api/maintenance/{action}")
    async def maintain(action: str, value: dict, request: Request):
        access(request, mutation=True)
        idle()
        if action == "clear-logs":
            clear_logs(runtime.data_dir)
        elif action == "reset-state":
            reset_state(runtime.store)
        elif action == "factory-reset":
            if value.get("confirm") is not True:
                raise ValueError("Explicit confirmation is required")
            saved = backup(runtime.store, runtime.data_dir / "backups") if value.get("backup", True) else None
            operation = runtime.coordinator.submit(Command(action="standby"))
            await runtime.coordinator.task
            if runtime.coordinator.operation(operation.id).status != "succeeded":
                raise RuntimeError("Cleanup failed; data has been preserved")
            factory_reset(runtime.store, confirm=True)
            await runtime.reset_settings()
            return {"ok": True, "backup": str(saved) if saved else None}
        elif action == "update":
            return runtime.coordinator.submit(Command(action="update", arguments=value))
        else:
            raise HTTPException(404, "Unknown maintenance action")
        return {"ok": True}

    from omi.web import mount_web
    mount_web(app, available=lambda: runtime.local_control)
    return app
