from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

STRUCTURE_PATH = Path(__file__).resolve().parents[2] / "agent_pi" / "data" / "structure.json"
_LOCK = RLock()


def ensure_agent_config(version: str) -> Dict[str, Any]:
    from jsonconfig import ensure_config  # reuse existing helper

    cfg = ensure_config(STRUCTURE_PATH, version=version)
    return cfg


def load_structure() -> Dict[str, Any]:
    with _LOCK:
        if not STRUCTURE_PATH.exists():
            return {}
        return json.loads(STRUCTURE_PATH.read_text(encoding="utf-8"))


def save_structure(data: Dict[str, Any]) -> None:
    with _LOCK:
        STRUCTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STRUCTURE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_identity(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if data is None:
        data = load_structure()
    return data.get("identity", {}) if isinstance(data, dict) else {}


def update_service_enabled(name: str, enabled: bool) -> Dict[str, Any]:
    data = load_structure()
    services = data.setdefault("services", [])
    for entry in services:
        if isinstance(entry, dict) and entry.get("name") == name:
            entry["enabled"] = enabled
            break
    save_structure(data)
    return data
