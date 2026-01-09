import sys
from pathlib import Path
from contextlib import asynccontextmanager
import asyncio
import logging
import uvicorn

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Setup Paths and sys.path BEFORE other imports
BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.append(str(SRC_DIR))

from service_manager import ServiceManager
from system import get_system_status
from display_manager import DisplayManager
from logger import log_event
import ui
from NetComHandler import handshake, close_comm_channel

# Setup Logging
logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("omimidi.client")

# Managers
service_manager = ServiceManager()
display_manager = DisplayManager() # Auto-detects driver

# --- LIFESPAN (Web Server Only) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # This only runs if uvicorn starts (Standalone Mode)
    LOGGER.info("Starting Web Server (Standalone Mode)...")
    yield
    LOGGER.info("Stopping Web Server...")

# --- APP DEFINITION ---
app = FastAPI(title="OMI Agent Client", lifespan=lifespan)

# Mount Static & Assets
STATIC_DIR = BASE_DIR / "web" / "static"
TEMPLATES_DIR = BASE_DIR / "web" / "templates"
UTILITIES_DIR = BASE_DIR / "web" / "utilities"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/assets", StaticFiles(directory=UTILITIES_DIR), name="assets")

# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --- ROUTES ---
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    status = get_system_status()
    services = service_manager.get_services()
    
    # Determine active service (for iframe)
    active_service = None
    for svc in services.values():
        if svc.get("running") and svc.get("web_port"):
            active_service = svc
            break
            
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "status": status,
        "services": services,
        "active_service": active_service
    })

@app.get("/api/status")
async def api_status():
    return {
        "system": get_system_status(),
        "services": service_manager.get_services()
    }

@app.post("/api/services/{svc_id}/start")
async def start_service(svc_id: str):
    if service_manager.start_service(svc_id):
        return {"status": "started", "id": svc_id}
    raise HTTPException(status_code=500, detail="Failed to start service")

@app.post("/api/services/{svc_id}/stop")
async def stop_service(svc_id: str):
    if service_manager.stop_service(svc_id):
        return {"status": "stopped", "id": svc_id}
    raise HTTPException(status_code=500, detail="Failed to stop service")

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})

@app.post("/api/system/{action}")
async def system_control(action: str):
    import subprocess
    if action == "reboot":
        subprocess.run(["sudo", "reboot"])
        return {"status": "rebooting"}
    elif action == "shutdown":
        subprocess.run(["sudo", "shutdown", "now"])
        return {"status": "shutting_down"}
    raise HTTPException(status_code=400, detail="Invalid action")

@app.post("/api/network/config")
async def network_config(config: dict):
    # TODO: Implement actual network configuration logic
    LOGGER.info(f"Received network config: {config}")
    return {"status": "received", "config": config}

# --- MAIN ENTRY POINT ---
def restore_active_service():
    """Restores the active service from structure.json."""
    try:
        structure_path = BASE_DIR / "data" / "structure.json"
        if structure_path.exists():
            import json
            with structure_path.open("r", encoding="utf-8") as f:
                structure = json.load(f)
            
            for svc in structure.get("services", []):
                if svc.get("enabled"):
                    svc_id = svc.get("id") or svc.get("name")
                    if svc_id:
                        LOGGER.info(f"Restoring active service: {svc_id}")
                        service_manager.start_service(svc_id)
                    break 
    except Exception as e:
        LOGGER.error(f"Failed to restore active service state: {e}")

def main():
    LOGGER.info("Starting OMI Client Boot Sequence...")
    
    # 1. Init UI
    ui.LoadingUI(10, "BOOTING...")
    
    # 2. Handshake (Blocking)
    ui.LoadingUI(30, "HANDSHAKE...")
    # Note: handshake() uses NetComHandler which starts a thread.
    connected = handshake()
    
    if connected:
        ui.LoadingUI(100, "CONNECTED")
        LOGGER.info("Mode: CONNECTED (Server found)")
    else:
        ui.LoadingUI(100, "STANDALONE")
        LOGGER.info("Mode: STANDALONE (No server)")
    
    # 3. Restore Service (Applies to both modes)
    restore_active_service()
    
    # 4. Start UI & Main Loop
    ui.StartStandardUI(json_path=BASE_DIR / "data" / "structure.json")
    
    try:
        if connected:
            # Connected Mode: No Web Server, just wait and listen
            LOGGER.info("Running in Connected Mode (Web Server DISABLED)")
            while True:
                time.sleep(1)
        else:
            # Standalone Mode: Start Web Server
            LOGGER.info("Running in Standalone Mode (Web Server ENABLED)")
            uvicorn.run(app, host="0.0.0.0", port=8000)
            
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user")
    finally:
        LOGGER.info("Shutting down...")
        # Stop services and persist state
        for svc_id in list(service_manager.processes.keys()):
            service_manager.stop_service(svc_id, persist_state=True)
            
        display_manager.cleanup()
        close_comm_channel()

if __name__ == "__main__":
    import time # Ensure time is imported
    main()
