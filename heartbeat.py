# heartbeat.py — UPDATEHB sin hilo y sin 'ips'
# deps: psutil, netifaces
from __future__ import annotations
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

import psutil
import netifaces

STRUCTURE_PATH = Path("agent_pi/data/structure.json")

# ---------------- Vars de estado expuestas ----------------
CpuUsage: Optional[float] = None  # %
TEMP: Optional[float] = None      # ºC

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
            out.append({
                "iface": item.get("iface"),
                "ip": item.get("ip"),
                "netmask": item.get("netmask")
            })
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
        for a in addrs[netifaces.AF_INET]:
            ip = a.get("addr")
            nm = a.get("netmask")
            if ip and not str(ip).startswith("127."):
                out.append({"iface": iface, "ip": ip, "netmask": nm})
    out.sort(key=lambda d: (d.get("iface") or "", d.get("ip") or ""))
    return out

def _enrich_ip_info(ip_info: List[Dict[str, Optional[str]]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for x in ip_info:
        ip = x.get("ip")
        nm = x.get("netmask")
        pfx = _mask_to_prefix(nm)
        enriched.append({
            "iface": x.get("iface"),
            "ip": ip,
            "netmask": nm,
            "prefix": pfx,
            "cidr": f"{ip}/{pfx}" if (ip and pfx is not None) else (ip or None)
        })
    return enriched

def _get_cpu_usage() -> float:
    return float(psutil.cpu_percent(interval=0.2))

def _get_temp_c() -> Optional[float]:
    # 1) ruta típica de RPi
    try:
        p = Path("/sys/class/thermal/thermal_zone0/temp")
        if p.exists():
            v = p.read_text().strip()
            return round(float(v) / (1000.0 if float(v) > 200 else 1.0), 1)
    except Exception:
        pass
    # 2) fallback a psutil
    try:
        temps = psutil.sensors_temperatures(fahrenheit=False)
        for _, entries in temps.items():
            for e in entries:
                if hasattr(e, "current") and e.current is not None:
                    return float(e.current)
    except Exception:
        pass
    return None

# ---------------- API principal ----------------
def UPDATEHB(path: Path = STRUCTURE_PATH) -> Dict[str, Any]:

    global CpuUsage, TEMP

    # leer JSON si existe
    data: Dict[str, Any] = {}
    try:
        if path.exists():
            data = _read_json(path)
    except Exception:
        data = {}

    # métricas
    CpuUsage = _get_cpu_usage()
    TEMP     = _get_temp_c()
    ip_info  = _get_ip_info()

    # estado previo (acepta strings o objetos)
    prev_ifaces = _normalize_json_interfaces(data.get("network", {}).get("interfaces")) if data else []
    prev_ips    = [x.get("ip") for x in prev_ifaces if x.get("ip")]
    now_ips     = [x.get("ip") for x in ip_info if x.get("ip")]

    # ¿cambió algo?
    changed = sorted(prev_ips) != sorted(now_ips)
    if changed and data:
        data.setdefault("network", {})["interfaces"] = _enrich_ip_info(ip_info)
        try:
            _write_json(path, data)
        except Exception:
            pass

    # snapshot para UI/cliente (enriquecido)
    return {
        "cpu": CpuUsage,
        "temp": TEMP,
        "ifaces": _enrich_ip_info(ip_info)
    }

# alias por si ya lo usabas antes
def snapshothb(path: Path = STRUCTURE_PATH) -> Dict[str, Any]:
    return UPDATEHB(path)
