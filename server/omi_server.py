import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.append(str(SRC_DIR))

from logger import log_event, log_print  # type: ignore
from omiDB import initialize_db, upsert_device  # type: ignore

module_name = f"{Path(__file__).parent.name}.{Path(__file__).stem}"

BROADCAST_INTERVAL = 3.0
BROADCAST_PORT = 39653
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 50500
PAYLOAD_TAG = "OMI_SERVER"

ACTIVE_CONNECTIONS: Dict[str, socket.socket] = {}
SESSIONS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers de socket
# ---------------------------------------------------------------------------

def _get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname()) or "127.0.0.1"


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
    return json.loads(data.splitlines()[0].decode("utf-8"))


def _register_session(serial: str, conn: socket.socket) -> None:
    with SESSIONS_LOCK:
        previous = ACTIVE_CONNECTIONS.get(serial)
        if previous and previous is not conn:
            try:
                previous.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                previous.close()
            except OSError:
                pass
        ACTIVE_CONNECTIONS[serial] = conn


def _cleanup_session(serial: Optional[str], conn: socket.socket) -> None:
    if serial is None:
        return
    with SESSIONS_LOCK:
        current = ACTIVE_CONNECTIONS.get(serial)
        if current is conn:
            ACTIVE_CONNECTIONS.pop(serial, None)


# ---------------------------------------------------------------------------
# Handlers de mensajes
# ---------------------------------------------------------------------------

def _handle_handshake(conn: socket.socket, addr, message: Dict[str, Any]) -> Dict[str, Any]:
    payload = message.get("cliente_payload")
    if not isinstance(payload, dict):
        log_event("error", module_name, f"payload sin 'cliente_payload' desde {addr}")
        _send_json(conn, {"type": "error", "message": "missing_cliente_payload"})
        return {"continue": False}

    device_payload = upsert_device(payload)
    serial = device_payload.get("serial")
    _send_json(conn, {"type": "handshake_response", "cliente_payload": device_payload})
    log_print("info", module_name, f"handshake {serial} procesado")

    if isinstance(serial, str):
        _register_session(serial, conn)
    return {"continue": True, "serial": serial}


def _handle_close(conn: socket.socket, addr, message: Dict[str, Any]) -> Dict[str, Any]:
    reason = message.get("reason") or "sin motivo"
    log_print("info", module_name, f"Cierre solicitado por {addr}: {reason}")
    try:
        _send_json(conn, {"type": "close_ack", "reason": "ack"})
    except OSError:
        pass
    return {"continue": False}


MESSAGE_HANDLERS: Dict[str, Any] = {
    "handshake": _handle_handshake,
    "close": _handle_close,
}


# ---------------------------------------------------------------------------
# Broadcast del servidor
# ---------------------------------------------------------------------------

def broadcast_server_ip() -> None:
    payload = f"{PAYLOAD_TAG}|{{}}|{SERVER_PORT}".encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        log_print("info", module_name, f"iniciando broadcast UDP en el puerto {BROADCAST_PORT}")

        while True:
            ip = _get_local_ip()
            try:
                sock.sendto(payload.replace(b"{}", ip.encode("utf-8")), ("<broadcast>", BROADCAST_PORT))
                log_event("info", module_name, f"broadcast {PAYLOAD_TAG}|{ip}|{SERVER_PORT}")
            except OSError as exc:
                log_event("error", module_name, f"fallo broadcast: {exc}")
            time.sleep(BROADCAST_INTERVAL)


# ---------------------------------------------------------------------------
# Gestión de clientes TCP
# ---------------------------------------------------------------------------

def handle_client(conn: socket.socket, addr) -> None:
    remote = f"{addr[0]}:{addr[1]}"
    log_print("info", module_name, f"conexión entrante desde {remote}")
    session_serial: Optional[str] = None

    try:
        while True:
            try:
                message = _receive_json(conn)
            except ValueError:
                log_event("warning", module_name, f"Conexión cerrada por {remote}")
                break
            except json.JSONDecodeError as exc:
                log_event("error", module_name, f"Mensaje JSON inválido desde {remote}: {exc}")
                continue
            except OSError as exc:
                log_event("error", module_name, f"Error de socket con {remote}: {exc}")
                break

            msg_type = message.get("type") or "handshake"
            handler = MESSAGE_HANDLERS.get(msg_type)
            if not handler:
                log_event("error", module_name, f"Tipo de mensaje desconocido: {msg_type}")
                _send_json(conn, {"type": "error", "message": "unsupported_type"})
                continue

            result = handler(conn, addr, message) or {}
            serial = result.get("serial")
            if serial and isinstance(serial, str):
                session_serial = serial
                _register_session(serial, conn)

            if not result.get("continue", True):
                break

    finally:
        _cleanup_session(session_serial, conn)
        try:
            conn.close()
        except OSError:
            pass
        log_print("info", module_name, f"Conexión con {remote} cerrada")


def start_listener_server() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((SERVER_HOST, SERVER_PORT))
        server_sock.listen()
        log_print("info", module_name, f"escuchando mensajes entrantes en TCP en {SERVER_HOST}:{SERVER_PORT}")

        while True:
            conn, addr = server_sock.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    log_print("info", module_name, "iniciando server")
    initialize_db()
    broadcaster = threading.Thread(target=broadcast_server_ip, daemon=True)
    broadcaster.start()
    start_listener_server()


if __name__ == "__main__":
    main()
