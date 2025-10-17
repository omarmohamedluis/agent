import json
import socket
import subprocess
from pathlib import Path
from typing import Any, Dict

DEFAULT_SERVICES = [
    {"name": "standby",              "enabled": True},
    {"name": "MIDI",                 "enabled": False},
    {"name": "satellite",            "enabled": False},
    {"name": "companion",            "enabled": False},
    {"name": "OSCkey",               "enabled": False},
    {"name": "scauting",             "enabled": False},
]

def get_serial() -> str:
    """
    Devuelve el número de serie único de la Raspberry Pi.
    Si falla, devuelve un identificador de fallback.
    """
    try:
        # Método estándar de Raspberry Pi
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
    return {
        "version": {"version": version},
        "identity": {"index": None, "name": "", "serial": serial, "host": host},
        "network": {"interfaces": []},
        "services": list(DEFAULT_SERVICES),
        "config": {"heartbeat_interval_s": 5},
    }

def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

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
        # Si el serial no coincide, se regenera
        data = _default_structure(version, serial, host)
        _write_json(p, data)
        return data

    has_changes = False

    # Actualiza la versión si cambió
    if data.get("version", {}).get("version") != version:
        data["version"]["version"] = version
        has_changes = True

    identity = data.setdefault("identity", {})
    if identity.get("host") != host:
        identity["host"] = host
        has_changes = True

    if has_changes:
        _write_json(p, data)

    return data
