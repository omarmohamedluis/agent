import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent #carpeta server
SRC_DIR = BASE_DIR / "src"
sys.path.append(str(SRC_DIR))

from logger import log_event, log_print  # type: ignore
from omiDB import initialize_db, upsert_device # type: ignore
module_name = f"{Path(__file__).parent.name}.{Path(__file__).stem}"

BROADCAST_INTERVAL = 3.0
BROADCAST_PORT = 39653
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 50500
PAYLOAD_TAG = "OMI_SERVER"

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)




# --- helpers -----------------------------------------------------------------


def _get_local_ip() -> str:
    #Obtiene la IP local preferida para salir a la red
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
    line = data.splitlines()[0]
    return json.loads(line.decode("utf-8"))


# --- helpers messages----------------------------------------------------------

def _handle_handshake(conn: socket.socket, addr, message: Dict[str, Any]) -> None:
    payload = message.get("cliente_payload")
    if not isinstance(payload, dict):
        log_event("error", module_name, f"payload sin 'cliente_payload' desde {addr}")
        _send_json(conn, {"error": "missing_cliente_payload"})
        return
    device_payload = upsert_device(payload)
    _send_json(conn, {"type": "handshake_response", "cliente_payload": device_payload})
    log_print("info", module_name, f"handshake {device_payload.get('serial')} procesado")


# --- broadcast ----------------------------------------------------------------


def broadcast_server_ip() -> None:
    print("vuelta")
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


# --- biblioteca de handlers ----------------------------------------------------------------

MESSAGE_HANDLERS = {
    "handshake": _handle_handshake
    # otros tipos en el futuro
}

# --- comunicacion con clientes -------------------------------------------------------------


def handle_client(conn: socket.socket, addr) -> None:
    with conn:
        remote = f"{addr[0]}:{addr[1]}"
        log_print("info", module_name, f"conexión entrante desde {remote}")
        try:
            message = _receive_json(conn)
        except (ValueError, json.JSONDecodeError) as exc:
            log_event("error", module_name, f"mensaje inválido desde {remote}: {exc}")
            return

        msg_type = message.get("type") or "handshake"  # por compatibilidad
        handler = MESSAGE_HANDLERS.get(msg_type)
        if not handler:
            log_event("error", module_name, f"Tipo de mensaje desconocido: {msg_type}")
            _send_json(conn, {"error": "unsupported_type"})
            return

        handler(conn, addr, message)



def start_listent_server() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((SERVER_HOST, SERVER_PORT))
        server_sock.listen()
        log_print(
            "info",
            module_name,
            f"escuchando mensajes entrantes en TCP en {SERVER_HOST}:{SERVER_PORT}",
        )
        while True:
            conn, addr = server_sock.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()


# --- main ---------------------------------------------------------------------





def main() -> None:
    log_print("info", module_name, "iniciando server")
    initialize_db()
    broadcaster = threading.Thread(target=broadcast_server_ip, daemon=True)
    broadcaster.start()
    start_listent_server()


if __name__ == "__main__":
    main()
