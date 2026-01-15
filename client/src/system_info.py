import json
import socket
from pathlib import Path
from typing import Optional
from logger import log_event

module_name = "system_info"

def get_sys_version(project_root: Path) -> str:
    sys_info_path = project_root / "docs" / "SYS_INFO.JSON"
    if not sys_info_path.exists():
        log_event("warning", module_name, f"No se encontró SYS_INFO.JSON en {sys_info_path}")
        return ""

    try:
        with sys_info_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        log_event("error", module_name, f"Error leyendo SYS_INFO.JSON: {exc}")
        return ""

    return (
        data.get("VERSION")
        or data.get("VERISON")  # tolera el typo en la clave
        or data.get("version")
        or ""
    )

def get_serial() -> str:
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8")
        for line in cpuinfo.splitlines():
            if line.lower().startswith("serial"):
                _, _, value = line.partition(":")
                serial = value.strip()
                if serial:
                    return serial
    except FileNotFoundError:
        log_event("warning", module_name, "No se pudo leer /proc/cpuinfo para obtener el serial")
    except Exception as exc:
        log_event("warning", module_name, f"Fallo obteniendo el serial: {exc}")
    return "unknown-serial"

def get_host() -> str:
    try:
        return socket.gethostname()
    except Exception as exc:
        log_event("warning", module_name, f"Fallo obteniendo el hostname: {exc}")
        return "unknown-host"
