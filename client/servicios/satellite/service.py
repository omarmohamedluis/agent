#!/usr/bin/env python3
import os
import socket # Added for hostname resolution

import sys
import json
import time
import shutil
import signal
import subprocess
import threading
import re
from pathlib import Path

# Configuración
SERVICE_DIR = Path(__file__).resolve().parent
CODE_DIR = SERVICE_DIR / "satellite_code"
CONFIGS_DIR = SERVICE_DIR / "configs"
RUNTIME_CONFIG = SERVICE_DIR / "runtime_config.json"
ACTIVE_CONFIG_FILE = SERVICE_DIR / "active_config.txt"

# Determinar log file desde el entorno OMI o local
OMI_LOG_PATH = os.environ.get("OMI_LOG_PATH")
if OMI_LOG_PATH:
    LOG_FILE = Path(OMI_LOG_PATH)
else:
    LOG_FILE = SERVICE_DIR / "service.log"

# Asegurar directorios
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

# ANSI escape codes regex
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def write_log(component: str, level: str, msg: str):
    """Escribe en el log con el formato estándar de OMI."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    clean_msg = ANSI_ESCAPE.sub('', msg).strip()
    if not clean_msg:
        return
        
    line = f"{timestamp} [{level}] {component}: {clean_msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

def log_wrapper(msg, level="INFO"):
    write_log("omisatellite.wrapper", level, msg)

def log_core(msg, level="INFO"):
    write_log("omisatellite.core", level, msg)
    
def log_web(msg, level="INFO"):
    write_log("omisatellite.web", level, msg)

def stream_reader(pipe, log_func, prefix=""):
    """Lee de un pipe línea a línea y lo manda al log_func."""
    # Regex for inner timestamp like [2026-02-11 12:09:47]
    # Note: Satellite logs use [YYYY-MM-DD HH:MM:SS] at the start
    inner_timestamp_re = re.compile(r'^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\s*')

    # Known benign errors to suppress
    ignore_patterns = [
        "Failed to fill device TypeError: device has been closed",
        "at HIDAsync.<computed>",
        "at NodeHIDDevice.sendReports",
        "at DefaultButtonsLcdService.fillPanelBuffer",
        "at async StreamDeckBase.fillPanelBuffer",
        "at async Promise.all",
        "at async StreamDeckWrapper.showStatus",
        "at async Object.fn"
    ]

    try:
        with pipe:
            for line in iter(pipe.readline, b''):
                decoded = line.decode('utf-8', errors='replace').strip()
                if not decoded:
                    continue
                
                # Check for suppression
                if any(pattern in decoded for pattern in ignore_patterns):
                    continue

                # Strip inner timestamp
                clean_content = inner_timestamp_re.sub('', decoded)

                # Detectar nivel básico
                level = "INFO"
                if "error" in clean_content.lower(): 
                   level = "ERROR"
                elif "warn" in clean_content.lower(): 
                   level = "WARNING"
                   
                log_func(clean_content, level)
    except Exception as e:
        log_wrapper(f"Error leyendo stream: {e}", "ERROR")

def setup_fnm():
    """Instala FNM localmente y configura Node."""
    fnm_dir = SERVICE_DIR / "bin"
    fnm_exe = fnm_dir / "fnm"
    
    if not fnm_exe.exists():
        log_wrapper("Instalando FNM localmente...")
        import urllib.request
        install_script_path = SERVICE_DIR / "install_fnm.sh"
        try:
            url = "https://fnm.vercel.app/install"
            with urllib.request.urlopen(url) as response, open(install_script_path, 'wb') as out_file:
                shutil.copyfileobj(response, out_file)
                
            subprocess.run(
                ["bash", str(install_script_path), "--install-dir", str(fnm_dir), "--skip-shell"],
                check=True, capture_output=True
            )
            if install_script_path.exists(): install_script_path.unlink()
        except Exception as e:
            log_wrapper(f"Error instalando FNM: {e}", "ERROR")
            raise Exception(f"No se pudo instalar FNM: {e}")
            
    # Ensure repo is cloned/initialized
    if not (CODE_DIR / "package.json").exists():
        log_wrapper("Satellite no encontrado o incompleto. Intentando inicializar submódulo...")
        try:
            # Intentar inicializar si es un submódulo
            subprocess.run(
                ["git", "submodule", "update", "--init", "--recursive", str(CODE_DIR)],
                cwd=SERVICE_DIR.parent.parent.parent, # Root del repositorio (agent/)
                check=True, capture_output=True
            )
        except Exception as e:
            log_wrapper(f"Fallo al inicializar submódulo: {e}. Cayendo a clonado manual...", "WARNING")

    if not (CODE_DIR / "package.json").exists():
        log_wrapper("Aún no se encuentra Satellite. Clonando repositorio manualmente...")
        if CODE_DIR.exists():
            log_wrapper("Limpiando directorio incompleto...")
            shutil.rmtree(CODE_DIR)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "https://github.com/bitfocus/companion-satellite.git", str(CODE_DIR)],
                check=True, capture_output=True
            )
        except subprocess.CalledProcessError as e:
            log_wrapper(f"Error clonando repositorio: {e}", "ERROR")
            raise Exception(f"No se pudo clonar el repositorio: {e}")

    log_wrapper("Configurando Node v24 via FNM...")
    env = os.environ.copy()
    env["FNM_DIR"] = str(SERVICE_DIR / "fnm_data")
    env["PATH"] = f"{SERVICE_DIR / 'bin'}:{env.get('PATH', '')}"
    env["COREPACK_ENABLE_DOWNLOAD_PROMPT"] = "0"
    
    try:
        subprocess.run([str(fnm_exe), "install"], cwd=CODE_DIR, env=env, check=True, capture_output=True)
        node_bin = subprocess.check_output(
            [str(fnm_exe), "exec", "which", "node"], 
            cwd=CODE_DIR, env=env
        ).decode().strip()
        
        node_dir = str(Path(node_bin).parent)
        env["PATH"] = f"{node_dir}:{env['PATH']}"
        log_wrapper(f"Node listo: {node_bin}")
        return node_bin, env
    except Exception as e:
        log_wrapper(f"Error en configuración de entorno: {e}", "ERROR")
        raise Exception(f"Error configurando entorno de Node: {e}")

# State File
STATE_FILE = SERVICE_DIR / "service_state.json"

def update_status(state, message=None, progress=None):
    """Actualiza el estado para la Web UI."""
    data = {
        "state": state,
        "message": message,
        "progress": progress,
        "timestamp": time.time()
    }
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        log_wrapper(f"Error guardando estado: {e}", "ERROR")

def install_satellite_thread(node_bin, env, is_update=False, config_mode=False):
    """Proceso de instalación/compilación en segundo plano."""
    try:
        update_status("installing", "Verificando herramientas...", 10)
        bin_dir = Path(node_bin).parent
        corepack_bin = bin_dir / "corepack"
        yarn_bin = bin_dir / "yarn"

        if is_update:
             update_status("installing", "Actualizando código fuente...", 20)
             try:
                 subprocess.run(["git", "pull"], cwd=CODE_DIR, check=True, capture_output=True)
             except Exception as e:
                 log_wrapper(f"Error en git pull: {e}", "WARNING")

        update_status("installing", "Habilitando Yarn (Corepack)...", 30)
        if corepack_bin.exists():
            try:
                subprocess.run([str(corepack_bin), "enable"], cwd=CODE_DIR, env=env, check=True, capture_output=True)
            except: pass

        # Always install if update or missing
        if is_update or not (CODE_DIR / "node_modules").exists():
            update_status("installing", "Actualizando dependencias...", 40)
            cmd = [str(yarn_bin), "install"] if yarn_bin.exists() else [str(corepack_bin), "yarn", "install"]
            subprocess.run(cmd, cwd=CODE_DIR, env=env, check=True, capture_output=True)

        # Always build if update or missing
        main_js = CODE_DIR / "satellite" / "dist" / "main.js"
        if is_update or not main_js.exists():
            update_status("installing", "Compilando Satellite...", 70)
            cmd = [str(yarn_bin), "build"] if yarn_bin.exists() else [str(corepack_bin), "yarn", "build"]
            subprocess.run(cmd, cwd=CODE_DIR, env=env, check=True, capture_output=True)
        
        if config_mode:
            update_status("config", "Instalación/Actualización completada.", 100)
        else:
            update_status("starting", "¡Casi listo!", 100)
        log_wrapper("Instalación/Actualización finalizada satisfactoriamente.")
    except Exception as e:
        log_wrapper(f"Fallo en hilo de instalación: {e}", "ERROR")
        update_status("error", f"Error de instalación: {str(e)}")

def get_active_config_name():
    if ACTIVE_CONFIG_FILE.exists():
        return ACTIVE_CONFIG_FILE.read_text().strip()
    return "default"

def setup_runtime_config():
    """Copia el preset seleccionado a runtime_config.json."""
    if not ACTIVE_CONFIG_FILE.exists():
        ACTIVE_CONFIG_FILE.write_text("default", encoding="utf-8")
        
    active = get_active_config_name()
    source = CONFIGS_DIR / f"{active}.json"
    
    if not source.exists():
        log_wrapper(f"Creando preset por defecto: {active}")
        default_conf = {
            "file_info": {
                "name": active,
                "ui_port": 9002,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
            },
            "remoteIp": "127.0.0.1", "remotePort": 16622, "restPort": 9999,
            "surfacePluginsEnabled": {"elgato-streamdeck": True, "loupedeck": True, "infinitton": True},
            "net": {
                "vlan": 60, "vlan_active": False, "ip_mode": "dhcp",
                "ip": "", "mask": "", "gateway": ""
            }
        }
        with open(source, 'w') as f:
            json.dump(default_conf, f, indent=4)
            
    log_wrapper(f"Usando preset: {active}")
    shutil.copy(source, RUNTIME_CONFIG)
    return active

def run_satellite(node_bin, env):
    """Lanza Satellite procesando y limpiando logs."""
    main_js = CODE_DIR / "satellite" / "dist" / "main.js"
    cmd = [node_bin, str(main_js), str(RUNTIME_CONFIG)]
    log_wrapper(f"Lanzando Satellite (Config: {get_active_config_name()})")
    
    # Pipe stdout/stderr to capture and clean
    # setsid to allow killing the process group later
    proc = subprocess.Popen(
        cmd, 
        cwd=CODE_DIR, 
        env=env, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.STDOUT, 
        preexec_fn=os.setsid
    )
    
    # Start reader thread
    t = threading.Thread(target=stream_reader, args=(proc.stdout, log_core))
    t.daemon = True
    t.start()
    
    return proc

def run_web_wrapper():
    """Lanza el Wrapper. Redirige a logs."""
    # NOTA: Uvicorn es muy ruidoso, usamos --no-access-log
    host = "0.0.0.0"
    port = 9002
    cmd = [sys.executable, "-m", "uvicorn", "web.app:app", "--host", host, "--port", str(port), "--no-access-log"]
    
    display_host = host
    if host == "0.0.0.0":
        try:
            display_host = socket.gethostname() or "localhost"
        except:
            display_host = "localhost"

    log_wrapper(f"🌐 WebUI en http://{display_host}:{port}")
    
    proc = subprocess.Popen(
        cmd, 
        cwd=SERVICE_DIR, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.STDOUT, 
        preexec_fn=os.setsid
    )
    
    t = threading.Thread(target=stream_reader, args=(proc.stdout, log_web))
    t.daemon = True
    t.start()
    
    return proc

def main():
    log_wrapper("=== Iniciando Servicio Bitfocus Satellite Wrapper ===")
    
    # Asegurar configuración desde el inicio (para sincro de Agent)
    setup_runtime_config()
    
    # Limpieza inicial
    if STATE_FILE.exists():
        try: os.unlink(STATE_FILE)
        except: pass

    # --- MODO CONFIGURACIÓN ---
    config_mode = os.environ.get("OMI_CONFIG_MODE") == "1"
    if config_mode:
        log_wrapper("⚠️ INICIANDO EN MODO CONFIGURACIÓN (Offline) ⚠️")
        update_status("config", "Modo Configuración Activo", 100)

    # 1. Iniciar Web Wrapper pronto para feedback
    web_proc = run_web_wrapper()
    
    INSTALL_FLAG = SERVICE_DIR / "install.flag"
    RESTART_FLAG = SERVICE_DIR / "restart_satellite.flag"
    
    installed = False
    node_bin = None
    env = None
    
    # Si no es modo config, verificar instalación
    if not config_mode:
        main_js = CODE_DIR / "satellite" / "dist" / "main.js"
        if main_js.exists():
            try:
                update_status("starting", "Verificando entorno...", 10)
                node_bin, env = setup_fnm()
                installed = True
                update_status("starting", "Preparado.", 100)
            except Exception as e:
                log_wrapper(f"Error cargando entorno: {e}", "ERROR")
                update_status("error", f"Error de entorno: {e}")
        else:
            log_wrapper("Satellite no instalado. Esperando acción del usuario en Web UI.", "WARNING")
            update_status("not_installed", "Requiere instalación manual.")
    else:
        # En modo config, el wrapper debe saber que está "listo"
        installed = True

    sat_proc = None
    
    def handle_stop(signum, frame):
        log_wrapper("Cierre solicitado. Limpiando procesos...")
        if sat_proc:
            try: os.killpg(os.getpgid(sat_proc.pid), signal.SIGTERM)
            except: pass
        if web_proc:
            try: os.killpg(os.getpgid(web_proc.pid), signal.SIGTERM)
            except: pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    
    loop_count = 0
    while True:
        loop_count += 1
        try:
            # A. Instalacion Manual / Actualización (Permitido en ambos modos)
            if (INSTALL_FLAG.exists() or (SERVICE_DIR / "update.flag").exists()):
                is_update = (SERVICE_DIR / "update.flag").exists()
                action_name = "Actualización" if is_update else "Instalación"
                
                log_wrapper(f"Trigger de {action_name} recibido.")
                
                if INSTALL_FLAG.exists(): INSTALL_FLAG.unlink()
                if (SERVICE_DIR / "update.flag").exists(): (SERVICE_DIR / "update.flag").unlink()
                
                try:
                    update_status("installing", f"Iniciando {action_name}...", 5)
                    node_bin, env = setup_fnm()
                    threading.Thread(target=install_satellite_thread, args=(node_bin, env, is_update, config_mode)).start()
                except Exception as e:
                    update_status("error", f"Fallo al iniciar setup: {e}")

            # B. Detectar fin de instalacion
            if not config_mode and not installed:
                try:
                    if (CODE_DIR / "satellite" / "dist" / "main.js").exists():
                        node_bin, env = setup_fnm()
                        setup_runtime_config()
                        installed = True
                        log_wrapper("Instalación completada y detectada.")
                except: pass

            # C. Gestionar Proceso Satellite (Solo si NO es modo config e instalado)
            if not config_mode and installed:
                if RESTART_FLAG.exists():
                    log_wrapper("Petición de reinicio de Satellite (Cambio de config).")
                    RESTART_FLAG.unlink()
                    if sat_proc:
                        try: os.killpg(os.getpgid(sat_proc.pid), signal.SIGTERM)
                        except: pass
                        sat_proc.wait(timeout=2)
                    sat_proc = None

                if sat_proc is None or sat_proc.poll() is not None:
                    if sat_proc is not None:
                        log_wrapper(f"Satellite se detuvo (code: {sat_proc.returncode}). Reiniciando...", "WARNING")
                        time.sleep(3)
                    
                    setup_runtime_config() # Asegurar que tenemos la última config antes de lanzar
                    try:
                        sat_proc = run_satellite(node_bin, env)
                        update_status("running", "Activo")
                    except Exception as e:
                        log_wrapper(f"Error lanzando Satellite: {e}", "ERROR")
                        time.sleep(5)
            
            # D. Verificar Web Wrapper
            if web_proc and web_proc.poll() is not None:
                log_wrapper("Web Wrapper se cerró inesperadamente. Reiniciando...", "WARNING")
                web_proc = run_web_wrapper()
                
        except Exception as e:
            log_wrapper(f"Error en loop de servicio: {e}", "ERROR")
            
        time.sleep(1)

if __name__ == "__main__":
    main()
