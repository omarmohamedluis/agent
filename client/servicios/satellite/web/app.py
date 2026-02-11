from fastapi import FastAPI, Request, HTTPException, Body, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import os
import json
import shutil
import asyncio
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List

# Local imports
try:
    from .network_utils import ping_targets
except ImportError:
    import sys
    sys.path.append(os.path.dirname(__file__))
    from network_utils import ping_targets

app = FastAPI()

# Rutas relativas a este archivo (client/servicios/satellite/web/app.py)
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIGS_DIR = BASE_DIR / "configs"
ACTIVE_CONFIG_FILE = BASE_DIR / "active_config.txt"
RUNTIME_CONFIG = BASE_DIR / "runtime_config.json"
RESTART_FLAG = BASE_DIR / "restart_satellite.flag"

templates = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))

class PresetModel(BaseModel):
    name: str

# --- WebSocket Manager for Ping Logs ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

manager = ConnectionManager()

def get_active_config_name():
    if ACTIVE_CONFIG_FILE.exists():
        return ACTIVE_CONFIG_FILE.read_text().strip()
    return "default"

def get_preset_data(name: str):
    file_path = CONFIGS_DIR / f"{name}.json"
    if not file_path.exists():
        return {
            "remoteIp": "127.0.0.1", "remotePort": 16622, "restPort": 9999,
            "surfacePluginsEnabled": {"elgato-streamdeck": True, "loupedeck": True, "infinitton": True},
            "net": {"vlan": 60, "vlan_active": False, "ip_mode": "dhcp", "ip": "", "mask": "", "gateway": ""}
        }
    with open(file_path, "r") as f:
        return json.load(f)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/presets")
async def list_presets():
    presets = []
    if CONFIGS_DIR.exists():
        for f in CONFIGS_DIR.glob("*.json"):
            presets.append(f.stem)
    return {"presets": sorted(presets), "active": get_active_config_name()}

@app.get("/api/presets/content/{name}")
async def get_preset_content(name: str):
    try:
        if name == "active":
            name = get_active_config_name()
        data = get_preset_data(name)
        # Ensure name is returned in the response for the UI
        data["_preset_name"] = name
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo preset: {e}")

@app.post("/api/presets/load")
async def load_preset(preset: PresetModel):
    target = CONFIGS_DIR / f"{preset.name}.json"
    if not target.exists():
        raise HTTPException(status_code=404, detail="Preset no existe")
    
    ACTIVE_CONFIG_FILE.write_text(preset.name)
    RESTART_FLAG.touch()
    return {"status": "ok", "message": f"Preset {preset.name} cargado. Reiniciando..."}

@app.post("/api/config/save")
async def save_config(name: str = Body(...), content: dict = Body(...)):
    """Guarda la configuración del formulario y sincroniza con el Agente."""
    target = CONFIGS_DIR / f"{name}.json"
    try:
        # Asegurar estructura 'net'
        if "net" not in content:
            content["net"] = {"vlan": 60, "vlan_active": False, "ip_mode": "dhcp", "ip": "", "mask": "", "gateway": ""}
        
        with open(target, 'w') as f:
            json.dump(content, f, indent=4)
        
        active = get_active_config_name()
        if name == active:
            # Notificar al Agente para recarga completa (Red + Servicio)
            def _notify_agent():
                try:
                    url = "http://localhost:8000/api/services/satellite/reload_config"
                    req = urllib.request.Request(url, method="POST")
                    with urllib.request.urlopen(req, timeout=2) as r:
                        pass
                except Exception as e:
                    print(f"Error notificando al agente: {e}")
            
            threading.Thread(target=_notify_agent, daemon=True).start()
            return {"status": "ok", "message": "Configuración guardada. Sincronizando red y reiniciando..."}
            
        return {"status": "ok", "message": "Preset guardado."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {e}")

@app.post("/api/ping")
async def ping_request(ips: List[str] = Body(...)):
    """Inicia proceso de ping asíncrono."""
    loop = asyncio.get_event_loop()
    
    def _run_ping():
        def _cb(res):
            asyncio.run_coroutine_threadsafe(manager.broadcast({"type": "ping_log", "data": res}), loop)
            
        results = ping_targets(ips, callback=_cb)
        asyncio.run_coroutine_threadsafe(manager.broadcast({"type": "ping_log", "data": {"summary": True, "total": len(results)}}), loop)

    threading.Thread(target=_run_ping, daemon=True).start()
    return {"status": "ok", "message": "Ping iniciado."}

@app.post("/api/restart_service")
async def restart_service():
    """Solicita reinicio completo al Agente."""
    try:
        url = "http://localhost:8000/api/services/satellite/reload_config"
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=1) as r:
            return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/close_config")
async def close_config():
    """Solicita cierre al Agente."""
    try:
        url = "http://localhost:8000/api/services/satellite/stop"
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=1) as r:
            return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/api/status")
async def get_status():
    state_file = BASE_DIR / "service_state.json"
    if state_file.exists():
        try:
            with open(state_file, 'r') as f:
                return json.load(f)
        except: pass
    return {"state": "unknown", "message": "Esperando servicio..."}

@app.post("/api/install")
async def trigger_install():
    flag = BASE_DIR / "install.flag"
    flag.touch()
    return {"status": "ok", "message": "Instalación iniciada"}
