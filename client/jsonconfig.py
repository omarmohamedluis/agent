import json
import socket
import subprocess
from pathlib import Path
from typing import Any, Dict, List

STANDBY_SERVICE = "standby"
SERVICES_DIR = Path(__file__).resolve().parent / "servicios"


def discover_services() -> List[str]:
    names: List[str] = []
    if SERVICES_DIR.exists():
        for child in SERVICES_DIR.iterdir():
            if child.is_dir() and (child / "service.py").exists():
                names.append(child.name)
    if STANDBY_SERVICE not in names:
        names.append(STANDBY_SERVICE)
    names.sort(key=lambda n: (0 if n == STANDBY_SERVICE else 1, n.lower()))
    return names


def get_serial() -> str:
    """
    Devuelve el número de serie único de la Raspberry Pi.
    Si falla, devuelve un identificador de fallback.
    """
    try:
        out = subprocess.check_output("cat /proc/cpuinfo | grep Serial", shell=True, text=True)
        serial = out.strip().split(":")[1].strip()
        if serial:
            return serial
    except Exception:
        pass
    return "unknown-serial"


def get_host() -> str:
    """
    Devuelve el hostname de la máquina.
    Si falla, devuelve un identificador de fallback.
    """
    try:
        host = socket.gethostname()
        if host:
            return host
    except Exception:
        pass
    return "unknown-host"


def _default_structure(version: str, serial: str, host: str) -> Dict[str, Any]:
    services = [{"name": name, "enabled": name == STANDBY_SERVICE} for name in discover_services()]
    if not services:
        services = [{"name": STANDBY_SERVICE, "enabled": True}]
    return {
        "version": {"version": version},
        "identity": {"index": None, "name": "", "serial": serial, "host": host},
        "network": {"interfaces": []},
        "services": services,
        "config": {"heartbeat_interval_s": 5},
    }


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _sync_services(data: Dict[str, Any]) -> bool:
    services = data.setdefault("services", [])
    discovered = discover_services()
    by_name = {s.get("name"): bool(s.get("enabled")) for s in services if isinstance(s, dict) and s.get("name")}
    active = next((name for name, enabled in by_name.items() if enabled), None)
    if active not in discovered:
        active = STANDBY_SERVICE if STANDBY_SERVICE in discovered else None
    updated = []
    for name in discovered:
        updated.append({"name": name, "enabled": name == active})
    if not any(item["enabled"] for item in updated) and updated:
        updated[0]["enabled"] = True
    if updated != services:
        data["services"] = updated
        return True
    return False


def ensure_config(path: str | Path, version: str = "0.0.1") -> Dict[str, Any]:
    """
    - Si el archivo no existe: lo crea con la plantilla.
    - Si existe y el serial coincide: no hace nada.
    - Si no coincide: lo regenera.
    """
    p = Path(path)
    serial = get_serial()
    host = get_host()

    if not p.exists():
        data = _default_structure(version, serial, host)
        _write_json(p, data)
        return data

    try:
        data = _read_json(p)
    except Exception:
        data = _default_structure(version, serial, host)
        _write_json(p, data)
        return data

    current_serial = str(data.get("identity", {}).get("serial", ""))
    if current_serial != serial:
        data = _default_structure(version, serial, host)
        _write_json(p, data)
        return data

    has_changes = False

    if data.get("version", {}).get("version") != version:
        data.setdefault("version", {})["version"] = version
        has_changes = True

    identity = data.setdefault("identity", {})
    if identity.get("host") != host:
        identity["host"] = host
        has_changes = True

    if _sync_services(data):
        has_changes = True

    if has_changes:
        _write_json(p, data)

    return data


def read_config(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    try:
        return _read_json(p)
    except Exception:
        return ensure_config(p)


def set_active_service(path: str | Path, name: str) -> Dict[str, Any]:
    p = Path(path)
    data = read_config(p)
    available = {entry["name"] for entry in data.get("services", []) if isinstance(entry, dict)}
    if name not in available:
        _sync_services(data)
        available = {entry["name"] for entry in data.get("services", []) if isinstance(entry, dict)}
    if name not in available:
        raise ValueError(f"unknown service: {name}")
    for entry in data.get("services", []):
        if isinstance(entry, dict):
            entry["enabled"] = entry.get("name") == name
    if not any(entry.get("enabled") for entry in data.get("services", []) if isinstance(entry, dict)):
        for entry in data.get("services", []):
            if isinstance(entry, dict) and entry.get("name") == STANDBY_SERVICE:
                entry["enabled"] = True
                break
    _write_json(p, data)
    return data


def get_enabled_service(data: Dict[str, Any]) -> str | None:
    for entry in data.get("services", []):
        if isinstance(entry, dict) and entry.get("enabled"):
            return entry.get("name")
    return None
