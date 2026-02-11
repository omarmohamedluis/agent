import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

LOGGER = logging.getLogger("omi.server.storage")

DATA_DIR = Path(__file__).resolve().parent / "data"
AGENTS_FILE = DATA_DIR / "agents.json"
CONFIGS_FILE = DATA_DIR / "configs.json"

def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not AGENTS_FILE.exists():
        with AGENTS_FILE.open("w", encoding="utf-8") as f:
            json.dump({}, f)
    if not CONFIGS_FILE.exists():
        with CONFIGS_FILE.open("w", encoding="utf-8") as f:
            json.dump({}, f)

def load_json(path: Path) -> Dict[str, Any]:
    _ensure_data_dir()
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        LOGGER.error(f"Error loading {path.name}: {e}")
        return {}

def save_json(path: Path, data: Dict[str, Any]):
    _ensure_data_dir()
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        LOGGER.error(f"Error saving {path.name}: {e}")

# --- Agents Storage ---

def get_agents() -> Dict[str, Any]:
    return load_json(AGENTS_FILE)

def upsert_agent(serial: str, data: Dict[str, Any]):
    agents = get_agents()
    if serial not in agents:
        agents[serial] = {}
    agents[serial].update(data)
    save_json(AGENTS_FILE, agents)

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
