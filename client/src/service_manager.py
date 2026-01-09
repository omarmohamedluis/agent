import json
import subprocess
import logging
import os
import signal
from pathlib import Path
from typing import Dict, Any, Optional

LOGGER = logging.getLogger("omimidi.service_manager")

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_JSON_PATH = BASE_DIR / "servicios" / "servicios.json"
STRUCTURE_PATH = BASE_DIR / "data" / "structure.json"

class ServiceManager:
    def __init__(self):
        self.processes: Dict[str, subprocess.Popen] = {}
        self.services_config: Dict[str, Any] = {}
        self._load_config()

    def _load_config(self):
        try:
            with SERVICES_JSON_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                # Convert list to dict keyed by id for easier access
                self.services_config = {s["id"]: s for s in data.get("services", [])}
        except Exception as e:
            LOGGER.error(f"Failed to load services config: {e}")
            self.services_config = {}

    def _update_structure_active_service(self, active_svc_id: Optional[str]):
        """Updates structure.json to set 'enabled' flag for the active service."""
        try:
            if not STRUCTURE_PATH.exists():
                return

            with STRUCTURE_PATH.open("r", encoding="utf-8") as f:
                structure = json.load(f)
            
            services = structure.get("services", [])
            updated = False
            
            # If services list is empty in structure.json but we have config, maybe we should populate it?
            # For now, let's assume structure.json has the services list synced or we just update what's there.
            # Actually, NetComHandler updates structure.json services list.
            
            for svc in services:
                if svc.get("name") == active_svc_id:
                    if not svc.get("enabled"):
                        svc["enabled"] = True
                        updated = True
                else:
                    if svc.get("enabled"):
                        svc["enabled"] = False
                        updated = True
            
            if updated:
                with STRUCTURE_PATH.open("w", encoding="utf-8") as f:
                    json.dump(structure, f, indent=2, ensure_ascii=False)
                    
        except Exception as e:
            LOGGER.error(f"Failed to update structure.json active service: {e}")

    def get_services(self) -> Dict[str, Any]:
        """Return all services with their current status."""
        status_map = {}
        for svc_id, config in self.services_config.items():
            proc = self.processes.get(svc_id)
            is_running = proc is not None and proc.poll() is None
            status_map[svc_id] = {
                **config,
                "running": is_running,
                "pid": proc.pid if is_running else None
            }
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
                
            return True
        except Exception as e:
            LOGGER.error(f"Failed to stop service {svc_id}: {e}")
            return False
