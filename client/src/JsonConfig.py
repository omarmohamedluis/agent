# jgestion del jsons

import ipaddress
import json
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from logger import log_event, log_print


module_name = f"{Path(__file__).parent.name}.{Path(__file__).stem}"

log_event("info", module_name, "iniciando lectura y edicion del json")

# helpers

def cargar_json(ruta: Path | str) -> dict[str, Any]:
    ruta = Path(ruta)
    with ruta.open("r", encoding="utf-8") as f:
        datos = json.load(f)
    return datos


def guardar_json(ruta: Path | str, datos: dict[str, Any]) -> None:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)


def _load_sys_version(project_root: Path) -> str:
    sys_info_path = project_root / "docs" / "SYS_INFO.JSON"
    if not sys_info_path.exists():
        log_event("warning", module_name, f"No se encontró SYS_INFO.JSON en {sys_info_path}")
        return ""

    try:
        with sys_info_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:  # noqa: BLE001
        log_event("error", module_name, f"Error leyendo SYS_INFO.JSON: {exc}")
        return ""

    return (
        data.get("VERSION")
        or data.get("VERISON")  # tolera el typo en la clave
        or data.get("version")
        or ""
    )


def _get_serial() -> str:
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
    except Exception as exc:  # noqa: BLE001
        log_event("warning", module_name, f"Fallo obteniendo el serial: {exc}")
    return "unknown-serial"


def _get_host() -> str:
    try:
        return socket.gethostname()
    except Exception as exc:  # noqa: BLE001
        log_event("warning", module_name, f"Fallo obteniendo el hostname: {exc}")
        return "unknown-host"


def _run_ip_json(*args: str) -> list[dict[str, Any]]:
    try:
        output = subprocess.check_output(["ip", "-json", *args], text=True)
        return json.loads(output)
    except FileNotFoundError:
        log_event("error", module_name, "Comando 'ip' no disponible en el sistema")
    except subprocess.CalledProcessError as exc:
        log_event("error", module_name, f"Fallo ejecutando 'ip {' '.join(args)}': {exc}")
    except json.JSONDecodeError as exc:
        log_event("error", module_name, f"Respuesta inválida de 'ip {' '.join(args)}': {exc}")
    except Exception as exc:  # noqa: BLE001
        log_event("error", module_name, f"Error inesperado usando 'ip {' '.join(args)}': {exc}")
    return []


def _get_vlan_map() -> dict[str, str]:
    vlan_map: dict[str, str] = {}
    for link in _run_ip_json("link", "show"):
        ifname = link.get("ifname")
        if not ifname or ifname == "lo":
            continue
        linkinfo = link.get("linkinfo") or {}
        if linkinfo.get("info_kind") == "vlan":
            vlan_id = (linkinfo.get("info_data") or {}).get("id")
            if vlan_id is not None:
                vlan_map[ifname] = str(vlan_id)
    return vlan_map


def _get_network_interfaces() -> list[dict[str, Any]]:
    interfaces: list[dict[str, Any]] = []
    vlan_map = _get_vlan_map()

    for entry in _run_ip_json("addr", "show"):
        ifname = entry.get("ifname")
        if not ifname:
            continue
        if ifname == "lo":
            continue

        for addr in entry.get("addr_info", []):
            if addr.get("family") != "inet":
                continue

            ip_addr = addr.get("local")
            prefixlen = addr.get("prefixlen")
            if not ip_addr or prefixlen is None:
                continue

            try:
                iface = ipaddress.IPv4Interface(f"{ip_addr}/{prefixlen}")
                netmask = str(iface.netmask)
                cidr = iface.with_prefixlen
            except ValueError:
                netmask = ""
                cidr = f"{ip_addr}/{prefixlen}"

            interfaces.append(
                {
                    "name": ifname,
                    "ip": ip_addr,
                    "netmask": netmask,
                    "cidr": cidr,
                    "vlan": vlan_map.get(ifname),
                }
            )

    if not interfaces:
        log_event("warning", module_name, "No se detectaron interfaces de red IPv4")

    return interfaces


def _fill_json(datos: dict[str, Any], data_path: Path, project_root: Path) -> dict[str, Any]:
    version = _load_sys_version(project_root)
    if version:
        datos.setdefault("version", {})["version"] = version

    identity = datos.setdefault("identity", {})
    identity.setdefault("index", identity.get("index"))
    identity["serial"] = _get_serial()
    identity["host"] = _get_host()
    identity.setdefault("name", identity["host"])
    if isinstance(identity.get("name"), str) and identity["name"].strip() == "":
        identity["name"] = identity["host"]

    network_section = datos.setdefault("network", {})
    network_section["interfaces"] = _get_network_interfaces()

    guardar_json(data_path, datos)
    log_print("info", module_name, "structure.json actualizado")

    return datos


# llamadas desde fuera

def InitJson() -> dict[str, Any]:
    log_event("info", module_name, "iniciando lectura y edicion del json")

    project_root = Path(__file__).resolve().parents[2]
    data_path = project_root / "client" / "data" / "structure.json"
    template_path = project_root / "docs" / "InfoClient.json"

    if not data_path.exists():
        if not template_path.exists():
            log_event(
                "error",
                module_name,
                f"No se encontró la plantilla InfoClient.json en {template_path}",
            )
            raise FileNotFoundError(f"No se encontró la plantilla InfoClient.json en {template_path}")

        log_event(
            "warning",
            module_name,
            "structure.json no encontrado, creando copia desde docs/InfoClient.json",
        )
        data_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template_path, data_path)

    datos = cargar_json(data_path)
    datos = _fill_json(datos, data_path, project_root)

    return datos



def UpdateNet():
    log_event("info", module_name, "actualizadno info de interfaces")
