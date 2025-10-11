from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess, json, os, time, psutil, socket

# NEW: UI
from ui.oled_ui import OledUI

APP_PORT = 9000
DATA_DIR = "/etc/omi"
DEVICE_FILE = os.path.join(DATA_DIR, "device.json")

app = FastAPI(title="omiAgent", version="0.1")

# --- UI global ---
ui = OledUI()  # crea la instancia (no bloquea)

def read_device():
    if os.path.exists(DEVICE_FILE):
        return json.load(open(DEVICE_FILE))
    return {"index": None, "hostname": socket.gethostname(), "role": "standby"}

def write_device(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DEVICE_FILE, "w") as f:
        json.dump(d, f)

def svc(cmd):
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def service_status(name):
    r = svc(f"systemctl is-active {name}")
    return r.stdout.strip() if r.returncode == 0 else "inactive"

def list_services():
    names = ["omimidi","omiosc","companion-satellite","companion"]
    return [{"name": n, "status": service_status(n)} for n in names]

# ----------------- FastAPI lifecycle -----------------
@app.on_event("startup")
def _on_startup():
    # 1) Arranca pantalla en modo LOADING
    ui.start_boot()
    ui.set_progress(5, "Leyendo dispositivo...")
    dev = read_device()

    # 2) Pasitos de boot con progreso (ajusta a tus necesidades)
    ui.set_progress(20, "Comprobando servicios...")
    _ = list_services()  # simple scan para que tarde "algo" real

    ui.set_progress(35, "Inicializando sensores...")
    # (psutil caliente, etc.)
    _ = psutil.cpu_percent(interval=0.1)

    ui.set_progress(60, "Preparando API...")
    # (no hay mucho más que hacer aquí; FastAPI ya está subiendo)

    ui.set_progress(85, "Finalizando...")
    # 3) Cuando el server está listo, cambiamos a READY
    index = dev.get("index")
    role = dev.get("role", "standby")
    ui.set_ready(profile=role, index=index)
    # Consideramos “conectado” (puedes cambiar esto a tu lógica real de servidor remoto)
    ui.set_connection(True)
    ui.set_progress(100, "Listo")

@app.on_event("shutdown")
def _on_shutdown():
    # Apaga el hilo de la UI
    ui.stop()

# ----------------- Endpoints -----------------
@app.get("/v1/health")
def health():
    dev = read_device()
    temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
    return {
        "device_id": dev.get("device_id", socket.gethostname()),
        "hostname": dev.get("hostname", socket.gethostname()),
        "index": dev.get("index"),
        "role": dev.get("role","standby"),
        "ip": socket.gethostbyname(socket.gethostname()),
        "services": {s["name"]: s["status"] for s in list_services()},
        "cpu": psutil.cpu_percent(interval=0.2),
        "mem": psutil.virtual_memory()._asdict(),
        "temp": {k:[t.current for t in v] for k,v in temps.items()} if temps else {},
        "uptime": time.time() - psutil.boot_time()
    }

class Identity(BaseModel):
    index: int
    hostname: str | None = None

@app.put("/v1/identity")
def identity(body: Identity):
    dev = read_device()
    dev["index"] = body.index
    if body.hostname:
        dev["hostname"] = body.hostname
        svc(f"sudo hostnamectl set-hostname {body.hostname}")
    write_device(dev)

    # NEW: refleja al momento en el header (READY mantiene el perfil actual)
    ui.set_ready(profile=dev.get("role","standby"), index=dev.get("index"))
    return {"ok": True}

class RoleBody(BaseModel):
    role: str

ROLE_TO_SERVICES = {
    "standby": [],
    "omimidi": ["omimidi"],
    "omiosc": ["omiosc"],
    "satellite": ["companion-satellite"],
    "companion": ["companion"]
}

def stop_all():
    for n in ["omimidi","omiosc","companion-satellite","companion"]:
        svc(f"sudo systemctl stop {n}")

def start_role(role: str):
    for n in ROLE_TO_SERVICES.get(role, []):
        svc(f"sudo systemctl enable {n}")
        svc(f"sudo systemctl restart {n}")

@app.put("/v1/role")
def set_role(body: RoleBody):
    role = body.role.lower()
    if role not in ROLE_TO_SERVICES:
        raise HTTPException(status_code=400, detail="unknown role")
    prev = read_device().get("role","standby")

    # Opcional: feedback visual mientras cambia el rol
    ui.set_progress(10, f"Cambiando rol → {role}…")
    ui.set_connection(False)

    stop_all()
    start_role(role)
    dev = read_device()
    dev["role"] = role
    write_device(dev)
    # verificación simple
    ok = all(service_status(n) == "active" for n in ROLE_TO_SERVICES[role])
    if not ok and role != "standby":
        # rollback
        ui.set_progress(60, "Fallo. Revirtiendo…")
        stop_all()
        start_role(prev)
        dev["role"] = prev
        write_device(dev)
        # reflejar rollback en pantalla
        ui.set_ready(profile=prev, index=dev.get("index"))
        ui.set_connection(True)
        return {"ok": False, "reason": "start_failed", "prev": prev, "now": prev}

    # OK: refleja el nuevo rol en pantalla
    ui.set_ready(profile=role, index=dev.get("index"))
    ui.set_connection(True)
    ui.set_progress(100, "Listo")
    return {"ok": True, "prev": prev, "now": role}

@app.get("/v1/services")
def services():
    return {"services": list_services()}

@app.post("/v1/services/{name}/{action}")
def service_ctl(name: str, action: str):
    if action not in ["start","stop","restart"]:
        raise HTTPException(status_code=400, detail="bad action")
    r = svc(f"sudo systemctl {action} {name}")
    return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}

@app.put("/v1/configs/{service}")
async def put_config(service: str, body: dict):
    role_dir = f"/etc/omi/roles/{service if service!='satellite' else 'satellite'}"
    os.makedirs(role_dir, exist_ok=True)
    fname = "config.json" if service in ["satellite","companion"] else ("map.json" if service=="omimidi" else "routes.json")
    with open(os.path.join(role_dir, fname), "w") as f:
        json.dump(body, f, indent=2)
    return {"ok": True}

@app.get("/v1/logs/{name}")
def logs(name: str, tail: int = 300):
    r = svc(f"journalctl -u {name} -n {tail} --no-pager")
    return r.stdout or r.stderr

@app.post("/v1/power/{action}")
def power(action: str):
    if action == "reboot":
        svc("sudo reboot")
        return {"ok": True}
    if action == "shutdown":
        svc("sudo shutdown -h now")
        return {"ok": True}
    raise HTTPException(status_code=400, detail="bad action")
