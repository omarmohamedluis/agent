# agent_listener.py
from __future__ import annotations

import copy
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from AppHandler import (
    current_logical_service,
    get_status as get_service_runtime_status,
    set_runtime_env,
    start_service,
)
from heartbeat import UPDATEHB
from jsonconfig import (
    STANDBY_SERVICE,
    discover_services,
    ensure_config,
    get_enabled_service,
    read_config,
    set_active_service,
)
from logger import get_agent_logger
from ui import EstandardUse, LoadingUI, ErrorUI, ErrorUIBlink, UIOFF

SOFVERSION = "0.0.1"
BCAST_PORT = 37020
SERVER_REPLY_PORT = 37021
STRUCTURE_PATH = Path(__file__).resolve().parent / "agent_pi" / "data" / "structure.json"
SERVER_TIMEOUT_S = 5.0
SERVICE_MONITOR_INTERVAL_S = 2.0

logger = get_agent_logger()

SERVER_HTTP_PORT_DEFAULT = 8000
SERVER_INFO_PATH = Path(__file__).resolve().parent / "agent_pi" / "data" / "server.json"
SERVER_API_BASE: Optional[str] = None

SNAPSHOT_LOCK = threading.Lock()
CONFIG_LOCK = threading.Lock()
SERVICE_LOCK = threading.RLock()

CURRENT_SNAPSHOT: Dict[str, Any] | None = None
CFG: Dict[str, Any] = {}
SERVER_ONLINE = False
LAST_SERVER_CONTACT = 0.0

STOP_REFRESH = threading.Event()
REFRESH_THREAD: Optional[threading.Thread] = None
SERVICE_MONITOR_STOP = threading.Event()
SERVICE_MONITOR_THREAD: Optional[threading.Thread] = None

SERVICE_STATUS: Dict[str, Any] = {
    "expected": STANDBY_SERVICE,
    "actual": None,
    "logical": STANDBY_SERVICE,
    "running": False,
    "pid": None,
    "last_error": None,
    "returncode": None,
    "config_name": None,
    "web_url": None,
    "timestamp": 0.0,
    "error": None,
}
SERVICE_ERROR: Optional[str] = None


def _set_config(data: Dict[str, Any]) -> None:
    global CFG
    with CONFIG_LOCK:
        CFG = data


def _update_server_api(server_ip: str, http_port: Optional[int]) -> None:
    global SERVER_API_BASE
    port = http_port or SERVER_HTTP_PORT_DEFAULT
    SERVER_API_BASE = f"http://{server_ip}:{port}"
    identity = _current_config().get("identity", {}) if CFG else {}
    info = {
        "api": SERVER_API_BASE,
        "serial": (identity or {}).get("serial", ""),
        "host": (identity or {}).get("host", ""),
    }
    set_runtime_env(
        {
            "OMI_SERVER_API": info["api"],
            "OMI_AGENT_SERIAL": info["serial"],
            "OMI_AGENT_HOST": info["host"],
        }
    )
    _write_server_info(info)
    _upload_midi_config_to_server()
    logger.info("Servidor API detectado en %s", SERVER_API_BASE)




def _write_server_info(info: Dict[str, Any]) -> None:
    try:
        SERVER_INFO_PATH.parent.mkdir(parents=True, exist_ok=True)
        SERVER_INFO_PATH.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("No se pudo escribir server.json: %s", exc)
def _current_config() -> Dict[str, Any]:
    with CONFIG_LOCK:
        return copy.deepcopy(CFG)


def _primary_ip(snapshot: Dict[str, Any] | None) -> Optional[str]:
    if not snapshot:
        return None
    ifaces = snapshot.get("ifaces") or []
    wifi = [i for i in ifaces if isinstance(i.get("iface"), str) and i["iface"].lower().startswith("wl")]
    candidates = wifi or ifaces
    for iface in candidates:
        ip = iface.get("ip")
        if ip and not ip.startswith("127."):
            return ip
    return None


def _midi_map_path() -> Path:
    return Path(__file__).resolve().parent / "servicios" / "MIDI" / "OMIMIDI_map.json"


def _read_midi_config() -> Dict[str, Any]:
    try:
        return json.loads(_midi_map_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_midi_config(data: Dict[str, Any]) -> None:
    _midi_map_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")



def _upload_midi_config_to_server() -> None:
    if not SERVER_API_BASE:
        return
    data = _read_midi_config()
    if not data:
        return
    info = {
        "name": data.get("config_name", "default"),
        "data": data,
        "serial": (_current_config().get("identity", {}) or {}).get("serial", ""),
        "host": (_current_config().get("identity", {}) or {}).get("host", ""),
        "source": "client_sync",
        "overwrite": True,
    }
    try:
        payload = json.dumps(info).encode('utf-8')
        req = urllib.request.Request(f"{SERVER_API_BASE}/api/configs/MIDI", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5)
        logger.info("Preset MIDI sincronizado con el servidor")
    except Exception as exc:
        logger.warning("No se pudo sincronizar preset local con servidor: %s", exc)

def _download_service_config(service: str, config_name: str) -> None:
    if not SERVER_API_BASE:
        raise RuntimeError("sin servidor API disponible")
    url = f"{SERVER_API_BASE}/api/configs/{service}/{config_name}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            logger.warning("Preset %s/%s no existe en servidor, se mantiene configuración local", service, config_name)
            return
        raise RuntimeError(f"configuración '{config_name}' no disponible ({exc.code})") from exc
    except Exception as exc:
        raise RuntimeError(f"no se pudo descargar la configuración '{config_name}'") from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    if service == "MIDI":
        if not isinstance(data, dict):
            raise RuntimeError("datos de configuración MIDI inválidos")
        if config_name and not data.get("config_name"):
            data["config_name"] = config_name
        _write_midi_config(data)
    else:
        raise RuntimeError(f"descarga de configuración no soportada para {service}")


def _update_service_status(*, expected: Optional[str] = None, runtime: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    global SERVICE_STATUS
    if runtime is None:
        runtime = get_service_runtime_status()
    if expected is None:
        expected = get_enabled_service(_current_config()) or STANDBY_SERVICE

    config_name = None
    web_url = None
    if expected == "MIDI":
        midi_cfg = _read_midi_config()
        config_name = midi_cfg.get("config_name")
        if bool(runtime.get("running")):
            snap = _current_snapshot()
            primary_ip = _primary_ip(snap)
            port = midi_cfg.get("ui_port", 9001)
            if primary_ip:
                web_url = f"http://{primary_ip}:{port}"

    state = {
        "expected": expected,
        "actual": runtime.get("name"),
        "logical": runtime.get("logical"),
        "running": bool(runtime.get("running")),
        "pid": runtime.get("pid"),
        "last_error": runtime.get("last_error"),
        "returncode": runtime.get("returncode"),
        "config_name": config_name,
        "web_url": web_url,
        "timestamp": time.time(),
        "error": SERVICE_ERROR,
    }

    with SERVICE_LOCK:
        SERVICE_STATUS.update(state)
        return copy.deepcopy(SERVICE_STATUS)


def _get_service_state() -> Dict[str, Any]:
    with SERVICE_LOCK:
        return copy.deepcopy(SERVICE_STATUS)


def _set_service_error(message: str) -> None:
    global SERVICE_ERROR
    SERVICE_ERROR = message
    logger.warning(message)
    state = _get_service_state()
    service_label = (state.get("expected") or "").upper()
    ui_label = f"{service_label[:10]} ERR" if service_label else "ERROR"
    try:
        ErrorUIBlink(ui_label)
    except Exception:
        try:
            ErrorUI(ui_label)
        except Exception:
            pass
    _update_service_status()


def _clear_service_error() -> None:
    global SERVICE_ERROR
    if SERVICE_ERROR:
        SERVICE_ERROR = None
        _update_service_status()


def _reset_server_status() -> None:
    global SERVER_ONLINE, LAST_SERVER_CONTACT
    with SNAPSHOT_LOCK:
        SERVER_ONLINE = False
        LAST_SERVER_CONTACT = 0.0


def _set_snapshot(data: Dict[str, Any]) -> None:
    global CURRENT_SNAPSHOT
    with SNAPSHOT_LOCK:
        CURRENT_SNAPSHOT = data


def _current_snapshot() -> Optional[Dict[str, Any]]:
    with SNAPSHOT_LOCK:
        return copy.deepcopy(CURRENT_SNAPSHOT)


def _get_snapshot(use_fallback: bool = True) -> Dict[str, Any]:
    with SNAPSHOT_LOCK:
        snapshot = CURRENT_SNAPSHOT
    if snapshot is None and use_fallback:
        snapshot = UPDATEHB(STRUCTURE_PATH)
        _set_snapshot(snapshot)
    return copy.deepcopy(snapshot) if snapshot is not None else {}


def _mark_server_seen() -> None:
    global SERVER_ONLINE, LAST_SERVER_CONTACT
    with SNAPSHOT_LOCK:
        SERVER_ONLINE = True
        LAST_SERVER_CONTACT = time.time()


def _server_is_online() -> bool:
    global SERVER_ONLINE
    with SNAPSHOT_LOCK:
        if not SERVER_ONLINE or LAST_SERVER_CONTACT == 0.0:
            return False
        if (time.time() - LAST_SERVER_CONTACT) > SERVER_TIMEOUT_S:
            SERVER_ONLINE = False
            return False
        return True


def _refresh_loop() -> None:
    while not STOP_REFRESH.is_set():
        try:
            snap = UPDATEHB(STRUCTURE_PATH)
            _set_snapshot(snap)
            try:
                EstandardUse(snap, server_online=_server_is_online(), json_path=STRUCTURE_PATH)
            except Exception:
                pass
        except Exception as exc:
            logger.exception("Error actualizando snapshot: %s", exc)
        STOP_REFRESH.wait(1.0)


def _start_refresh_thread() -> None:
    global REFRESH_THREAD
    if REFRESH_THREAD and REFRESH_THREAD.is_alive():
        return
    STOP_REFRESH.clear()
    REFRESH_THREAD = threading.Thread(target=_refresh_loop, name="omi-refresh", daemon=True)
    REFRESH_THREAD.start()


def _stop_refresh_thread() -> None:
    global REFRESH_THREAD
    STOP_REFRESH.set()
    if REFRESH_THREAD and REFRESH_THREAD.is_alive():
        REFRESH_THREAD.join(timeout=1.5)
    REFRESH_THREAD = None


def _start_service_monitor() -> None:
    global SERVICE_MONITOR_THREAD
    if SERVICE_MONITOR_THREAD and SERVICE_MONITOR_THREAD.is_alive():
        return
    SERVICE_MONITOR_STOP.clear()
    SERVICE_MONITOR_THREAD = threading.Thread(target=_service_monitor_loop, name="omi-service-monitor", daemon=True)
    SERVICE_MONITOR_THREAD.start()


def _stop_service_monitor() -> None:
    global SERVICE_MONITOR_THREAD
    SERVICE_MONITOR_STOP.set()
    if SERVICE_MONITOR_THREAD and SERVICE_MONITOR_THREAD.is_alive():
        SERVICE_MONITOR_THREAD.join(timeout=1.5)
    SERVICE_MONITOR_THREAD = None


def _service_monitor_loop() -> None:
    while not SERVICE_MONITOR_STOP.is_set():
        try:
            cfg = _current_config()
            expected = get_enabled_service(cfg) or STANDBY_SERVICE
            runtime = get_service_runtime_status()
            state = _update_service_status(expected=expected, runtime=runtime)
            current_config_name = state.get("config_name") if isinstance(state, dict) else None

            if expected != STANDBY_SERVICE and not runtime.get("running"):
                rc = runtime.get("returncode")
                label = (expected or "").upper()
                message = f"{label} ERROR (rc={rc})" if rc is not None else f"{label} ERROR"
                _set_service_error(message)
                try:
                    _apply_active_service(expected, config_name=current_config_name)
                    logger.info("Servicio '%s' relanzado después de una caída (rc=%s)", expected, rc)
                except Exception as exc:
                    logger.error("No se pudo relanzar '%s': %s", expected, exc)
                    try:
                        _apply_active_service(STANDBY_SERVICE)
                    except Exception as inner:
                        logger.error("No se pudo forzar standby tras fallo: %s", inner)
            elif runtime.get("running") and SERVICE_ERROR:
                _clear_service_error()
        except Exception as exc:
            logger.exception("Error en monitor de servicios: %s", exc)
        finally:
            SERVICE_MONITOR_STOP.wait(SERVICE_MONITOR_INTERVAL_S)


def _apply_active_service(service: str, *, config_name: Optional[str] = None) -> Dict[str, Any]:
    service = (service or "").strip()
    if not service:
        raise ValueError("nombre de servicio vacío")

    available = discover_services()
    if service not in available:
        raise ValueError(f"servicio desconocido: {service}")

    with SERVICE_LOCK:
        snapshot = _current_config() or read_config(STRUCTURE_PATH)
        previous = get_enabled_service(snapshot) or STANDBY_SERVICE
        runtime = get_service_runtime_status()
        running_same = runtime.get("running") and runtime.get("name") == service

        if service == previous and running_same:
            if config_name and service != STANDBY_SERVICE:
                _download_service_config(service, config_name)
            cfg = set_active_service(STRUCTURE_PATH, service)
            _set_config(cfg)
            _update_service_status(expected=service, runtime=runtime)
            return cfg

        if service == STANDBY_SERVICE:
            start_service(STANDBY_SERVICE)
            cfg = set_active_service(STRUCTURE_PATH, service)
            _set_config(cfg)
            _update_service_status(expected=service)
            _clear_service_error()
            return cfg

        if config_name:
            _download_service_config(service, config_name)

        if not start_service(service):
            raise RuntimeError(f"no se pudo iniciar el servicio '{service}'")

        try:
            cfg = set_active_service(STRUCTURE_PATH, service)
        except Exception as exc:
            start_service(previous)
            raise exc

        _set_config(cfg)
        _update_service_status(expected=service)
        _clear_service_error()
        return cfg


def _handle_service_command(payload: Dict[str, Any], s_reply: socket.socket, addr) -> None:
    request_id = payload.get("request_id")
    service = payload.get("service")
    config_target = payload.get("config")
    reply_port = int(payload.get("reply_port", addr[1])) if payload.get("reply_port") else SERVER_REPLY_PORT
    serial = (_current_config().get("identity", {}) or {}).get("serial")

    response: Dict[str, Any] = {
        "type": "SERVICE_ACK",
        "request_id": request_id,
        "service": service,
        "serial": serial,
        "ok": False,
        "error": None,
        "services": None,
        "service_state": None,
    }

    try:
        cfg = _apply_active_service(service, config_name=config_target)
        response["ok"] = True
        response["services"] = cfg.get("services")
        response["service_state"] = _get_service_state()
        logger.info("Servicio activo cambiado a '%s' por petición de %s", service, addr[0])
    except Exception as exc:
        message = str(exc)
        _set_service_error(message)
        response["error"] = message
        response["services"] = _current_config().get("services")
        response["service_state"] = _get_service_state()
        logger.error("Error cambiando servicio a '%s': %s", service, exc)

    if config_target:
        response["config"] = config_target

    try:
        s_reply.sendto(json.dumps(response).encode("utf-8"), (addr[0], reply_port))
    except Exception as exc:
        logger.error("Error enviando ACK al servidor: %s", exc)


def _build_status_payload(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _current_config()
    identity = cfg.get("identity", {})
    state = _update_service_status()
    return {
        "type": "AGENT_STATUS",
        "serial": identity.get("serial") or "pi-unknown",
        "index": identity.get("index"),
        "name": identity.get("name"),
        "host": identity.get("host") or "unknown-host",
        "version": cfg.get("version", {}).get("version", SOFVERSION),
        "services": cfg.get("services", []),
        "available_services": discover_services(),
        "service_state": state,
        "server_api": SERVER_API_BASE,
        "heartbeat": {
            "cpu": snapshot.get("cpu"),
            "temp": snapshot.get("temp"),
            "ifaces": snapshot.get("ifaces"),
        },
        "logical_service": current_logical_service(),
    }


LoadingUI(0, "INICIANDO")

time.sleep(1)

LoadingUI(30, "LEYENDO")

logger.info("Inicializando agente OMI")
_reset_server_status()

cfg_boot = ensure_config(STRUCTURE_PATH, version=SOFVERSION)
_set_config(cfg_boot)
identity_boot = cfg_boot.get("identity", {})
set_runtime_env(
    {
        "OMI_AGENT_SERIAL": identity_boot.get("serial", ""),
        "OMI_AGENT_HOST": identity_boot.get("host", ""),
    }
)
_update_service_status()

initial_service = get_enabled_service(cfg_boot) or STANDBY_SERVICE
try:
    _apply_active_service(initial_service)
except Exception as exc:
    _set_service_error(f"Inicio fallido: {exc}")

_initial_snap = UPDATEHB(STRUCTURE_PATH)
_set_snapshot(_initial_snap)

LoadingUI(40, "CARGADO")


def listen_and_reply():
    _reset_server_status()

    try:
        LoadingUI(50, "ESPERANDO SERVER")
    except Exception:
        pass

    _start_refresh_thread()
    _start_service_monitor()

    s_listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s_listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s_listen.bind(("", BCAST_PORT))
    s_listen.settimeout(0.5)
    logger.info("Escuchando broadcast en :%s", BCAST_PORT)

    s_reply = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        while True:
            try:
                data, addr = s_listen.recvfrom(4096)
            except socket.timeout:
                continue

            try:
                payload = json.loads(data.decode("utf-8", "ignore"))
            except Exception as exc:
                logger.warning("Mensaje inválido desde %s: %s", addr[0], exc)
                continue

            msg_type = payload.get("type")

            if msg_type == "DISCOVER":
                server_ip = payload.get("server_ip") or addr[0]
                reply_port = int(payload.get("reply_port", SERVER_REPLY_PORT))
                _update_server_api(server_ip, payload.get("http_port"))
                snap = _get_snapshot()
                reply = _build_status_payload(snap)
                try:
                    s_reply.sendto(json.dumps(reply).encode("utf-8"), (server_ip, reply_port))
                    logger.info("Estado enviado a %s:%s", server_ip, reply_port)
                    _mark_server_seen()
                except Exception as exc:
                    logger.error("Error enviando estado al servidor: %s", exc)

            elif msg_type == "SET_SERVICE":
                logger.info("Solicitud de cambio de servicio desde %s → %s", addr[0], payload.get("service"))
                _handle_service_command(payload, s_reply, addr)
                _mark_server_seen()

            else:
                logger.debug("Mensaje no reconocido de %s: %s", addr[0], msg_type)

    except KeyboardInterrupt:
        logger.info("Agente detenido por usuario")
    finally:
        _stop_service_monitor()
        _stop_refresh_thread()
        try:
            s_listen.close()
        except Exception:
            pass
        try:
            s_reply.close()
        except Exception:
            pass
        try:
            UIOFF()
        except Exception:
            pass


if __name__ == "__main__":
    listen_and_reply()
