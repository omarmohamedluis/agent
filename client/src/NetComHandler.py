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


#    helpers

def _persist_server_endpoint(ip: str, port: Optional[int]) -> None:
    data = {"ip": ip}
    if port is not None:
        data["port"] = port
    with SERVER_INFO_PATH.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)

def _parse_broadcast_message(message: str) -> Tuple[Optional[str], Optional[int]]:
    """Extrae IP y puerto anunciados por el servidor a partir del broadcast."""
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


def _build_cliente_payload(structure: Dict[str, Any]) -> Dict[str, Any]:
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

def _listen_for_server_broadcast(
    port: int = BROADCAST_PORT, timeout: float = SERVER_TIMEOUT
) -> bool:
    """Escucha el broadcast; si lo obtiene, guarda IP/puerto y devuelve True."""
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
            log_event("error", module_name, "Error recibiendo broadcast: {exc}")
            return False

    message_text = message.decode("utf-8", errors="replace")
    ip, server_port = _parse_broadcast_message(message_text)
    if not ip:
        log_event("error", module_name, "Broadcast con formato inesperado: {message_text}")
        return False

    _persist_server_endpoint(ip, server_port)
    log_event("info", module_name, "broadcast recibido, Registro del servidor actualizado: {ip}:{server_port or 'desconocido'}")
    return True

def _reply_server_with_handshake_request():
    print("enviando respuesta al server")
    # me he quedado aqui!!!! tambien revisar el servidor que lo ha hecho codex solo y me da miedo xDDDDD


#    public API

def handshake() -> bool:
    log_print("info", module_name, "iniciando handshake con el servidor")
    if _listen_for_server_broadcast():
        with STRUCTURE_PATH.open("r", encoding="utf-8") as handle:
            _build_cliente_payload()

        return True
    else:
        return False
    
def check_server_status() -> bool:
    """Devuelve True o False de forma aleatoria (~50% de probabilidad)."""
    return random.random() >= 0.5



