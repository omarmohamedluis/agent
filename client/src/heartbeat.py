"""
Monitor de Sistema (Heartbeat).
Realiza el seguimiento periódico de métricas del sistema (CPU, temperatura) y de red (IPs, interfaces),
notificando a los listeners registrados (como la UI).
"""
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import netifaces
import psutil

from logger import get_logger
from structure_manager import get_structure_manager

LOGGER = get_logger("omiclient.heartbeat")
STRUCTURE_MANAGER = get_structure_manager()

# ---------------- Vars de estado expuestas ----------------
CpuUsage: Optional[float] = None  # %
TEMP: Optional[float] = None      # ºC

# ---------------- Estado interno del hilo ----------------
_metrics_lock = threading.Lock()
_metrics_snapshot: Dict[str, Any] = {"cpu": None, "temp": None, "ifaces": []}

_listeners: List[Callable[[Dict[str, Any]], None]] = []
_listeners_lock = threading.Lock()

_poll_interval_fast: float = 1.0   # CPU / Temp
_poll_interval_slow: float = 10.0  # Red

_stop_event = threading.Event()
_active_event = threading.Event()
_heartbeat_thread: Optional[threading.Thread] = None


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
    [{"name":"eth0","ip":"192.168.1.23","netmask":"255.255.255.0"}, ...]
    """
    out: List[Dict[str, Optional[str]]] = []
    try:
        for iface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(iface)
            if netifaces.AF_INET in addrs:
                for addr in addrs[netifaces.AF_INET]:
                    ip = addr.get("addr")
                    netmask = addr.get("netmask")
                    if ip and not str(ip).startswith("127."):
                        out.append({"name": iface, "ip": ip, "netmask": netmask})
            else:
                # Incluir interfaz incluso si no tiene IP (ej. VLAN raw o trunk)
                if iface != "lo":
                    out.append({"name": iface, "ip": None, "netmask": None})
    except Exception as e:
        LOGGER.error(f"Error leyendo interfaces: {e}")
    
    out.sort(key=lambda d: (d.get("name") or "", d.get("ip") or ""))
    return out


def _enrich_ip_info(ip_info: List[Dict[str, Optional[str]]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for entry in ip_info:
        ip = entry.get("ip")
        netmask = entry.get("netmask")
        prefix = _mask_to_prefix(netmask)
        enriched.append(
            {
                "name": entry.get("name"),
                "ip": ip,
                "netmask": netmask,
                "cidr": f"{ip}/{prefix}" if (ip and prefix is not None) else (ip or None),
                "vlan": None, # Heartbeat aún no detecta VLANs, pero mantiene la clave
            }
        )
    return enriched

def _determine_main_nic(ifaces: List[Dict[str, Any]]) -> Optional[str]:
    """
    Determina la interfaz principal basada en prioridad:
    eth0 > wlan0 > eth0.vlan > otras
    Retorna el nombre de la interfaz o None.
    """
    # Mapa de prioridades
    # 0: eth0 (física cableada)
    # 1: wlan0 (wifi)
    # 2: eth0.X (vlan)
    # 3: otras
    
    candidates = []
    
    for iface in ifaces:
        name = iface.get("name", "")
        ip = iface.get("ip")
        
        # Solo consideramos interfaces con IP para ser "Main NIC"
        if not ip:
            continue
            
        priority = 99
        if name == "eth0":
            priority = 0
        elif name == "wlan0":
            priority = 1
        elif name.startswith("eth0."):
            priority = 2
        
        candidates.append((priority, name))
    
    if not candidates:
        return None
        
    # Ordenar por prioridad (menor es mejor)
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _get_cpu_usage() -> float:
    return float(psutil.cpu_percent(interval=None)) # Interval None es no bloqueante si se llama periódicamente


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
        _metrics_snapshot["main_nic"] = snapshot.get("main_nic")
        _metrics_snapshot["main_nic_ip"] = snapshot.get("main_nic_ip")

        CpuUsage = _metrics_snapshot["cpu"]
        TEMP = _metrics_snapshot["temp"]

        published = {
            "cpu": _metrics_snapshot["cpu"],
            "temp": _metrics_snapshot["temp"],
            "ifaces": list(_metrics_snapshot["ifaces"]),
            "main_nic": _metrics_snapshot["main_nic"],
            "main_nic_ip": _metrics_snapshot["main_nic_ip"],
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
def _compute_fast_metrics() -> Dict[str, Any]:
    """Calcula métricas rápidas (CPU, Temp)."""
    cpu = _get_cpu_usage()
    temp = _get_temp_c()
    
    if cpu is not None and cpu > 50.0:
        try:
            # Solo logueamos si es muy alto para no saturar
            if cpu > 80.0:
                proc_cpu = psutil.Process().cpu_percent(interval=None)
                LOGGER.warning(f"CPU crítica: {cpu:.1f}% (Sistema)")
        except Exception:
            pass
            
    if temp is not None and temp > 75.0:
        LOGGER.warning(f"Temperatura crítica: {temp:.1f}°C")
        
    return {"cpu": cpu, "temp": temp}

def _compute_network_metrics() -> Dict[str, Any]:
    """Calcula métricas de red (lento)."""
    ip_info = _get_ip_info()
    enriched_ifaces = _enrich_ip_info(ip_info)
    
    # Determinar Main NIC
    main_nic = _determine_main_nic(enriched_ifaces)
    
    main_nic_ip = None
    if main_nic:
        for iface in enriched_ifaces:
            if iface.get("name") == main_nic:
                main_nic_ip = iface.get("ip")
                break
    
    # Actualizar StructureManager
    STRUCTURE_MANAGER.update_network_interfaces(enriched_ifaces, main_nic=main_nic)
    
    return {"ifaces": enriched_ifaces, "main_nic": main_nic, "main_nic_ip": main_nic_ip}


def _heartbeat_loop() -> None:
    last_active_state: Optional[bool] = None
    
    last_network_check = 0.0
    
    # Init psutil cpu
    psutil.cpu_percent(interval=None)

    while not _stop_event.is_set():
        is_active = _active_event.is_set()
        now = time.time()

        if is_active:
            try:
                # 1. Métricas Rápidas (Siempre)
                fast_metrics = _compute_fast_metrics()
                
                # 2. Métricas Lentas (Intervalo)
                network_metrics = {}
                if now - last_network_check >= _poll_interval_slow:
                    network_metrics = _compute_network_metrics()
                    last_network_check = now
                else:
                    # Reutilizar últimas ifaces conocidas de memoria si es posible
                    with _metrics_lock:
                        network_metrics["ifaces"] = _metrics_snapshot.get("ifaces", [])
                        network_metrics["main_nic"] = _metrics_snapshot.get("main_nic")
                        network_metrics["main_nic_ip"] = _metrics_snapshot.get("main_nic_ip")
                
                # Fusionar
                snapshot = {**fast_metrics, **network_metrics}
                _set_metrics(snapshot)
                
            except Exception as exc:
                LOGGER.error(f"Error capturando métricas del heartbeat: {exc}")
        else:
            if last_active_state is not False:
                _set_metrics(_empty_snapshot())

        last_active_state = is_active

        if _stop_event.wait(_poll_interval_fast):
            break

    _set_metrics(_empty_snapshot())


# ---------------- API pública ----------------
def start_heartbeat(
    path: Optional[Path] = None, # Obsoleto
    interval: float = 1.0,
    start_active: bool = True,
) -> None:
    """Arranca (o reinicia) el hilo si no está vivo."""
    global _heartbeat_thread, _poll_interval_fast, _stop_event, _active_event

    _poll_interval_fast = max(0.2, float(interval))

    if _heartbeat_thread and _heartbeat_thread.is_alive():
        if start_active:
            resume_heartbeat()
        else:
            pause_heartbeat()
        return

    LOGGER.info(f"Iniciando heartbeat híbrido: Rápido={_poll_interval_fast}s, Lento={_poll_interval_slow}s")
    
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
    LOGGER.info("Heartbeat en pausa")
    _active_event.clear()


def resume_heartbeat() -> None:
    """Vuelve a activar el muestreo en el hilo existente."""
    if not _active_event.is_set():
        LOGGER.info("Heartbeat reanudado")
        _active_event.set()


def stop_heartbeat() -> None:
    """Detiene el hilo por completo y limpia las métricas."""
    LOGGER.info("Heartbeat detenido")
    global _heartbeat_thread

    # Limpiar interfaces de red en structure.json vía Manager
    try:
        STRUCTURE_MANAGER.update_network_interfaces([])
    except Exception as e:
        LOGGER.error(f"Fallo al limpiar info de red al detener: {e}")

    if not _heartbeat_thread:
        _set_metrics(_empty_snapshot())
        return

    _active_event.clear()
    _stop_event.set()
    _heartbeat_thread.join(timeout=_poll_interval_fast + 1.0)
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
        return {
            "cpu": _metrics_snapshot.get("cpu"),
            "temp": _metrics_snapshot.get("temp"),
            "ifaces": list(_metrics_snapshot.get("ifaces", [])),
            "main_nic": _metrics_snapshot.get("main_nic"),
            "main_nic_ip": _metrics_snapshot.get("main_nic_ip"),
        }

def force_update_interfaces() -> None:
    """Fuerza una lectura inmediata de todas las métricas y actualiza el snapshot interno."""
    try:
        fast = _compute_fast_metrics()
        net = _compute_network_metrics()
        _set_metrics({**fast, **net})
        LOGGER.info("Métricas de sistema y red actualizadas (Forzado)")
    except Exception as e:
        LOGGER.error(f"Error en actualización forzada: {e}")