# heartbeat.py — ultra básico
# deps: psutil, netifaces
from __future__ import annotations
import time
import json
from pathlib import Path
from threading import Thread, Event
from typing import List, Dict, Any, Optional

import psutil
import netifaces

STRUCTURE_PATH = Path("agent_pi/data/structure.json")

# ---------------- Vars de estado (las que pediste) ----------------
interval: int = 5                 # se cargará desde el JSON
NETNICS: List[str] = []           # array de IPs
CpuUsage: Optional[float] = None  # %
TEMP: Optional[float] = None      # ºC
active: bool = True               # centinela

# Control interno del hilo
_loop_thread: Optional[Thread] = None
_stop_evt: Event = Event()


# ---------------- Utils JSON muy simples ----------------
def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------- Lecturas del sistema ----------------
def _get_ips_real() -> List[str]:
    """Devuelve lista de IPv4 reales (sin loopback)."""
    ips: List[str] = []
    for iface in netifaces.interfaces():
        addrs = netifaces.ifaddresses(iface)
        for fam, lst in addrs.items():
            if fam == netifaces.AF_INET:
                for a in lst:
                    ip = a.get("addr")
                    if ip and not ip.startswith("127."):
                        ips.append(ip)
    # orden estable
    return sorted(set(ips))

def _get_cpu_usage() -> float:
    # muestreo corto para no bloquear mucho
    return float(psutil.cpu_percent(interval=0.2))

def _get_temp_c() -> Optional[float]:
    # 1) ruta típica de RPi
    try:
        p = Path("/sys/class/thermal/thermal_zone0/temp")
        if p.exists():
            v = p.read_text().strip()
            # suele venir en milicelsius
            return round(float(v) / (1000.0 if float(v) > 200 else 1.0), 1)
    except Exception:
        pass
    # 2) fallback a psutil
    try:
        temps = psutil.sensors_temperatures(fahrenheit=False)
        for key, entries in temps.items():
            for e in entries:
                if hasattr(e, "current") and e.current is not None:
                    return float(e.current)
    except Exception:
        pass
    return None


# ---------------- API pública: start/stop ----------------
def set_active(flag: bool) -> None:
    """Cambia la centinela. Si se pone en False, el bucle se detiene limpiamente."""
    global active
    active = bool(flag)
    if not active:
        _stop_evt.set()

def starthb(path: Path = STRUCTURE_PATH) -> None:
    """Inicia el bucle de heartbeat en un hilo."""
    global _loop_thread, _stop_evt
    if _loop_thread and _loop_thread.is_alive():
        return
    _stop_evt.clear()
    _loop_thread = Thread(target=_run_loop, args=(path,), daemon=True)
    _loop_thread.start()

def stophb() -> None:
    """Detiene el bucle de heartbeat y deja CPU/TEMP en null y NETNICS vacío."""
    set_active(False)

# --- API extra: snapshot de métricas en vivo ---

def snapshothb():
    """Devuelve una foto actual del heartbeat: cpu, temp, ips."""
    return {
        "cpu": CpuUsage,     # puede ser None hasta que mida
        "temp": TEMP,        # puede ser None si no hay sensor
        "ips": list(NETNICS) # copia defensiva
    }



# ---------------- Lógica principal del heartbeat ----------------
def _run_loop(path: Path) -> None:
    global interval, NETNICS, CpuUsage, TEMP

    # 1) Cargar JSON; si no existe, no hacemos nada (PoC simple)
    if not path.exists():
        # nada que hacer: salimos en silencio
        return

    try:
        data = _read_json(path)
    except Exception:
        return

    # 2) Inicialización desde JSON
    try:
        interval = int(data.get("config", {}).get("heartbeat_interval_s", 5))
    except Exception:
        interval = 5

    # JSON guarda interfaces como lista (array de IPs). Si viniera vacía, se rellena luego.
    NETNICS = list(data.get("network", {}).get("interfaces", []))

    # 3) Bucle
    while active and not _stop_evt.is_set():
        # 3.1 Consultas
        CpuUsage = _get_cpu_usage()
        TEMP = _get_temp_c()
        ips_real = _get_ips_real()

        # 3.2 Comparar IPs con JSON
        # Los comparamos como conjuntos ordenados
        if sorted(NETNICS) != sorted(ips_real):
            # Actualizamos variable y JSON
            NETNICS = ips_real
            # Sobrescribir JSON en network.interfaces (PoC: lista de IPs)
            data.setdefault("network", {})["interfaces"] = NETNICS
            try:
                _write_json(path, data)
            except Exception:
                # si falla escritura, ignoramos en PoC
                pass

        # 3.3 Espera según interval (respetando stop inmediato)
        # esperar en pasitos para reaccionar rápido a stop
        slept = 0.0
        step = 0.2
        while slept < max(0.2, float(interval)) and active and not _stop_evt.is_set():
            time.sleep(step)
            slept += step

    # 4) Limpieza al parar
    CpuUsage = None
    TEMP = None
    NETNICS = []
    # opcional: limpiar también en el JSON
    try:
        data = _read_json(path)
        data.setdefault("network", {})["interfaces"] = []
        _write_json(path, data)
    except Exception:
        pass
