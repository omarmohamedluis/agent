# agent_listener.py
import copy
import json
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict

from AppHandler import current_logical_service, start_service
from heartbeat import UPDATEHB
from jsonconfig import (
    STANDBY_SERVICE,
    discover_services,
    ensure_config,
    get_enabled_service,
    read_config,
    set_active_service,
)
from ui import EstandardUse, LoadingUI, UIOFF

SOFVERSION = "0.0.1"
BCAST_PORT = 37020
SERVER_REPLY_PORT = 37021
STRUCTURE_PATH = Path(__file__).resolve().parent / "agent_pi" / "data" / "structure.json"

SNAPSHOT_LOCK = threading.Lock()
CONFIG_LOCK = threading.Lock()
CURRENT_SNAPSHOT: Dict[str, Any] | None = None
CFG: Dict[str, Any] = {}
SERVER_ONLINE = False
LAST_SERVER_CONTACT = 0.0
SERVER_TIMEOUT_S = 5.0
STOP_REFRESH = threading.Event()
REFRESH_THREAD: threading.Thread | None = None


def _reset_server_status() -> None:
    global SERVER_ONLINE, LAST_SERVER_CONTACT
    with SNAPSHOT_LOCK:
        SERVER_ONLINE = False
        LAST_SERVER_CONTACT = 0.0


def _set_snapshot(data: Dict[str, Any]) -> None:
    global CURRENT_SNAPSHOT
    with SNAPSHOT_LOCK:
        CURRENT_SNAPSHOT = data


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
            print(f"[agent] error actualizando snapshot: {exc}")
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


def _current_config() -> Dict[str, Any]:
    with CONFIG_LOCK:
        return copy.deepcopy(CFG)


def _apply_active_service(service: str) -> Dict[str, Any]:
    service = (service or "").strip()
    if not service:
        raise ValueError("empty service name")

    available = discover_services()
    if service not in available:
        raise ValueError(f"unknown service: {service}")

    before = _current_config() or read_config(STRUCTURE_PATH)
    previous = get_enabled_service(before) or STANDBY_SERVICE

    if service == previous:
        cfg = set_active_service(STRUCTURE_PATH, service)
        with CONFIG_LOCK:
            CFG = cfg
        return cfg

    if not start_service(service):
        raise RuntimeError(f"no se pudo iniciar el servicio '{service}'")

    try:
        cfg = set_active_service(STRUCTURE_PATH, service)
    except Exception as exc:
        # revertir estado lógico al anterior
        start_service(previous)
        raise exc

    with CONFIG_LOCK:
        CFG = cfg
    return cfg


def _handle_service_command(payload: Dict[str, Any], s_reply: socket.socket, addr) -> None:
    request_id = payload.get("request_id")
    service = payload.get("service")
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
    }

    services_state = None
    try:
        cfg = _apply_active_service(service)
        response["ok"] = True
        services_state = cfg.get("services")
        print(f"[agent] servicio activo cambiado a {service}")
    except Exception as exc:
        response["error"] = str(exc)
        services_state = _current_config().get("services")
        print(f"[agent] error cambiando servicio a {service}: {exc}")

    response["services"] = services_state

    try:
        s_reply.sendto(json.dumps(response).encode("utf-8"), (addr[0], reply_port))
    except Exception as exc:
        print(f"[agent] error enviando ACK: {exc}")


def _build_status_payload(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _current_config()
    identity = cfg.get("identity", {})
    return {
        "type": "AGENT_STATUS",
        "serial": identity.get("serial") or "pi-unknown",
        "index": identity.get("index"),
        "name": identity.get("name"),
        "host": identity.get("host") or "unknown-host",
        "version": cfg.get("version", {}).get("version", SOFVERSION),
        "services": cfg.get("services", []),
        "available_services": discover_services(),
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

_reset_server_status()

CFG = ensure_config(STRUCTURE_PATH, version=SOFVERSION)
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

    s_listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s_listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s_listen.bind(("", BCAST_PORT))
    s_listen.settimeout(0.5)
    print(f"[agent] escuchando broadcast en :{BCAST_PORT}")

    s_reply = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        while True:
            try:
                data, addr = s_listen.recvfrom(4096)
            except socket.timeout:
                continue

            msg = data.decode("utf-8", "ignore")
            print(f"✓ mensaje recibido de {addr[0]}:{addr[1]} → {msg}")

            try:
                payload = json.loads(msg)
            except Exception as exc:
                print(f"[agent] JSON inválido: {exc}")
                continue

            msg_type = payload.get("type")

            if msg_type == "DISCOVER":
                server_ip = payload.get("server_ip") or addr[0]
                reply_port = int(payload.get("reply_port", SERVER_REPLY_PORT))
                snap = _get_snapshot()
                reply = _build_status_payload(snap)
                try:
                    s_reply.sendto(json.dumps(reply).encode("utf-8"), (server_ip, reply_port))
                    print(f"→ estado enviado a {server_ip}:{reply_port}")
                    _mark_server_seen()
                except Exception as exc:
                    print(f"[agent] error enviando estado: {exc}")

            elif msg_type == "SET_SERVICE":
                _handle_service_command(payload, s_reply, addr)
                _mark_server_seen()

            else:
                print(f"[agent] tipo de mensaje desconocido: {msg_type}")

    except KeyboardInterrupt:
        print("\n[agent] detenido por usuario.")
    finally:
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
