"""
Network Communication Handler (NetComHandler) - HTTP Version.
Manages communication with the OMI Central Server using HTTP.
Handles discovery via UDP broadcast and subsequent API calls.
"""
import json
import socket
import threading
import time
import requests
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from heartbeat import get_heartbeat_snapshot
from logger import log_event, log_print
from structure_manager import get_structure_manager

STRUCTURE_MANAGER = get_structure_manager()
SERVER_INFO_PATH = Path(__file__).resolve().parents[1] / "data" / "server.json"

module_name = "omiclient.net_com_handler"

# Configuration
BROADCAST_PORT = 39653
SERVER_TIMEOUT = 5.0

# Persistent communication state
_session_active = threading.Event()
_last_contact_time: float = 0.0
_sender_thread: Optional[threading.Thread] = None
_sender_stop = threading.Event()

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
    message = message.strip()
    if "|" in message:
        parts = message.split("|")
        if len(parts) >= 3:
            ip = parts[1].strip()
            try:
                port = int(parts[2].strip())
                return ip, port
            except ValueError:
                return ip, None
        elif len(parts) >= 2:
            return parts[1].strip(), None
    return None, None


def _build_client_payload() -> Dict[str, Any]:
    structure = STRUCTURE_MANAGER.get_structure()
    identity = structure.get("identity", {})
    version_info = structure.get("version", {}).get("version")

    active_service = STRUCTURE_MANAGER.get_active_service() or {}
    status = active_service.get("running") and not active_service.get("config_mode")
    
    service_state = {
        "actual": active_service.get("name"),
        "configuration": active_service.get("active_config") or active_service.get("configuration"),
        "web_port": active_service.get("web_port"),
        "running": bool(status)
    }

    heartbeat_snapshot = get_heartbeat_snapshot()
    presets = _collect_local_presets()

    return {
        "serial": identity.get("serial"),
        "host": identity.get("host") or identity.get("name"),
        "hostname": identity.get("host") or identity.get("name"),
        "cpu": heartbeat_snapshot.get("cpu"),
        "temp": heartbeat_snapshot.get("temp"),
        "ip": heartbeat_snapshot.get("main_nic_ip"),
        "active_service": service_state.get("actual"),
        "active_config": service_state.get("configuration"),
        "presets": presets,
        "version": version_info,
        "index": identity.get("index")
    }


def _collect_local_presets() -> Dict[str, Any]:
    presets = {}
    servicios_dir = Path(__file__).resolve().parents[1] / "servicios"
    if not servicios_dir.exists():
        return presets
        
    for svc_dir in servicios_dir.iterdir():
        if not svc_dir.is_dir():
            continue
        configs_dir = svc_dir / "configs"
        if not configs_dir.exists():
            continue
            
        svc_presets = {}
        for cfg_file in configs_dir.glob("*.json"):
            try:
                with cfg_file.open("r", encoding="utf-8") as f:
                    svc_presets[cfg_file.stem] = {"data": json.load(f)}
            except Exception:
                continue
        if svc_presets:
            presets[svc_dir.name] = svc_presets
    return presets

# ---------------------------------------------------------------------------
# HTTP Helpers
# ---------------------------------------------------------------------------

def _get_server_url(path: str) -> Optional[str]:
    ip, port = _load_server_endpoint()
    if not ip:
        return None
    target_port = port if port else 9000
    return f"http://{ip}:{target_port}{path}"

def _post_json(path: str, payload: Dict[str, Any], timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    url = _get_server_url(path)
    if not url:
        return None
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        if response.status_code == 200:
            return response.json()
        # log_event("debug", module_name, f"HTTP Error {response.status_code} on {path}")
    except Exception as e:
        log_event("debug", module_name, f"HTTP Request failed on {path}: {e}")
    return None

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _listen_for_server_broadcast(port: int = BROADCAST_PORT, timeout: float = SERVER_TIMEOUT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.bind(("", port))
            sock.settimeout(timeout)
            message, addr = sock.recvfrom(1024)
            sender_ip = addr[0]
        except socket.timeout:
            return False
        except OSError:
            return False

    message_text = message.decode("utf-8", errors="replace")
    payload_ip, payload_port = _parse_broadcast_message(message_text)
    final_ip = payload_ip if payload_ip else sender_ip
    
    if not final_ip:
        return False

    _save_server_endpoint(final_ip, payload_port)
    log_event("debug", module_name, f"Director detected at {final_ip}:{payload_port or 9000}")
    return True

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _apply_server_configs(configs: Dict[str, Any]):
    servicios_dir = Path(__file__).resolve().parents[1] / "servicios"
    for svc_id, svc_configs in configs.items():
        configs_dir = servicios_dir / svc_id / "configs"
        configs_dir.mkdir(parents=True, exist_ok=True)
        for name, cfg_info in svc_configs.items():
            data = cfg_info.get("data")
            if data:
                cfg_file = configs_dir / f"{name}.json"
                try:
                    with cfg_file.open("w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass

def _handle_command(cmd_data: Dict[str, Any]):
    cmd = cmd_data.get("command")
    if not cmd:
        return
    log_print("info", module_name, f"Command received: {cmd}")
    if cmd == "shutdown":
        import subprocess
        subprocess.run(["sudo", "shutdown", "now"])
    elif cmd == "reboot":
        import subprocess
        subprocess.run(["sudo", "reboot"])

# ---------------------------------------------------------------------------
# Loops
# ---------------------------------------------------------------------------

def _sender_loop() -> None:
    log_print("debug", module_name, "Starting heartbeat sender loop...")
    while not _sender_stop.is_set():
        if not _session_active.is_set():
            break
            
        payload = _build_client_payload()
        res = _post_json("/api/heartbeat", payload)
        if res:
            global _last_contact_time
            _last_contact_time = time.time()
            if res.get("command"):
                _handle_command(res)
        
        if _sender_stop.wait(5.0):
            break
    log_print("debug", module_name, "Heartbeat sender loop finished.")

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def handshake() -> bool:
    if _session_active.is_set():
        return True

    if not _listen_for_server_broadcast():
        return False

    payload = _build_client_payload()
    res = _post_json("/api/handshake", payload)
    if res and res.get("status") == "ok":
        server_configs = res.get("configs")
        if isinstance(server_configs, dict):
            _apply_server_configs(server_configs)

        global _last_contact_time
        _last_contact_time = time.time()
        log_print("info", module_name, "Handshake ACCEPTED by Director (HTTP).")
        
        _session_active.set()
        global _sender_thread, _sender_stop
        _sender_stop.clear()
        _sender_thread = threading.Thread(target=_sender_loop, name="NetComSender", daemon=True)
        _sender_thread.start()
        return True
    
    return False


def check_server_status() -> bool:
    return _session_active.is_set()


def get_last_contact_time() -> float:
    return _last_contact_time


def close_comm_channel(reason: str = "client_shutdown") -> None:
    # We could send a 'close' event via POST, but for HTTP it's less critical
    _session_active.clear()
    _sender_stop.set()
    log_print("info", module_name, f"Session finished: {reason}")
