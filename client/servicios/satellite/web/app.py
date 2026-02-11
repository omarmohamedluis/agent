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
import time
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
async def save_config(name: str = Body(...), content: dict = Body(...), original_name: str = Body(None), restart: bool = Body(False)):
    """Guarda la configuración, renombra si es necesario y detiene/reinicia el servicio."""
    try:
        # Asegurar estructura 'net'
        if "net" not in content:
            content["net"] = {"vlan": 60, "vlan_active": False, "ip_mode": "dhcp", "ip": "", "mask": "", "gateway": ""}
        
        # 1. Renaming Logic
        if original_name and original_name != name:
            old_file = CONFIGS_DIR / f"{original_name}.json"
            if old_file.exists():
                old_file.unlink()
                
        target = CONFIGS_DIR / f"{name}.json"
        
        # Update name in file_info if present (or add it)
        # Preserve ui_port and add version
        if "file_info" not in content:
             content["file_info"] = {}
        
        # Read existing config to preserve ui_port if not provided
        existing_data = get_preset_data(name)
        current_ui_port = existing_data.get("file_info", {}).get("ui_port", 9002)
        
        content["file_info"]["name"] = name
        content["file_info"]["ui_port"] = content.get("file_info", {}).get("ui_port", current_ui_port)
        content["file_info"]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        # Add installed version
        try:
            package_json = BASE_DIR / "satellite_code" / "package.json"
            if package_json.exists():
                with open(package_json, 'r') as f:
                    pkg = json.load(f)
                    content["file_info"]["version"] = pkg.get("version", "0.0.0")
        except:
             pass

        with open(target, 'w') as f:
            json.dump(content, f, indent=4)
        
        # 2. Update Active Config to the new name
        ACTIVE_CONFIG_FILE.write_text(name)

        # 3. STOP Service (Standby) instead of Reload
        # We need to tell the agent to STOP the service.
        # The user will manually start it again.
        def _manage_agent_service():
            try:
                # Determine action
                action = "reload_config" if restart else "stop"
                url = f"http://127.0.0.1:8000/api/services/satellite/{action}"
                req = urllib.request.Request(url, method="POST")
                with urllib.request.urlopen(req, timeout=2) as r:
                    pass
            except Exception as e:
                print(f"Error managing service on agent ({action}): {e}")
        
        threading.Thread(target=_manage_agent_service, daemon=True).start()
        
        msg = "Configuración guardada. Reiniciando..." if restart else "Configuración guardada. Deteniendo servicio (Standby)..."
        return {"status": "ok", "message": msg}

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
        # Check if we are in config mode, if so, we might want to just stop?
        # But this button is only shown in Running mode usually.
        url = "http://127.0.0.1:8000/api/services/satellite/reload_config"
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=1) as r:
            return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/close_config")
async def close_config():
    """Solicita cierre al Agente."""
    try:
        url = "http://127.0.0.1:8000/api/services/satellite/stop"
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

# --- Companion Manager Endpoints ---

@app.get("/api/companion/status")
async def get_companion_status():
    """Returns local version if installed."""
    package_json = BASE_DIR / "satellite_code" / "package.json"
    version = None
    if package_json.exists():
        try:
            with open(package_json, 'r') as f:
                data = json.load(f)
                version = data.get("version")
        except: pass
    
    return {"installed": bool(version), "version": version}

@app.get("/api/companion/check_update")
async def check_update():
    """Checks latest version on GitHub."""
    try:
        url = "https://api.github.com/repos/bitfocus/companion-satellite/releases/latest"
        # User-Agent is required by GitHub API
        req = urllib.request.Request(url, headers={'User-Agent': 'OMI-Agent'})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.load(r)
            tag_name = data.get("tag_name", "").lstrip("v")
            return {"latest_version": tag_name, "url": data.get("html_url")}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/companion/update")
async def trigger_update():
    """Triggers update process (git pull + build)."""
    flag = BASE_DIR / "update.flag"
    flag.touch()
    return {"status": "ok", "message": "Actualización iniciada"}

@app.post("/api/install")
async def trigger_install():
    flag = BASE_DIR / "install.flag"
    flag.touch()
    return {"status": "ok", "message": "Instalación iniciada"}
