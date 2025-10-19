# Seguimiento periódico de métricas (CPU, temperatura, IPs) para el cliente.
# REVISAR

import json
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import netifaces
import psutil

STRUCTURE_PATH = Path(__file__).resolve().parent / "agent_pi" / "data" / "structure.json"

# ---------------- Vars de estado expuestas ----------------
CpuUsage: Optional[float] = None  # %
TEMP: Optional[float] = None      # ºC

# ---------------- Estado interno del hilo ----------------
_metrics_lock = threading.Lock()
_metrics_snapshot: Dict[str, Any] = {"cpu": None, "temp": None, "ifaces": []}

_listeners: List[Callable[[Dict[str, Any]], None]] = []
_listeners_lock = threading.Lock()

_poll_interval: float = 1.0
_structure_path: Path = STRUCTURE_PATH

_stop_event = threading.Event()
_active_event = threading.Event()
_heartbeat_thread: Optional[threading.Thread] = None


# ---------------- Utils JSON ----------------
def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# Acepta formatos antiguos (lista de strings) o nuevos (lista de objetos)
def _normalize_json_interfaces(val) -> List[Dict[str, Optional[str]]]:
    out: List[Dict[str, Optional[str]]] = []
    if not isinstance(val, list):
        return out
    for item in val:
        if isinstance(item, dict):
            out.append(
                {
                    "iface": item.get("iface"),
                    "ip": item.get("ip"),
                    "netmask": item.get("netmask"),
                }
            )
        elif isinstance(item, str):
            out.append({"iface": None, "ip": item, "netmask": None})
    return out


# ---------------- Lecturas del sistema ----------------
def _mask_to_prefix(netmask: Optional[str]) -> Optional[int]:
    if not netmask:
        return None
    try:
        return sum(bin(int(part)).count("1") for part in netmask.split("."))
    except Exception:
        return None


def _get_ip_info() -> List[Dict[str, Optional[str]]]:
    """
    Devuelve lista de dicts por interfaz IPv4 (sin loopback):
    [{"iface":"eth0","ip":"192.168.1.23","netmask":"255.255.255.0"}, ...]
    """
    out: List[Dict[str, Optional[str]]] = []
    for iface in netifaces.interfaces():
        addrs = netifaces.ifaddresses(iface)
        if netifaces.AF_INET not in addrs:
            continue
        for addr in addrs[netifaces.AF_INET]:
            ip = addr.get("addr")
            netmask = addr.get("netmask")
            if ip and not str(ip).startswith("127."):
                out.append({"iface": iface, "ip": ip, "netmask": netmask})
    out.sort(key=lambda d: (d.get("iface") or "", d.get("ip") or ""))
    return out


def _enrich_ip_info(ip_info: List[Dict[str, Optional[str]]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for entry in ip_info:
        ip = entry.get("ip")
        netmask = entry.get("netmask")
        prefix = _mask_to_prefix(netmask)
        enriched.append(
            {
                "iface": entry.get("iface"),
                "ip": ip,
                "netmask": netmask,
                "prefix": prefix,
                "cidr": f"{ip}/{prefix}" if (ip and prefix is not None) else (ip or None),
            }
        )
    return enriched


def _get_cpu_usage() -> float:
    return float(psutil.cpu_percent(interval=0.2))


def _get_temp_c() -> Optional[float]:
    # 1) ruta típica de RPi
    try:
        path = Path("/sys/class/thermal/thermal_zone0/temp")
        if path.exists():
            raw = path.read_text().strip()
            value = float(raw)
            return round(value / (1000.0 if value > 200 else 1.0), 1)
    except Exception:
        pass
    # 2) fallback a psutil
    try:
        temps = psutil.sensors_temperatures(fahrenheit=False)
        for _, entries in temps.items():
            for entry in entries:
                if hasattr(entry, "current") and entry.current is not None:
                    return float(entry.current)
    except Exception:
        pass
    return None


# ---------------- Gestión de estado compartido ----------------
def _empty_snapshot() -> Dict[str, Any]:
    return {"cpu": None, "temp": None, "ifaces": []}


def _set_metrics(snapshot: Dict[str, Any]) -> None:
    """Guarda la instantánea y avisa a los listeners."""
    global CpuUsage, TEMP

    with _metrics_lock:
        _metrics_snapshot["cpu"] = snapshot.get("cpu")
        _metrics_snapshot["temp"] = snapshot.get("temp")
        ifaces = snapshot.get("ifaces") or []
        _metrics_snapshot["ifaces"] = list(ifaces)

        CpuUsage = _metrics_snapshot["cpu"]
        TEMP = _metrics_snapshot["temp"]

        published = {
            "cpu": _metrics_snapshot["cpu"],
            "temp": _metrics_snapshot["temp"],
            "ifaces": list(_metrics_snapshot["ifaces"]),
        }

    _notify_listeners(published)


def _notify_listeners(snapshot: Dict[str, Any]) -> None:
    with _listeners_lock:
        listeners = list(_listeners)

    for listener in listeners:
        try:
            listener(dict(snapshot))
        except Exception:
            # No propagamos el fallo de un listener para no detener el hilo.
            continue





# ---------------- Cálculo principal ----------------
def _compute_snapshot(path: Path) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    try:
        if path.exists():
            data = _read_json(path)
    except Exception:
        data = {}

    cpu = _get_cpu_usage()
    temp = _get_temp_c()
    ip_info = _get_ip_info()

    prev_ifaces = _normalize_json_interfaces(data.get("network", {}).get("interfaces")) if data else []
    prev_ips = [entry.get("ip") for entry in prev_ifaces if entry.get("ip")]
    now_ips = [entry.get("ip") for entry in ip_info if entry.get("ip")]

    if data and sorted(prev_ips) != sorted(now_ips):
        data.setdefault("network", {})["interfaces"] = _enrich_ip_info(ip_info)
        try:
            _write_json(path, data)
        except Exception:
            pass

    return {
        "cpu": cpu,
        "temp": temp,
        "ifaces": _enrich_ip_info(ip_info),
    }


def _heartbeat_loop() -> None:
    last_active_state: Optional[bool] = None

    while not _stop_event.is_set():
        is_active = _active_event.is_set()

        if is_active:
            snapshot = _compute_snapshot(_structure_path)
            _set_metrics(snapshot)
        else:
            if last_active_state is not False:
                _set_metrics(_empty_snapshot())

        last_active_state = is_active

        if _stop_event.wait(_poll_interval):
            break

    _set_metrics(_empty_snapshot())


# ---------------- API pública ----------------
def start_heartbeat(
    path: Path = STRUCTURE_PATH,
    interval: float = 1.0,
    start_active: bool = True,
) -> None:
    """Arranca (o reinicia) el hilo si no está vivo y actualiza la configuración."""
    global _heartbeat_thread, _poll_interval, _structure_path, _stop_event, _active_event

    _structure_path = path
    _poll_interval = max(0.2, float(interval))

    if _heartbeat_thread and _heartbeat_thread.is_alive():
        if start_active:
            resume_heartbeat()
        else:
            pause_heartbeat()
        return

    _stop_event = threading.Event()
    _active_event = threading.Event()

    if start_active:
        _active_event.set()
    else:
        _active_event.clear()

    _heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        name="HeartbeatThread",
        daemon=True,
    )
    _heartbeat_thread.start()


def pause_heartbeat() -> None:
    """Mantiene vivo el hilo pero detiene las lecturas."""
    _active_event.clear()


def resume_heartbeat() -> None:
    """Vuelve a activar el muestreo en el hilo existente."""
    _active_event.set()


def stop_heartbeat() -> None:
    """Detiene el hilo por completo y limpia las métricas."""
    global _heartbeat_thread

    if not _heartbeat_thread:
        _set_metrics(_empty_snapshot())
        return

    _active_event.clear()
    _stop_event.set()
    _heartbeat_thread.join(timeout=_poll_interval + 1.0)
    _heartbeat_thread = None
    _set_metrics(_empty_snapshot())


def register_heartbeat_listener(callback: Callable[[Dict[str, Any]], None]) -> None:
    """Permite que otro módulo reciba actualizaciones automáticas."""
    if not callable(callback):
        raise TypeError("callback debe ser callable")

    with _listeners_lock:
        if callback not in _listeners:
            _listeners.append(callback)


def unregister_heartbeat_listener(callback: Callable[[Dict[str, Any]], None]) -> None:
    with _listeners_lock:
        if callback in _listeners:
            _listeners.remove(callback)

def get_heartbeat_snapshot() -> Dict[str, Any]:
    with _metrics_lock:
        return {
            "cpu": _metrics_snapshot["cpu"],
            "temp": _metrics_snapshot["temp"],
            "ifaces": list(_metrics_snapshot["ifaces"]),
        }