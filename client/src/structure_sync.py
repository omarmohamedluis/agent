import json
import sys
import subprocess
import logging
from pathlib import Path
from typing import Any, Dict, Optional

# Setup Logging
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BASE_DIR / "src"))
from logger import get_logger

LOGGER = get_logger("omimidi.structure_sync")
STRUCTURE_PATH = BASE_DIR / "data" / "structure.json"
STRUCTURE_LOCK_PATH = BASE_DIR / "data" / "structure.json.lock"
SERVICES_JSON_PATH = BASE_DIR / "servicios" / "servicios.json"
NET_MANAGER_PATH = BASE_DIR / "src" / "net_manager.py"

def sync_service(svc_id: str):
    """Syncs metadata for a specific service from its map file to structure.json."""
    LOGGER.info(f"Starting sync for service: {svc_id}")
    try:
        if not STRUCTURE_PATH.exists():
            LOGGER.error(f"Structure file not found: {STRUCTURE_PATH}")
            return False
        if not SERVICES_JSON_PATH.exists():
            LOGGER.error(f"Services config not found: {SERVICES_JSON_PATH}")
            return False

        # 1. Load services config to find CWD
        with SERVICES_JSON_PATH.open("r", encoding="utf-8") as f:
            services_config = {s["id"]: s for s in json.load(f).get("services", [])}
        
        if svc_id not in services_config:
            return False
        
        config = services_config[svc_id]
        cwd = BASE_DIR / config.get("cwd", ".")
        
        # 2. Load structure.json
        from file_lock import file_lock
        with file_lock(STRUCTURE_LOCK_PATH):
            with STRUCTURE_PATH.open("r", encoding="utf-8") as f:
                structure = json.load(f)
            
            services = structure.get("services", [])
            target_svc = next((s for s in services if s.get("name") == svc_id), None)
            if not target_svc:
                return False

            # 3. Try to find and read map file
            updated = False
            map_files = ["OMIMIDI_map.json", "map.json", "config.json"]
            for mf in map_files:
                p = cwd / mf
                if p.exists():
                    with p.open("r", encoding="utf-8") as f_map:
                        mdata = json.load(f_map)
                        
                        # Sync web_port (prefer map file, fallback to services config)
                        file_info = mdata.get("file_info", {})
                        map_port = file_info.get("ui_port")
                        web_port = map_port if map_port else config.get("web_port")
                        
                        if web_port is not None and target_svc.get("web_port") != web_port:
                            target_svc["web_port"] = web_port
                            updated = True
                        
                        # Sync VLAN info (MIDI specific structure)
                        net = mdata.get("net", {})
                        vlan = net.get("vlan")
                        vlan_active = net.get("vlan_active")
                        
                        if vlan is not None and target_svc.get("vlan") != vlan:
                            target_svc["vlan"] = vlan
                            updated = True
                        if vlan_active is not None and target_svc.get("vlan_active") != vlan_active:
                            target_svc["vlan_active"] = vlan_active
                            updated = True

                        # Sync Configuration Name
                        file_info = mdata.get("file_info", {})
                        config_name = file_info.get("name")
                        if config_name and target_svc.get("configuration") != config_name:
                            target_svc["configuration"] = config_name
                            updated = True
                    break

            if updated:
                with STRUCTURE_PATH.open("w", encoding="utf-8") as f:
                    json.dump(structure, f, indent=2, ensure_ascii=False)
                
                LOGGER.info(f"Successfully updated structure.json for {svc_id}")
                # 4. Trigger net_manager
                subprocess.run(["python3", str(NET_MANAGER_PATH)], 
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            else:
                LOGGER.info(f"No changes detected for {svc_id}")
            
    except Exception as e:
        LOGGER.error(f"Error syncing service {svc_id}: {e}", exc_info=True)
        return False
    return False

if __name__ == "__main__":
    if len(sys.argv) > 1:
        svc_id = sys.argv[1]
        if sync_service(svc_id):
            print(f"Successfully synced {svc_id}")
        else:
            print(f"No changes or error syncing {svc_id}")
    else:
        print("Usage: python3 structure_sync.py <service_id>")
