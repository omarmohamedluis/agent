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
        self.config_mode_services: set = set()
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
            
            # Detectar si está en modo configuración
            is_config_mode = False
            if is_running:
                # Verificar variable de entorno en el proceso (si es posible)
                # O mejor, mantener un registro interno.
                # Como no tenemos registro interno fácil sin cambiar __init__,
                # podemos inferirlo si el comando tiene la variable de entorno.
                # Pero subprocess.Popen no expone env fácilmente después de creado.
                # Usaremos un hack: si el log path tiene "configs" o algo así? No.
                # Mejor: añadir un set 'config_mode_services' en __init__.
                pass

            # Obtener config activa
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
            
            # Inyectar Configuración Activa
            config_path = self._get_active_config_path(svc_id)
            if config_path:
                env["OMI_CONFIG_PATH"] = str(config_path)
                LOGGER.info(f"Usando configuración: {config_path}")
            
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
            self.config_mode_services.discard(svc_id)
            
            # Actualizar Estado de Servicio Activo a None (STANDBY)
            if not self.processes and not persist_state:
                 STRUCTURE_MANAGER.update_service_state(svc_id, enabled=False)
            
            return True
        except Exception as e:
            LOGGER.error(f"Fallo al detener servicio {svc_id}: {e}")
            return False
        finally:
            STRUCTURE_MANAGER.clear_busy(f"SERVICE_OP_{svc_id}")

    # --- Gestión de Configuraciones (Multi-Config) ---
    def _get_service_dir(self, svc_id: str) -> Path:
        return BASE_DIR / "servicios" / svc_id

    def _get_configs_dir(self, svc_id: str) -> Path:
        return self._get_service_dir(svc_id) / "configs"

    def _get_active_config_path(self, svc_id: str) -> Path:
        """Devuelve la ruta al archivo JSON de configuración activo."""
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
        """Lista las configuraciones disponibles para un servicio y auto-crea Default si falta."""
        configs_dir = self._get_configs_dir(svc_id)
        if not configs_dir.exists():
            configs_dir.mkdir(parents=True, exist_ok=True)
            
        files = list(configs_dir.glob("*.json"))
        if not files:
            # Si no hay configs, crear Default basada en plantilla o mapa legacy
            import shutil
            
            default_config = configs_dir / "Default.json"
            template = configs_dir / "Base_config.json.template"
            
            # Prioridad 1: Nueva plantilla Base_config.json.template
            if template.exists():
                try:
                    shutil.copy(template, default_config)
                    LOGGER.info(f"Creada configuración Default.json para {svc_id} desde plantilla BASE")
                    files = [default_config]
                except Exception as e:
                    LOGGER.error(f"Error creando Default.json desde plantilla: {e}")
            else:
                # Prioridad 2: Intentar localizar mapa legacy para migración
                service_dir = self._get_service_dir(svc_id)
                if svc_id == "MIDI":
                    legacy_map = service_dir / "OMIMIDI_map.json"
                else:
                    legacy_map = service_dir / f"{svc_id}_map.json"
                    
                if legacy_map.exists():
                    try:
                        shutil.copy(legacy_map, default_config)
                        LOGGER.info(f"Creada configuración Default.json para {svc_id} desde mapa legacy")
                        files = [default_config]
                    except Exception as e:
                        LOGGER.error(f"Error creando Default.json desde legacy: {e}")
                else:
                    # Prioridad 3: Intentar buscar template legacy
                    legacy_template = legacy_map.with_suffix(".json.template") if 'legacy_map' in locals() else None
                    if legacy_template and legacy_template.exists():
                        try:
                            shutil.copy(legacy_template, default_config)
                            files = [default_config]
                        except Exception:
                            pass

        return sorted([f.stem for f in files])

    def select_config(self, svc_id: str, config_name: str) -> bool:
        """Selecciona una configuración como activa (guarda el nombre)."""
        configs_dir = self._get_configs_dir(svc_id)
        target = configs_dir / f"{config_name}.json"
        
        if not target.exists():
            LOGGER.error(f"Configuración {config_name} no encontrada para {svc_id}")
            return False
            
        try:
            # Guardar el nombre de la config activa en un archivo de texto
            active_file = self._get_service_dir(svc_id) / "active_config.txt"
            active_file.write_text(config_name, encoding="utf-8")
            LOGGER.info(f"Configuración activa para {svc_id} establecida a: {config_name}")
            return True
        except Exception as e:
            LOGGER.error(f"Fallo al seleccionar configuración {config_name}: {e}")
            return False

    def save_config_as(self, svc_id: str, config_name: str) -> bool:
        """Guarda la configuración activa actual con un nombre."""
        import shutil
        
        configs_dir = self._get_configs_dir(svc_id)
        configs_dir.mkdir(parents=True, exist_ok=True)
        
        src = self._get_active_config_path(svc_id)
        dst = configs_dir / f"{config_name}.json"
        
        if not src.exists():
            LOGGER.error(f"No hay configuración activa para guardar en {svc_id}")
            return False
            
        try:
            shutil.copy(src, dst)
            LOGGER.info(f"Configuración activa guardada como {config_name} para {svc_id}")
            return True
        except Exception as e:
            LOGGER.error(f"Fallo al guardar configuración como {config_name}: {e}")
            return False
            
    def delete_config(self, svc_id: str, config_name: str) -> bool:
        """Elimina un archivo de configuración específico."""
        if config_name == "Default":
            LOGGER.warning(f"Intento de eliminar configuración Default en {svc_id}")
            return False

        configs_dir = self._get_configs_dir(svc_id)
        target = configs_dir / f"{config_name}.json"
        
        if not target.exists():
            LOGGER.warning(f"Configuración {config_name} no existe en {svc_id}")
            return False
            
        try:
            target.unlink()
            LOGGER.info(f"Configuración {config_name} eliminada de {svc_id}")
            
            # Si la activa era la eliminada, volver a Default
            active_file = self._get_service_dir(svc_id) / "active_config.txt"
            if active_file.exists():
                current = active_file.read_text(encoding="utf-8").strip()
                if current == config_name:
                    active_file.write_text("Default", encoding="utf-8")
                    LOGGER.info(f"Configuración activa revertida a Default para {svc_id}")
            
            return True
        except Exception as e:
            LOGGER.error(f"Fallo al eliminar configuración {config_name}: {e}")
            return False

    def start_config_mode(self, svc_id: str) -> bool:
        """Inicia el servicio en MODO CONFIGURACIÓN (Offline/Mock)."""
        # Es similar a start_service pero inyectando la variable de entorno
        
        if svc_id not in self.services_config:
            return False
            
        # Si ya corre, detenerlo (para cambiar de modo)
        if svc_id in self.processes and self.processes[svc_id].poll() is None:
            self.stop_service(svc_id)
            
        config = self.services_config[svc_id]
        cwd = BASE_DIR / config.get("cwd", ".")
        entry = config.get("entry", [])
        cmd = [x.replace("${PYTHON}", "python3") for x in entry]
        
        env = os.environ.copy()
        env["OMI_CONFIG_MODE"] = "1" # FLAG MÁGICA
        
        # Inyectar ruta de configuración activa
        config_path = self._get_active_config_path(svc_id)
        env["OMI_CONFIG_PATH"] = str(config_path)

        # Logs separados para config mode? O los mismos? Los mismos está bien.
        log_rel_path = config.get("logs", {}).get("stdout", f"logs/services/{svc_id}.log")
        log_path = BASE_DIR / log_rel_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env["OMI_LOG_PATH"] = str(log_path)
        env["OMI_SERVICE_ID"] = svc_id
        
        try:
            STRUCTURE_MANAGER.set_busy(f"CONFIG_MODE_{svc_id}", f"Iniciando {svc_id} (Config)...")
            
            # MODO EXCLUSIVO TAMBIÉN AQUÍ
            for other_id in list(self.processes.keys()):
                if other_id != svc_id:
                    self.stop_service(other_id)

            LOGGER.info(f"Iniciando {svc_id} en MODO CONFIGURACIÓN")
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
            LOGGER.error(f"Fallo al iniciar modo configuración para {svc_id}: {e}")
            return False
        finally:
            STRUCTURE_MANAGER.clear_busy(f"CONFIG_MODE_{svc_id}")

    def duplicate_config(self, svc_id: str, src_name: str, dst_name: str) -> bool:
        """Duplica una configuración existente o una plantilla con un nuevo nombre."""
        import shutil
        configs_dir = self._get_configs_dir(svc_id)
        
        # Si src_name termina en .template, buscamos el archivo directamente
        if src_name.endswith(".template"):
            src = configs_dir / src_name
        else:
            src = configs_dir / f"{src_name}.json"
            
        dst = configs_dir / f"{dst_name}.json"

        if not src.exists():
            LOGGER.error(f"Configuración origen {src_name} no encontrada para {svc_id}")
            return False
            
        if dst.exists():
            LOGGER.warning(f"Configuración destino {dst_name} ya existe para {svc_id}")
            # Podríamos sobrescribir o fallar. Fallamos para seguridad.
            return False

        try:
            shutil.copy(src, dst)
            
            # Post-procesar para actualizar el nombre interno si existe (en file_info)
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
                    LOGGER.warning(f"No se pudo actualizar el metadato 'name' en {dst.name}: {e}")

            LOGGER.info(f"Configuración {src_name} duplicada como {dst_name} para {svc_id}")
            return True
        except Exception as e:
            LOGGER.error(f"Fallo al duplicar configuración: {e}")
            return False

    def clone_service(self, svc_id: str, new_display_name: str) -> bool:
        """
        Clona un servicio: duplica su carpeta, asigna un nuevo puerto y actualiza servicios.json.
        """
        if svc_id not in self.services_config:
            LOGGER.error(f"Servicio origen {svc_id} no encontrado")
            return False

        # Generar ID único basado en el nombre
        new_id = new_display_name.replace(" ", "_").strip()
        if not new_id:
            new_id = f"{svc_id}_clone"
        
        # Asegurar que el ID sea único en el mapa de configuración
        base_id = new_id
        counter = 1
        while new_id in self.services_config:
            new_id = f"{base_id}_{counter}"
            counter += 1

        orig_config = self.services_config[svc_id]
        orig_cwd_rel = orig_config.get("cwd", ".")
        orig_dir = BASE_DIR / orig_cwd_rel
        
        # La nueva carpeta irá en servicios/new_id
        new_cwd_rel = f"servicios/{new_id}"
        new_dir = BASE_DIR / new_cwd_rel

        # 1. Duplicar Carpeta del Servicio (si existe y es interna)
        import shutil
        try:
            if orig_dir.exists() and orig_cwd_rel != ".":
                LOGGER.info(f"Clonando directorio {orig_dir} a {new_dir}")
                
                def ignore_heavy(path, names):
                    # Ignorar carpetas pesadas para que el clon sea rápido y no duplique datos innecesarios
                    return ['node_modules', '.git', 'logs', '__pycache__', 'bin', 'fnm_data', '.venv']
                
                shutil.copytree(orig_dir, new_dir, ignore=ignore_heavy)
                
                # Limpiar cualquier estado de ejecución en la copia
                for cleanup in ["service_state.json", "active_config.txt", "runtime_config.json"]:
                    target = new_dir / cleanup
                    if target.exists():
                        target.unlink()
            else:
                LOGGER.warning(f"El servicio {svc_id} no tiene una carpeta propia clonable clara ({orig_cwd_rel})")
                # Procedemos igual, quizás use la misma carpeta (aunque arriesgado por colisión de configs)
                new_cwd_rel = orig_cwd_rel
        except Exception as e:
            LOGGER.error(f"Fallo al duplicar directorio de servicio: {e}")
            return False

        # 2. Determinar nuevo puerto web único
        # Buscar el puerto web más alto y sumarle 1
        existing_ports = [s.get("web_port", 0) for s in self.services_config.values() if s.get("web_port")]
        new_port = max(existing_ports) + 1 if existing_ports else 9010

        # 3. Crear nueva definición de configuración
        new_config = orig_config.copy()
        new_config["id"] = new_id
        new_config["display_name"] = new_display_name
        new_config["cwd"] = new_cwd_rel
        new_config["web_port"] = new_port
        
        # Ajustar rutas de logs para que sean independientes
        if "logs" in new_config:
            logs = new_config["logs"].copy()
            for key, val in logs.items():
                p = Path(val)
                logs[key] = f"logs/services/{new_id.lower()}{p.suffix}"
            new_config["logs"] = logs

        # 4. Persistir en servicios.json
        services_json_path = BASE_DIR / "servicios" / "servicios.json"
        try:
            with services_json_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            
            data["services"].append(new_config)
            
            with services_json_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            # 5. Recargar Manager y Sincronizar Estructura
            self._load_config()
            STRUCTURE_MANAGER.sync_from_servicios_json()
            
            LOGGER.info(f"Servicio {svc_id} clonado exitosamente como {new_id} en puerto {new_port}")
            return True
        except Exception as e:
            LOGGER.error(f"Fallo al actualizar servicios.json: {e}")
            return False
