#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from fastapi import FastAPI
from pydantic import BaseModel
import subprocess, json, os, socket, psutil, time, hashlib, threading
import requests

# ====== CONFIG ======
MAIN_VERSION  = "0.0.1"
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
DATA_DIR      = os.path.join(SCRIPT_DIR, "data")            # ← ruta relativa
PIINFO_FILE   = os.path.join(DATA_DIR, "PiInfo.json")
TOKEN_FILE    = os.path.join(DATA_DIR, "agent_token")       # ← token persistente
FILLER_SCRIPT = os.path.join(SCRIPT_DIR, "DataConfig", "fill_rpi_config.py")

# ====== UI ======
from ui.oled_ui import OledUI
ui = OledUI()  # instancia global

app = FastAPI(title="omiAgent-MVP+UI", version=MAIN_VERSION)
PIINFO_CACHE = {}
STOP_HEARTBEAT = threading.Event()

# -------------------- Helpers --------------------
def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)

def run_filler(version: str):
    print(f"[agent] Rellenador --version {version}", flush=True)
    r = subprocess.run(["python3", FILLER_SCRIPT, "--version", version],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    print(r.stdout.strip())
    if r.returncode != 0:
        print("[agent][ERROR] Rellenador falló:", r.stderr.strip(), flush=True)

    # Copia si el rellenador genera PiInfo.json junto a su script
    src = os.path.join(os.path.dirname(FILLER_SCRIPT), "PiInfo.json")
    if os.path.exists(src):
        ensure_dirs()
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
        with open(PIINFO_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"[agent] PiInfo.json actualizado en {PIINFO_FILE}", flush=True)

def load_piinfo():
    global PIINFO_CACHE
    if os.path.exists(PIINFO_FILE):
        with open(PIINFO_FILE, "r", encoding="utf-8") as f:
            PIINFO_CACHE = json.load(f)
    else:
        PIINFO_CACHE = {}
    return PIINFO_CACHE

def save_piinfo(conf: dict):
    ensure_dirs()
    with open(PIINFO_FILE, "w", encoding="utf-8") as f:
        json.dump(conf, f, indent=2, ensure_ascii=False)

def infer_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(0.2)
        try:
            s.connect(("1.1.1.1", 53)); ip = s.getsockname()[0]
        finally:
            s.close()
        return ip
    except:
        return socket.gethostbyname(socket.gethostname())

def read_token() -> str | None:
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            t = f.read().strip()
            return t or None
    except FileNotFoundError:
        return None

def write_token(tok: str):
    ensure_dirs()
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(tok.strip())
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except Exception:
        pass

def normalized_api_base(server_block: dict) -> str | None:
    """Devuelve api_base usable. Si falta, intenta construir http://<address>:8443."""
    api_base = (server_block or {}).get("api_base", "") or ""
    address  = (server_block or {}).get("address", "") or ""
    if api_base:
        return api_base
    if address:
        # Nuestro server corre http en 8443 en el MVP
        return f"http://{address}:8443"
    return None

def sha256_json(obj: dict) -> str:
    data = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()

def http_post(url: str, json_body: dict, token: str | None = None, timeout=3.0) -> requests.Response:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Acepta http/https. Si es https con cert autofirmado, desactiva verify en MVP.
    verify = not url.lower().startswith("https://")
    # Nota: verify=False para https (autofirmado) → set verify=False
    verify = False if url.lower().startswith("https://") else True
    return requests.post(url, json=json_body, headers=headers, timeout=timeout, verify=verify)

# -------------------- REST (handshake + heartbeat) --------------------
def do_handshake_and_start_heartbeat(pi: dict):
    """Hace handshake con el server, guarda token e index si aplica y lanza el thread de heartbeat."""
    ident   = pi.get("identity", {}) or {}
    serial  = ident.get("serial", "UNKNOWN")
    index   = ident.get("index", 99)
    serverb = pi.get("server", {}) or {}
    api_base = normalized_api_base(serverb)

    if not api_base:
        print("[agent] Sin api_base del servidor. No se puede hacer handshake.", flush=True)
        ui.set_connection(False)
        return

    observed_sha = sha256_json(pi)
    body = {
        "serial": serial,
        "hostname": socket.gethostname(),
        "versions": {"agent": MAIN_VERSION},
        "observed_sha": observed_sha,
        "identity": {"index": index},
    }

    url = f"{api_base}/v1/agents/handshake"
    print(f"[agent] Handshake → {url}", flush=True)
    try:
        r = http_post(url, body, token=None, timeout=4.0)
        r.raise_for_status()
        resp = r.json()
    except Exception as e:
        print(f"[agent][handshake][ERROR] {e}", flush=True)
        ui.set_connection(False)
        return

    # token
    tok = (resp or {}).get("token")
    if tok:
        write_token(tok)

    # asignación de index
    assign = (resp or {}).get("assign") or {}
    if "index" in assign and isinstance(assign["index"], int):
        new_idx = assign["index"]
        if new_idx != index:
            print(f"[agent] Index asignado por server: {new_idx} (antes {index})", flush=True)
            pi.setdefault("identity", {})["index"] = new_idx
            save_piinfo(pi)  # persistimos
            # refleja en UI
            ui.set_ready(profile="standby", index=new_idx)

    ui.set_connection(True)
    # lanza heartbeat
    start_heartbeat_thread(pi)

def heartbeat_loop(pi_supplier, interval_s: int):
    """Bucle de latidos. pi_supplier es una función que devuelve la config actual."""
    while not STOP_HEARTBEAT.is_set():
        pi = pi_supplier()
        ident = pi.get("identity", {}) or {}
        serverb = pi.get("server", {}) or {}
        token = read_token()
        api_base = normalized_api_base(serverb)
        if not token or not api_base:
            time.sleep(interval_s)
            continue

        serial = ident.get("serial", "UNKNOWN")
        observed_sha = sha256_json(pi)

        body = {
            "serial": serial,
            "observed_sha": observed_sha,
            "service": "standby",   # MVP: fijo, ya lo haremos dinámico
            "stats": {
                "cpu": psutil.cpu_percent(interval=0.05),
                "ip": infer_ip()
            }
        }
        url = f"{api_base}/v1/agents/heartbeat/{serial}"
        try:
            r = http_post(url, body, token=token, timeout=3.0)
            # si el token caducó o no vale, el server devolverá 401
            if r.status_code == 401:
                print("[agent][heartbeat] 401 Unauthorized (token). Reintentará tras renovar.", flush=True)
        except Exception as e:
            print(f"[agent][heartbeat][WARN] {e}", flush=True)

        # pinta “conectado” si todo OK recientemente
        ui.set_connection(True)
        STOP_HEARTBEAT.wait(interval_s)

def start_heartbeat_thread(pi_initial: dict):
    # intervalo desde config, default 5s
    interval = int(pi_initial.get("config", {}).get("heartbeat_interval_s", 5) or 5)
    STOP_HEARTBEAT.clear()
    t = threading.Thread(target=heartbeat_loop, args=(lambda: (PIINFO_CACHE or load_piinfo()), interval), daemon=True)
    t.start()

# -------------------- Lifecycle --------------------
@app.on_event("startup")
def startup():
    # 1) UI primero
    ui.start_boot()
    ui.set_progress(5, "Arrancando…")

    # 2) JSON rellenado
    ui.set_progress(15, "Leyendo hardware…")
    ensure_dirs()
    run_filler(MAIN_VERSION)

    # 3) Cargar PiInfo y READY mínimo
    ui.set_progress(45, "Cargando configuración…")
    pi = load_piinfo()
    ident = pi.get("identity", {})
    index = ident.get("index", 99)
    role  = "standby"

    _ = psutil.cpu_percent(interval=0.15)
    ui.set_progress(70, "Inicializando UI…")
    time.sleep(0.1)
    ui.set_ready(profile=role, index=index)
    ui.set_connection(False)
    ui.set_progress(85, "Contacto con servidor…")

    # 4) Handshake REST + heartbeat
    do_handshake_and_start_heartbeat(pi)
    ui.set_progress(100, "Listo")

@app.on_event("shutdown")
def shutdown():
    STOP_HEARTBEAT.set()
    ui.stop()

# -------------------- Endpoints locales (debug) --------------------
@app.get("/v1/health")
def health():
    pi = PIINFO_CACHE or load_piinfo()
    ident = pi.get("identity", {})
    return {
        "serial": ident.get("serial", "UNKNOWN"),
        "index": ident.get("index", 99),
        "ip": infer_ip(),
        "cpu": psutil.cpu_percent(interval=0.1)
    }

@app.get("/v1/config")
def get_config():
    return PIINFO_CACHE or load_piinfo()

# ====== UI helpers ======
class UIConnBody(BaseModel):
    connected: bool

@app.put("/v1/ui/connection")
def ui_connection(body: UIConnBody):
    ui.set_connection(bool(body.connected))
    return {"ok": True, "connected": body.connected}

class UIStatusBody(BaseModel):
    message: str | None = None
    progress: int | None = None  # 0-100

@app.put("/v1/ui/status")
def ui_status(body: UIStatusBody):
    pct = body.progress if body.progress is not None else None
    ui.set_progress(pct if pct is not None else 100, body.message)
    return {"ok": True, "progress": pct, "message": body.message}
