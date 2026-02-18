import logging
import datetime
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

def configure_logging():
    log_dir = Path(__file__).resolve().parent / "logs" / "server"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"server_{timestamp}.log"
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger("omi.server")

def configure_message_logging():
    log_dir = Path(__file__).resolve().parent / "logs" / "server"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "messages.log"
    
    logger = logging.getLogger("omi.messages")
    logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers if reloaded
    if not logger.handlers:
        handler = logging.FileHandler(log_file)
        # Detailed format for precise diagnostics
        formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    return logger

LOGGER = configure_logging()
MESSAGE_LOGGER = configure_message_logging()

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

# Communication Events for UI Pills
event_queue: List[Dict[str, Any]] = []
event_lock = threading.Lock()

def emit_event(serial: str, type: str, message: str):
    with event_lock:
        event = {
            "id": int(time.time() * 1000),
            "timestamp": time.time(),
            "serial": serial,
            "type": type,
            "message": message
        }
        event_queue.append(event)
        # Keep only last 20 events
        if len(event_queue) > 20:
            event_queue.pop(0)
            
    # Also log to file for permanent audit
    MESSAGE_LOGGER.info(f"[{type.upper()}] [{serial}] {message}")

# --- Models ---

class HandshakePayload(BaseModel):
    serial: str
    host: str
    hostname: Optional[str] = None
    ip: Optional[str] = None
    active_service: Optional[str] = None
    active_config: Optional[str] = None
    web_port: Optional[int] = None
    cpu: Optional[float] = None
    temp: Optional[float] = None
    presets: Dict[str, Dict[str, Dict[str, Any]]] # {service_id: {config_name: {data: ...}}}

class HeartbeatPayload(BaseModel):
    serial: str
    cpu: Optional[float] = None
    temp: Optional[float] = None
    hostname: Optional[str] = None
    ip: Optional[str] = None
    active_service: Optional[str] = None
    active_config: Optional[str] = None
    web_port: Optional[int] = None
    system_status: Optional[Dict[str, Any]] = None

class CommandPayload(BaseModel):
    action: str # shutdown, reboot, wol, start_service
    service_id: Optional[str] = None
    config_name: Optional[str] = None

# --- API Endpoints ---

@app.post("/api/handshake")
async def handshake(payload: HandshakePayload):
    # 1. Update/Register agent with minimal state for immediate visibility
    # We only store what's in ALLOWED_AGENT_FIELDS. 
    # Use 'host' as the source for identity.
    storage.upsert_agent(payload.serial, {
        "host": payload.host,
        "ip": payload.ip,
        "active_service": payload.active_service,
        "active_config": payload.active_config,
        "active_service_port": payload.web_port,
        "cpu": payload.cpu,
        "temp": payload.temp,
        "status": "online",
        "system_status": {
            "is_busy": False, 
            "busy_message": ""
        },
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
    
    # Discovery Event with state and services
    status_info = "Standby"
    if payload.active_service:
        status_info = f"Running: {payload.active_service}"
    
    ip_info = f" [IP: {payload.ip or 'N/A'}]"
    services_found = list(payload.presets.keys())
    services_msg = f" [Services: {', '.join(services_found)}]" if services_found else " [No services found]"
    
    emit_event(payload.serial, "discovery", f"Handshake with {payload.host} ({status_info}){ip_info}{services_msg}")
    
    # 3. Check for pending commands to deliver immediately
    state = agent_runtime_state.get(payload.serial, {})
    commands = state.get("commands", [])
    cmd = None
    if commands:
        cmd = commands.pop(0)
        state["commands"] = commands
        agent_runtime_state[payload.serial] = state
        emit_event(payload.serial, "command", f"Delivered (via handshake): {cmd.get('action')} to client")
        
        # Update busy message to indicate it's now executing
        storage.upsert_agent(payload.serial, {
            "system_status": {
                "is_busy": True,
                "busy_message": f"Executing {cmd.get('action')}..."
            }
        })

    # 4. Return ALL configs and any pending command
    return {
        "status": "ok",
        "configs": storage.get_configs(),
        "command": cmd
    }

@app.post("/api/heartbeat")
async def heartbeat(payload: HeartbeatPayload):
    # Get current state to check for locks
    current_agents = storage.get_agents()
    agent_data = current_agents.get(payload.serial, {})
    
    # State Machine Logic:
    # 1. If we are awaiting the FIRST stable heartbeat after a sync, clear the busy lock now.
    is_busy = False
    busy_message = ""
    awaiting_stable = agent_data.get("awaiting_stable_heartbeat", False)
    
    if awaiting_stable:
        # Transition to normal mode
        is_busy = False
        busy_message = ""
        awaiting_stable = False
    else:
        # Keep old busy status if locker is active
        current_status = agent_data.get("system_status", {})
        is_busy = current_status.get("is_busy", False)
        busy_message = current_status.get("busy_message", "")

    storage.upsert_agent(payload.serial, {
        "cpu": payload.cpu,
        "temp": payload.temp,
        "hostname": payload.hostname,
        "ip": payload.ip,
        "active_service": payload.active_service,
        "active_config": payload.active_config,
        "active_service_port": payload.web_port,
        "system_status": {
            "is_busy": is_busy,
            "busy_message": busy_message
        },
        "awaiting_stable_heartbeat": awaiting_stable,
        "last_seen": time.time()
    })
    
    # Enhance Heartbeat Diagnostic Pill
    status_info = "Standby"
    if payload.system_status and payload.system_status.get("is_busy"):
        status_info = f"Busy: {payload.system_status.get('busy_message')}"
    elif payload.active_service:
        status_info = f"Running: {payload.active_service}"
    
    ip_info = f" [IP: {payload.ip or 'N/A'}]"
    emit_event(payload.serial, "heartbeat", f"Heartbeat ({status_info}){ip_info}")
    
    # Check for commands
    state = agent_runtime_state.get(payload.serial, {})
    commands = state.get("commands", [])
    if commands:
        cmd = commands.pop(0)
        state["commands"] = commands
        agent_runtime_state[payload.serial] = state
        emit_event(payload.serial, "command", f"Delivered: {cmd.get('action')} to client")
        
        # Update busy message to indicate it's now executing
        storage.upsert_agent(payload.serial, {
            "system_status": {
                "is_busy": True,
                "busy_message": f"Executing {cmd.get('action')}..."
            }
        })
        
        return {"status": "ok", "command": cmd}
    
    return {"status": "ok"}

@app.get("/api/agents")
async def get_agents():
    agents = storage.get_agents()
    now = time.time()
    for serial, data in agents.items():
        # Add dynamic status
        last_seen = data.get("last_seen", 0)
        system_status = data.get("system_status", {})
        
        if now - last_seen > 15:
            data["status"] = "offline"
        elif system_status and system_status.get("is_busy"):
            data["status"] = "loading"
            data["busy_message"] = system_status.get("busy_message", "Loading...")
        else:
            data["status"] = "online"
            
    return {
        "agents": agents,
        "events": event_queue
    }

@app.post("/api/agents/{serial}/command")
async def agent_command(serial: str, payload: CommandPayload):
    # Ensure agent exists in storage (even if offline)
    agents = storage.get_agents()
    if serial not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")

    state = agent_runtime_state.setdefault(serial, {"commands": []})
    state["commands"].append(payload.dict())
    
    agent = agents[serial]
    is_offline = agent.get("status") == "offline" or (time.time() - agent.get("last_seen", 0) > 30)
    
    msg = f"Queueing command: {payload.action}"
    if is_offline:
        msg = f"Programmed: {payload.action} (Queued for next boot)"
    
    emit_event(serial, "command", msg)
    
    # LOCK UI immediately
    storage.upsert_agent(serial, {
        "system_status": {
            "is_busy": True,
            "busy_message": "Programmed..." if is_offline else "Command Queued..."
        }
    })
    
    # If starting/stopping a service, set immediate feedback and start timeout
    if payload.action == "start_service":
        storage.upsert_agent(serial, {
            "last_launch_at": time.time(),
            "status": "loading",
            "system_status": {
                "is_busy": True,
                "busy_message": "Launching..."
            }
        })
    elif payload.action == "stop_service":
        storage.upsert_agent(serial, {
            "last_launch_at": time.time(),
            "status": "loading",
            "system_status": {
                "is_busy": True,
                "busy_message": "Stopping..."
            }
        })
    
    return {"status": "queued"}

@app.post("/api/agents/{serial}/ready")
async def agent_ready(serial: str, payload: Dict[str, Any]):
    """Called by the client when a service is fully ready, providing final state."""
    # State Machine: Keep is_busy = True but set sync confirmation flag
    # We extract target fields from payload to avoid polluting storage
    storage.upsert_agent(serial, {
        "last_launch_at": payload.get("last_launch_at") or 0,
        "status": "online",
        "active_service": payload.get("active_service"),
        "active_config": payload.get("active_config"),
        "active_service_port": payload.get("web_port"),
        "ip": payload.get("ip"),
        "system_status": {
            "is_busy": True,
            "busy_message": "Syncing..."
        },
        "awaiting_stable_heartbeat": True
    })
    
    final_state = "Standby"
    if payload.get("active_service"):
        final_state = f"Active ({payload.get('active_service')})"
        
    emit_event(serial, "sync", f"Final State Confirmed: {final_state}")
    return {"status": "ok"}

@app.delete("/api/agents/{serial}")
async def delete_agent(serial: str):
    storage.delete_agent(serial)
    emit_event(serial, "removal", f"Agent removed from registry")
    return {"status": "ok"}

@app.post("/api/agents/{serial}/id")
async def update_agent_id(serial: str, payload: Dict[str, Any]):
    new_id = payload.get("id")
    if new_id is None:
        raise HTTPException(status_code=400, detail="ID required")
    try:
        new_id = int(new_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID must be an integer")
        
    if storage.update_agent_id(serial, new_id):
        return {"status": "ok", "id": new_id}
    raise HTTPException(status_code=404, detail="Agent not found")

@app.post("/api/agents/global-command")
async def global_command(payload: CommandPayload):
    agents = storage.get_agents()
    for serial in agents.keys():
        state = agent_runtime_state.setdefault(serial, {"commands": []})
        state["commands"].append(payload.dict())
    return {"status": "ok", "count": len(agents)}

@app.get("/api/configs")
async def list_configs(service_id: Optional[str] = None):
    return {"configs": storage.get_configs(service_id)}

@app.post("/api/configs/{service_id}")
async def save_config(service_id: str, name: str, data: Dict[str, Any], serial: Optional[str] = None):
    # Use serial if provided, else storage defaults to "server"
    if serial:
        storage.save_config(service_id, name, data, serial)
    else:
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
    # Data and logs are now outside the server/ package, 
    # so we don't need reload_excludes to avoid infinite reload loops.
    uvicorn.run("server.app:app", host="0.0.0.0", port=9000, reload=False)
