import time
import socket
import threading
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from server import storage

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start broadcaster
    threading.Thread(target=broadcaster_loop, daemon=True).start()
    yield
    # Shutdown logic (if any) can go here

app = FastAPI(title="OMI Agent Server", lifespan=lifespan)
BASE_DIR = Path(__file__).resolve().parent

# Storage for runtime state (online status, etc.)
# {serial: {"commands": ["shutdown", ...]}}
agent_runtime_state: Dict[str, Dict[str, Any]] = {}

# --- Models ---

class HandshakePayload(BaseModel):
    serial: str
    host: str
    presets: Dict[str, Dict[str, Dict[str, Any]]] # {service_id: {config_name: {data: ...}}}

class HeartbeatPayload(BaseModel):
    serial: str
    cpu: Optional[float] = None
    temp: Optional[float] = None
    ifaces: List[Dict[str, Any]] = []
    hostname: Optional[str] = None
    ip: Optional[str] = None
    active_service: Optional[str] = None
    active_config: Optional[str] = None

class CommandPayload(BaseModel):
    action: str # shutdown, reboot, wol, start_service
    service_id: Optional[str] = None
    config_name: Optional[str] = None

# --- API Endpoints ---

@app.post("/api/handshake")
async def handshake(payload: HandshakePayload):
    # 1. Update/Register agent
    storage.upsert_agent(payload.serial, {
        "host": payload.host,
        "last_seen": time.time()
    })
    
    # 2. Merge presets
    all_server_configs = storage.get_configs()
    added_something = False
    
    for svc_id, configs in payload.presets.items():
        server_svc_configs = all_server_configs.get(svc_id, {})
        for cfg_name, cfg_data in configs.items():
            if cfg_name not in server_svc_configs:
                # Add to server if missing
                storage.save_config(svc_id, cfg_name, cfg_data.get("data", {}), serial=payload.serial)
                added_something = True
    
    # 3. Return ALL configs for the services the client mentioned
    # This allows client to replace local configs with server's master list
    return {
        "status": "ok",
        "configs": storage.get_configs()
    }

@app.post("/api/heartbeat")
async def heartbeat(payload: HeartbeatPayload):
    storage.upsert_agent(payload.serial, {
        "cpu": payload.cpu,
        "temp": payload.temp,
        "ifaces": payload.ifaces,
        "hostname": payload.hostname,
        "ip": payload.ip,
        "active_service": payload.active_service,
        "active_config": payload.active_config,
        "last_seen": time.time()
    })
    
    # Check for commands
    state = agent_runtime_state.get(payload.serial, {})
    commands = state.get("commands", [])
    if commands:
        cmd = commands.pop(0)
        state["commands"] = commands
        agent_runtime_state[payload.serial] = state
        return {"status": "ok", "command": cmd}
    
    return {"status": "ok"}

@app.get("/api/agents")
async def get_agents():
    agents = storage.get_agents()
    now = time.time()
    for serial, data in agents.items():
        # Add dynamic status
        last_seen = data.get("last_seen", 0)
        if now - last_seen < 15:
            data["status"] = "online"
        elif now - last_seen < 60:
            data["status"] = "away"
        else:
            data["status"] = "offline"
            
    return {"agents": agents}

@app.post("/api/agents/{serial}/command")
async def agent_command(serial: str, payload: CommandPayload):
    state = agent_runtime_state.setdefault(serial, {"commands": []})
    state["commands"].append(payload.action)
    return {"status": "queued"}

@app.post("/api/agents/global-command")
async def global_command(payload: CommandPayload):
    agents = storage.get_agents()
    for serial in agents.keys():
        state = agent_runtime_state.setdefault(serial, {"commands": []})
        state["commands"].append(payload.action)
    return {"status": "ok", "count": len(agents)}

@app.get("/api/configs")
async def list_configs(service_id: Optional[str] = None):
    return {"configs": storage.get_configs(service_id)}

@app.post("/api/configs/{service_id}")
async def save_config(service_id: str, name: str, data: Dict[str, Any]):
    storage.save_config(service_id, name, data)
    return {"status": "ok"}

@app.delete("/api/configs/{service_id}/{name}")
async def delete_config(service_id: str, name: str):
    storage.delete_config(service_id, name)
    return {"status": "ok"}

# --- Web UI ---

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "web" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# --- Broadcaster ---

def get_local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"

def broadcaster_loop():
    port = 39653
    server_port = 9000
    tag = "OMI_DIRECTOR"
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    
    while True:
        ip = get_local_ip()
        payload = f"{tag}|{ip}|{server_port}".encode("utf-8")
        try:
            sock.sendto(payload, ("<broadcast>", port))
        except Exception:
            pass
        time.sleep(5.0) # More relaxed interval

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.app:app", host="0.0.0.0", port=9000, reload=True)
