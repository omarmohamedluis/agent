"""
Gestor de Servicios (ServiceManager).
Controla el ciclo de vida de los servicios (inicio, parada, estado),
gestionando procesos en segundo plano y sincronizando su configuración.
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
        self._load_config()

    def _load_config(self):
        # Sincronizar desde servicios.json vía manager para asegurar que structure.json esté actualizado
        STRUCTURE_MANAGER.sync_from_servicios_json()
        
        # Cargar mapa de configuración interno desde estructura (¿o seguir usando servicios.json para config cruda?)
        # El código original cargaba servicios.json directamente. Mantengamos eso por ahora ya que contiene detalles de ejecución (cmd, cwd)
        # que podrían no estar completamente en la lista de servicios de structure.json (structure.json tiene metadatos).
        
        services_json_path = BASE_DIR / "servicios" / "servicios.json"
        
        # Auto-restaurar desde plantilla si falta (Auto-Reparación)
        if not services_json_path.exists():
            template_path = services_json_path.with_suffix(".json.template")
            if template_path.exists():
                LOGGER.info(f"Restaurando {services_json_path.name} desde plantilla...")
                import shutil
                try:
                    shutil.copy(template_path, services_json_path)
                except Exception as e:
                    LOGGER.error(f"Fallo al restaurar plantilla: {e}")

        try:
            with services_json_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                self.services_config = {s["id"]: s for s in data.get("services", [])}
        except Exception as e:
            LOGGER.error(f"Fallo al cargar configuración de servicios: {e}")
            self.services_config = {}

    def stop_all(self, persist_state: bool = False):
        """Detiene todos los servicios."""
        for svc_id in list(self.processes.keys()):
            self.stop_service(svc_id, persist_state=persist_state)

    def get_services(self) -> Dict[str, Any]:
        """Retorna todos los servicios con su estado actual, fusionando estado dinámico de structure.json."""
        status_map = {}
        
        # 1. Obtener configuración base y estado del proceso
        for svc_id, config in self.services_config.items():
            proc = self.processes.get(svc_id)
            is_running = proc is not None and proc.poll() is None
            status_map[svc_id] = {
                **config,
                "running": is_running,
                "pid": proc.pid if is_running else None
            }

        # 2. Fusionar estado dinámico de structure.json vía Manager
        structure = STRUCTURE_MANAGER.get_structure()
        for svc in structure.get("services", []):
            svc_name = svc.get("name")
            if svc_name in status_map:
                # Fusionar campos dinámicos
                if "web_port" in svc:
                    status_map[svc_name]["web_port"] = svc["web_port"]
                if "enabled" in svc:
                    status_map[svc_name]["enabled"] = svc["enabled"]
                if "display_name" in svc:
                    status_map[svc_name]["display_name"] = svc["display_name"]

        return status_map

    def start_service(self, svc_id: str) -> bool:
        if svc_id not in self.services_config:
            LOGGER.error(f"Servicio {svc_id} no encontrado")
            return False

        if svc_id in self.processes and self.processes[svc_id].poll() is None:
            LOGGER.info(f"El servicio {svc_id} ya se está ejecutando")
            return True

        config = self.services_config[svc_id]
        if config.get("type") != "process":
            LOGGER.info(f"El servicio {svc_id} no es de tipo proceso")
            return False

        try:
            STRUCTURE_MANAGER.set_busy(f"SERVICE_OP_{svc_id}", f"Iniciando {svc_id}...")
            
            cwd = BASE_DIR / config.get("cwd", ".")
            entry = config.get("entry", [])
            
            # Resolver variable ${PYTHON}
            cmd = [x.replace("${PYTHON}", "python3") for x in entry]
            
            # MODO EXCLUSIVO: Detener todos los otros servicios en ejecución primero
            for other_id in list(self.processes.keys()):
                if other_id != svc_id:
                    LOGGER.info(f"Modo exclusivo: Deteniendo {other_id} antes de iniciar {svc_id}")
                    self.stop_service(other_id)
            
            LOGGER.info(f"Iniciando servicio {svc_id}: {cmd} en {cwd}")
            
            # Actualizar Estado de Servicio Activo en structure.json
            # Nota: client.py ya debería haberlo habilitado para asegurar configuración de red correcta,
            # pero lo reafirmamos aquí.
            STRUCTURE_MANAGER.update_service_state(svc_id, enabled=True)
            
            # Preparar Entorno
            env = os.environ.copy()
            
            # Determinar Ruta de Log
            log_rel_path = config.get("logs", {}).get("stdout", f"logs/services/{svc_id}.log")
            log_path = BASE_DIR / log_rel_path
            
            # Asegurar que el directorio existe
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            env["OMI_LOG_PATH"] = str(log_path)
            env["OMI_SERVICE_ID"] = svc_id
            
            LOGGER.info(f"Iniciando servicio {svc_id} con ruta de log: {log_path}")

            # Iniciar Proceso
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                start_new_session=True, # setsid
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self.processes[svc_id] = proc
            
            # Disparar sincronización de metadatos (web_port, etc)
            try:
                STRUCTURE_MANAGER.sync_service_metadata(svc_id)
            except Exception as e:
                LOGGER.error(f"Fallo al sincronizar metadatos del servicio: {e}")

            return True
        except Exception as e:
            LOGGER.error(f"Fallo al iniciar servicio {svc_id}: {e}")
            return False
        finally:
            STRUCTURE_MANAGER.clear_busy(f"SERVICE_OP_{svc_id}")

    def stop_service(self, svc_id: str, persist_state: bool = False) -> bool:
        proc = self.processes.get(svc_id)
        if not proc:
            return False

        if proc.poll() is not None:
            del self.processes[svc_id]
            return True

        try:
            STRUCTURE_MANAGER.set_busy(f"SERVICE_OP_{svc_id}", f"Deteniendo {svc_id}...")
            LOGGER.info(f"Deteniendo servicio {svc_id} (PID {proc.pid})")
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            
            del self.processes[svc_id]
            
            # Actualizar Estado de Servicio Activo a None (STANDBY)
            if not self.processes and not persist_state:
                 STRUCTURE_MANAGER.update_service_state(svc_id, enabled=False)
            
            return True
        except Exception as e:
            LOGGER.error(f"Fallo al detener servicio {svc_id}: {e}")
            return False
        finally:
            STRUCTURE_MANAGER.clear_busy(f"SERVICE_OP_{svc_id}")
