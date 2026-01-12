import json
import subprocess
import logging
import os
import signal
from pathlib import Path
from typing import Dict, Any, Optional

from logger import get_logger

LOGGER = get_logger("omimidi.service_manager")

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_JSON_PATH = BASE_DIR / "servicios" / "servicios.json"
STRUCTURE_PATH = BASE_DIR / "data" / "structure.json"
STRUCTURE_LOCK_PATH = BASE_DIR / "data" / "structure.json.lock"

class ServiceManager:
    def __init__(self):
        self.processes: Dict[str, subprocess.Popen] = {}
        self.services_config: Dict[str, Any] = {}
        self._load_config()

    def _load_config(self):
        # Auto-restore from template if missing (Self-Healing)
        if not SERVICES_JSON_PATH.exists():
            template_path = SERVICES_JSON_PATH.with_suffix(".json.template")
            if template_path.exists():
                LOGGER.info(f"Restoring {SERVICES_JSON_PATH.name} from template...")
                import shutil
                try:
                    shutil.copy(template_path, SERVICES_JSON_PATH)
                except Exception as e:
                    LOGGER.error(f"Failed to restore template: {e}")

        try:
            with SERVICES_JSON_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                # Convert list to dict keyed by id for easier access
                self.services_config = {s["id"]: s for s in data.get("services", [])}
        except Exception as e:
            LOGGER.error(f"Failed to load services config: {e}")
            self.services_config = {}

    def _update_structure_active_service(self, active_svc_id: Optional[str]):
        """Updates structure.json to set 'enabled' flag and sync metadata for the active service."""
        try:
            if not STRUCTURE_PATH.exists():
                return

            from file_lock import file_lock
            with file_lock(STRUCTURE_LOCK_PATH):
                with STRUCTURE_PATH.open("r", encoding="utf-8") as f:
                    structure = json.load(f)
                
                services = structure.get("services", [])
                updated = False
                
                for svc in services:
                    svc_name = svc.get("name")
                    is_active = (svc_name == active_svc_id)
                    
                    # Sync enabled state
                    if svc.get("enabled") != is_active:
                        svc["enabled"] = is_active
                        updated = True
                    
                    # Sync web_port from internal config if active
                    if is_active and active_svc_id in self.services_config:
                        config = self.services_config[active_svc_id]
                        web_port = config.get("web_port")
                        if svc.get("web_port") != web_port:
                            svc["web_port"] = web_port
                            updated = True

                if updated:
                    with STRUCTURE_PATH.open("w", encoding="utf-8") as f:
                        json.dump(structure, f, indent=2, ensure_ascii=False)
                    LOGGER.info(f"Updated structure.json with active service: {active_svc_id}")
            
            # If we have an active service, trigger metadata sync from its own map file
            if active_svc_id:
                from structure_sync import sync_service
                sync_service(active_svc_id)
                    
        except Exception as e:
            LOGGER.error(f"Failed to update structure.json active service: {e}")

    def stop_all(self, persist_state: bool = False):
        """Stops all services."""
        for svc_id in list(self.processes.keys()):
            self.stop_service(svc_id, persist_state=persist_state)

    def get_services(self) -> Dict[str, Any]:
        """Return all services with their current status, merging dynamic state from structure.json."""
        status_map = {}
        
        # 1. Get base config and process status
        for svc_id, config in self.services_config.items():
            proc = self.processes.get(svc_id)
            is_running = proc is not None and proc.poll() is None
            status_map[svc_id] = {
                **config,
                "running": is_running,
                "pid": proc.pid if is_running else None
            }

        # 2. Merge dynamic state from structure.json
        try:
            if STRUCTURE_PATH.exists():
                from file_lock import file_lock
                with file_lock(STRUCTURE_LOCK_PATH):
                    with STRUCTURE_PATH.open("r", encoding="utf-8") as f:
                        structure = json.load(f)
                    
                    for svc in structure.get("services", []):
                        svc_name = svc.get("name")
                        if svc_name in status_map:
                            # Merge dynamic fields
                            if "web_port" in svc:
                                status_map[svc_name]["web_port"] = svc["web_port"]
                            if "enabled" in svc:
                                status_map[svc_name]["enabled"] = svc["enabled"]
                            if "display_name" in svc:
                                status_map[svc_name]["display_name"] = svc["display_name"]
        except Exception as e:
            LOGGER.error(f"Failed to merge structure.json data in get_services: {e}")

        return status_map

    def start_service(self, svc_id: str) -> bool:
        if svc_id not in self.services_config:
            LOGGER.error(f"Service {svc_id} not found")
            return False

        if svc_id in self.processes and self.processes[svc_id].poll() is None:
            LOGGER.info(f"Service {svc_id} is already running")
            return True

        config = self.services_config[svc_id]
        if config.get("type") != "process":
            LOGGER.info(f"Service {svc_id} is not a process type")
            return False

        try:
            cwd = BASE_DIR / config.get("cwd", ".")
            entry = config.get("entry", [])
            
            # Resolve ${PYTHON} variable
            cmd = [x.replace("${PYTHON}", "python3") for x in entry]
            
            # EXCLUSIVE MODE: Stop all other running services first
            for other_id in list(self.processes.keys()):
                if other_id != svc_id:
                    LOGGER.info(f"Exclusive mode: Stopping {other_id} before starting {svc_id}")
                    self.stop_service(other_id)
            
            LOGGER.info(f"Starting service {svc_id}: {cmd} in {cwd}")
            
            # Update Active Service State in structure.json
            self._update_structure_active_service(svc_id)

            # Trigger Network Update (VLANs)
            try:
                from net_manager import update_nics
                update_nics()
            except Exception as e:
                LOGGER.error(f"Failed to update NICs before start: {e}")
            
            # Prepare Environment
            env = os.environ.copy()
            
            # Determine Log Path
            # We use the 'stdout' path from JSON as the target for the internal logger
            log_rel_path = config.get("logs", {}).get("stdout", f"logs/services/{svc_id}.log")
            log_path = BASE_DIR / log_rel_path
            
            # Ensure directory exists
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            env["OMI_LOG_PATH"] = str(log_path)
            env["OMI_SERVICE_ID"] = svc_id
            
            LOGGER.info(f"Starting service {svc_id} with log path: {log_path}")

            # Start Process
            # We do NOT redirect stdout/stderr here, relying on the service to log to OMI_LOG_PATH
            # We redirect to DEVNULL to avoid cluttering the client console or blocking pipes
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                start_new_session=True, # setsid
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self.processes[svc_id] = proc
            return True
        except Exception as e:
            LOGGER.error(f"Failed to start service {svc_id}: {e}")
            return False

    def stop_service(self, svc_id: str, persist_state: bool = False) -> bool:
        proc = self.processes.get(svc_id)
        if not proc:
            return False

        if proc.poll() is not None:
            del self.processes[svc_id]
            return True

        try:
            LOGGER.info(f"Stopping service {svc_id} (PID {proc.pid})")
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            
            del self.processes[svc_id]
            
            # Update Active Service State to None (STANDBY)
            # Only if this was the active service? 
            # If we stop a service, we should check if any other is running (unlikely in exclusive mode)
            # or just set all to disabled.
            if not self.processes and not persist_state:
                 self._update_structure_active_service(None)
            
            # Trigger Network Update (Cleanup/Revert)
            try:
                from net_manager import update_nics
                update_nics()
            except Exception as e:
                LOGGER.error(f"Failed to update NICs after stop: {e}")
                
            return True
        except Exception as e:
            LOGGER.error(f"Failed to stop service {svc_id}: {e}")
            return False
