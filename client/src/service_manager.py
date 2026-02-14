"""
Service Manager (ServiceManager).
Controls the lifecycle of services (start, stop, status),
managing background processes and synchronizing their configuration.
"""
import json
import subprocess
import logging
import os
import signal
from pathlib import Path
from typing import Dict, Any, Optional

from logger import get_logger
from structure_manager import get_structure_manager

LOGGER = get_logger("omiclient.service_manager")
STRUCTURE_MANAGER = get_structure_manager()

BASE_DIR = Path(__file__).resolve().parents[1]

class ServiceManager:
    def __init__(self):
        self.processes: Dict[str, subprocess.Popen] = {}
        self.services_config: Dict[str, Any] = {}
        self.config_mode_services: set = set()
        self._load_config()

    def _load_config(self):
        services_json_path = BASE_DIR / "servicios" / "servicios.json"
        
        # Auto-restore from template if missing (Auto-Repair)
        if not services_json_path.exists():
            template_path = services_json_path.with_suffix(".json.template")
            if template_path.exists():
                LOGGER.info(f"Restoring {services_json_path.name} from template...")
                import shutil
                try:
                    shutil.copy(template_path, services_json_path)
                except Exception as e:
                    LOGGER.error(f"Failed to restore template: {e}")

        # Sync from servicios.json via manager to ensure structure.json is up to date
        STRUCTURE_MANAGER.sync_from_servicios_json()

        try:
            with services_json_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                self.services_config = {s["id"]: s for s in data.get("services", [])}
                
                # Auto-initialize configs for all services (Template -> Default.json)
                for svc_id in self.services_config:
                    self.get_configs(svc_id)
        except Exception as e:
            LOGGER.error(f"Failed to load service configuration: {e}")
            self.services_config = {}

    def stop_all(self, persist_state: bool = False):
        """Stops all services."""
        for svc_id in list(self.processes.keys()):
            self.stop_service(svc_id, persist_state=persist_state)

    def get_services(self) -> Dict[str, Any]:
        """Returns all services with their current state, merging dynamic state from structure.json."""
        status_map = {}
        
        # 1. Get base config and process state
        for svc_id, config in self.services_config.items():
            proc = self.processes.get(svc_id)
            is_running = proc is not None and proc.poll() is None
            
            # Detect if in config mode
            is_config_mode = False
            if is_running:
                # Check environment variable in process (if possible)
                # Or better, keep internal registry.
                # Since we don't have easy internal registry without changing __init__,
                # we can infer if command has env var.
                # But subprocess.Popen doesn't expose env easily after creation.
                # We'll use a hack: if log path has "configs" or something? No.
                # Better: added a set 'config_mode_services' in __init__.
                pass

            # Get active config
            active_config = "Default"
            try:
                active_file = self._get_service_dir(svc_id) / "active_config.txt"
                if active_file.exists():
                    active_config = active_file.read_text(encoding="utf-8").strip() or "Default"
            except Exception:
                pass

            status_map[svc_id] = {
                **config,
                "running": is_running,
                "pid": proc.pid if is_running else None,
                "config_mode": (svc_id in self.config_mode_services) if is_running else False,
                "active_config": active_config
            }

        # 2. Merge dynamic state from structure.json via Manager
        structure = STRUCTURE_MANAGER.get_structure()
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

        return status_map

    def start_service(self, svc_id: str) -> bool:
        if svc_id not in self.services_config:
            LOGGER.error(f"Service {svc_id} not found")
            return False

        if svc_id in self.processes:
            proc = self.processes[svc_id]
            if proc.poll() is None:
                LOGGER.info(f"Service {svc_id} is already running (PID {proc.pid})")
                return True
            else:
                # Clean invalid reference
                del self.processes[svc_id]

        config = self.services_config[svc_id]
        if config.get("type") != "process":
            LOGGER.info(f"Service {svc_id} is not of type process")
            return False

        subprocess_obj = None
        try:
            STRUCTURE_MANAGER.set_busy(f"SERVICE_OP_{svc_id}", f"Starting {svc_id}...")
            
            cwd = BASE_DIR / config.get("cwd", ".")
            entry = config.get("entry", [])
            
            if not entry:
                 LOGGER.error(f"Service {svc_id} has no entry point defined")
                 return False

            # Resolve variable ${PYTHON}
            cmd = [x.replace("${PYTHON}", "python3") for x in entry]
            
            # EXCLUSIVE MODE: Stop all other running services first
            # Copy keys to avoid "dictionary changed size during iteration"
            active_ids = list(self.processes.keys())
            for other_id in active_ids:
                if other_id != svc_id:
                    LOGGER.info(f"Exclusive mode: Stopping {other_id} before starting {svc_id}")
                    self.stop_service(other_id)
            
            LOGGER.info(f"Starting service {svc_id}: {cmd} in {cwd}")
            
            # Update Active Service State in structure.json
            STRUCTURE_MANAGER.update_service_state(svc_id, enabled=True)
            
            # Prepare Environment
            env = os.environ.copy()
            
            # Determine Log Path
            log_rel_path = config.get("logs", {}).get("stdout", f"logs/services/{svc_id}.log")
            log_path = BASE_DIR / log_rel_path
            
            # Ensure directory exists
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                LOGGER.error(f"Could not create log directory for {svc_id}: {e}")
                # Continue without log or fail? Fail to not lose output
                # return False
            
            env["OMI_LOG_PATH"] = str(log_path)
            env["OMI_SERVICE_ID"] = svc_id
            
            # Inject Active Configuration
            config_path = self._get_active_config_path(svc_id)
            if config_path:
                env["OMI_CONFIG_PATH"] = str(config_path)
                LOGGER.info(f"Using configuration: {config_path}")
            
            LOGGER.info(f"Starting service {svc_id} with log path: {log_path}")

            # Start Process
            subprocess_obj = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                start_new_session=True, # setsid
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Wait a bit to see if it crashes immediately
            try:
                subprocess_obj.wait(timeout=0.5)
                # If does not raise TimeoutExpired, it finished (crash?)
                if subprocess_obj.returncode != 0:
                     LOGGER.error(f"Service {svc_id} exited immediately with code {subprocess_obj.returncode}")
                     return False
            except subprocess.TimeoutExpired:
                # Still running, all good
                pass

            self.processes[svc_id] = subprocess_obj
            
            # Update Running State
            STRUCTURE_MANAGER.update_service_state(svc_id, running=True)
            
            # Trigger metadata sync (web_port, etc)
            try:
                STRUCTURE_MANAGER.sync_service_metadata(svc_id)
            except Exception as e:
                LOGGER.error(f"Failed to sync service metadata: {e}")

            return True
            
        except OSError as e:
             LOGGER.error(f"OS Error starting {svc_id}: {e}")
             return False
        except Exception as e:
            LOGGER.error(f"Unexpected failure starting service {svc_id}: {e}")
            return False
        finally:
            STRUCTURE_MANAGER.clear_busy(f"SERVICE_OP_{svc_id}")

    def stop_service(self, svc_id: str, persist_state: bool = False) -> bool:
        proc = self.processes.get(svc_id)
        if not proc:
            # If not in map, assume stopped.
            # Ensure state in StructureManager just in case
            if not persist_state:
                 STRUCTURE_MANAGER.update_service_state(svc_id, enabled=False)
            return True

        # Check if already dead
        if proc.poll() is not None:
            del self.processes[svc_id]
            self.config_mode_services.discard(svc_id)
            if not persist_state:
                 STRUCTURE_MANAGER.update_service_state(svc_id, enabled=False)
            return True

        try:
            STRUCTURE_MANAGER.set_busy(f"SERVICE_OP_{svc_id}", f"Stopping {svc_id}...")
            LOGGER.info(f"Stopping service {svc_id} (PID {proc.pid})")
            
            # Attempt to terminate process group gently
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                # No longer exists
                pass
                
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                LOGGER.warning(f"Service {svc_id} did not respond to SIGTERM, forcing SIGKILL...")
                if proc.poll() is None: # Check if still running before sending SIGKILL
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                STRUCTURE_MANAGER.update_service_state(svc_id, enabled=not persist_state, running=False)
            
            del self.processes[svc_id]
            self.config_mode_services.discard(svc_id)
            
            # Update Active Service State to None (STANDBY)
            if not self.processes and not persist_state:
                 STRUCTURE_MANAGER.update_service_state(svc_id, enabled=False, running=False)
            else:
                 STRUCTURE_MANAGER.update_service_state(svc_id, running=False)
            
            return True
        except Exception as e:
            LOGGER.error(f"Failed to stop service {svc_id}: {e}")
            # Force map cleanup to avoid zombie state
            if svc_id in self.processes:
                del self.processes[svc_id]
            return False
        finally:
            STRUCTURE_MANAGER.clear_busy(f"SERVICE_OP_{svc_id}")

    # --- Configuration Management (Multi-Config) ---
    def _get_service_dir(self, svc_id: str) -> Path:
        return BASE_DIR / "servicios" / svc_id

    def _get_configs_dir(self, svc_id: str) -> Path:
        return self._get_service_dir(svc_id) / "configs"

    def _get_active_config_path(self, svc_id: str) -> Path:
        """Returns the path to the active JSON configuration file."""
        service_dir = self._get_service_dir(svc_id)
        active_file = service_dir / "active_config.txt"
        configs_dir = self._get_configs_dir(svc_id)
        
        config_name = "Default"
        if active_file.exists():
            try:
                config_name = active_file.read_text(encoding="utf-8").strip() or "Default"
            except Exception:
                pass
        
        return configs_dir / f"{config_name}.json"

    def get_configs(self, svc_id: str) -> list:
        """Lists available configurations for a service and auto-creates Default if missing."""
        configs_dir = self._get_configs_dir(svc_id)
        if not configs_dir.exists():
            configs_dir.mkdir(parents=True, exist_ok=True)
            
        files = list(configs_dir.glob("*.json"))
        if not files:
            # If no configs, create Default based on template or legacy map
            import shutil
            
            default_config = configs_dir / "Default.json"
            template = configs_dir / "Base_config.json.template"
            
            # Priority 1: New template Base_config.json.template
            if template.exists():
                try:
                    shutil.copy(template, default_config)
                    LOGGER.info(f"Created Default.json config for {svc_id} from BASE template")
                    files = [default_config]
                except Exception as e:
                    LOGGER.error(f"Error creating Default.json from template: {e}")
            else:
                # Priority 2: Attempt to locate legacy map for migration
                service_dir = self._get_service_dir(svc_id)
                if svc_id == "MIDI":
                    legacy_map = service_dir / "OMIMIDI_map.json"
                else:
                    legacy_map = service_dir / f"{svc_id}_map.json"
                    
                if legacy_map.exists():
                    try:
                        shutil.copy(legacy_map, default_config)
                        LOGGER.info(f"Created Default.json config for {svc_id} from legacy map")
                        files = [default_config]
                    except Exception as e:
                        LOGGER.error(f"Error creating Default.json from legacy: {e}")
                else:
                    # Priority 3: Attempt to search legacy template
                    legacy_template = legacy_map.with_suffix(".json.template") if 'legacy_map' in locals() else None
                    if legacy_template and legacy_template.exists():
                        try:
                            shutil.copy(legacy_template, default_config)
                            files = [default_config]
                        except Exception:
                            pass

        return sorted([f.stem for f in files])

    def select_config(self, svc_id: str, config_name: str) -> bool:
        """Selects a configuration as active (saves the name)."""
        configs_dir = self._get_configs_dir(svc_id)
        target = configs_dir / f"{config_name}.json"
        
        if not target.exists():
            LOGGER.error(f"Configuration {config_name} not found for {svc_id}")
            return False
            
        try:
            # Save active config name in text file
            active_file = self._get_service_dir(svc_id) / "active_config.txt"
            active_file.write_text(config_name, encoding="utf-8")
            LOGGER.info(f"Active configuration for {svc_id} set to: {config_name}")
            return True
        except Exception as e:
            LOGGER.error(f"Failed to select configuration {config_name}: {e}")
            return False

    def save_config_as(self, svc_id: str, config_name: str) -> bool:
        """Saves current active configuration with a name."""
        import shutil
        
        configs_dir = self._get_configs_dir(svc_id)
        configs_dir.mkdir(parents=True, exist_ok=True)
        
        src = self._get_active_config_path(svc_id)
        dst = configs_dir / f"{config_name}.json"
        
        if not src.exists():
            LOGGER.error(f"No active configuration to save in {svc_id}")
            return False
            
        try:
            shutil.copy(src, dst)
            LOGGER.info(f"Active configuration saved as {config_name} for {svc_id}")
            return True
        except Exception as e:
            LOGGER.error(f"Failed to save configuration as {config_name}: {e}")
            return False
            
    def delete_config(self, svc_id: str, config_name: str) -> bool:
        """Deletes a specific configuration file."""
        if config_name == "Default":
            LOGGER.warning(f"Attempt to delete Default configuration in {svc_id}")
            return False

        configs_dir = self._get_configs_dir(svc_id)
        target = configs_dir / f"{config_name}.json"
        
        if not target.exists():
            LOGGER.warning(f"Configuration {config_name} does not exist in {svc_id}")
            return False
            
        try:
            target.unlink()
            LOGGER.info(f"Configuration {config_name} deleted from {svc_id}")
            
            # If active was deleted, revert to Default
            active_file = self._get_service_dir(svc_id) / "active_config.txt"
            if active_file.exists():
                current = active_file.read_text(encoding="utf-8").strip()
                if current == config_name:
                    active_file.write_text("Default", encoding="utf-8")
                    LOGGER.info(f"Active configuration reverted to Default for {svc_id}")
            
            return True
        except Exception as e:
            LOGGER.error(f"Failed to delete configuration {config_name}: {e}")
            return False

    def start_config_mode(self, svc_id: str) -> bool:
        """Starts service in CONFIGURATION MODE (Offline/Mock)."""
        # Similar to start_service but injecting environment variable
        
        if svc_id not in self.services_config:
            return False
            
        # If running, stop it (to change mode)
        if svc_id in self.processes and self.processes[svc_id].poll() is None:
            self.stop_service(svc_id)
            
        config = self.services_config[svc_id]
        cwd = BASE_DIR / config.get("cwd", ".")
        entry = config.get("entry", [])
        cmd = [x.replace("${PYTHON}", "python3") for x in entry]
        
        env = os.environ.copy()
        env["OMI_CONFIG_MODE"] = "1" # MAGIC FLAG
        
        # Inject active configuration path
        config_path = self._get_active_config_path(svc_id)
        env["OMI_CONFIG_PATH"] = str(config_path)

        # Separate logs for config mode? Or same? Same is fine.
        log_rel_path = config.get("logs", {}).get("stdout", f"logs/services/{svc_id}.log")
        log_path = BASE_DIR / log_rel_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env["OMI_LOG_PATH"] = str(log_path)
        env["OMI_SERVICE_ID"] = svc_id
        
        try:
            STRUCTURE_MANAGER.set_busy(f"CONFIG_MODE_{svc_id}", f"Starting {svc_id} (Config)...")
            
            # EXCLUSIVE MODE HERE TOO
            for other_id in list(self.processes.keys()):
                if other_id != svc_id:
                    self.stop_service(other_id)

            LOGGER.info(f"Starting {svc_id} in CONFIGURATION MODE")
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self.processes[svc_id] = proc
            self.config_mode_services.add(svc_id)
            return True
        except Exception as e:
            LOGGER.error(f"Failed to start configuration mode for {svc_id}: {e}")
            return False
        finally:
            STRUCTURE_MANAGER.clear_busy(f"CONFIG_MODE_{svc_id}")

    def duplicate_config(self, svc_id: str, src_name: str, dst_name: str) -> bool:
        """Duplicates an existing configuration or template with a new name."""
        import shutil
        configs_dir = self._get_configs_dir(svc_id)
        
        # If src_name ends with .template, find file directly
        if src_name.endswith(".template"):
            src = configs_dir / src_name
        else:
            src = configs_dir / f"{src_name}.json"
            
        dst = configs_dir / f"{dst_name}.json"

        if not src.exists():
            LOGGER.error(f"Source configuration {src_name} not found for {svc_id}")
            return False
            
        if dst.exists():
            LOGGER.warning(f"Destination configuration {dst_name} already exists for {svc_id}")
            # Could overwrite or fail. Fail for safety.
            return False

        try:
            shutil.copy(src, dst)
            
            # Post-process to update internal name if exists (in file_info)
            if dst.suffix == ".json":
                try:
                    import json
                    with dst.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    if "file_info" in data and isinstance(data["file_info"], dict):
                        data["file_info"]["name"] = dst_name
                        with dst.open("w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    LOGGER.warning(f"Could not update 'name' metadata in {dst.name}: {e}")

            LOGGER.info(f"Configuration {src_name} duplicated as {dst_name} for {svc_id}")
            return True
        except Exception as e:
            LOGGER.error(f"Failed to duplicate configuration: {e}")
            return False

    def clone_service(self, svc_id: str, new_display_name: str) -> bool:
        """
        Clones a service: duplicates its folder, assigns new port and updates servicios.json.
        """
        if svc_id not in self.services_config:
            LOGGER.error(f"Source service {svc_id} not found")
            return False

        # Generate unique ID based on name
        new_id = new_display_name.replace(" ", "_").strip()
        if not new_id:
            new_id = f"{svc_id}_clone"
        
        # Ensure ID is unique in config map
        base_id = new_id
        counter = 1
        while new_id in self.services_config:
            new_id = f"{base_id}_{counter}"
            counter += 1

        orig_config = self.services_config[svc_id]
        orig_cwd_rel = orig_config.get("cwd", ".")
        orig_dir = BASE_DIR / orig_cwd_rel
        
        # New folder will go in servicios/new_id
        new_cwd_rel = f"servicios/{new_id}"
        new_dir = BASE_DIR / new_cwd_rel

        # 1. Duplicate Service Directory (if exists and is internal)
        import shutil
        try:
            if orig_dir.exists() and orig_cwd_rel != ".":
                LOGGER.info(f"Cloning directory {orig_dir} to {new_dir}")
                
                def ignore_heavy(path, names):
                    # Ignore heavy folders for fast cloning and to avoid duplicating unnecessary data
                    return ['node_modules', '.git', 'logs', '__pycache__', 'bin', 'fnm_data', '.venv']
                
                shutil.copytree(orig_dir, new_dir, ignore=ignore_heavy)
                
                # Clean any execution state in copy
                for cleanup in ["service_state.json", "active_config.txt", "runtime_config.json"]:
                    target = new_dir / cleanup
                    if target.exists():
                        target.unlink()
            else:
                LOGGER.warning(f"Service {svc_id} does not have a clear cloneable folder ({orig_cwd_rel})")
                # Proceed anyway, maybe use same folder (risky due to config collision)
                new_cwd_rel = orig_cwd_rel
        except Exception as e:
            LOGGER.error(f"Failed to duplicate service directory: {e}")
            return False

        # 2. Determine new unique web port
        # Find highest web port and add 1
        existing_ports = [s.get("web_port", 0) for s in self.services_config.values() if s.get("web_port")]
        new_port = max(existing_ports) + 1 if existing_ports else 9010

        # 3. Create new configuration definition
        new_config = orig_config.copy()
        new_config["id"] = new_id
        new_config["display_name"] = new_display_name
        new_config["cwd"] = new_cwd_rel
        new_config["web_port"] = new_port
        
        # Adjust log paths to be independent
        if "logs" in new_config:
            logs = new_config["logs"].copy()
            for key, val in logs.items():
                p = Path(val)
                logs[key] = f"logs/services/{new_id.lower()}{p.suffix}"
            new_config["logs"] = logs

        # 4. Persist in servicios.json
        services_json_path = BASE_DIR / "servicios" / "servicios.json"
        try:
            with services_json_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            
            data["services"].append(new_config)
            
            with services_json_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            # 5. Reload Manager and Sync Structure
            self._load_config()
            STRUCTURE_MANAGER.sync_from_servicios_json()
            
            LOGGER.info(f"Service {svc_id} successfully cloned as {new_id} on port {new_port}")
            return True
        except Exception as e:
            LOGGER.error(f"Failed to update servicios.json: {e}")
            return False
