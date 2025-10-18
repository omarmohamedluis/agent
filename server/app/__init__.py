from __future__ import annotations

import logging
from typing import Dict

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ..db import init_db
from .broadcast import BroadcastManager
from .registry import DeviceRegistry
from .routes import router
from .settings import Settings
from .network import NetworkManager

LOGGER = logging.getLogger("omi.server.app")


def create_app() -> FastAPI:
    settings = Settings()
    registry = DeviceRegistry(settings.status_ttl)
    manager = BroadcastManager(settings, registry)
    network = NetworkManager()

    init_db()

    app = FastAPI(title="OMI Control Server", version="0.2")
    app.state.context = _build_context(settings, registry, manager, network)

    if settings.static_root.exists():
        app.mount("/static", StaticFiles(directory=settings.static_root), name="static")

    app.include_router(router)

    @app.on_event("startup")
    async def _startup() -> None:
        manager.start()
        LOGGER.info("Broadcast manager iniciado")

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        manager.stop()
        LOGGER.info("Broadcast manager detenido")

    return app


def _build_context(
    settings: Settings,
    registry: DeviceRegistry,
    manager: BroadcastManager,
    network: NetworkManager,
) -> Dict[str, object]:
    return {
        "settings": settings,
        "registry": registry,
        "manager": manager,
        "network": network,
    }
