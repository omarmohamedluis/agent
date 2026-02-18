import logging
from typing import Any, Dict, Optional

from heartbeat import (
    get_heartbeat_snapshot,
    register_heartbeat_listener,
    unregister_heartbeat_listener
)
from net_com_handler import check_server_status
from display_manager import DisplayManager
from structure_manager import get_structure_manager

# --- Configuración ---
LOGGER = logging.getLogger("omiclient.ui")
STRUCTURE_MANAGER = get_structure_manager()

# --- Estado Global de UI ---
_display_manager = DisplayManager(driver_name="ssd1306")
_standard_listener_registered = False

def init():
    """Inicializa la pantalla y el driver correspondiente."""
    try:
        _display_manager.init()
        LOGGER.info(f"UI inicializada con driver: {_display_manager.driver_name}")
    except Exception as e:
        LOGGER.error(f"Fallo al inicializar pantalla: {e}")

# --- API Pública de UI (Delegación al Driver) ---

def show_loading_ui(percent: int, label: str = ""):
    """Muestra pantalla de carga delegando al driver."""
    stop_standard_ui()
    if _display_manager.driver:
        _display_manager.driver.render_loading(percent, label)

def show_message_ui(label: str = "", is_error: bool = False):
    """Muestra un mensaje delegando al driver."""
    stop_standard_ui()
    if _display_manager.driver:
        _display_manager.driver.render_message(label, is_error)

def update_standard_ui(snapshot: Dict[str, Any]) -> None:
    """Actualiza la interfaz estándar delegando al driver."""
    structure = STRUCTURE_MANAGER.get_structure()
    
    # 1. Verificar Estado "Busy" del sistema
    sys_status = structure.get("system_status", {})
    if sys_status.get("is_busy"):
        msg = sys_status.get("busy_message") or "PROCESANDO..."
        show_loading_ui(50, msg)
        return

    # 2. Render normal delegando al driver activo
    if _display_manager.driver:
        server_online = check_server_status()
        _display_manager.driver.render_standard(snapshot, structure, server_online)

def _heartbeat_callback(snapshot: Dict[str, Any]):
    update_standard_ui(snapshot)

def start_standard_ui():
    """Inicia la actualización automática de la UI basada en el heartbeat."""
    global _standard_listener_registered
    if not _standard_listener_registered:
        register_heartbeat_listener(_heartbeat_callback)
        _standard_listener_registered = True
    update_standard_ui(get_heartbeat_snapshot())

def stop_standard_ui():
    """Detiene la actualización automática de la UI."""
    global _standard_listener_registered
    if _standard_listener_registered:
        unregister_heartbeat_listener(_heartbeat_callback)
        _standard_listener_registered = False

def turn_ui_off():
    """Apaga la pantalla."""
    stop_standard_ui()
    _display_manager.clear()

# Inicializar al importar
init()
