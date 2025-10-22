# aqui se haran centraran las comunicaciones del sistema

import json
import socket
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from heartbeat import get_heartbeat_snapshot
from logger import log_event, log_print

STRUCTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "structure.json"
SERVER_INFO_PATH = Path(__file__).resolve().parents[1] / "data" / "server.json"

module_name = f"{Path(__file__).parent.name}.{Path(__file__).stem}"

SERVER_TIMEOUT = 5.0
BROADCAST_PORT = 39653
HANDSHAKE_TIMEOUT = 10.0

# Estado de la comunicación persistente
_comm_lock = threading.Lock()
_comm_socket: Optional[socket.socket] = None
_receiver_thread: Optional[threading.Thread] = None
_receiver_stop = threading.Event()
_session_active = threading.Event()


# ---------------------------------------------------------------------------
# Helpers de archivo/configuración
# ---------------------------------------------------------------------------

def _load_server_endpoint() -> Tuple[Optional[str], Optional[int]]:
    try:
        with SERVER_INFO_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        log_event("error", module_name, "No se pudo leer la información del servidor almacenada.")
        return None, None
    return data.get("ip"), data.get("port")


def _save_server_endpoint(ip: str, port: Optional[int]) -> None:
    data = {"ip": ip}
    if port is not None:
        data["port"] = port
    with SERVER_INFO_PATH.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)


def _parse_broadcast_message(message: str) -> Tuple[Optional[str], Optional[int]]:
    if "|" in message:
        parts = message.split("|")
        if len(parts) >= 3:
            ip = parts[1].strip()
            try:
                port = int(parts[2].strip())
            except ValueError:
                return ip, None
            return ip, port
    if ":" in message:
        _, ip = message.split(":", 1)
        return ip.strip(), None
    return None, None


def _build_client_payload(structure: Dict[str, Any]) -> Dict[str, Any]:
    identity = structure.get("identity", {})
    version_info = structure.get("version", {}).get("version")

    active_service = next((svc for svc in structure.get("services", []) if svc.get("enabled")), {})
    service_state = {
        "actual": active_service.get("name"),
        "configuration": active_service.get("configuration"),
        "web_port": active_service.get("web_port"),
    }

    heartbeat_snapshot = get_heartbeat_snapshot()

    return {
        "version": version_info,
        "serial": identity.get("serial"),
        "host": identity.get("host") or identity.get("name"),
        "index": identity.get("index"),
        "heartbeat": {
            "cpu": heartbeat_snapshot.get("cpu"),
            "temp": heartbeat_snapshot.get("temp"),
        },
        "service_state": service_state,
    }


# ---------------------------------------------------------------------------
# Helpers de sockets
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Escucha del broadcast
# ---------------------------------------------------------------------------

def _listen_for_server_broadcast(port: int = BROADCAST_PORT, timeout: float = SERVER_TIMEOUT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        sock.settimeout(timeout)
        try:
            message, _ = sock.recvfrom(1024)
        except socket.timeout:
            log_event("error", module_name, "No se recibió broadcast en el tiempo esperado.")
            return False
        except OSError as exc:
            log_event("error", module_name, f"Error recibiendo broadcast: {exc}")
            return False

    message_text = message.decode("utf-8", errors="replace")
    ip, server_port = _parse_broadcast_message(message_text)
    if not ip:
        log_event("error", module_name, f"Broadcast con formato inesperado: {message_text}")
        return False

    _save_server_endpoint(ip, server_port)
    log_event(
        "info",
        module_name,
        f"broadcast recibido; endpoint actualizado a {ip}:{server_port or 'desconocido'}",
    )
    return True


# ---------------------------------------------------------------------------
# Gestión de mensajes entrantes desde el servidor
# ---------------------------------------------------------------------------

def _update_active_service(
    services: list[Dict[str, Any]],
    target_name: Optional[str],
    configuration: Any,
) -> None:
    for service in services:
        is_target = service.get("name") == target_name
        service["enabled"] = bool(is_target)
        if is_target:
            service["configuration"] = configuration
        else:
            service.setdefault("configuration", None)


def _handle_handshake_response(message: Dict[str, Any]) -> bool:
    payload = message.get("cliente_payload")
    if not isinstance(payload, dict):
        log_event("error", module_name, "Handshake: respuesta sin cliente_payload")
        return False

    try:
        with STRUCTURE_PATH.open("r", encoding="utf-8") as handle:
            structure = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        log_event("error", module_name, f"No se pudo leer structure.json: {exc}")
        return False

    identity = structure.setdefault("identity", {})
    if payload.get("host"):
        identity["host"] = payload["host"]
    if payload.get("index") is not None:
        identity["index"] = payload["index"]

    service_state = payload.get("service_state") or {}
    services = structure.setdefault("services", [])
    _update_active_service(
        services,
        service_state.get("actual"),
        service_state.get("configuration"),
    )

    try:
        with STRUCTURE_PATH.open("w", encoding="utf-8") as handle:
            json.dump(structure, handle, indent=2, ensure_ascii=False)
    except OSError as exc:
        log_event("error", module_name, f"No se pudo escribir structure.json: {exc}")
        return False

    log_print(
        "info",
        module_name,
        f"structure.json actualizado; servicio activo: {service_state.get('actual')}",
    )
    log_event("debug", module_name, f"payload aplicado: {payload}")
    return True


def _handle_server_error(message: Dict[str, Any]) -> bool:
    log_event("error", module_name, f"Error recibido del servidor: {message}")
    return True


def _handle_command(message: Dict[str, Any]) -> bool:
    log_print("info", module_name, f"Comando recibido del servidor: {message}")
    return True


def _handle_close_notice(message: Dict[str, Any]) -> bool:
    reason = message.get("reason") or "sin motivo"
    log_print("warning", module_name, f"El servidor cerró la comunicación: {reason}")
    _receiver_stop.set()
    return False


def _handle_close_ack(message: Dict[str, Any]) -> bool:
    log_event("debug", module_name, f"Confirmación de cierre del servidor: {message}")
    _receiver_stop.set()
    return False


MESSAGE_HANDLERS: Dict[str, Any] = {
    "handshake_response": _handle_handshake_response,
    "error": _handle_server_error,
    "command": _handle_command,
    "close": _handle_close_notice,
    "close_ack": _handle_close_ack,
}


def _handle_server_message(message: Dict[str, Any]) -> bool:
    msg_type = message.get("type")
    handler = MESSAGE_HANDLERS.get(msg_type)
    if handler is None:
        log_event("warning", module_name, f"Mensaje con tipo desconocido recibido: {msg_type}")
        return True
    try:
        return bool(handler(message))
    except Exception as exc:  # noqa: BLE001
        log_event("error", module_name, f"Fallo procesando mensaje {msg_type}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Gestión del canal persistente
# ---------------------------------------------------------------------------

def _receiver_loop() -> None:
    global _comm_socket, _receiver_thread
    while not _receiver_stop.is_set():
        with _comm_lock:
            conn = _comm_socket
        if conn is None:
            break
        try:
            message = _receive_json(conn)
        except ValueError:
            log_event("warning", module_name, "El servidor cerró la conexión de forma inesperada")
            break
        except (OSError, json.JSONDecodeError) as exc:
            log_event("error", module_name, f"Error leyendo del servidor: {exc}")
            break

        if not _handle_server_message(message):
            break

    _session_active.clear()
    _receiver_stop.set()
    with _comm_lock:
        if _comm_socket is not None:
            try:
                _comm_socket.close()
            except OSError:
                pass
            _comm_socket = None
    _receiver_thread = None
    log_print("info", module_name, "Canal de comunicación con el servidor cerrado")


def _start_receiver_thread() -> None:
    global _receiver_thread
    _receiver_stop.clear()
    _session_active.set()
    _receiver_thread = threading.Thread(
        target=_receiver_loop,
        name="ServerCommReceiver",
        daemon=True,
    )
    _receiver_thread.start()


def _open_comm_channel(cliente_payload: Dict[str, Any]) -> bool:
    global _comm_socket

    with _comm_lock:
        if _comm_socket is not None:
            log_event("warning", module_name, "Ya existe un canal de comunicación abierto")
            return True

    ip, port = _load_server_endpoint()
    if not ip or not port:
        log_print("error", module_name, "Endpoint del servidor no disponible")
        return False

    log_print("info", module_name, f"Estableciendo canal con el servidor {ip}:{port}")

    conn: Optional[socket.socket] = None
    try:
        conn = socket.create_connection((ip, port), timeout=HANDSHAKE_TIMEOUT)
        conn.settimeout(HANDSHAKE_TIMEOUT)
        _send_json(conn, {"type": "handshake", "cliente_payload": cliente_payload})
        response = _receive_json(conn)
    except socket.timeout:
        log_event("error", module_name, "Timeout esperando respuesta del servidor")
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        return False
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log_event("error", module_name, f"Handshake sin respuesta o inválido: {exc}")
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        return False

    if response.get("type") != "handshake_response":
        log_event("error", module_name, f"Respuesta inesperada del servidor: {response}")
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return False

    if not _handle_handshake_response(response):
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        return False

    if conn is None:
        return False

    conn.settimeout(None)
    with _comm_lock:
        _comm_socket = conn
    _start_receiver_thread()
    log_print("info", module_name, "Canal de comunicación establecido con el servidor")
    return True


def close_comm_channel(reason: str = "client_shutdown") -> None:
    global _comm_socket, _receiver_thread

    with _comm_lock:
        conn = _comm_socket
    if conn is None:
        return

    log_print("info", module_name, "Cerrando canal de comunicación con el servidor")
    notify_error = False
    try:
        _send_json(conn, {"type": "close", "reason": reason})
    except OSError as exc:
        log_event("warning", module_name, f"No se pudo notificar cierre al servidor: {exc}")
        notify_error = True

    if not notify_error:
        _receiver_stop.wait(timeout=2.0)

    _receiver_stop.set()
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        conn.close()
    except OSError:
        pass

    with _comm_lock:
        _comm_socket = None

    if _receiver_thread and _receiver_thread.is_alive():
        _receiver_thread.join(timeout=2.0)
    _receiver_thread = None
    _session_active.clear()


def send_message(message_type: str, body: Optional[Dict[str, Any]] = None) -> bool:
    payload = {"type": message_type}
    if body:
        payload.update(body)

    if not _session_active.is_set():
        log_event("error", module_name, "Intento de enviar mensaje sin un canal activo")
        return False

    with _comm_lock:
        conn = _comm_socket
    if conn is None:
        log_event("error", module_name, "No existe un canal de comunicación abierto")
        return False

    try:
        _send_json(conn, payload)
        return True
    except OSError as exc:
        log_event("error", module_name, f"No se pudo enviar el mensaje al servidor: {exc}")
        _receiver_stop.set()
        return False


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def handshake() -> bool:
    log_print("info", module_name, "iniciando handshake con el servidor")
    if not _listen_for_server_broadcast():
        return False

    with STRUCTURE_PATH.open("r", encoding="utf-8") as handle:
        structure = json.load(handle)
    payload = _build_client_payload(structure)
    return _open_comm_channel(payload)


def check_server_status() -> bool:
    return _session_active.is_set()


