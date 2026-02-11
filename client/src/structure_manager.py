"""
Gestor de Estructura (StructureManager).
Centraliza el acceso y modificación de `structure.json`, gestionando el estado del sistema,
la configuración de servicios y la sincronización con archivos externos.
"""
import json
import threading
import shutil
import subprocess
import ipaddress
from pathlib import Path
from typing import Any, Dict, List, Optional

from logger import get_logger, log_event, log_print
from system_info import get_sys_version, get_serial, get_host
from file_lock import file_lock

LOGGER = get_logger("omiclient.structure_manager")

class StructureManager:
    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(StructureManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        self.base_dir = Path(__file__).resolve().parents[1]
        self.data_dir = self.base_dir / "data"
        self.structure_path = self.data_dir / "structure.json"
        self.lock_path = self.data_dir / "structure.json.lock"
        self.servicios_path = self.base_dir / "servicios" / "servicios.json"
        self.project_root = self.base_dir.parent

        self._data: Dict[str, Any] = {}
        self._data_lock = threading.RLock()
        
        # Estado solo en tiempo de ejecución (no se guarda en disco)
        self._busy_tasks: Dict[str, str] = {} # task_id -> mensaje
        self._service_map_mtimes: Dict[str, float] = {} # svc_id -> last_mtime
        self._status_lock = threading.Lock()
        
        self._initialized = True
        self._last_mtime = 0.0
        LOGGER.info("StructureManager inicializado")
        self.load_structure()

        self._listeners = []
        self._listeners_lock = threading.Lock()

    def set_busy(self, task_id: str, message: str):
        """Establece una tarea como ocupada."""
        with self._status_lock:
            self._busy_tasks[task_id] = message
        self._notify_listeners()

    def clear_busy(self, task_id: str):
        """Limpia una tarea ocupada."""
        with self._status_lock:
            if task_id in self._busy_tasks:
                del self._busy_tasks[task_id]
        self._notify_listeners()

    def add_listener(self, callback):
        with self._listeners_lock:
            self._listeners.append(callback)

    def _notify_listeners(self):
        with self._listeners_lock:
            listeners = list(self._listeners)
        
        # Obtener estructura completa incluyendo estado
        full_data = self.get_structure()
        
        for cb in listeners:
            try:
                cb(full_data)
            except Exception as e:
                LOGGER.error(f"Error en listener de estructura: {e}")

    def check_for_external_changes(self):
        """Verifica si structure.json ha cambiado en disco y recarga si es necesario."""
        try:
            if not self.structure_path.exists():
                return

            current_mtime = self.structure_path.stat().st_mtime
            if current_mtime > self._last_mtime:
                LOGGER.info("Cambio externo detectado en structure.json, recargando...")
                self.load_structure()
                self._notify_listeners()
        except Exception as e:
            LOGGER.error(f"Error verificando cambios externos: {e}")

    def load_structure(self) -> Dict[str, Any]:
        """Carga structure.json en memoria. Lo crea desde plantilla si falta."""
        with self._data_lock:
            if not self.structure_path.exists():
                self._create_from_template()
            
            try:
                # Aún usamos bloqueo de archivo para lectura para asegurar consistencia
                with file_lock(self.lock_path):
                    with self.structure_path.open("r", encoding="utf-8") as f:
                        self._data = json.load(f)
            except Exception as e:
                LOGGER.error(f"Fallo al cargar structure.json: {e}")
                self._data = {}
            
            if self.structure_path.exists():
                self._last_mtime = self.structure_path.stat().st_mtime
            
            # Auto-completar info faltante
            self._enrich_system_info()
            return self.get_structure() # Retorna estructura completa con estado

    def get_structure(self) -> Dict[str, Any]:
        """Retorna una copia de la estructura en memoria fusionada con el estado en tiempo de ejecución."""
        with self._data_lock:
            data = json.loads(json.dumps(self._data)) # Copia profunda
        
        with self._status_lock:
            # Aplanar tareas ocupadas en un estado único para la UI
            is_busy = len(self._busy_tasks) > 0
            # Usar el último mensaje añadido o uno por defecto
            busy_message = list(self._busy_tasks.values())[-1] if is_busy else ""
            
            data["system_status"] = {
                "is_busy": is_busy,
                "busy_message": busy_message,
                "tasks": self._busy_tasks.copy()
            }
            
        return data

    def save_structure(self) -> bool:
        """Persiste la estructura en memoria al disco."""
        with self._data_lock:
            try:
                with file_lock(self.lock_path):
                    self.data_dir.mkdir(parents=True, exist_ok=True)
                    with self.structure_path.open("w", encoding="utf-8") as f:
                        json.dump(self._data, f, indent=2, ensure_ascii=False)
                    self._last_mtime = self.structure_path.stat().st_mtime
                return True
            except Exception as e:
                LOGGER.error(f"Fallo al guardar structure.json: {e}")
                return False

    def _create_from_template(self):
        template_path = self.project_root / "docs" / "InfoClient.json"
        if not template_path.exists():
            LOGGER.error(f"Plantilla no encontrada en {template_path}")
            return

        LOGGER.warning("structure.json no encontrado, creando desde plantilla")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            with file_lock(self.lock_path):
                shutil.copyfile(template_path, self.structure_path)
        except Exception as e:
            LOGGER.error(f"Fallo al crear structure.json desde plantilla: {e}")

    def _enrich_system_info(self):
        """Actualiza información de identidad y versión en memoria."""
        with self._data_lock:
            version = get_sys_version(self.project_root)
            if version:
                self._data.setdefault("version", {})["version"] = version

            identity = self._data.setdefault("identity", {})
            identity.setdefault("index", identity.get("index"))
            identity["serial"] = get_serial()
            identity["host"] = get_host()
            identity.setdefault("name", identity["host"])
            if not identity["name"] or identity["name"].strip() == "":
                identity["name"] = identity["host"]

    def update_network_interfaces(self, interfaces: List[Dict[str, Any]], main_nic: Optional[str] = None) -> bool:
        """Actualiza interfaces de red y main_nic. Retorna True si hubo cambios."""
        with self._data_lock:
            network = self._data.setdefault("network", {})
            current_interfaces = network.get("interfaces", [])
            current_main_nic = network.get("main_nic")
            
            # Verificar cambios
            interfaces_changed = json.dumps(current_interfaces, sort_keys=True) != json.dumps(interfaces, sort_keys=True)
            main_nic_changed = current_main_nic != main_nic
            
            if interfaces_changed or main_nic_changed:
                network["interfaces"] = interfaces
                if main_nic is not None:
                    network["main_nic"] = main_nic
                self.save_structure()
                return True
            return False

    def update_service_state(self, svc_id: Optional[str], enabled: bool, web_port: Optional[int] = None) -> bool:
        """Actualiza el estado habilitado y puerto web de un servicio. Impone exclusividad si enabled=True."""
        updated = False
        with self._data_lock:
            services = self._data.get("services", [])
            for svc in services:
                svc_name = svc.get("name")
                is_target = (svc_name == svc_id)
                
                if is_target:
                    # Servicio objetivo: establecer estado deseado
                    if svc.get("enabled") != enabled:
                        svc["enabled"] = enabled
                        updated = True
                    
                    if web_port is not None and svc.get("web_port") != web_port:
                        svc["web_port"] = web_port
                        updated = True
                else:
                    # Otros servicios:
                    # Si estamos habilitando un nuevo servicio, deshabilitar los otros (Exclusivo)
                    if enabled and svc.get("enabled"):
                        svc["enabled"] = False
                        updated = True
            
            if updated:
                self.save_structure()
        return updated

    def sync_from_servicios_json(self):
        """Sincroniza servicios disponibles desde servicios.json a structure.json."""
        if not self.servicios_path.exists():
            return

        with self._data_lock:
            try:
                with self.servicios_path.open("r", encoding="utf-8") as f:
                    servicios_data = json.load(f)
                
                current_services = {s["name"]: s for s in self._data.get("services", [])}
                new_services_list = []
                updated = False

                for s in servicios_data.get("services", []):
                    svc_id = s.get("id")
                    if svc_id in current_services:
                        # Actualizar metadatos
                        svc = current_services[svc_id]
                        if svc.get("display_name") != s.get("display_name") or svc.get("web_port") != s.get("web_port"):
                            svc["display_name"] = s.get("display_name")
                            svc["web_port"] = s.get("web_port")
                            updated = True
                        new_services_list.append(svc)
                    else:
                        # Añadir nuevo
                        new_services_list.append({
                            "name": svc_id,
                            "display_name": s.get("display_name"),
                            "enabled": False,
                            "configuration": "default",
                            "web_port": s.get("web_port"),
                            "vlan": None,
                            "vlan_active": False,
                            "vlan_mode": "dhcp",
                            "vlan_ip": "",
                            "vlan_mask": "",
                            "vlan_gateway": "",
                            "ip_mode": "dhcp",
                            "ip": "",
                            "mask": "",
                            "gateway": ""
                        })
                        updated = True
                
                # Verificar si se eliminaron servicios
                if len(new_services_list) != len(current_services):
                     updated = True

                if updated:
                    self._data["services"] = new_services_list
                    self.save_structure()
                    LOGGER.info("Servicios sincronizados desde servicios.json")

            except Exception as e:
                LOGGER.error(f"Error sincronizando servicios.json: {e}")

    def get_active_service(self) -> Optional[Dict[str, Any]]:
        with self._data_lock:
            for svc in self._data.get("services", []):
                if svc.get("enabled"):
                    return svc
        return None

    def sync_service_metadata(self, svc_id: str):
        """Sincroniza metadatos para un servicio específico desde su archivo map a structure.json."""
        LOGGER.info(f"Iniciando sincronización para servicio: {svc_id}")
        
        # 1. Encontrar CWD desde config de servicios
        if not self.servicios_path.exists():
            return

        try:
            with self.servicios_path.open("r", encoding="utf-8") as f:
                services_config = {s["id"]: s for s in json.load(f).get("services", [])}
            
            if svc_id not in services_config:
                return
            
            config = services_config[svc_id]
            cwd = self.base_dir / config.get("cwd", ".")
            
            with self._data_lock:
                services = self._data.get("services", [])
                target_svc = next((s for s in services if s.get("name") == svc_id), None)
                if not target_svc:
                    return

                # 3. Intentar encontrar y leer archivo map
                updated = False
                map_files = []
                
                # Prioridad 1: Configuración Activa
                active_config_file = cwd / "active_config.txt"
                if active_config_file.exists():
                    try:
                        active_name = active_config_file.read_text(encoding="utf-8").strip()
                        if active_name:
                            # Asumimos que está en configs/
                            map_files.append(cwd / "configs" / f"{active_name}.json")
                            # También intentar en raíz por compatibilidad
                            map_files.append(cwd / f"{active_name}.json")
                    except Exception as e:
                        LOGGER.warning(f"Error leyendo active_config.txt: {e}")

                # Prioridad 2: Archivos por defecto (Legacy)
                map_files.extend([
                    cwd / "configs" / "Default.json",
                    cwd / "configs" / "default.json",
                    cwd / "OMIMIDI_map.json", 
                    cwd / "map.json", 
                    cwd / "config.json"
                ])

                for p in map_files:
                    if p.exists():
                        try:
                            with p.open("r", encoding="utf-8") as f_map:
                                mdata = json.load(f_map)
                                
                                # Sincronizar web_port (preferir archivo map, fallback a config servicios)
                                file_info = mdata.get("file_info", {})
                                map_port = file_info.get("ui_port")
                                web_port = map_port if map_port else config.get("web_port")
                                
                                if web_port is not None and target_svc.get("web_port") != web_port:
                                    target_svc["web_port"] = web_port
                                    updated = True
                                
                                # Sincronizar info VLAN (Estructura específica MIDI)
                                net = mdata.get("net", {})
                                vlan = net.get("vlan")
                                vlan_active = net.get("vlan_active")
                                
                                # Configuración IP
                                ip_mode = net.get("ip_mode")
                                ip = net.get("ip")
                                mask = net.get("mask")
                                gateway = net.get("gateway")
                                
                                if vlan is not None and target_svc.get("vlan") != vlan:
                                    target_svc["vlan"] = vlan
                                    updated = True
                                if vlan_active is not None and target_svc.get("vlan_active") != vlan_active:
                                    target_svc["vlan_active"] = vlan_active
                                    updated = True
                                    
                                # Sincronizar campos IP (Nomenclatura estandarizada y compatible con NetManager)
                                if ip_mode:
                                    if target_svc.get("ip_mode") != ip_mode:
                                        target_svc["ip_mode"] = ip_mode
                                        updated = True
                                    if target_svc.get("vlan_mode") != ip_mode:
                                        target_svc["vlan_mode"] = ip_mode
                                        updated = True
                                        
                                if ip is not None:
                                    if target_svc.get("ip") != ip:
                                        target_svc["ip"] = ip
                                        updated = True
                                    if target_svc.get("vlan_ip") != ip:
                                        target_svc["vlan_ip"] = ip
                                        updated = True
                                        
                                if mask is not None:
                                    if target_svc.get("mask") != mask:
                                        target_svc["mask"] = mask
                                        updated = True
                                    if target_svc.get("vlan_mask") != mask:
                                        target_svc["vlan_mask"] = mask
                                        updated = True
                                        
                                if gateway is not None:
                                    if target_svc.get("gateway") != gateway:
                                        target_svc["gateway"] = gateway
                                        updated = True
                                    if target_svc.get("vlan_gateway") != gateway:
                                        target_svc["vlan_gateway"] = gateway
                                        updated = True

                                # Sincronizar Nombre de Configuración
                                file_info = mdata.get("file_info", {})
                                config_name = file_info.get("name")
                                if config_name and target_svc.get("configuration") != config_name:
                                    target_svc["configuration"] = config_name
                                    updated = True
                        except Exception as e:
                            LOGGER.error(f"Error leyendo archivo map {p}: {e}")
                        break

                if updated:
                    self.save_structure()
                    LOGGER.info(f"structure.json actualizado exitosamente para {svc_id}")
                    pass

        except Exception as e:
            LOGGER.error(f"Error sincronizando servicio {svc_id}: {e}")

# Accesor Singleton
_manager_instance = None
def get_structure_manager() -> StructureManager:
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = StructureManager()
    return _manager_instance
