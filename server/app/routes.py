from __future__ import annotations

import logging
from typing import Dict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import db
from .broadcast import BroadcastManager
from .devices import build_devices_payload
from .models import ConfigPayload, DeviceDesiredPayload, NetworkAckPayload, PowerRequest, ServiceRequest
from .registry import DeviceRegistry
from .settings import Settings
from .errors import api_error
from .network import NetworkManager
from .web import render_index

LOGGER = logging.getLogger("omi.server.routes")

NAV_TEMPLATE = (
    '<button class="nav-link{cls}" data-view-btn="devices">Home</button>'
    '<button class="nav-link" data-view-btn="clients">Clientes</button>'
    '<button class="nav-link" data-view-btn="services">Servicios</button>'
)

router = APIRouter()


def _context(request: Request) -> Dict[str, object]:
    return request.app.state.context


def _settings(request: Request) -> Settings:
    return request.app.state.context["settings"]


def _registry(request: Request) -> DeviceRegistry:
    return request.app.state.context["registry"]


def _manager(request: Request) -> BroadcastManager:
    return request.app.state.context["manager"]

def _network(request: Request) -> NetworkManager:
    return request.app.state.context["network"]

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    settings = _settings(request)
    host = request.url.hostname or "server"
    brand_html = f"OMI Control @ {host}".replace("&", "&amp;")
    nav_html = NAV_TEMPLATE.format(cls=" active")
    return render_index(
        settings,
        {
            "PAGE_TITLE": "OMI Control Server",
            "BRAND_HTML": brand_html,
            "NAV_LINKS": nav_html,
        },
    )


@router.get("/api/devices")
async def api_devices(request: Request):
    registry = _registry(request)
    return {"devices": build_devices_payload(registry)}


@router.get("/api/clients")
async def api_clients():
    return {"clients": db.list_devices()}


@router.post("/api/devices/{serial}/service")
async def api_set_service(serial: str, payload: ServiceRequest, request: Request):
    manager = _manager(request)
    reply = manager.request_service_change(serial, payload.service, config=payload.config)
    return reply


@router.post("/api/devices/{serial}/power")
async def api_power(serial: str, payload: PowerRequest, request: Request):
    manager = _manager(request)
    reply = manager.request_power_action(serial, payload.action)
    return reply


@router.get("/api/configs/{service_id}")
async def api_list_service_configs(service_id: str):
    LOGGER.info("API GET /api/configs/%s", service_id)
    return {"configs": db.list_configs(service_id)}


@router.get("/api/configs/{service_id}/{name}")
async def api_get_service_config(service_id: str, name: str):
    LOGGER.info("API GET /api/configs/%s/%s", service_id, name)
    cfg = db.get_config(service_id, name)
    if not cfg:
        api_error(
            404,
            "configuración no encontrada",
            logger=LOGGER,
            context={"service_id": service_id, "name": name},
        )
    return cfg


@router.post("/api/configs/{service_id}")
async def api_save_service_config(service_id: str, payload: ConfigPayload):
    LOGGER.info(
        "API POST /api/configs/%s → name=%s overwrite=%s serial=%s",
        service_id,
        payload.name,
        payload.overwrite,
        payload.serial,
    )
    existing = db.get_config(service_id, payload.name)
    if existing and not payload.overwrite:
        api_error(
            409,
            "ya existe una configuración con ese nombre",
            logger=LOGGER,
            context={"service_id": service_id, "name": payload.name},
        )
    db.save_config(service_id, payload.name, payload.data, payload.serial)
    return {"ok": True}


@router.delete("/api/configs/{service_id}/{name}")
async def api_delete_service_config(service_id: str, name: str):
    LOGGER.info("API DELETE /api/configs/%s/%s", service_id, name)
    db.delete_config(service_id, name)
    return {"ok": True}


@router.put("/api/devices/{serial}")
async def api_update_device(serial: str, payload: DeviceDesiredPayload, request: Request):
    LOGGER.info(
        "API PUT /api/devices/%s → desired_service=%s desired_config=%s",
        serial,
        payload.desired_service,
        payload.desired_config,
    )
    profile = payload.network.dict(exclude_none=True) if payload.network else None
    db.upsert_device(
        serial,
        desired_service=payload.desired_service,
        desired_config=payload.desired_config,
        network_profile=profile,
    )
    network = _network(request)
    network.record_desired_profile(serial, profile)
    return {"ok": True}


@router.post("/api/devices/{serial}/network/ack")
async def api_network_ack(serial: str, payload: NetworkAckPayload, request: Request):
    LOGGER.info(
        "API POST /api/devices/%s/network/ack → applied=%s",
        serial,
        payload.applied,
    )
    network = _network(request)
    network.acknowledge_profile(
        serial,
        {"applied": payload.applied, "message": payload.message},
    )
    return {"ok": True}


@router.delete("/api/devices/{serial}")
async def api_delete_device(serial: str, request: Request):
    LOGGER.info("API DELETE /api/devices/%s", serial)
    db.delete_device(serial)
    registry = _registry(request)
    manager = _manager(request)
    for dev in registry.list_devices():
        if not dev.get("online"):
            continue
        stored = registry.db_device(dev.get("serial", ""))
        if not stored:
            continue
        desired_index = stored.get("device_index")
        if desired_index is None:
            continue
        if dev.get("index") != desired_index:
            try:
                manager.request_index_update(dev.get("serial"), desired_index)
            except Exception as exc:
                LOGGER.error(
                    "No se pudo actualizar índice de %s tras borrar dispositivo: %s",
                    dev.get("serial"),
                    exc,
                )
    return {"ok": True}
