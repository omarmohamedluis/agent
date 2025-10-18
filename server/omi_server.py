import json
import socket
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Literal

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from db import (
    delete_config,
    delete_device,
    get_config,
    get_device,
    init_db,
    list_configs,
    list_devices,
    save_config,
    upsert_device,
    ensure_device_index,
)
from logger import get_server_logger

HTTP_PORT = 6969
BCAST_IP = "255.255.255.255"
BCAST_PORT = 37020
REPLY_PORT = 37021
DISCOVER_INTERVAL_S = 3.0
STATUS_TTL_S = 6.0

logger = get_server_logger()
init_db()


def build_devices_payload() -> List[Dict[str, Any]]:
    runtime_devices = registry.list_devices()
    desired_devices = {d["serial"]: d for d in list_devices()}
    result: List[Dict[str, Any]] = []
    seen = set()

    for dev in runtime_devices:
        serial = dev.get("serial")
        extra = desired_devices.get(serial or "") if serial else None
        payload = dict(dev)
        last_seen = payload.get("last_seen")
        if isinstance(last_seen, (int, float)):
            try:
                payload["last_seen"] = datetime.fromtimestamp(last_seen).isoformat()
            except Exception:
                payload["last_seen"] = None
        if extra:
            payload["desired_service"] = extra.get("desired_service")
            payload["desired_config"] = extra.get("desired_config")
            if extra.get("device_index") is not None:
                payload["index"] = extra.get("device_index")
                payload["device_index"] = extra.get("device_index")
        result.append(payload)
        if serial:
            seen.add(serial)

    for serial, extra in desired_devices.items():
        if serial in seen:
            continue
        result.append(
            {
                "serial": serial,
                "host": extra.get("host"),
                "services": [],
                "available_services": [],
                "heartbeat": {},
                "logical_service": None,
                "last_seen": extra.get("updated_at"),
                "ip": None,
                "online": False,
                "desired_service": extra.get("desired_service"),
                "desired_config": extra.get("desired_config"),
                "service_state": None,
                "index": extra.get("device_index"),
                "device_index": extra.get("device_index"),
            }
        )

    return result


class PendingRequest:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.payload: Optional[Dict[str, Any]] = None

    def update(self, payload: Dict[str, Any]) -> None:
        # Guardamos último estado para poder inspeccionarlo si se cancela
        self.payload = payload

    def set(self, payload: Dict[str, Any]) -> None:
        self.payload = payload
        self.event.set()

    def wait(self, timeout: float) -> Dict[str, Any]:
        if not self.event.wait(timeout):
            raise TimeoutError
        return self.payload or {}


class DeviceRegistry:
    def __init__(self) -> None:
        self._devices: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def update_from_status(self, payload: Dict[str, Any], addr) -> tuple[Optional[int], Optional[int]]:
        serial = payload.get("serial")
        if not serial:
            return None, None
        assigned_index = ensure_device_index(serial)
        info = {
            "serial": serial,
            "host": payload.get("host"),
            "name": payload.get("name"),
            "index": assigned_index,
            "version": payload.get("version"),
            "services": payload.get("services", []),
            "available_services": payload.get("available_services", []),
            "heartbeat": payload.get("heartbeat", {}),
            "service_state": payload.get("service_state"),
            "logical_service": payload.get("logical_service"),
            "last_seen": time.time(),
            "ip": addr[0],
        }
        with self._lock:
            self._devices[serial] = info
        upsert_device(serial, host=info.get("host"), device_index=assigned_index)
        return assigned_index, payload.get("index")

    def update_services(
        self,
        serial: str,
        services: Optional[List[Dict[str, Any]]],
        service_state: Optional[Dict[str, Any]] = None,
        *,
        transition: Optional[bool] = None,
        progress: Optional[int] = None,
        stage: Optional[str] = None,
    ) -> None:
        if services is None and service_state is None and transition is None:
            return
        with self._lock:
            if serial in self._devices:
                if services is not None:
                    self._devices[serial]["services"] = services
                if service_state is not None:
                    self._devices[serial]["service_state"] = service_state
                elif transition is not None or progress is not None or stage is not None:
                    state = dict(self._devices[serial].get("service_state") or {})
                    if transition is not None:
                        state["transition"] = bool(transition)
                    if progress is not None:
                        state["progress"] = progress
                    if stage is not None:
                        state["stage"] = stage
                    self._devices[serial]["service_state"] = state
                self._devices[serial]["last_seen"] = time.time()

    def update_index(self, serial: str, index: Optional[int]) -> None:
        if index is None:
            return
        with self._lock:
            if serial in self._devices:
                self._devices[serial]["index"] = int(index)

    def list_devices(self) -> List[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            devices = []
            for dev in self._devices.values():
                copy_dev = dict(dev)
                copy_dev["online"] = (now - dev.get("last_seen", 0.0)) < STATUS_TTL_S
                devices.append(copy_dev)
            return devices

    def get_device(self, serial: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            dev = self._devices.get(serial)
            return dict(dev) if dev else None


class BroadcastManager:
    def __init__(self, registry: DeviceRegistry) -> None:
        self.registry = registry
        self.stop_evt = threading.Event()
        self.broadcast_thread: Optional[threading.Thread] = None
        self.listen_thread: Optional[threading.Thread] = None
        self.command_socket: Optional[socket.socket] = None
        self.pending: Dict[str, PendingRequest] = {}
        self.pending_lock = threading.Lock()
        self.pending_index: set[str] = set()

    def start(self) -> None:
        if self.broadcast_thread and self.broadcast_thread.is_alive():
            return
        self.stop_evt.clear()
        self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.command_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.command_socket.bind(("", 0))

        self.broadcast_thread = threading.Thread(target=self._broadcast_loop, name="omi-broadcast", daemon=True)
        self.listen_thread = threading.Thread(target=self._listen_loop, name="omi-listen", daemon=True)
        self.broadcast_thread.start()
        self.listen_thread.start()

    def stop(self) -> None:
        self.stop_evt.set()
        if self.broadcast_thread:
            self.broadcast_thread.join(timeout=1.5)
        if self.listen_thread:
            self.listen_thread.join(timeout=1.5)
        if self.command_socket:
            try:
                self.command_socket.close()
            except Exception:
                pass
        with self.pending_lock:
            for pending in self.pending.values():
                pending.set({"ok": False, "error": "shutdown"})
            self.pending.clear()

    def _broadcast_loop(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            while not self.stop_evt.is_set():
                payload = {
                    "type": "DISCOVER",
                    "server_ip": self._local_ip(),
                    "reply_port": REPLY_PORT,
                    "http_port": HTTP_PORT,
                    "ts": time.time(),
                }
                try:
                    s.sendto(json.dumps(payload).encode("utf-8"), (BCAST_IP, BCAST_PORT))
                    logger.debug("Broadcast DISCOVER → %s:%s", BCAST_IP, BCAST_PORT)
                except Exception as exc:
                    logger.error("Error enviando broadcast: %s", exc)
                for _ in range(int(DISCOVER_INTERVAL_S * 10)):
                    if self.stop_evt.is_set():
                        break
                    time.sleep(0.1)
        finally:
            s.close()

    def _listen_loop(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", REPLY_PORT))
        s.settimeout(0.5)
        try:
            while not self.stop_evt.is_set():
                try:
                    data, addr = s.recvfrom(4096)
                except socket.timeout:
                    continue
                except Exception as exc:
                    logger.error("Error de socket en listener: %s", exc)
                    continue

                try:
                    payload = json.loads(data.decode("utf-8", "ignore"))
                except Exception:
                    logger.warning("JSON inválido recibido: %r", data)
                    continue

                msg_type = payload.get("type")

                if msg_type == "AGENT_STATUS":
                    assigned_index, reported_index = self.registry.update_from_status(payload, addr)
                    serial = payload.get("serial") or addr[0]
                    logger.info("Estado recibido de %s", serial)
                    if assigned_index is not None and payload.get("serial"):
                        if assigned_index != reported_index:
                            try:
                                self.request_index_update(payload["serial"], assigned_index)
                            except Exception as exc:
                                logger.error("No se pudo actualizar índice de %s: %s", payload["serial"], exc)
                elif msg_type == "SERVICE_ACK":
                    request_id = payload.get("request_id")
                    transition = bool(payload.get("transition"))
                    serial = payload.get("serial")
                    stage = payload.get("stage")
                    ok_flag = payload.get("ok")
                    progress = payload.get("progress")
                    if request_id:
                        if transition:
                            with self.pending_lock:
                                pending = self.pending.get(request_id)
                            # no set() until transición finalice
                            if pending:
                                pending.update(payload)
                        else:
                            with self.pending_lock:
                                pending = self.pending.pop(request_id, None)
                            if pending:
                                pending.set(payload)
                    if serial:
                        self.registry.update_services(
                            serial,
                            payload.get("services"),
                            payload.get("service_state"),
                            transition=transition,
                            progress=progress,
                            stage=stage,
                        )
                        logger.info(
                            "ACK de servicio recibido de %s (ok=%s, transition=%s, stage=%s)",
                            serial,
                            ok_flag,
                            transition,
                            stage,
                        )
                elif msg_type == "POWER_ACK":
                    request_id = payload.get("request_id")
                    if request_id:
                        with self.pending_lock:
                            pending = self.pending.pop(request_id, None)
                        if pending:
                            pending.set(payload)
                    serial = payload.get("serial")
                    if serial:
                        logger.info(
                            "ACK de energía (%s) recibido de %s (ok=%s)",
                            payload.get("action"),
                            serial,
                            payload.get("ok"),
                        )
                elif msg_type == "INDEX_ACK":
                    request_id = payload.get("request_id")
                    if request_id:
                        with self.pending_lock:
                            pending = self.pending.pop(request_id, None)
                        if pending:
                            pending.set(payload)
                    serial = payload.get("serial")
                    if serial:
                        with self.pending_lock:
                            self.pending_index.discard(serial)
                        self.registry.update_index(serial, payload.get("index"))
                        logger.info(
                            "ACK de índice recibido de %s (index=%s, ok=%s)",
                            serial,
                            payload.get("index"),
                            payload.get("ok"),
                        )
                else:
                    logger.debug("Mensaje desconocido de %s: %s", addr[0], payload)
        finally:
            s.close()

    def _local_ip(self) -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"
        finally:
            s.close()

    def request_service_change(self, serial: str, service: str, *, config: Optional[str] = None, timeout: float = 25.0) -> Dict[str, Any]:
        device = self.registry.get_device(serial)
        if not device:
            raise ValueError("Dispositivo desconocido")
        if not device.get("ip"):
            raise ValueError("No se conoce la IP del dispositivo")

        request_id = str(uuid.uuid4())
        message = {
            "type": "SET_SERVICE",
            "service": service,
            "request_id": request_id,
            "reply_port": REPLY_PORT,
        }
        if config:
            message["config"] = config

        pending = PendingRequest()
        with self.pending_lock:
            self.pending[request_id] = pending

        try:
            if not self.command_socket:
                raise RuntimeError("Socket de comando no disponible")
            self.command_socket.sendto(json.dumps(message).encode("utf-8"), (device["ip"], BCAST_PORT))
            logger.info("Comando SET_SERVICE → %s (%s)", serial, service)
        except Exception as exc:
            with self.pending_lock:
                self.pending.pop(request_id, None)
            raise RuntimeError(f"Error enviando comando: {exc}") from exc

        try:
            reply = pending.wait(timeout)
        except TimeoutError:
            with self.pending_lock:
                self.pending.pop(request_id, None)
            raise TimeoutError("El agente no respondió al cambio de servicio")

        return reply

    def request_power_action(self, serial: str, action: str, timeout: float = 10.0) -> Dict[str, Any]:
        action = (action or "").lower()
        if action not in {"shutdown", "reboot"}:
            raise ValueError("Acción de energía no soportada")

        device = self.registry.get_device(serial)
        if not device:
            raise ValueError("Dispositivo desconocido")
        if not device.get("ip"):
            raise ValueError("No se conoce la IP del dispositivo")

        request_id = str(uuid.uuid4())
        message = {
            "type": "POWER",
            "action": action,
            "request_id": request_id,
            "reply_port": REPLY_PORT,
        }

        pending = PendingRequest()
        with self.pending_lock:
            self.pending[request_id] = pending

        try:
            if not self.command_socket:
                raise RuntimeError("Socket de comando no disponible")
            self.command_socket.sendto(json.dumps(message).encode("utf-8"), (device["ip"], BCAST_PORT))
            logger.info("Comando POWER(%s) → %s", action, serial)
        except Exception as exc:
            with self.pending_lock:
                self.pending.pop(request_id, None)
            raise RuntimeError(f"Error enviando comando: {exc}") from exc

        try:
            reply = pending.wait(timeout)
        except TimeoutError:
            with self.pending_lock:
                self.pending.pop(request_id, None)
            raise TimeoutError("El agente no confirmó la orden de energía")

        return reply or {"ok": False, "error": "sin respuesta"}

    def request_index_update(self, serial: str, index: int, timeout: float = 10.0) -> Dict[str, Any]:
        device = self.registry.get_device(serial)
        if not device:
            raise ValueError("Dispositivo desconocido")
        if not device.get("ip"):
            raise ValueError("No se conoce la IP del dispositivo")

        with self.pending_lock:
            if serial in self.pending_index:
                return {"ok": True, "pending": True}

        request_id = str(uuid.uuid4())
        message = {
            "type": "SET_INDEX",
            "index": int(index),
            "request_id": request_id,
            "reply_port": REPLY_PORT,
        }

        pending = PendingRequest()
        with self.pending_lock:
            self.pending[request_id] = pending
            self.pending_index.add(serial)

        try:
            if not self.command_socket:
                raise RuntimeError("Socket de comando no disponible")
            self.command_socket.sendto(json.dumps(message).encode("utf-8"), (device["ip"], BCAST_PORT))
            logger.info("Comando SET_INDEX(%s) → %s", index, serial)
        except Exception as exc:
            with self.pending_lock:
                self.pending.pop(request_id, None)
                self.pending_index.discard(serial)
            raise RuntimeError(f"Error enviando comando: {exc}") from exc

        try:
            reply = pending.wait(timeout)
        except TimeoutError:
            with self.pending_lock:
                self.pending.pop(request_id, None)
                self.pending_index.discard(serial)
            raise TimeoutError("El agente no confirmó la actualización de índice")

        with self.pending_lock:
            self.pending_index.discard(serial)
        return reply or {"ok": False, "error": "sin respuesta"}


registry = DeviceRegistry()
manager = BroadcastManager(registry)


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.start()
    logger.info("Gestor de broadcast iniciado")
    try:
        yield
    finally:
        manager.stop()
        logger.info("Gestor de broadcast detenido")


app = FastAPI(title="OMI Control Server", version="0.1", lifespan=lifespan)


class ServiceRequest(BaseModel):
    service: str
    config: Optional[str] = None


class PowerRequest(BaseModel):
    action: Literal["shutdown", "reboot"]


class ConfigPayload(BaseModel):
    name: str
    data: Dict[str, Any]
    serial: Optional[str] = None
    overwrite: bool = False


class DeviceDesiredPayload(BaseModel):
    desired_service: Optional[str] = None
    desired_config: Optional[str] = None


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(HTML_PAGE)


@app.get("/api/devices")
async def api_devices() -> Dict[str, Any]:
    logger.info("API GET /api/devices")
    return {"devices": build_devices_payload()}


@app.get("/api/clients")
async def api_clients() -> Dict[str, Any]:
    logger.info("API GET /api/clients")
    return {"clients": list_devices()}


@app.post("/api/devices/{serial}/service")
async def api_set_service(serial: str, payload: ServiceRequest) -> Dict[str, Any]:
    logger.info("API POST /api/devices/%s/service → service=%s config=%s", serial, payload.service, payload.config)
    try:
        reply = manager.request_service_change(serial, payload.service, config=payload.config)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not reply.get("ok"):
        logger.warning("Cambio de servicio en %s falló: %s", serial, reply.get("error"))
        raise HTTPException(status_code=400, detail=reply.get("error") or "error desconocido")

    logger.info("Servicio en %s confirmado como '%s'", serial, reply.get("service"))
    upsert_device(serial, desired_service=payload.service, desired_config=payload.config)
    return reply


@app.post("/api/devices/{serial}/power")
async def api_power_action(serial: str, payload: PowerRequest) -> Dict[str, Any]:
    logger.info("API POST /api/devices/%s/power → action=%s", serial, payload.action)
    try:
        reply = manager.request_power_action(serial, payload.action)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not reply.get("ok"):
        raise HTTPException(status_code=400, detail=reply.get("error") or "error desconocido")

    return reply


@app.get("/api/configs/{service_id}")
async def api_list_service_configs(service_id: str) -> Dict[str, Any]:
    logger.info("API GET /api/configs/%s", service_id)
    return {"configs": list_configs(service_id)}


@app.get("/api/configs/{service_id}/{name}")
async def api_get_service_config(service_id: str, name: str) -> Dict[str, Any]:
    logger.info("API GET /api/configs/%s/%s", service_id, name)
    cfg = get_config(service_id, name)
    if not cfg:
        raise HTTPException(status_code=404, detail="configuración no encontrada")
    return cfg


@app.post("/api/configs/{service_id}")
async def api_save_service_config(service_id: str, payload: ConfigPayload) -> Dict[str, Any]:
    logger.info("API POST /api/configs/%s → name=%s overwrite=%s serial=%s", service_id, payload.name, payload.overwrite, payload.serial)
    existing = get_config(service_id, payload.name)
    if existing and not payload.overwrite:
        raise HTTPException(status_code=409, detail="ya existe una configuración con ese nombre")
    save_config(service_id, payload.name, payload.data, payload.serial)
    return {"ok": True}


@app.delete("/api/configs/{service_id}/{name}")
async def api_delete_service_config(service_id: str, name: str) -> Dict[str, Any]:
    logger.info("API DELETE /api/configs/%s/%s", service_id, name)
    delete_config(service_id, name)
    return {"ok": True}


@app.put("/api/devices/{serial}")
async def api_update_device(serial: str, payload: DeviceDesiredPayload) -> Dict[str, Any]:
    logger.info("API PUT /api/devices/%s → desired_service=%s desired_config=%s", serial, payload.desired_service, payload.desired_config)
    upsert_device(serial, desired_service=payload.desired_service, desired_config=payload.desired_config)
    return {"ok": True}


@app.delete("/api/devices/{serial}")
async def api_delete_device(serial: str) -> Dict[str, Any]:
    logger.info("API DELETE /api/devices/%s", serial)
    delete_device(serial)
    for dev in registry.list_devices():
        if not dev.get("online"):
            continue
        stored = get_device(dev.get("serial", ""))
        if not stored:
            continue
        desired_index = stored.get("device_index")
        if desired_index is None:
            continue
        if dev.get("index") != desired_index:
            try:
                manager.request_index_update(dev.get("serial"), desired_index)
            except Exception as exc:
                logger.error(
                    "No se pudo actualizar índice de %s tras borrar dispositivo: %s",
                    dev.get("serial"),
                    exc,
                )
    return {"ok": True}


HTML_PATH = Path(__file__).resolve().parent / "web" / "index.html"
HTML_PAGE = HTML_PATH.read_text(encoding="utf-8")

JS_APP = """
(function(){
  var configCache = {};
  var currentDevices = [];
  var currentClients = [];

  function escapeHtml(str){
    var map = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
    var value = (str === undefined || str === null) ? '' : String(str);
    return value.replace(/[&<>"']/g, function(c){ return map[c] || c; });
  }

  function toArray(list){ return Array.prototype.slice.call(list || []); }

  function formatDate(value){
    if(!value) return '—';
    var date = new Date(value);
    if(isNaN(date.getTime())) return escapeHtml(value);
    return date.toLocaleString();
  }

  function getJson(url){
    return fetch(url).then(function(res){
      if(!res.ok) throw new Error(url + ' => ' + res.status);
      return res.json();
    });
  }

  function fetchDevices(){
    return getJson('/api/devices').then(function(data){ return data.devices || []; });
  }

  function fetchClients(){
    return getJson('/api/clients').then(function(data){ return data.clients || []; });
  }

  function ensureConfigs(service, force){
    if(!service || service === 'standby') return Promise.resolve([]);
    if(!force && configCache[service]) return Promise.resolve(configCache[service]);
    return getJson('/api/configs/' + encodeURIComponent(service)).then(function(data){
      configCache[service] = data.configs || [];
      return configCache[service];
    });
  }

  function renderConfigOptions(service, active){
    var configs = configCache[service] || [];
    var html = '<option value="">(actual)</option>';
    configs.forEach(function(cfg){
      var selected = cfg.name === active ? 'selected' : '';
      html += '<option value="' + escapeHtml(cfg.name) + '" ' + selected + '>' + escapeHtml(cfg.name) + '</option>';
    });
    return html;
  }

  function renderDevice(dev){
    var online = !!dev.online;
    var state = dev.service_state || {};
    var heartbeat = dev.heartbeat || {};
    var services = dev.services || [];
    var available = dev.available_services || [];
    var activeEntry = services.find(function(s){ return s.enabled; });
    var active = state.expected || (activeEntry ? activeEntry.name : 'standby');
    var transition = !!state.transition;
    var progressValue = (typeof state.progress === 'number') ? Math.max(0, Math.min(100, Number(state.progress))) : null;
    var stageText = state.stage || (transition ? 'Sincronizando' : '');
    if(stageText){ stageText = stageText.charAt(0).toUpperCase() + stageText.slice(1); }
    var ledClass = 'status-led ' + (transition ? 'syncing' : (online ? 'online' : 'offline'));
    var statusLabel = transition ? 'Synking' : (online ? 'Online' : 'Offline');
    var cpu = (heartbeat.cpu != null) ? Number(heartbeat.cpu).toFixed(0) + '%' : '--';
    var temp = (heartbeat.temp != null) ? Number(heartbeat.temp).toFixed(0) + '°C' : '--';
    var serviceReturn = state.returncode != null ? state.returncode : '—';
    var serviceError = state.error || state.last_error || '';
    var serviceConfig = state.config_name || '—';
    var webUrl = state.web_url || '';
    var desiredService = dev.desired_service || '—';
    var desiredConfig = dev.desired_config || '—';
    var ip = dev.ip || '-';
    var lastSeen = formatDate(dev.last_seen);
    var indexLabel = (dev.index !== undefined && dev.index !== null) ? ('#' + dev.index) : '#--';
    var nameHeader = '<span class="index-label">' + escapeHtml(indexLabel) + '</span>' + escapeHtml(dev.host || dev.serial || 'Agente');
    var availableOptions = available.map(function(name){
      return '<option value="' + escapeHtml(name) + '" ' + (name===active ? 'selected' : '') + '>' + escapeHtml(name) + '</option>';
    }).join('');
    var configsHtml = (active !== 'standby')
      ? '<select class="config-select" data-config-for="' + escapeHtml(dev.serial || '') + '" data-active-config="' + escapeHtml(state.config_name || '') + '" ' + (!online || transition ? 'disabled' : '') + '>' + renderConfigOptions(active, state.config_name) + '</select>'
      : '<div class="small">Sin opciones de configuración.</div>';
    var configBtn = webUrl
      ? '<button class="btn" data-config-url="' + escapeHtml(webUrl) + '" data-config-title="' + escapeHtml(dev.host || dev.serial || 'Configuración') + '" ' + (!online || transition ? 'disabled' : '') + '>Configurar</button>'
      : '<button class="btn" disabled>Configurar</button>';
    var powerButtons = '<div class="card-actions">' +
      '<button class="btn warning" data-power="reboot" data-serial="' + escapeHtml(dev.serial || '') + '" ' + (!online || transition ? 'disabled' : '') + '>Reiniciar</button>' +
      '<button class="btn danger-solid" data-power="shutdown" data-serial="' + escapeHtml(dev.serial || '') + '" ' + (!online || transition ? 'disabled' : '') + '>Apagar</button>' +
    '</div>';
    var transitionHtml = '—';
    if(transition){
      var pct = (progressValue != null ? progressValue : 0);
      transitionHtml = '<div>' + escapeHtml(stageText || 'Sincronizando') + '</div>' +
        '<div class="progress' + (pct >= 100 ? ' done' : '') + '"><div class="progress-inner" style="width:' + pct + '%;"></div></div>';
    } else if(stageText){
      transitionHtml = escapeHtml(stageText);
    }

    return (
      '<div class="card" data-serial="' + escapeHtml(dev.serial || '') + '">' +
      '<div class="card-headline"><h2><span class="' + ledClass + '"></span>' + nameHeader + (online ? '' : ' <span class="tag">Offline</span>') + '</h2>' + powerButtons + '</div>' +
      '<div class="small">Serial: ' + escapeHtml(dev.serial || '?') + '</div>' +
      '<div class="small">IP: ' + escapeHtml(ip) + '</div>' +
      '<div class="small">Último contacto: ' + escapeHtml(lastSeen) + '</div>' +
      '<table class="table">' +
        '<tr><th>Estado</th><td class="' + (online ? 'status-ok' : 'status-bad') + '">' + escapeHtml(statusLabel) + '</td></tr>' +
        '<tr><th>Transición</th><td>' + transitionHtml + '</td></tr>' +
        '<tr><th>Servicio activo</th><td>' + escapeHtml(active) + '</td></tr>' +
        '<tr><th>Config actual</th><td>' + escapeHtml(serviceConfig) + '</td></tr>' +
        '<tr><th>Return code</th><td>' + escapeHtml(serviceReturn) + '</td></tr>' +
        '<tr><th>Error servicio</th><td>' + (serviceError ? escapeHtml(serviceError) : '—') + '</td></tr>' +
        '<tr><th>CPU</th><td>' + cpu + '</td></tr>' +
        '<tr><th>Temperatura</th><td>' + temp + '</td></tr>' +
        '<tr><th>Deseado</th><td>' + escapeHtml(desiredService) + ' / ' + escapeHtml(desiredConfig) + '</td></tr>' +
        '<tr><th>Servicio</th><td>' +
          '<select data-service-select data-serial="' + escapeHtml(dev.serial || '') + '" data-active-service="' + escapeHtml(active) + '" ' + (!online || transition ? 'disabled' : '') + '>' +
            availableOptions +
          '</select>' +
          configsHtml +
          '<div style="margin-top:8px; display:flex; gap:8px; flex-wrap:wrap;">' +
            '<button class="btn" data-apply-service="' + escapeHtml(dev.serial || '') + '" ' + (!online || transition ? 'disabled' : '') + '>Aplicar</button>' +
            configBtn +
          '</div>' +
        '</td></tr>' +
      '</table>' +
      '</div>'
    );
  }

  function renderDevices(devices){
    currentDevices = devices;
    var container = document.getElementById('devicesView');
    var selectionSnapshot = {};
    toArray(container.querySelectorAll('.card[data-serial]')).forEach(function(card){
      var serial = card.dataset.serial;
      if(!serial) return;
      var serviceSel = card.querySelector('select[data-service-select]');
      var configSel = card.querySelector('select[data-config-for]');
      selectionSnapshot[serial] = {
        service: serviceSel ? serviceSel.value : null,
        config: configSel ? configSel.value : null
      };
    });
    if(!devices.length){
      container.innerHTML = '<div class="card">No se detectaron agentes.</div>';
      return;
    }
    container.innerHTML = devices.map(renderDevice).join('');

    toArray(container.querySelectorAll('select[data-service-select]')).forEach(function(sel){
      var serial = sel.dataset.serial;
      var active = sel.getAttribute('data-active-service');
      var snapshot = selectionSnapshot[serial];
      if(snapshot && snapshot.service !== null && snapshot.service !== undefined && snapshot.service !== active && !sel.disabled){
        sel.value = snapshot.service;
      }
    });

    toArray(container.querySelectorAll('select[data-config-for]')).forEach(function(sel){
      var serial = sel.dataset.configFor;
      var activeConfig = sel.getAttribute('data-active-config') || '';
      var snapshot = selectionSnapshot[serial];
      if(snapshot && snapshot.config !== null && snapshot.config !== undefined && snapshot.config !== activeConfig && !sel.disabled){
        sel.value = snapshot.config;
      }
    });

    toArray(container.querySelectorAll('select[data-service-select]')).forEach(function(sel){
      sel.addEventListener('change', function(){
        var service = sel.value;
        ensureConfigs(service).then(function(){
          var card = sel.closest('.card');
          var configSelect = card ? card.querySelector('select[data-config-for]') : null;
          if(configSelect){
            configSelect.innerHTML = renderConfigOptions(service, null);
            configSelect.disabled = (service === 'standby' || sel.disabled);
          }
        }).catch(console.error);
      });
    });

    toArray(container.querySelectorAll('button[data-power]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var serial = btn.dataset.serial;
        var action = btn.dataset.power;
        sendPowerCommand(serial, action, btn);
      });
    });

    toArray(container.querySelectorAll('button[data-apply-service]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var serial = btn.dataset.applyService;
        var card = btn.closest('.card');
        if(!card) return;
        var serviceSel = card.querySelector('select[data-service-select]');
        var configSel = card.querySelector('select[data-config-for]');
        var service = serviceSel ? serviceSel.value : '';
        var config = configSel ? configSel.value : '';
        sendServiceChange(serial, service, config);
      });
    });

    toArray(container.querySelectorAll('button[data-config-url]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var url = btn.dataset.configUrl;
        var title = btn.dataset.configTitle || 'Configuración';
        openConfig(url, title);
      });
    });
  }

  function renderServicesView(){
    var container = document.getElementById('servicesView');
    var midi = configCache['MIDI'] || [];
    var html = '<div class="card"><h2>Configuraciones MIDI</h2>';
    if(!midi.length){
      html += '<div class="small">Todavía no hay configuraciones guardadas.</div>';
    } else {
      html += '<table class="table"><tr><th>Nombre</th><th>Última actualización</th><th></th></tr>';
      midi.forEach(function(cfg){
        html += '<tr><td>' + escapeHtml(cfg.name) + '</td><td>' + escapeHtml(cfg.updated_at || '') + '</td><td><button class="btn" data-delete-config="MIDI::' + escapeHtml(cfg.name) + '">Eliminar</button></td></tr>';
      });
      html += '</table>';
    }
    html += '<div class="small">Las configuraciones se sincronizan automáticamente cuando cada Pi guarda sus ajustes.</div></div>';
    container.innerHTML = html;

    toArray(container.querySelectorAll('button[data-delete-config]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var parts = btn.dataset.deleteConfig.split('::');
        var service = parts[0];
        var name = parts[1];
        if(!service || !name) return;
        if(!confirm('¿Eliminar la configuración "' + name + '"?')) return;
        fetch('/api/configs/' + encodeURIComponent(service) + '/' + encodeURIComponent(name), { method:'DELETE' })
          .then(function(){ delete configCache[service]; return Promise.all([loadServiceConfigs(), loadDevices()]); })
          .catch(function(err){ alert('No se pudo eliminar la configuración: ' + err); });
      });
    });
  }

  function renderClientsView(clients){
    currentClients = clients;
    var container = document.getElementById('clientsView');
    if(!clients.length){
      container.innerHTML = '<div class="card">No hay clientes registrados.</div>';
      return;
    }
    var html = '<div class="card"><h2>Clientes registrados</h2>';
    html += '<table class="table"><tr><th>Serial</th><th>Host</th><th>Servicio deseado</th><th>Configuración</th><th>Actualizado</th><th></th></tr>';
    clients.forEach(function(client){
      html += '<tr>' +
        '<td>' + escapeHtml(client.serial || '') + '</td>' +
        '<td>' + escapeHtml(client.host || '—') + '</td>' +
        '<td>' + escapeHtml(client.desired_service || '—') + '</td>' +
        '<td>' + escapeHtml(client.desired_config || '—') + '</td>' +
        '<td>' + escapeHtml(client.updated_at || '') + '</td>' +
        '<td><button class="btn" data-remove-client="' + escapeHtml(client.serial || '') + '">Eliminar</button></td>' +
      '</tr>';
    });
    html += '</table></div>';
    container.innerHTML = html;

    toArray(container.querySelectorAll('button[data-remove-client]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var serial = btn.dataset.removeClient;
        if(!serial || !confirm('¿Eliminar el cliente ' + serial + '?')) return;
        fetch('/api/devices/' + encodeURIComponent(serial), { method:'DELETE' })
          .then(function(){ return Promise.all([loadClients(), loadDevices()]); })
          .catch(function(err){ alert('No se pudo eliminar el cliente: ' + err); });
      });
    });
  }

  function sendServiceChange(serial, service, config){
    if(!service){
      alert('Selecciona un servicio.');
      return;
    }
    var body = { service: service };
    if(config) body.config = config;
    fetch('/api/devices/' + encodeURIComponent(serial) + '/service', {
      method:'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function(res){
      if(!res.ok){
        return res.json().catch(function(){ return { detail:'error' }; }).then(function(detail){
          throw new Error(detail.detail || res.status);
        });
      }
    }).catch(function(err){
      alert('Error cambiando servicio: ' + err);
    }).finally(function(){
      ensureConfigs(service, true).finally(function(){ setTimeout(loadDevices, 500); });
    });
  }

  function sendPowerCommand(serial, action, button){
    if(!serial || !action) return;
    var confirmMsg = action === 'shutdown'
      ? '¿Apagar la Raspberry ' + serial + '?'
      : '¿Reiniciar la Raspberry ' + serial + '?';
    if(!confirm(confirmMsg)) return;
    if(button) button.disabled = true;
    fetch('/api/devices/' + encodeURIComponent(serial) + '/power', {
      method:'POST',
      headers:{ 'Content-Type':'application/json' },
      body: JSON.stringify({ action: action })
    }).then(function(res){
      if(!res.ok){
        return res.json().catch(function(){ return { detail:'error' }; }).then(function(detail){
          throw new Error(detail.detail || res.status);
        });
      }
    }).catch(function(err){
      alert('Error enviando comando: ' + err);
    }).finally(function(){
      if(button){ button.disabled = false; }
      setTimeout(loadDevices, 1200);
    });
  }

  function showView(view){
    toArray(document.querySelectorAll('[data-view-btn]')).forEach(function(btn){
      btn.classList.toggle('active', btn.dataset.viewBtn === view);
    });
    document.getElementById('devicesView').classList.toggle('hidden', view !== 'devices');
    document.getElementById('clientsView').classList.toggle('hidden', view !== 'clients');
    document.getElementById('servicesView').classList.toggle('hidden', view !== 'services');
    if(view === 'services'){
      loadServiceConfigs();
    } else if(view === 'clients'){
      loadClients();
    }
  }

  function openConfig(url, title){
    if(!url) return;
    var overlay = document.getElementById('configOverlay');
    document.getElementById('configFrame').src = url;
    document.getElementById('overlayTitle').textContent = title || 'Configuración';
    overlay.classList.add('active');
  }

  function closeConfig(){
    var overlay = document.getElementById('configOverlay');
    overlay.classList.remove('active');
    document.getElementById('configFrame').src = 'about:blank';
  }

  var closeBtn = document.getElementById('closeOverlayBtn');
  if(closeBtn){
    closeBtn.addEventListener('click', function(){
      closeConfig();
      showView('devices');
    });
  }

  toArray(document.querySelectorAll('[data-view-btn]')).forEach(function(btn){
    btn.addEventListener('click', function(){ showView(btn.dataset.viewBtn); });
  });

  function loadDevices(){
    fetchDevices().then(function(devices){
      var services = new Set();
      devices.forEach(function(dev){ (dev.available_services || []).forEach(function(s){ services.add(s); }); });
      return Promise.all(Array.from(services).map(function(service){ return ensureConfigs(service); })).then(function(){
        renderDevices(devices);
      });
    }).catch(console.error);
  }

  function loadServiceConfigs(){
    ensureConfigs('MIDI', true).then(renderServicesView).catch(console.error);
  }

  function loadClients(){
    fetchClients().then(renderClientsView).catch(console.error);
  }

  setInterval(loadDevices, 4000);
  setInterval(loadServiceConfigs, 15000);
  setInterval(loadClients, 15000);
  loadDevices();
  loadServiceConfigs();
  loadClients();
})();
"""

HTML_PAGE = HTML_PAGE.replace("{{APP_SCRIPT}}", JS_APP)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("omi_server:app", host="0.0.0.0", port=HTTP_PORT, reload=False)
