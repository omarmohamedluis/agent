# aqui se haran centraran las comunicaciones del sistema

import socket
import json
import random
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from logger import log_event, log_print
from heartbeat import get_heartbeat_snapshot

STRUCTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "structure.json"
SERVER_INFO_PATH = Path(__file__).resolve().parents[1] / "data" / "server.json"


module_name = f"{Path(__file__).parent.name}.{Path(__file__).stem}"

SERVER_TIMEOUT = 5.0
BROADCAST_PORT = 39653
HANDSHAKE_TIMEOUT = 10.0 

#    helpers

def _load_server_endpoint() -> Tuple[Optional[str], Optional[int]]:
    try:
        with SERVER_INFO_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        log_event("error", module_name, "No se pudo leer la información del servidor almacenada.")
        return None, None
    return data.get("ip"), data.get("port")

def _save_server_endpoint(ip: str, port: Optional[int]) -> None:
    # crea el json del servidor
    data = {"ip": ip}
    if port is not None:
        data["port"] = port
    with SERVER_INFO_PATH.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)

def _parse_broadcast_message(message: str) -> Tuple[Optional[str], Optional[int]]:
    # Extrae IP y puerto anunciados por el servidor a partir del broadcast.
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
    """Arma el JSON que enviaremos en el handshake."""
    identity = structure.get("identity", {})
    version_info = structure.get("version", {}).get("version")

    # Servicio marcado como enabled en structure.json (o dict vacío si ninguno)
    active_service = next(
        (svc for svc in structure.get("services", []) if svc.get("enabled")),
        {}
    )
    service_state = {
        "actual": active_service.get("name"),
        "configuration": active_service.get("configuration"),
        "web_port": active_service.get("web_port"),
    }

    heartbeat_snapshot = get_heartbeat_snapshot()  # {"cpu":.., "temp":.., "ifaces":[...]}

    cliente_payload = {
        "version": version_info,
        "serial": identity.get("serial"),
        "host": identity.get("host") or identity.get("name"),
        "index": identity.get("index"),
        "heartbeat": {
            "cpu": heartbeat_snapshot.get("cpu"),
            "temp": heartbeat_snapshot.get("temp")
        },
        "service_state": service_state,
    }
    return cliente_payload

def _listen_for_server_broadcast(port: int = BROADCAST_PORT, timeout: float = SERVER_TIMEOUT) -> bool:
    # Escucha el broadcast; si lo obtiene, guarda IP/puerto y devuelve True
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        sock.settimeout(timeout)
        try:
            message, address = sock.recvfrom(1024)
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

def _reply_server_with_handshake_request(cliente_payload: Dict[str, Any]) -> bool:
    log_print("info", module_name, "iniciando handshake")
    ip, port = _load_server_endpoint()
    if not ip or not port:
        log_print("error", module_name, "Endpoint del servidor no disponible")
        return False

    try:
        with socket.create_connection((ip, port), timeout=HANDSHAKE_TIMEOUT) as conn:
            log_event("debug", module_name, f"abriendo socket de handshake hacia {ip}:{port}")
            conn.settimeout(HANDSHAKE_TIMEOUT)

            message = {"type": "handshake", "cliente_payload": cliente_payload}
            conn.sendall(json.dumps(message).encode("utf-8") + b"\n")

            response_raw = conn.recv(65536)
            response = json.loads(response_raw.decode("utf-8"))

    except socket.timeout:
        log_event("error", module_name, "Timeout esperando respuesta del servidor")
        return False
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log_event("error", module_name, f"Handshake sin respuesta o inválido: {exc}")
        return False

    msg_type = response.get("type")
    if msg_type != "handshake_response":
        log_event("error", module_name, f"Respuesta inesperada del servidor: {msg_type}")
        return False

    return _handle_handshake_response(response)


def _update_active_service(
    services: list[Dict[str, Any]],
    target_name: Optional[str],
    configuration: Any,
) -> None:
    """Pone en `True` solo el servicio indicado y actualiza sus datos."""
    for service in services:
        is_target = service.get("name") == target_name
        service["enabled"] = bool(is_target)
        if is_target:
            service["configuration"] = configuration
        else:
            # Aseguramos que los demás queden desactivados
            service.setdefault("configuration", None)
    # Si el servicio objetivo no existe, todos quedan en False


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

    log_print("info", module_name, f"structure.json actualizado; servicio activo: {service_state.get('actual')}")
    log_event("debug", module_name, f"payload aplicado: {payload}")
    return True


def _handle_error(message: Dict[str, Any]) -> bool:
    log_event("error", module_name, f"Error recibido: {message}")
    return False


MESSAGE_HANDLERS: Dict[str, Any] = {
    "handshake_response": _handle_handshake_response,
    "error": _handle_error,
    # añade aquí otros (status_report, command, etc.)
}


#    public API

def handshake() -> bool:
    log_print("info", module_name, "iniciando handshake con el servidor")
    if _listen_for_server_broadcast():
        with STRUCTURE_PATH.open("r", encoding="utf-8") as handle:
            structure = json.load(handle)
        payload = _build_client_payload(structure)
        if _reply_server_with_handshake_request(payload):
            return True
    return False
    
def check_server_status() -> bool:
    """Devuelve True o False de forma aleatoria (~50% de probabilidad)."""
    return random.random() >= 0.5



