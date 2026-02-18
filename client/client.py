"""
Punto de entrada principal del Cliente OMI Agent.
Gestiona el servidor web, el ciclo de vida de los servicios y la comunicación básica.
"""
import sys
import json
import subprocess # Added for terminal cleanup
import asyncio
import logging
import uvicorn
import socket
import signal
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Configuración de rutas y sys.path ANTES de otras importaciones
BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.append(str(SRC_DIR))

from logger import configure_logging, get_logger

# Monkey-patch para threading.Thread para asegurar que todos los hilos tengan nombre
# Esto ayuda a identificar hilos creados por librerías (como uvicorn/anyio) que no los nombran.
_original_thread_init = threading.Thread.__init__

def _patched_thread_init(self, *args, **kwargs):
    _original_thread_init(self, *args, **kwargs)
    if not self.name or self.name.startswith("Thread-"):
        # Intentar inferir un mejor nombre o simplemente etiquetarlo
        self.name = f"AutoNamed-{self.name}"

threading.Thread.__init__ = _patched_thread_init

# Configurar Logging INMEDIATAMENTE
configure_logging()
LOGGER = get_logger("omiclient.core")

from service_manager import ServiceManager
from system import get_system_status
import ui
from net_com_handler import handshake, close_comm_channel, get_last_contact_time, check_server_status, push_config_to_server
from structure_manager import get_structure_manager
from heartbeat import start_heartbeat, get_heartbeat_snapshot

# Gestores
STRUCTURE_MANAGER = get_structure_manager()
service_manager = ServiceManager()


# --- CICLO DE VIDA (Solo Servidor Web) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Esto solo se ejecuta si arranca uvicorn (Modo Standalone)
    LOGGER.info("Iniciando Servidor Web (Modo Standalone)...")
    
    # Nombrar los hilos del ejecutor por defecto para mejor depuración
    import concurrent.futures
    try:
        loop = asyncio.get_running_loop()
        executor = concurrent.futures.ThreadPoolExecutor(thread_name_prefix="AsyncWorker")
        loop.set_default_executor(executor)
    except Exception as e:
        LOGGER.warning(f"Fallo al configurar ejecutor nombrado: {e}")

    yield
    LOGGER.info("Deteniendo Servidor Web...")

# --- DEFINICIÓN DE LA APP ---
app = FastAPI(title="OMI Agent Client", lifespan=lifespan)

# Montar Estáticos y Assets
STATIC_DIR = BASE_DIR / "web" / "static"
TEMPLATES_DIR = BASE_DIR / "web" / "templates"
UTILITIES_DIR = BASE_DIR / "web" / "utilities"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/assets", StaticFiles(directory=UTILITIES_DIR), name="assets")

# Plantillas
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --- SERVER MANAGEMENT ---
class StandaloneServer(uvicorn.Server):
    def install_setup(self):
        super().install_setup()
        # Avoid uvicorn swallowing signals
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, signal.default_int_handler)

_standalone_server: Optional[StandaloneServer] = None
_standalone_thread: Optional[threading.Thread] = None

def start_standalone_ui():
    global _standalone_server, _standalone_thread
    if _standalone_server and _standalone_server.started:
        return

    LOGGER.info("Starting Client API Server...")
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    _standalone_server = StandaloneServer(config)
    
    _standalone_thread = threading.Thread(target=_standalone_server.run, name="StandaloneUI", daemon=True)
    _standalone_thread.start()

def stop_standalone_ui():
    global _standalone_server
    if _standalone_server and _standalone_server.started:
        LOGGER.info("Stopping local Standalone UI...")
        _standalone_server.should_exit = True

# --- RUTAS ---
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    status = get_system_status()
    services = service_manager.get_services()
    
    # Determinar servicio activo (para el iframe)
    active_service = None
    for svc in services.values():
        if svc.get("running") and svc.get("web_port"):
            active_service = svc
            break
            
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "status": status,
        "services": services,
        "active_service": active_service
    })

@app.get("/api/status")
async def api_status():
    structure = STRUCTURE_MANAGER.get_structure()
    return {
        "system": get_system_status(),
        "services": service_manager.get_services(),
        "system_status": structure.get("system_status", {})
    }

@app.post("/api/services/{svc_id}/start")
async def start_service(svc_id: str):
    global suppress_network_listener, last_network_sig
    # Notify server we are doing things (Ritual Start)
    try:
        from net_com_handler import send_immediate_heartbeat, pause_reporting, resume_reporting, report_ready
        # Send one heartbeat with 'is_busy' true before pausing
        await asyncio.to_thread(send_immediate_heartbeat)
        pause_reporting()
    except Exception as e:
        LOGGER.error(f"Error notifying ritual start: {e}")

    try:
        # 1. Habilitar Servicio en Estructura (para que net_manager lo vea)
        STRUCTURE_MANAGER.update_service_state(svc_id, enabled=True)
        
        # 2. Sincronizar Metadatos (CRÍTICO: Hacerlo ANTES de configurar red)
        try:
            await asyncio.to_thread(STRUCTURE_MANAGER.sync_service_metadata, svc_id)
        except Exception as e:
            LOGGER.error(f"Fallo al sincronizar metadatos previos al inicio para {svc_id}: {e}")

        # Actualizar firma para evitar que el listener se dispare
        last_network_sig = get_network_signature(STRUCTURE_MANAGER.get_structure())

        # 3. Configurar Red
        try:
            from net_manager import update_nics
            await asyncio.to_thread(update_nics)
        except Exception as e:
            LOGGER.error(f"Fallo al actualizar red antes de iniciar servicio {svc_id}: {e}")
        
        # 3. Iniciar Proceso (ahora persiste 'running'=True)
        if await asyncio.to_thread(service_manager.start_service, svc_id):
            return {"status": "started", "id": svc_id}
        raise HTTPException(status_code=500, detail="Fallo al iniciar servicio")
    finally:
        suppress_network_listener = False
        
        # Ritual End: Resume heartbeats and report ready
        try:
            from net_com_handler import resume_reporting, report_ready
            resume_reporting()
            await asyncio.to_thread(report_ready)
        except Exception as e:
            LOGGER.error(f"Error notifying ritual end: {e}")

@app.post("/api/services/{svc_id}/stop")
async def stop_service(svc_id: str):
    global suppress_network_listener, last_network_sig
    # Notify server we are doing things (Ritual Start)
    try:
        from net_com_handler import send_immediate_heartbeat, pause_reporting, resume_reporting, report_ready
        # Send one heartbeat with 'is_busy' true before pausing
        await asyncio.to_thread(send_immediate_heartbeat)
        pause_reporting()
    except Exception as e:
        LOGGER.error(f"Error notifying ritual start: {e}")

    try:
        # Verificar si está en modo configuración ANTES de detenerlo
        is_config_mode = svc_id in service_manager.config_mode_services

        # 1. Detener Proceso (ahora persiste 'running'=False)
        if await asyncio.to_thread(service_manager.stop_service, svc_id):
            # 2. Actualizar Red (Limpieza)
            if not is_config_mode:
                last_network_sig = get_network_signature(STRUCTURE_MANAGER.get_structure())
                try:
                    from net_manager import update_nics
                    await asyncio.to_thread(update_nics)
                except Exception as e:
                    LOGGER.error(f"Fallo al actualizar red tras detener servicio {svc_id}: {e}")
                
            return {"status": "stopped", "id": svc_id}
        raise HTTPException(status_code=500, detail="Fallo al detener servicio")
    finally:
        suppress_network_listener = False
        
        # Ritual End: Resume heartbeats and report ready
        try:
            from net_com_handler import resume_reporting, report_ready
            resume_reporting()
            await asyncio.to_thread(report_ready)
        except Exception as e:
            LOGGER.error(f"Error notifying ritual end: {e}")

@app.post("/api/services/{svc_id}/reload_config")
async def reload_service_config(svc_id: str):
    """
    Detiene el servicio, sincroniza metadatos desde el archivo map, actualiza red y reinicia.
    Llamado por el servicio o la UI cuando cambia la configuración.
    """
    global suppress_network_listener, last_network_sig
    
    LOGGER.info(f"Recargando configuración para servicio: {svc_id}")
    
    # Establecer Estado Ocupado para Feedback en UI
    STRUCTURE_MANAGER.set_busy(f"RELOAD_{svc_id}", f"Configurando {svc_id}...")
    
    # Notify server we are doing things (Ritual Start)
    try:
        from net_com_handler import send_immediate_heartbeat, pause_reporting, resume_reporting, report_ready
        # Send one heartbeat with 'is_busy' true before pausing
        await asyncio.to_thread(send_immediate_heartbeat)
        pause_reporting()
    except Exception as e:
        LOGGER.error(f"Error notifying ritual start: {e}")

    suppress_network_listener = True
    try:
        # 1. Detener Servicio (Bloqueante)
        # Usamos asyncio.to_thread para evitar bloquear el bucle de eventos
        # Esperar un poco para que el servicio que solicitó el reload pueda terminar su request
        await asyncio.sleep(2.0)
        
        if not await asyncio.to_thread(service_manager.stop_service, svc_id):
            LOGGER.warning(f"No se pudo detener servicio {svc_id} (¿quizás no corría?), procediendo con sync...")
        
        # 2. Sincronizar Metadatos (Leer nuevo archivo map)
        try:
            await asyncio.to_thread(STRUCTURE_MANAGER.sync_service_metadata, svc_id)
            
            # Sincronizar con el servidor central tras recarga
            try:
                structure = STRUCTURE_MANAGER.get_structure()
                services = structure.get("services", [])
                target_svc = next((s for s in services if s.get("name") == svc_id), {})
                current_config = target_svc.get("configuration", "Default")
                config_path = service_manager._get_configs_dir(svc_id) / f"{current_config}.json"
                if config_path.exists():
                    with config_path.open("r", encoding="utf-8") as f:
                        config_data = json.load(f)
                    push_config_to_server(svc_id, current_config, config_data)
                    LOGGER.info(f"Configuración '{current_config}' sincronizada tras reload de {svc_id}")
            except Exception as e:
                LOGGER.warning(f"Fallo al sincronizar config tras reload: {e}")
        except Exception as e:
            LOGGER.error(f"Fallo al sincronizar metadatos para {svc_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Fallo al sincronizar metadatos: {e}")

        # 3. Habilitar Servicio en Estructura (Podría haber sido deshabilitado por stop_service)
        await asyncio.to_thread(STRUCTURE_MANAGER.update_service_state, svc_id, enabled=True)
        
        # Actualizar last_network_sig manualmente para que el listener no se dispare
        last_network_sig = get_network_signature(STRUCTURE_MANAGER.get_structure())

        # 4. Actualizar Red (VLANs/IPs) - LLAMADA EXPLÍCITA
        try:
            from net_manager import update_nics
            await asyncio.to_thread(update_nics)
        except Exception as e:
            LOGGER.error(f"Fallo al actualizar red para {svc_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Fallo al actualizar red: {e}")

        # Pequeña espera para asegurar que la UI capte el estado ocupado y la red se asiente
        await asyncio.sleep(2)

        # 5. Iniciar Servicio
        if await asyncio.to_thread(service_manager.start_service, svc_id):
            return {"status": "reloaded", "id": svc_id}
        
        raise HTTPException(status_code=500, detail="Fallo al reiniciar servicio")
    
    finally:
        suppress_network_listener = False
        STRUCTURE_MANAGER.clear_busy(f"RELOAD_{svc_id}")
        
        # Ritual End: Resume heartbeats and report ready
        try:
            from net_com_handler import resume_reporting, report_ready
            resume_reporting()
            await asyncio.to_thread(report_ready)
        except Exception as e:
            LOGGER.error(f"Error notifying ritual end: {e}")

@app.post("/api/services/{svc_id}/message")
async def service_message(svc_id: str, message: dict):
    """
    Endpoint para que los servicios envíen mensajes/actualizaciones de estado al cliente.
    Ejemplo payload: {"type": "status", "data": {"ready": true}}
    """
    LOGGER.info(f"Mensaje de servicio {svc_id}: {message}")
    return {"status": "received", "id": svc_id}

# --- ENDPOINTS GESTIÓN DE CONFIGURACIONES (MULTI-CONFIG) ---
@app.get("/api/services/{svc_id}/configs")
async def list_service_configs(svc_id: str):
    configs = service_manager.get_configs(svc_id)
    return {"configs": configs}

@app.post("/api/services/{svc_id}/config/select")
async def select_service_config(svc_id: str, payload: dict):
    config_name = payload.get("name")
    if not config_name:
        raise HTTPException(status_code=400, detail="Nombre de configuración requerido")
        
    if service_manager.select_config(svc_id, config_name):
        # Sincronizar metadatos inmediatamente después de cargar
        STRUCTURE_MANAGER.sync_service_metadata(svc_id)
        return {"status": "selected", "config": config_name}
    raise HTTPException(status_code=500, detail="Fallo al seleccionar configuración")

@app.post("/api/services/{svc_id}/config/duplicate")
async def duplicate_service_config(svc_id: str, payload: dict):
    src_name = payload.get("source")
    dst_name = payload.get("name")
    if not src_name or not dst_name:
         raise HTTPException(status_code=400, detail="Nombre de origen y destino requeridos")

    if service_manager.duplicate_config(svc_id, src_name, dst_name):
        return {"status": "duplicated", "config": dst_name}
    raise HTTPException(status_code=500, detail="Fallo al duplicar configuración")

@app.delete("/api/services/{svc_id}/config/{name}")
async def delete_service_config(svc_id: str, name: str):
    if name == "Default":
        raise HTTPException(status_code=400, detail="No se puede eliminar la configuración Default")
        
    if service_manager.delete_config(svc_id, name):
        return {"status": "deleted", "config": name}
    raise HTTPException(status_code=500, detail="Fallo al eliminar configuración")

@app.post("/api/services/{svc_id}/config/save")
async def save_service_config(svc_id: str, payload: dict):
    config_name = payload.get("name")
    if not config_name:
        raise HTTPException(status_code=400, detail="Nombre de configuración requerido")
        
    if service_manager.save_config_as(svc_id, config_name):
        # Sincronizar con el servidor central si está conectado
        try:
            config_path = service_manager._get_configs_dir(svc_id) / f"{config_name}.json"
            if config_path.exists():
                with config_path.open("r", encoding="utf-8") as f:
                    config_data = json.load(f)
                push_config_to_server(svc_id, config_name, config_data)
        except Exception as e:
            LOGGER.warning(f"Fallo al sincronizar config {config_name} con el servidor: {e}")
            
        return {"status": "saved", "config": config_name}
    raise HTTPException(status_code=500, detail="Fallo al guardar configuración")


@app.post("/api/services/{svc_id}/configure")
async def start_service_config_mode(svc_id: str):
    """Inicia el servicio en modo configuración (Offline)."""
    # Verificar que no haya otros servicios corriendo (el manager ya lo hace, pero bueno)
    if await asyncio.to_thread(service_manager.start_config_mode, svc_id):
        # Obtener el puerto para que el frontend pueda redirigir si es necesario
        svc_info = service_manager.get_services().get(svc_id, {})
        port = svc_info.get("web_port", 8000)
        return {"status": "config_mode_started", "id": svc_id, "web_port": port}
    raise HTTPException(status_code=500, detail="Fallo al iniciar modo configuración")

# --- ENDPOINTS VISOR DE LOGS ---
@app.get("/api/logs/list")
async def list_logs():
    """Lista archivos de log disponibles en logs/components y logs/services."""
    log_root = BASE_DIR / "logs"
    result = {"components": [], "services": []}
    
    # Componentes
    comp_dir = log_root / "components"
    if comp_dir.exists():
        result["components"] = [f.name for f in comp_dir.glob("*.log")]
        
    # Servicios
    svc_dir = log_root / "services"
    if svc_dir.exists():
        result["services"] = [f.name for f in svc_dir.glob("*.log")]
        
    return result

@app.get("/api/logs/read")
async def read_log(category: str, filename: str, lines: int = 200):
    """Lee las últimas N líneas de un archivo de log."""
    if category not in ["components", "services"]:
        raise HTTPException(status_code=400, detail="Categoría inválida")
        
    log_path = BASE_DIR / "logs" / category / filename
    
    # Chequeo de seguridad para evitar path traversal
    try:
        log_path = log_path.resolve()
        if not str(log_path).startswith(str((BASE_DIR / "logs").resolve())):
             raise HTTPException(status_code=403, detail="Acceso denegado")
    except Exception:
         raise HTTPException(status_code=403, detail="Acceso denegado")

    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Archivo de log no encontrado")
        
    try:
        from collections import deque
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            # deque(f, lines) lee el archivo y mantiene solo las últimas 'lines' líneas
            last_lines = list(deque(f, lines))
            return {"content": "".join(last_lines)}
    except Exception as e:
        LOGGER.error(f"Error leyendo log {filename}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/repo/branches")
async def list_repo_branches():
    """List available git branches in the client repository."""
    try:
        import subprocess
        # Get local and remote branches
        result = subprocess.run(
            ["git", "branch", "-a", "--format=%(refname:short)"],
            capture_output=True, text=True, check=True, cwd=str(BASE_DIR.parent)
        )
        branches = set()
        for line in result.stdout.splitlines():
            branch = line.strip()
            if branch.startswith("origin/"):
                branch = branch[7:]
            if branch and "HEAD" not in branch:
                branches.add(branch)
        return {"branches": sorted(list(branches))}
    except Exception as e:
        LOGGER.error(f"Error listing branches: {e}")
        raise HTTPException(status_code=500, detail="Failed to list branches")

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    structure = STRUCTURE_MANAGER.get_structure()
    network = structure.get("network", {}).get("desired", {})
            
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "network": network
    })

def graceful_cleanup():
    LOGGER.info("Realizando limpieza del sistema antes de apagado/reinicio...")
    
    # Notify server we are cleaning up (if not already notified)
    try:
        from net_com_handler import send_immediate_heartbeat
        send_immediate_heartbeat()
    except Exception as e:
        LOGGER.debug(f"Could not send final heartbeat: {e}")

    try:
        service_manager.stop_all(persist_state=True)
    except Exception as e:
        LOGGER.error(f"Error deteniendo servicios durante limpieza: {e}")
        
    try:
        ui.turn_ui_off()
    except Exception as e:
        LOGGER.error(f"Error apagando UI durante limpieza: {e}")

    try:
        close_comm_channel()
    except Exception as e:
        LOGGER.error(f"Error cerrando canal de comunicación durante limpieza: {e}")

@app.post("/api/system/cleanup")
async def api_system_cleanup():
    await asyncio.to_thread(graceful_cleanup)
    return {"status": "cleaned"}

@app.post("/api/system/{action}")
async def system_control(action: str):
    import subprocess
    
    if action == "reboot":
        STRUCTURE_MANAGER.set_busy("SYSTEM_REBOOT", "REBOOTING...")
        # Notify server we are doing things
        try:
            from net_com_handler import send_immediate_heartbeat
            await asyncio.to_thread(send_immediate_heartbeat)
        except:
            pass
        # Dar tiempo a la UI para actualizarse
        await asyncio.sleep(3)
        await asyncio.to_thread(graceful_cleanup)
        subprocess.run(["sudo", "reboot"])
        return {"status": "rebooting"}
        
    elif action == "shutdown":
        STRUCTURE_MANAGER.set_busy("SYSTEM_SHUTDOWN", "SHUTTING DOWN...")
        # Notify server we are doing things
        try:
            from net_com_handler import send_immediate_heartbeat
            await asyncio.to_thread(send_immediate_heartbeat)
        except:
            pass
        # Dar tiempo a la UI para actualizarse
        await asyncio.sleep(3)
        await asyncio.to_thread(graceful_cleanup)
        subprocess.run(["sudo", "shutdown", "now"])
        return {"status": "shutting_down"}
    elif action == "update":
        # Usually triggered via /api/system/update but included for consistency
        return {"status": "update_started"}
    raise HTTPException(status_code=400, detail="Acción inválida")

@app.post("/api/system/update")
async def system_update(payload: dict):
    branch = payload.get("branch", "main")
    STRUCTURE_MANAGER.set_busy("SYSTEM_UPDATE", f"UPDATING ({branch})...")
    
    # Notify server we are doing things
    try:
        from net_com_handler import send_immediate_heartbeat
        await asyncio.to_thread(send_immediate_heartbeat)
    except:
        pass
        
    # Dar tiempo a la UI para actualizarse
    await asyncio.sleep(3)
    
    # 1. Parada limpia
    await asyncio.to_thread(graceful_cleanup)
    
    # 2. Guardar info de branch si no existe (por si acaso no vino de net_com_handler)
    update_config = Path("/tmp/omi_update.json")
    if not update_config.exists():
        with update_config.open("w") as f:
            json.dump({"branch": branch}, f)
            
    # 3. Lanzar script de update totalmente independiente
    # Usamos setsid para que sea líder de su propia sesión de procesos
    update_script = BASE_DIR.parent / "scripts" / "update.sh"
    
    LOGGER.info(f"EJECUTANDO SCRIPT DE ACTUALIZACIÓN: {update_script}")
    
    # Abrir logs para el script de update
    log_dir = BASE_DIR / "logs" / "components"
    log_dir.mkdir(parents=True, exist_ok=True)
    update_log = open(log_dir / "update_script.log", "a")
    
    subprocess.Popen(
        [str(update_script)],
        stdout=update_log,
        stderr=update_log,
        start_new_session=True,
        cwd=str(BASE_DIR.parent)
    )
    
    # 4. Salir del programa actual
    # Damos un pequeñísimo margen para que el proceso hijo se desprenda
    # Pero el sys.exit debe ser pronto.
    threading.Timer(1.0, lambda: sys.exit(0)).start()
    
    return {"status": "updating", "branch": branch}

@app.post("/api/network/config")
async def network_config(config: dict):
    """Guarda la configuración de red y activa el gestor de red."""
    try:
        # Actualizamos manualmente usando bloqueo de archivo
        from file_lock import file_lock
        structure_path = BASE_DIR / "data" / "structure.json"
        lock_path = BASE_DIR / "data" / "structure.json.lock"
        
        with file_lock(lock_path):
            import json
            with structure_path.open("r", encoding="utf-8") as f:
                structure = json.load(f)
            
            network = structure.setdefault("network", {})
            desired = network.setdefault("desired", {})
            
            desired["mode"] = config.get("mode", "dhcp")
            desired["ip"] = config.get("ip")
            desired["mask"] = config.get("mask")
            desired["gateway"] = config.get("gateway")
            desired["vlan"] = config.get("vlan")
            desired["vlan_from_service"] = config.get("vlan_from_service", False)
            desired["vlan_mode"] = config.get("vlan_mode", "dhcp")
            desired["vlan_ip"] = config.get("vlan_ip")
            desired["vlan_mask"] = config.get("vlan_mask")
            desired["vlan_gateway"] = config.get("vlan_gateway")

            with structure_path.open("w", encoding="utf-8") as f:
                json.dump(structure, f, indent=2, ensure_ascii=False)
        
        # Forzar recarga en el gestor
        STRUCTURE_MANAGER.load_structure()
        
        LOGGER.info(f"Configuración de red actualizada: {config}")

        # Disparar net_manager.py
        try:
            from net_manager import update_nics
            update_nics()
        except Exception as e:
            LOGGER.error(f"Fallo al disparar net_manager: {e}")

        return {"status": "applied", "config": config}
    except Exception as e:
        LOGGER.error(f"Error actualizando configuración de red: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- HELPERS LISTENER DE RED ---
def get_network_signature(data):
    """Extrae una firma de la configuración relevante de red."""
    # Solo rastrear config 'desired', ignorar 'interfaces' y 'main_nic'
    net = data.get("network", {}).get("desired", {})
    
    # Extraer config de red de servicios
    services_sig = []
    for svc in data.get("services", []):
        services_sig.append({
            "name": svc.get("name"),
            "vlan": svc.get("vlan"),
            "ip_mode": svc.get("ip_mode"),
            "ip": svc.get("ip"),
            "mask": svc.get("mask"),
            "gateway": svc.get("gateway")
        })
    # Ordenar por nombre para consistencia
    services_sig.sort(key=lambda x: x["name"])
    
    return {
        "network": net,
        "services": services_sig
    }

# Estado global para el listener
last_network_sig = {}
suppress_network_listener = False

def on_structure_change(data):
    global last_network_sig, suppress_network_listener
    
    if suppress_network_listener:
        return

    try:
        current_sig = get_network_signature(data)
        
        # Solo proceder si la config de red cambió realmente
        if current_sig == last_network_sig:
            return

        last_network_sig = current_sig
        
        from net_manager import update_nics
        LOGGER.info("Configuración de red cambió en estructura (Global o Servicio), aplicando cambios...")
        
        if update_nics():
            LOGGER.warning("Configuración de red cambió. Reiniciando servicio activo...")
            active = STRUCTURE_MANAGER.get_active_service()
            if active:
                # Solo reiniciar si realmente está corriendo (tiene PID) para evitar bucles
                # Pero asumimos que si estamos aquí, la CONFIG cambió, así que es seguro reiniciar.
                service_manager.stop_service(active.get("name"))
                import time
                time.sleep(1)
                service_manager.start_service(active.get("name"))
    except Exception as e:
        LOGGER.error(f"Error manejando cambio de estructura: {e}")

# Inicializar estado del listener
try:
    last_network_sig = get_network_signature(STRUCTURE_MANAGER.get_structure())
except:
    pass

STRUCTURE_MANAGER.add_listener(on_structure_change)

# --- PUNTO DE ENTRADA PRINCIPAL ---
def restore_active_service():
    """Restaura el servicio activo desde structure.json."""
    try:
        svc = STRUCTURE_MANAGER.get_active_service()
        if svc:
            svc_id = svc.get("id") or svc.get("name")
            if svc_id:
                LOGGER.info(f"Restaurando servicio activo: {svc_id}")
                # La red ya se configuró en el paso de arranque 2.5
                service_manager.start_service(svc_id)
    except Exception as e:
        LOGGER.error(f"Fallo al restaurar estado de servicio activo: {e}")

def main():
    LOGGER.info("Iniciando Secuencia de Arranque OMI Client...")
    
    # 0. Inicializar Structure JSON (Manejado por instanciación del Manager)
    
    # 0.1 Iniciar Servidor API Local (Fundamental para comandos internos)
    # Lo iniciamos una vez y se mantiene activo siempre.
    start_standalone_ui()
    
    # 0.2 Iniciar Heartbeat
    start_heartbeat()
    
    # 1. Init UI (Explicit loading screens)
    ui.show_loading_ui(10, "INICIANDO...")
    
    # 1.5 Forzar Actualización de Interfaces de Red (Poblar structure.json)
    try:
        from heartbeat import force_update_interfaces
        force_update_interfaces()
    except Exception as e:
        LOGGER.error(f"Fallo al forzar actualización de interfaces: {e}")
    
    # 2. Handshake (Bloqueante)
    ui.show_loading_ui(30, "CONECTANDO...")
    connected = handshake()
    
    if connected:
        ui.show_loading_ui(100, "CONECTADO")
        LOGGER.info("Modo: CONECTADO (Servidor encontrado)")
    else:
        ui.show_loading_ui(100, "STANDALONE")
        LOGGER.info("Modo: STANDALONE (Sin servidor)")
    
    # 2.5 Actualizar Configuración de Red (Lógica Inteligente)
    # Asegura que las VLANs se apliquen antes de iniciar servicios
    try:
        from net_manager import update_nics
        update_nics()
    except Exception as e:
        LOGGER.error(f"Fallo al aplicar config de red durante arranque: {e}")

    # 3. Restaurar Servicio (Aplica a ambos modos)
    restore_active_service()
    
    # 4. Iniciar UI y Bucle Principal
    ui.start_standard_ui()
    
    # Manejo de Señales
    stop_event = threading.Event()
    
    def handle_signal(signum, frame):
        LOGGER.info(f"Recibida señal {signum}, iniciando apagado...")
        stop_event.set()
        
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        last_check = 0
        is_server_alive = connected
        
        while not stop_event.is_set():
            now = time.time()
            
            # Connectivity check (every 2 seconds)
            if now - last_check > 2.0:
                from net_com_handler import get_last_contact_time # Importar aquí para evitar circular
                last_contact = get_last_contact_time()
                current_server_alive = (now - last_contact < 10.0)
                
                if not current_server_alive and is_server_alive:
                    LOGGER.warning("Servidor perdido (> 10s).")
                    is_server_alive = False
                elif current_server_alive and not is_server_alive:
                    LOGGER.info("Servidor recuperado.")
                    is_server_alive = True
                
                last_check = now
            
            time.sleep(1)
            
    except KeyboardInterrupt:
        LOGGER.info("Interrumpido por usuario (KeyboardInterrupt)")
    except Exception as e:
        LOGGER.error(f"Error inesperado en bucle principal: {e}", exc_info=True)
    finally:
        graceful_cleanup()
        
        # Restaurar terminal (fix para freezing)
        try:
            subprocess.run(["stty", "sane"], stderr=subprocess.DEVNULL)
        except Exception:
            pass

        LOGGER.info("Apagado completo.")

if __name__ == "__main__":
    main()
