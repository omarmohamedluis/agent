import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import datetime

# Volatile directories moved outside the source package to prevent watchdog reloads
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs" / "storage"

AGENTS_FILE = DATA_DIR / "agents.json"
CONFIGS_FILE = DATA_DIR / "configs.json"

def _ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not AGENTS_FILE.exists():
        with AGENTS_FILE.open("w", encoding="utf-8") as f:
            json.dump({}, f)
    if not CONFIGS_FILE.exists():
        with CONFIGS_FILE.open("w", encoding="utf-8") as f:
            json.dump({}, f)

def _configure_storage_logging():
    _ensure_dirs()
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"storage_{timestamp}.log"
    
    logger = logging.getLogger("omi.server.storage")
    logger.setLevel(logging.INFO)
    # Prevent storage logs from propagating to the root server logger (avoiding duplicates)
    logger.propagate = False
    
    # Avoid duplicate handlers if re-called
    if not logger.handlers:
        fh = logging.FileHandler(log_file)
        formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    return logger

LOGGER = _configure_storage_logging()

def load_json(path: Path) -> Dict[str, Any]:
    _ensure_dirs()
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        LOGGER.error(f"Error loading {path.name}: {e}")
        return {}

def save_json(path: Path, data: Dict[str, Any]):
    _ensure_dirs()
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        LOGGER.error(f"Error saving {path.name}: {e}")

ALLOWED_AGENT_FIELDS = [
    "id", "host", "ip", "cpu", "temp", 
    "active_service", "active_config", "system_status", 
    "status", "last_seen", "last_launch_at", "version", 
    "awaiting_stable_heartbeat"
]

def get_agents() -> Dict[str, Any]:
    return load_json(AGENTS_FILE)

def upsert_agent(serial: str, data: Dict[str, Any]):
    agents = get_agents()
    if serial not in agents:
        # Assign next sequential ID
        max_id = 0
        for agent in agents.values():
            try:
                max_id = max(max_id, int(agent.get("id", 0)))
            except (ValueError, TypeError):
                continue
        agents[serial] = {"id": max_id + 1}
    
    # Filter incoming data against whitelist
    filtered_data = {k: v for k, v in data.items() if k in ALLOWED_AGENT_FIELDS}
    
    # Specifically ensure ID is not overwritten by incoming data unless it's the internal one
    existing_id = agents[serial].get("id")
    
    agents[serial].update(filtered_data)
    
    if existing_id is not None:
        agents[serial]["id"] = existing_id
        
    save_json(AGENTS_FILE, agents)

def update_agent_id(serial: str, new_id: int):
    agents = get_agents()
    if serial in agents:
        agents[serial]["id"] = new_id
        save_json(AGENTS_FILE, agents)
        return True
    return False

def delete_agent(serial: str):
    agents = get_agents()
    if serial in agents:
        del agents[serial]
        save_json(AGENTS_FILE, agents)

# --- Configs Storage ---

def get_configs(service_id: Optional[str] = None) -> Dict[str, Any]:
    all_configs = load_json(CONFIGS_FILE)
    if service_id:
        return all_configs.get(service_id, {})
    return all_configs

def save_config(service_id: str, name: str, data: Dict[str, Any], serial: str = "server"):
    all_configs = get_configs()
    service_configs = all_configs.setdefault(service_id, {})
    service_configs[name] = {
        "data": data,
        "serial": serial
    }
    save_json(CONFIGS_FILE, all_configs)

def delete_config(service_id: str, name: str):
    all_configs = get_configs()
    if service_id in all_configs and name in all_configs[service_id]:
        del all_configs[service_id][name]
        save_json(CONFIGS_FILE, all_configs)
