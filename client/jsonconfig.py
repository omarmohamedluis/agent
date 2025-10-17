import json
import socket
import subprocess
from pathlib import Path
from typing import Any, Dict, List

STANDBY_SERVICE = "standby"
SERVICES_DIR = Path(__file__).resolve().parent / "servicios"
SERVICES_MANIFEST = SERVICES_DIR / "sercivios.json"
_MANIFEST_CACHE: Dict[str, Any] | None = None


def _load_manifest() -> Dict[str, Any]:
    global _MANIFEST_CACHE
    if _MANIFEST_CACHE is not None:
        return _MANIFEST_CACHE
    if SERVICES_MANIFEST.exists():
        try:
            with SERVICES_MANIFEST.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    _MANIFEST_CACHE = data
                    return data
        except Exception:
            pass
    _MANIFEST_CACHE = {"services": []}
    return _MANIFEST_CACHE


def get_service_definition(service_id: str) -> Dict[str, Any] | None:
    manifest = _load_manifest()
    for entry in manifest.get("services", []):
        if isinstance(entry, dict) and entry.get("id") == service_id:
            return entry
    if service_id == STANDBY_SERVICE:
        return {
            "id": STANDBY_SERVICE,
            "display_name": "Standby",
            "type": "logical",
            "description": "Sin proceso activo.",
        }
    return None


def list_service_definitions() -> List[Dict[str, Any]]:
    manifest = _load_manifest()
    defs: List[Dict[str, Any]] = []
    seen = set()
    for entry in manifest.get("services", []):
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        if isinstance(sid, str) and sid not in seen:
            defs.append(entry)
            seen.add(sid)
    if STANDBY_SERVICE not in seen:
        defs.insert(0, get_service_definition(STANDBY_SERVICE) or {"id": STANDBY_SERVICE})
    return defs


def discover_services() -> List[str]:
    manifest = _load_manifest()
    services = []
    for entry in manifest.get("services", []):
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        if isinstance(sid, str) and sid.strip():
            services.append(sid.strip())
    if STANDBY_SERVICE not in services:
        services.append(STANDBY_SERVICE)
    services = list(dict.fromkeys(services))  # preserve order, remove dupes
    if STANDBY_SERVICE in services:
        # ensure standby stays first
        services = [STANDBY_SERVICE] + [s for s in services if s != STANDBY_SERVICE]
    return services


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
    by_name = {
        s.get("name"): bool(s.get("enabled"))
        for s in services
        if isinstance(s, dict) and s.get("name")
    }
    active = next((name for name, enabled in by_name.items() if enabled), None)
    if active not in discovered:
        active = STANDBY_SERVICE if STANDBY_SERVICE in discovered else None
    updated = [{"name": name, "enabled": name == active} for name in discovered]
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
