"""
Network Communication Handler (NetComHandler).
Manages communication with the Director (Server), listening for UDP beacons
to initiate connection and maintaining the link.
"""
import json
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from heartbeat import get_heartbeat_snapshot
from logger import log_event, log_print
from structure_manager import get_structure_manager

STRUCTURE_MANAGER = get_structure_manager()
SERVER_INFO_PATH = Path(__file__).resolve().parents[1] / "data" / "server.json"

module_name = "omiclient.net_com_handler"

# Configuration
SERVER_TIMEOUT = 5.0
BROADCAST_PORT = 39653
HANDSHAKE_TIMEOUT = 5.0
RECONNECT_DELAY = 3.0  # Seconds to wait before rescanning broadcast if connection is lost

# Persistent communication state
_comm_lock = threading.Lock()
_comm_socket: Optional[socket.socket] = None
_receiver_thread: Optional[threading.Thread] = None
_receiver_stop = threading.Event()
_session_active = threading.Event()

# ---------------------------------------------------------------------------
# File/Config Helpers
# ---------------------------------------------------------------------------

def _load_server_endpoint() -> Tuple[Optional[str], Optional[int]]:
    try:
        if not SERVER_INFO_PATH.exists():
            return None, None
        with SERVER_INFO_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        # log_event("debug", module_name, "Could not read server.json (normal at startup).")
        return None, None
    return data.get("ip"), data.get("port")


def _save_server_endpoint(ip: str, port: Optional[int]) -> None:
    data = {"ip": ip}
    if port is not None:
        data["port"] = port
    
    try:
        SERVER_INFO_PATH.parent.mkdir(parents=True, exist_ok=True)
        with SERVER_INFO_PATH.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
    except Exception as e:
        log_event("error", module_name, f"Failed to save server endpoint: {e}")


def _parse_broadcast_message(message: str) -> Tuple[Optional[str], Optional[int]]:
    """
    Parses broadcast message.
    Supported formats:
    - "OMI_DIRECTOR|IP|PORT" (New standard)
    - "ANY_STRING|IP|PORT"
    - "ANY_STRING:IP" (Legacy)
    """
    message = message.strip()
    
    # Attempt 1: Pipe Separator |
    if "|" in message:
        parts = message.split("|")
        # Expect at least HEADER|IP|PORT
        if len(parts) >= 2:
            # Assume IP is second element if there are 3, or check position
            # Standard format expected: HEADER|IP|PORT
            if len(parts) >= 3:
                ip = parts[1].strip()
                try:
                    port = int(parts[2].strip())
                    return ip, port
                except ValueError:
                    return ip, None
            # Simple fallback
            return parts[1].strip(), None

    # Attempt 2: Colon Separator : (Legacy)
    if ":" in message:
        parts = message.split(":", 1)
        if len(parts) == 2:
            return parts[1].strip(), None
            
    return None, None


def _build_client_payload() -> Dict[str, Any]:
    structure = STRUCTURE_MANAGER.get_structure()
    identity = structure.get("identity", {})
    version_info = structure.get("version", {}).get("version")

    active_service = STRUCTURE_MANAGER.get_active_service() or {}
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
        "mode": "LAN_TRUSTED" # Explicit mode indicator
    }


# ---------------------------------------------------------------------------
# Socket Helpers
# ---------------------------------------------------------------------------

def _send_json(conn: socket.socket, payload: Dict[str, Any]) -> None:
    try:
        data = json.dumps(payload).encode("utf-8") + b"\n"
        conn.sendall(data)
    except (TypeError, ValueError) as e:
        log_event("error", module_name, f"Error serializing JSON for sending: {e}")
        raise

def _receive_json(conn: socket.socket, buffer_size: int = 4096) -> Dict[str, Any]:
    data = bytearray()
    while True:
        try:
            chunk = conn.recv(buffer_size)
            if not chunk:
                break
            data.extend(chunk)
            if b"\n" in chunk:
                break
        except socket.timeout:
            raise socket.timeout("Timeout receiving data")
        except OSError as e:
            raise OSError(f"Socket error in reception: {e}")
            
    if not data:
        raise ValueError("Connection closed without data")
    
    try:
        line = data.splitlines()[0].decode("utf-8")
        return json.loads(line)
    except (json.JSONDecodeError, IndexError) as e:
        raise ValueError(f"Invalid JSON received: {e}")


# ---------------------------------------------------------------------------
# Broadcast Listener (Discovery)
# ---------------------------------------------------------------------------

def _listen_for_server_broadcast(port: int = BROADCAST_PORT, timeout: float = SERVER_TIMEOUT) -> bool:
    """Passively listens for a Director UDP beacon."""
    # log_print("debug", module_name, f"Listening for broadcasts on port {port}...")
    
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # In Linux SO_REUSEPORT can also help if there are multiple instances,
            # but SO_REUSEADDR is usually enough for multicast/broadcast.
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                
            sock.bind(("", port))
            sock.settimeout(timeout)
            
            message, addr = sock.recvfrom(1024)
            sender_ip = addr[0]
            
        except socket.timeout:
            # Normal if no server is active
            return False
        except OSError as exc:
            log_event("error", module_name, f"Error binding/receiving UDP broadcast: {exc}")
            return False

    message_text = message.decode("utf-8", errors="replace")
    # log_event("debug", module_name, f"Raw broadcast received from {sender_ip}: {message_text}")
    
    payload_ip, payload_port = _parse_broadcast_message(message_text)
    
    # If message doesn't have explicit IP, use sender's
    final_ip = payload_ip if payload_ip else sender_ip
    final_port = payload_port # Can be None
    
    if not final_ip:
        log_event("warning", module_name, "Broadcast received but Director IP could not be determined.")
        return False

    _save_server_endpoint(final_ip, final_port)
    log_event(
        "info",
        module_name,
        f"Director detected at {final_ip}:{final_port or 'Default'}"
    )
    return True


# ---------------------------------------------------------------------------
# Message Handlers (Protocol)
# ---------------------------------------------------------------------------

def _handle_handshake_response(message: Dict[str, Any]) -> bool:
    payload = message.get("cliente_payload")
    if not isinstance(payload, dict):
        log_event("error", module_name, "Handshake without valid return payload")
        return False

    # Update Identity (If Director assigns/corrects data)
    structure = STRUCTURE_MANAGER.get_structure()
    identity = structure.setdefault("identity", {})
    updated = False
    
    if payload.get("host") and identity.get("host") != payload["host"]:
        identity["host"] = payload["host"]
        updated = True
    if payload.get("index") is not None and identity.get("index") != payload["index"]:
        identity["index"] = payload["index"]
        updated = True
        
    # TODO: If identity changed, ideally we save it.
    # For now we trust StructureManager handles persistence if critical.

    # State Synchronization (Director tells us what we should be doing)
    service_state = payload.get("service_state") or {}
    target_svc = service_state.get("actual")
    
    if target_svc:
        current_svc = STRUCTURE_MANAGER.get_active_service()
        current_id = current_svc.get("name") if current_svc else None
        
        if current_id != target_svc:
            log_print("info", module_name, f"Director requests service change: {current_id} -> {target_svc}")
            STRUCTURE_MANAGER.update_service_state(target_svc, enabled=True)
            # Note: This doesn't start the process per-se, ServiceManager or Main Loop must react
            # to structure.json change. (See client.py: on_structure_change)

    log_print("info", module_name, "Handshake ACCEPTED by Director.")
    return True


def _handle_server_message(message: Dict[str, Any]) -> bool:
    msg_type = message.get("type")
    
    if msg_type == "handshake_response":
        return _handle_handshake_response(message)
    elif msg_type == "ping":
        # Simple keep-alive
        return True
    elif msg_type == "command":
        log_print("info", module_name, f"Command received: {message.get('command')}")
        # Remote commands would be implemented here (reboot, restart_service, etc)
        return True
    elif msg_type == "error":
        log_event("error", module_name, f"Remote error: {message.get('message')}")
        return True
    elif msg_type == "close":
        log_print("warning", module_name, "Director closed request.")
        return False
        
    log_event("warning", module_name, f"Unknown message type: {msg_type}")
    return True


# ---------------------------------------------------------------------------
# Main Communication Loop (Receiver)
# ---------------------------------------------------------------------------

def _receiver_loop() -> None:
    global _comm_socket, _receiver_thread
    
    log_print("debug", module_name, "Starting receiver loop...")
    
    while not _receiver_stop.is_set():
        with _comm_lock:
            conn = _comm_socket
            
        if conn is None:
            break
            
        try:
            # Blocking with implicit socket timeout or infinite
            message = _receive_json(conn)
            
            if not _handle_server_message(message):
                break
                
        except (ValueError, OSError) as e:
            if not _receiver_stop.is_set():
                log_event("warning", module_name, f"Connection lost with Director: {e}")
            break
        except Exception as e:
            log_event("error", module_name, f"Critical error in receiver: {e}")
            break

    # Cleanup on loop exit
    _session_active.clear()
    _receiver_stop.set()
    
    with _comm_lock:
        if _comm_socket is not None:
            try:
                _comm_socket.close()
            except: 
                pass
            _comm_socket = None
            
    _receiver_thread = None
    log_print("info", module_name, "Session with Director finished.")


def _start_receiver_thread() -> None:
    global _receiver_thread
    if _receiver_thread is not None and _receiver_thread.is_alive():
        return

    _receiver_stop.clear()
    _session_active.set()
    _receiver_thread = threading.Thread(
        target=_receiver_loop,
        name="NetComReceiver",
        daemon=True,
    )
    _receiver_thread.start()


def _connect_to_director(ip: str, port: int) -> bool:
    global _comm_socket
    
    log_print("info", module_name, f"Connecting to Director at {ip}:{port}...")
    
    conn = None
    try:
        conn = socket.create_connection((ip, port), timeout=HANDSHAKE_TIMEOUT)
        conn.settimeout(HANDSHAKE_TIMEOUT)
        
        # Send Handshake
        payload = _build_client_payload()
        _send_json(conn, {"type": "handshake", "cliente_payload": payload})
        
        # Wait for Response
        response = _receive_json(conn)
        
        if response.get("type") != "handshake_response":
            log_event("error", module_name, f"Invalid handshake response: {response}")
            conn.close()
            return False
            
        if not _handle_handshake_response(response):
            conn.close()
            return False
            
        # Connection established
        conn.settimeout(None) # Blocking mode for loop
        
        with _comm_lock:
            _comm_socket = conn
            
        _start_receiver_thread()
        return True
        
    except (socket.timeout, OSError, ValueError) as e:
        log_event("error", module_name, f"Failed to connect/handshake: {e}")
        if conn:
            try: conn.close()
            except: pass
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def handshake() -> bool:
    """
    Attempts to discover and connect to the Director.
    Returns True if connection is established.
    """
    if _session_active.is_set():
        return True

    # 1. Listen for Broadcast to find IP
    if not _listen_for_server_broadcast():
        return False

    # 2. Load discovered IP
    ip, port = _load_server_endpoint()
    if not ip:
        return False
        
    # If no port in broadcast, assume default?
    # Or Director should have sent it. Assume same as broadcast + N? 
    # For now assume data/server.json has correct info or broadcast had it.
    target_port = port if port else 9000 # Arbitrary default if missing
    
    # 3. Attempt TCP Connection
    return _connect_to_director(ip, target_port)


def check_server_status() -> bool:
    return _session_active.is_set()


def send_message(message_type: str, body: Optional[Dict[str, Any]] = None) -> bool:
    if not _session_active.is_set():
        return False

    payload = {"type": message_type}
    if body:
        payload.update(body)

    with _comm_lock:
        conn = _comm_socket
        if not conn:
            return False
        
        try:
            _send_json(conn, payload)
            return True
        except OSError:
            # Receiver will handle close
            return False

def close_comm_channel(reason: str = "client_shutdown") -> None:
    send_message("close", {"reason": reason})
    # Give it a moment to send
    time.sleep(0.1)
    
    _receiver_stop.set()
    with _comm_lock:
        if _comm_socket:
            try:
                _comm_socket.shutdown(socket.SHUT_RDWR)
                _comm_socket.close()
            except: pass
            _comm_socket = None
