import json
import socket
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

BCAST_IP = "255.255.255.255"
BCAST_PORT = 37020
REPLY_PORT = 37021
DISCOVER_INTERVAL_S = 3.0
STATUS_TTL_S = 6.0


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
            "logical_service": payload.get("logical_service"),
            "last_seen": time.time(),
            "ip": addr[0],
        }
        with self._lock:
            self._devices[serial] = info

    def update_services(self, serial: str, services: Optional[List[Dict[str, Any]]]) -> None:
        if services is None:
            return
        with self._lock:
            if serial in self._devices:
                self._devices[serial]["services"] = services
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
                    "ts": time.time(),
                }
                try:
                    s.sendto(json.dumps(payload).encode("utf-8"), (BCAST_IP, BCAST_PORT))
                    # print("→ broadcast DISCOVER")
                except Exception as exc:
                    print(f"[server] error enviando broadcast: {exc}")
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
                    print(f"[server] socket error: {exc}")
                    continue

                try:
                    payload = json.loads(data.decode("utf-8", "ignore"))
                except Exception:
                    print(f"[server] JSON inválido: {data!r}")
                    continue

                msg_type = payload.get("type")

                if msg_type == "AGENT_STATUS":
                    self.registry.update_from_status(payload, addr)
                elif msg_type == "SERVICE_ACK":
                    request_id = payload.get("request_id")
                    if request_id:
                        with self.pending_lock:
                            pending = self.pending.pop(request_id, None)
                        if pending:
                            pending.set(payload)
                    serial = payload.get("serial")
                    if serial:
                        self.registry.update_services(serial, payload.get("services"))
                else:
                    print(f"[server] mensaje desconocido: {payload}")
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

    def request_service_change(self, serial: str, service: str, timeout: float = 5.0) -> Dict[str, Any]:
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

        pending = PendingRequest()
        with self.pending_lock:
            self.pending[request_id] = pending

        try:
            if not self.command_socket:
                raise RuntimeError("Socket de comando no disponible")
            self.command_socket.sendto(json.dumps(message).encode("utf-8"), (device["ip"], BCAST_PORT))
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
app = FastAPI(title="OMI Control Server", version="0.1")


class ServiceRequest(BaseModel):
    service: str


@app.on_event("startup")
async def on_startup() -> None:
    manager.start()
    print("[server] manager iniciado")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    manager.stop()
    print("[server] manager detenido")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(HTML_PAGE)


@app.get("/api/devices")
async def api_devices() -> Dict[str, Any]:
    return {"devices": registry.list_devices()}


@app.post("/api/devices/{serial}/service")
async def api_set_service(serial: str, payload: ServiceRequest) -> Dict[str, Any]:
    try:
        reply = manager.request_service_change(serial, payload.service)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not reply.get("ok"):
        raise HTTPException(status_code=400, detail=reply.get("error") or "error desconocido")

    return reply


HTML_PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OMI Control Server</title>
<style>
body { font-family: system-ui, sans-serif; margin:0; padding:24px; background:#111; color:#eee; }
header { margin-bottom: 24px; }
.card { background:#1c1c1c; border:1px solid #2c2c2c; border-radius:12px; padding:16px; margin-bottom:16px; }
.card h2 { margin:0 0 8px; font-size:20px; }
.table { width:100%; border-collapse:collapse; margin-top:12px; }
.table th, .table td { padding:8px; border-bottom:1px solid #333; text-align:left; }
.badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; background:#333; }
button, select { background:#272727; color:#eee; border:1px solid #3a3a3a; border-radius:6px; padding:6px 12px; font-size:14px; }
button:hover { background:#333; cursor:pointer; }
.status-ok { color:#55d66b; }
.status-bad { color:#f06262; }
.small { font-size:12px; color:#aaa; }
</style>
</head>
<body>
<header>
  <h1>OMI Control Server</h1>
  <p>Monitoriza cada agente y cambia su servicio activo.</p>
</header>
<section id="devices"></section>
<script>
async function fetchDevices(){
  const res = await fetch('/api/devices');
  if(!res.ok) throw new Error('Error cargando dispositivos');
  return (await res.json()).devices || [];
}

function renderDevices(devices){
  const container = document.getElementById('devices');
  if(!devices.length){
    container.innerHTML = '<div class="card">No se detectaron agentes.</div>';
    return;
  }
  container.innerHTML = devices.map(dev => renderDevice(dev)).join('');
  container.querySelectorAll('select[data-serial]').forEach(sel => {
    sel.addEventListener('change', async ev => {
      const serial = sel.dataset.serial;
      const service = sel.value;
      sel.disabled = true;
      try {
        const resp = await fetch(`/api/devices/${serial}/service`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ service })
        });
        if(!resp.ok){
          const detail = await resp.json().catch(()=>({detail:'error'}));
          alert('Error cambiando servicio: ' + (detail.detail || resp.status));
        }
      } catch(err){
        alert('Error cambiando servicio: ' + err);
      } finally {
        setTimeout(loadDevices, 500);
      }
    });
  });
}

function renderDevice(dev){
  const online = dev.online;
  const heartbeat = dev.heartbeat || {};
  const services = dev.services || [];
  const available = dev.available_services || [];
  const active = (services.find(s => s.enabled) || {}).name || 'desconocido';
  const cpu = heartbeat.cpu != null ? heartbeat.cpu.toFixed(0) + '%' : '--';
  const temp = heartbeat.temp != null ? heartbeat.temp.toFixed(0) + '°C' : '--';
  const options = available.map(name => `<option value="${name}" ${name===active?'selected':''}>${name}</option>`).join('');
  return `
  <div class="card">
    <h2>${dev.host || dev.serial || 'Agente'}</h2>
    <div class="small">Serial: ${dev.serial || '?'}</div>
    <div class="small">IP: ${dev.ip || '-'}</div>
    <table class="table">
      <tr><th>Estado</th><td class="${online ? 'status-ok':'status-bad'}">${online ? 'Online' : 'Offline'}</td></tr>
      <tr><th>Servicio activo</th><td>${active}</td></tr>
      <tr><th>CPU</th><td>${cpu}</td></tr>
      <tr><th>Temperatura</th><td>${temp}</td></tr>
      <tr><th>Cambiar servicio</th><td>
        <select data-serial="${dev.serial}" ${!online ? 'disabled' : ''}>
          ${options}
        </select>
      </td></tr>
    </table>
  </div>`;
}

async function loadDevices(){
  try{
    const devices = await fetchDevices();
    renderDevices(devices);
  }catch(err){
    console.error(err);
  }
}

setInterval(loadDevices, 4000);
loadDevices();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("omi_server:app", host="0.0.0.0", port=8000, reload=False)
