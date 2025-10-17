import json
import socket
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

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
)
from logger import get_server_logger

HTTP_PORT = 8000
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
        if extra:
            payload["desired_service"] = extra.get("desired_service")
            payload["desired_config"] = extra.get("desired_config")
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
                "last_seen": None,
                "ip": None,
                "online": False,
                "desired_service": extra.get("desired_service"),
                "desired_config": extra.get("desired_config"),
                "service_state": None,
            }
        )

    return result


class PendingRequest:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.payload: Optional[Dict[str, Any]] = None

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

    def update_from_status(self, payload: Dict[str, Any], addr) -> None:
        serial = payload.get("serial")
        if not serial:
            return
        info = {
            "serial": serial,
            "host": payload.get("host"),
            "name": payload.get("name"),
            "index": payload.get("index"),
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
        upsert_device(serial, host=info.get("host"))

    def update_services(self, serial: str, services: Optional[List[Dict[str, Any]]], service_state: Optional[Dict[str, Any]] = None) -> None:
        if services is None and service_state is None:
            return
        with self._lock:
            if serial in self._devices:
                if services is not None:
                    self._devices[serial]["services"] = services
                if service_state is not None:
                    self._devices[serial]["service_state"] = service_state
                self._devices[serial]["last_seen"] = time.time()

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
                    self.registry.update_from_status(payload, addr)
                    logger.info("Estado recibido de %s", payload.get("serial") or addr[0])
                elif msg_type == "SERVICE_ACK":
                    request_id = payload.get("request_id")
                    if request_id:
                        with self.pending_lock:
                            pending = self.pending.pop(request_id, None)
                        if pending:
                            pending.set(payload)
                    serial = payload.get("serial")
                    if serial:
                        self.registry.update_services(serial, payload.get("services"), payload.get("service_state"))
                        logger.info("ACK de servicio recibido de %s (ok=%s)", serial, payload.get("ok"))
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

    def request_service_change(self, serial: str, service: str, *, config: Optional[str] = None, timeout: float = 5.0) -> Dict[str, Any]:
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
    return {"devices": build_devices_payload()}


@app.post("/api/devices/{serial}/service")
async def api_set_service(serial: str, payload: ServiceRequest) -> Dict[str, Any]:
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


@app.get("/api/configs/{service_id}")
async def api_list_service_configs(service_id: str) -> Dict[str, Any]:
    return {"configs": list_configs(service_id)}


@app.get("/api/configs/{service_id}/{name}")
async def api_get_service_config(service_id: str, name: str) -> Dict[str, Any]:
    cfg = get_config(service_id, name)
    if not cfg:
        raise HTTPException(status_code=404, detail="configuración no encontrada")
    return cfg


@app.post("/api/configs/{service_id}")
async def api_save_service_config(service_id: str, payload: ConfigPayload) -> Dict[str, Any]:
    existing = get_config(service_id, payload.name)
    if existing and not payload.overwrite:
        raise HTTPException(status_code=409, detail="ya existe una configuración con ese nombre")
    save_config(service_id, payload.name, payload.data, payload.serial)
    return {"ok": True}


@app.delete("/api/configs/{service_id}/{name}")
async def api_delete_service_config(service_id: str, name: str) -> Dict[str, Any]:
    delete_config(service_id, name)
    return {"ok": True}


@app.put("/api/devices/{serial}")
async def api_update_device(serial: str, payload: DeviceDesiredPayload) -> Dict[str, Any]:
    upsert_device(serial, desired_service=payload.desired_service, desired_config=payload.desired_config)
    return {"ok": True}


@app.delete("/api/devices/{serial}")
async def api_delete_device(serial: str) -> Dict[str, Any]:
    delete_device(serial)
    return {"ok": True}


HTML_PAGE = """<!doctype html>
<html lang=\"es\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>OMI Control Server</title>
<style>
:root { --bg:#111; --card:#1c1c1c; --line:#2c2c2c; --text:#eee; --muted:#aaa; --accent:#2ea043; --danger:#f06262; }
body { font-family: system-ui, sans-serif; margin:0; background:var(--bg); color:var(--text); }
.topbar { display:flex; align-items:center; justify-content:space-between; padding:16px 24px; border-bottom:1px solid var(--line); background:#141414; }
.brand { font-size:20px; font-weight:600; letter-spacing:0.04em; }
.nav { display:flex; gap:10px; flex-wrap:wrap; }
.nav-link { border:1px solid var(--line); background:#242424; color:var(--muted); padding:8px 16px; border-radius:18px; cursor:pointer; }
.nav-link.active { background:var(--accent); color:#041a07; font-weight:600; border-color:var(--accent); }
.nav-link:hover { color:var(--text); }
.container { max-width:1024px; margin:0 auto; padding:24px 24px 48px; }
.stack > * + * { margin-top:18px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }
.card h2 { margin:0 0 8px; font-size:20px; }
.table { width:100%; border-collapse:collapse; margin-top:12px; }
.table th, .table td { padding:8px; border-bottom:1px solid #333; text-align:left; vertical-align:top; }
.status-ok { color:#55d66b; font-weight:600; }
.status-bad { color:var(--danger); font-weight:600; }
.small { font-size:12px; color:var(--muted); }
.btn { background:#272727; color:var(--text); border:1px solid #3a3a3a; border-radius:8px; padding:6px 12px; cursor:pointer; }
.btn:hover { background:#333; }
.btn:disabled { cursor:not-allowed; opacity:0.6; }
select { background:#1e1e1e; color:var(--text); border:1px solid #3a3a3a; border-radius:8px; padding:6px 10px; min-width:140px; }
.view.hidden { display:none !important; }
.overlay { position:fixed; inset:0; background:rgba(0,0,0,0.85); display:none; flex-direction:column; z-index:1000; }
.overlay.active { display:flex; }
.overlay header { display:flex; align-items:center; justify-content:space-between; padding:12px 20px; background:#101010; border-bottom:1px solid #333; }
.overlay header h3 { margin:0; font-size:16px; color:var(--text); }
.overlay iframe { flex:1; border:0; background:#fff; }
.config-select { margin-top:6px; width:100%; }
#servicesView table { width:100%; border-collapse:collapse; }
#servicesView th, #servicesView td { padding:8px; border-bottom:1px solid #2f2f2f; text-align:left; }
.tag { display:inline-block; padding:2px 8px; border-radius:999px; background:#2f2f2f; font-size:12px; margin-left:6px; }
</style>
</head>
<body>
<header class="topbar">
  <div class="brand">OMI Control Server</div>
  <nav class="nav">
    <button class="nav-link active" data-view-btn="devices">Home</button>
    <button class="nav-link" data-view-btn="services">Servicios</button>
  </nav>
</header>
<main class="container stack view" id="devicesView"></main>
<section class="container stack view hidden" id="servicesView"></section>
<div class="overlay hidden" id="configOverlay">
  <header>
    <button class="btn" id="closeOverlayBtn">Volver al Home</button>
    <h3 id="overlayTitle"></h3>
  </header>
  <iframe id="configFrame" src="about:blank"></iframe>
</div>
<script>
const configCache = {};
let currentDevices = [];

function escapeHtml(str){
  return String(str ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c] || c));
}

async function fetchDevices(){
  const res = await fetch('/api/devices');
  if(!res.ok) throw new Error('Error cargando dispositivos');
  return (await res.json()).devices || [];
}

async function ensureConfigs(service, force=false){
  if(service === 'standby') return [];
  if(!force && configCache[service]) return configCache[service];
  const res = await fetch(`/api/configs/${service}`);
  if(!res.ok) throw new Error('Error cargando configuraciones');
  const data = await res.json();
  configCache[service] = data.configs || [];
  return configCache[service];
}

function renderConfigOptions(service, active){
  const configs = configCache[service] || [];
  let html = '<option value="">(actual)</option>';
  configs.forEach(cfg => {
    const selected = cfg.name === active ? 'selected' : '';
    html += `<option value="${escapeHtml(cfg.name)}" ${selected}>${escapeHtml(cfg.name)}</option>`;
  });
  return html;
}

function renderDevice(dev){
  const online = dev.online;
  const serviceState = dev.service_state || {};
  const heartbeat = dev.heartbeat || {};
  const services = dev.services || [];
  const available = dev.available_services || [];
  const activeEntry = services.find(s => s.enabled);
  const active = serviceState.expected || (activeEntry ? activeEntry.name : 'standby');
  const activeLabel = active || 'standby';
  const cpu = heartbeat.cpu != null ? heartbeat.cpu.toFixed(0) + '%' : '--';
  const temp = heartbeat.temp != null ? heartbeat.temp.toFixed(0) + '°C' : '--';
  const serviceReturn = serviceState.returncode != null ? serviceState.returncode : '—';
  const serviceError = serviceState.error || serviceState.last_error || '';
  const serviceConfig = serviceState.config_name || '—';
  const availableOptions = available.map(name => `<option value="${escapeHtml(name)}" ${name===activeLabel?'selected':''}>${escapeHtml(name)}</option>`).join('');
  const configsHtml = activeLabel !== 'standby' ? `<select class="config-select" data-config-for="${escapeHtml(dev.serial || '')}" ${!online ? 'disabled' : ''}>${renderConfigOptions(activeLabel, serviceState.config_name)}</select>` : '<div class="small">Sin opciones de configuración.</div>';
  const applyDisabled = !online;
  const webUrl = serviceState.web_url || '';
  const configBtn = webUrl ? `<button class="btn" data-config-url="${escapeHtml(webUrl)}" data-config-title="${escapeHtml(dev.host || dev.serial || 'Configuración')}">Configurar</button>` : '<button class="btn" disabled>Configurar</button>';
  const desiredService = dev.desired_service || '—';
  const desiredConfig = dev.desired_config || '—';
  const ip = dev.ip || '-';
  const nameHeader = escapeHtml(dev.host || dev.serial || 'Agente');
  return `
  <div class="card" data-serial="${escapeHtml(dev.serial || '')}">
    <h2>${nameHeader}${online ? '' : '<span class="tag">Offline</span>'}</h2>
    <div class="small">Serial: ${escapeHtml(dev.serial || '?')}</div>
    <div class="small">IP: ${escapeHtml(ip)}</div>
    <table class="table">
      <tr><th>Estado</th><td class="${online ? 'status-ok':'status-bad'}">${online ? 'Online' : 'Offline'}</td></tr>
      <tr><th>Servicio activo</th><td>${escapeHtml(activeLabel)}</td></tr>
      <tr><th>Config actual</th><td>${escapeHtml(serviceConfig)}</td></tr>
      <tr><th>Return code</th><td>${escapeHtml(serviceReturn)}</td></tr>
      <tr><th>Error servicio</th><td>${serviceError ? escapeHtml(serviceError) : '—'}</td></tr>
      <tr><th>CPU</th><td>${cpu}</td></tr>
      <tr><th>Temperatura</th><td>${temp}</td></tr>
      <tr><th>Deseado</th><td>${escapeHtml(desiredService)} / ${escapeHtml(desiredConfig)}</td></tr>
      <tr><th>Servicio</th><td>
        <select data-service-select data-serial="${escapeHtml(dev.serial || '')}" ${!online ? 'disabled' : ''}>
          ${availableOptions}
        </select>
        ${configsHtml}
        <div style="margin-top:8px; display:flex; gap:8px; flex-wrap:wrap;">
          <button class="btn" data-apply-service="${escapeHtml(dev.serial || '')}" ${applyDisabled ? 'disabled' : ''}>Aplicar</button>
          ${configBtn}
        </div>
      </td></tr>
    </table>
  </div>`;
}

function renderDevices(devices){
  currentDevices = devices;
  const container = document.getElementById('devicesView');
  if(!devices.length){
    container.innerHTML = '<div class="card">No se detectaron agentes.</div>';
    return;
  }
  container.innerHTML = devices.map(dev => renderDevice(dev)).join('');

  container.querySelectorAll('select[data-service-select]').forEach(sel => {
    sel.addEventListener('change', async () => {
      const service = sel.value;
      try {
        await ensureConfigs(service);
      } catch (err) {
        console.error(err);
      }
      const card = sel.closest('.card');
      const configSelect = card ? card.querySelector('select[data-config-for]') : null;
      if(configSelect){
        configSelect.innerHTML = renderConfigOptions(service, null);
        configSelect.disabled = service === 'standby' || sel.disabled;
      }
    });
  });

  container.querySelectorAll('button[data-apply-service]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const serial = btn.dataset.applyService;
      const card = btn.closest('.card');
      if(!card) return;
      const serviceSel = card.querySelector('select[data-service-select]');
      const configSel = card.querySelector('select[data-config-for]');
      const service = serviceSel ? serviceSel.value : '';
      const config = configSel ? configSel.value : '';
      await sendServiceChange(serial, service, config);
    });
  });

  container.querySelectorAll('button[data-config-url]').forEach(btn => {
    const url = btn.dataset.configUrl;
    const title = btn.dataset.configTitle || 'Configuración';
    btn.addEventListener('click', () => openConfig(url, title));
  });
}

function renderServicesView(){
  const container = document.getElementById('servicesView');
  const midi = configCache['MIDI'] || [];
  let html = '<div class="card"><h2>Configuraciones MIDI</h2>';
  if(!midi.length){
    html += '<div class="small">Todavía no hay configuraciones guardadas.</div>';
  } else {
    html += '<table class="table"><tr><th>Nombre</th><th>Última actualización</th><th></th></tr>';
    midi.forEach(cfg => {
      html += `<tr><td>${escapeHtml(cfg.name)}</td><td>${escapeHtml(cfg.updated_at || '')}</td><td><button class="btn" data-delete-config="MIDI::${escapeHtml(cfg.name)}">Eliminar</button></td></tr>`;
    });
    html += '</table>';
  }
  html += '<div class="small">Las configuraciones se sincronizan automáticamente cuando cada Pi guarda sus ajustes.</div></div>';
  container.innerHTML = html;

  container.querySelectorAll('button[data-delete-config]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const [service, name] = btn.dataset.deleteConfig.split('::');
      if(!confirm(`¿Eliminar la configuración "${name}"?`)) return;
      try {
        await fetch(`/api/configs/${service}/${encodeURIComponent(name)}`, { method:'DELETE' });
        delete configCache[service];
        await loadServiceConfigs();
        await loadDevices();
      } catch (err) {
        alert('No se pudo eliminar la configuración: ' + err);
      }
    });
  });
}

async function sendServiceChange(serial, service, config){
  if(!service){
    alert('Selecciona un servicio.');
    return;
  }
  try {
    const body = { service };
    if(config) body.config = config;
    const resp = await fetch(`/api/devices/${serial}/service`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if(!resp.ok){
      const detail = await resp.json().catch(()=>({detail:'error'}));
      alert('Error cambiando servicio: ' + (detail.detail || resp.status));
    }
  } catch(err){
    alert('Error cambiando servicio: ' + err);
  } finally {
    await ensureConfigs(service, true);
    setTimeout(loadDevices, 500);
  }
}

function showView(view){
  document.querySelectorAll('[data-view-btn]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.viewBtn === view);
  });
  document.getElementById('devicesView').classList.toggle('hidden', view !== 'devices');
  document.getElementById('servicesView').classList.toggle('hidden', view !== 'services');
}

function openConfig(url, title){
  if(!url) return;
  const overlay = document.getElementById('configOverlay');
  document.getElementById('configFrame').src = url;
  document.getElementById('overlayTitle').textContent = title;
  overlay.classList.add('active');
}

function closeConfig(){
  const overlay = document.getElementById('configOverlay');
  overlay.classList.remove('active');
  document.getElementById('configFrame').src = 'about:blank';
}

document.getElementById('closeOverlayBtn').addEventListener('click', () => {
  closeConfig();
  showView('devices');
});

document.querySelectorAll('[data-view-btn]').forEach(btn => {
  btn.addEventListener('click', () => showView(btn.dataset.viewBtn));
});

async function loadDevices(){
  try{
    const devices = await fetchDevices();
    const services = new Set();
    devices.forEach(dev => (dev.available_services || []).forEach(s => services.add(s)));
    await Promise.all(Array.from(services).map(s => ensureConfigs(s)));
    renderDevices(devices);
  }catch(err){
    console.error(err);
  }
}

async function loadServiceConfigs(){
  try{
    await ensureConfigs('MIDI', true);
    renderServicesView();
  }catch(err){
    console.error(err);
  }
}

setInterval(loadDevices, 4000);
setInterval(loadServiceConfigs, 12000);
loadDevices();
loadServiceConfigs();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("omi_server:app", host="0.0.0.0", port=HTTP_PORT, reload=False)
