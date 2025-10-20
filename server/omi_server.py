import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.append(str(BASE_DIR))
sys.path.append(str(SRC_DIR))

from logger import log_print  # type: ignore

module_name = f"{Path(__file__).parent.name}.{Path(__file__).stem}"

BROADCAST_INTERVAL = 3.0
BROADCAST_PORT = 39653
HANDSHAKE_HOST = "0.0.0.0"
HANDSHAKE_PORT = 50500
PAYLOAD_TAG = "OMI_SERVER"

DATA_DIR = BASE_DIR / "data"
REGISTRY_PATH = DATA_DIR / "devices.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# --- helpers -----------------------------------------------------------------


def _get_local_ip() -> str:
    """Obtiene la IP local preferida para salir a la red."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname()) or "127.0.0.1"


def _load_registry() -> Dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {}
    try:
        with REGISTRY_PATH.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_registry(registry: Dict[str, Any]) -> None:
    with REGISTRY_PATH.open("w", encoding="utf-8") as handle:
        json.dump(registry, handle, indent=2, sort_keys=True)


def _send_json(conn: socket.socket, payload: Dict[str, Any]) -> None:
    conn.sendall(json.dumps(payload).encode("utf-8") + b"\n")


def _receive_json(conn: socket.socket, buffer_size: int = 4096) -> Dict[str, Any]:
    data = bytearray()
    while True:
        chunk = conn.recv(buffer_size)
        if not chunk:
            break
        data.extend(chunk)
        if b"\n" in chunk:
            break
    if not data:
        raise ValueError("Conexión cerrada sin datos")
    line = data.splitlines()[0]
    return json.loads(line.decode("utf-8"))


# --- broadcast ----------------------------------------------------------------


def broadcast_server_ip() -> None:
    payload = f"{PAYLOAD_TAG}|{{}}|{HANDSHAKE_PORT}".encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        log_print("info", module_name, f"iniciando broadcast UDP en el puerto {BROADCAST_PORT}")

        while True:
            ip = _get_local_ip()
            try:
                sock.sendto(payload.replace(b"{}", ip.encode("utf-8")), ("<broadcast>", BROADCAST_PORT))
                log_print("info", module_name, f"broadcast {PAYLOAD_TAG}|{ip}|{HANDSHAKE_PORT}")
            except OSError as exc:
                log_print("error", module_name, f"fallo broadcast: {exc}")
            time.sleep(BROADCAST_INTERVAL)


# --- handshake ----------------------------------------------------------------


def _default_service_request() -> Dict[str, Any]:
    return {"service_to_run": "standby", "configuration": None}


def handle_client(conn: socket.socket, addr) -> None:
    with conn:
        remote = f"{addr[0]}:{addr[1]}"
        log_print("info", module_name, f"handshake entrante desde {remote}")
        try:
            message = _receive_json(conn)
        except (ValueError, json.JSONDecodeError) as exc:
            log_print("error", module_name, f"handshake inválido desde {remote}: {exc}")
            return

        payload = message.get("cliente_payload")
        print(f"Payload recibido: {json.dumps(message, indent=2, ensure_ascii=False)}")
        os._exit(0)

        if not isinstance(payload, dict):
            log_print("error", module_name, f"payload sin 'cliente_payload' desde {remote}")
            _send_json(conn, {"error": "missing_cliente_payload"})
            return

        serial = payload.get("serial") or f"unknown-{remote}"

        registry = _load_registry()
        device = registry.get(serial, {})
        device.update(
            {
                "cliente_payload": payload,
                "status": "syncing",
                "last_ip": addr[0],
                "last_seen": time.time(),
            }
        )
        registry[serial] = device
        _save_registry(registry)

        stored_payload = device.get("cliente_payload", payload)
        service_request = device.get("service_request") or _default_service_request()
        response: Dict[str, Any] = {
            "cliente_payload": {
                "serial": stored_payload.get("serial"),
                "host": stored_payload.get("host"),
                "index": stored_payload.get("index"),
            },
            "service_request": service_request,
        }
        if device.get("service_config") is not None:
            response["service_config"] = device["service_config"]

        _send_json(conn, response)

        try:
            result = _receive_json(conn)
        except (ValueError, json.JSONDecodeError):
            log_print("warning", module_name, f"sin respuesta final de {serial}")
            return

        matches = bool(result.get("handshake_result"))
        device["status"] = "synced" if matches else "mismatch"
        device["last_seen"] = time.time()
        registry[serial] = device
        _save_registry(registry)
        log_print("info", module_name, f"handshake {serial} -> {device['status']}")


def start_handshake_server() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((HANDSHAKE_HOST, HANDSHAKE_PORT))
        server_sock.listen()
        log_print(
            "info",
            module_name,
            f"escuchando handshakes TCP en {HANDSHAKE_HOST}:{HANDSHAKE_PORT}",
        )
        while True:
            conn, addr = server_sock.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()


# --- main ---------------------------------------------------------------------


def main() -> None:
    log_print("info", module_name, "iniciando server")
    broadcaster = threading.Thread(target=broadcast_server_ip, daemon=True)
    broadcaster.start()

    try:
        start_handshake_server()
    except KeyboardInterrupt:
        log_print("warning", module_name, "server detenido por el usuario")


if __name__ == "__main__":
    main()
