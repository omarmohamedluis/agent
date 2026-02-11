#!/usr/bin/env python3
import os
import sys
import json
import time
import shutil
import signal
import subprocess
import threading
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

# Logging unificado para el Wrapper y Satellite (filtrado)
def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [WRAPPER] {msg}"
    print(line, flush=True) # Console for systemd/supervisor
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

def setup_fnm():
    """Instala FNM localmente y configura Node."""
    fnm_dir = SERVICE_DIR / "bin"
    fnm_exe = fnm_dir / "fnm"
    
    if not fnm_exe.exists():
        log("Instalando FNM localmente...")
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
            log(f"Error instalando FNM: {e}")
            raise Exception(f"No se pudo instalar FNM: {e}")
            
    # Ensure repo is cloned
    if not (CODE_DIR / "package.json").exists():
        log("Satellite no encontrado o incompleto. Clonando repositorio...")
        if CODE_DIR.exists():
            log("Limpiando directorio incompleto...")
            shutil.rmtree(CODE_DIR)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "https://github.com/bitfocus/companion-satellite.git", str(CODE_DIR)],
                check=True, capture_output=True
            )
        except subprocess.CalledProcessError as e:
            log(f"Error clonando repositorio: {e}")
            raise Exception(f"No se pudo clonar el repositorio: {e}")

    log("Configurando Node v24 via FNM...")
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
        log(f"Node listo: {node_bin}")
        return node_bin, env
    except Exception as e:
        log(f"Error en configuración de entorno: {e}")
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
        log(f"Error guardando estado: {e}")

def install_satellite_thread(node_bin, env):
    """Proceso de instalación/compilación en segundo plano."""
    try:
        update_status("installing", "Verificando herramientas...", 10)
        bin_dir = Path(node_bin).parent
        corepack_bin = bin_dir / "corepack"
        yarn_bin = bin_dir / "yarn"

        update_status("installing", "Habilitando Yarn (Corepack)...", 30)
        if corepack_bin.exists():
            try:
                subprocess.run([str(corepack_bin), "enable"], cwd=CODE_DIR, env=env, check=True, capture_output=True)
            except: pass

        if not (CODE_DIR / "node_modules").exists():
            update_status("installing", "Descargando dependencias...", 40)
            cmd = [str(yarn_bin), "install"] if yarn_bin.exists() else [str(corepack_bin), "yarn", "install"]
            subprocess.run(cmd, cwd=CODE_DIR, env=env, check=True, capture_output=True)

        main_js = CODE_DIR / "satellite" / "dist" / "main.js"
        if not main_js.exists():
            update_status("installing", "Compilando Satellite...", 70)
            cmd = [str(yarn_bin), "build"] if yarn_bin.exists() else [str(corepack_bin), "yarn", "build"]
            subprocess.run(cmd, cwd=CODE_DIR, env=env, check=True, capture_output=True)
        
        update_status("starting", "¡Casi listo!", 100)
        log("Instalación/Compilación finalizada satisfactoriamente.")
    except Exception as e:
        log(f"Fallo en hilo de instalación: {e}")
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
        log(f"Creando preset por defecto: {active}")
        default_conf = {
            "remoteIp": "127.0.0.1", "remotePort": 16622, "restPort": 9999,
            "surfacePluginsEnabled": {"elgato-streamdeck": True, "loupedeck": True, "infinitton": True},
            "net": {
                "vlan": 60, "vlan_active": False, "ip_mode": "dhcp",
                "ip": "", "mask": "", "gateway": ""
            }
        }
        with open(source, 'w') as f:
            json.dump(default_conf, f, indent=4)
            
    log(f"Usando preset: {active}")
    shutil.copy(source, RUNTIME_CONFIG)
    return active

def run_satellite(node_bin, env):
    """Lanza Satellite redirigiendo stdout/stderr al log principal."""
    main_js = CODE_DIR / "satellite" / "dist" / "main.js"
    cmd = [node_bin, str(main_js), str(RUNTIME_CONFIG)]
    log(f"Lanzando Satellite (Config: {get_active_config_name()})")
    
    # Redirigir al log principal de OMI
    out = open(LOG_FILE, "a")
    # Usar os.setsid para poder matar el grupo de procesos
    proc = subprocess.Popen(cmd, cwd=CODE_DIR, env=env, stdout=out, stderr=subprocess.STDOUT, preexec_fn=os.setsid)
    return proc

def run_web_wrapper():
    """Lanza el Wrapper. Redirige a un log local para evitar ruido en OMI log."""
    # NOTA: Uvicorn es muy ruidoso con el polling. Mandamos su salida a un archivo separado
    # o lo silenciamos. Aquí usaremos un log interno del wrapper.
    wrapper_log = SERVICE_DIR / "web_wrapper.log"
    cmd = [sys.executable, "-m", "uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "9002", "--no-access-log"]
    log("Lanzando Servidor Web Wrapper (Puerto 9002)...")
    
    out = open(wrapper_log, "a")
    proc = subprocess.Popen(cmd, cwd=SERVICE_DIR, stdout=out, stderr=subprocess.STDOUT, preexec_fn=os.setsid)
    return proc

def main():
    log("=== Iniciando Servicio Bitfocus Satellite Wrapper ===")
    
    # Asegurar configuración desde el inicio (para sincro de Agent)
    setup_runtime_config()
    
    # --- MODO CONFIGURACIÓN ---
    config_mode = os.environ.get("OMI_CONFIG_MODE") == "1"
    if config_mode:
        log("⚠️ INICIANDO EN MODO CONFIGURACIÓN (Offline) ⚠️")
        update_status("starting", "Modo Configuración Activo", 100)

    # Limpieza inicial
    if STATE_FILE.exists():
        try: os.unlink(STATE_FILE)
        except: pass

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
                log(f"Error cargando entorno: {e}")
                update_status("error", f"Error de entorno: {e}")
        else:
            log("Satellite no instalado. Esperando acción del usuario en Web UI.")
            update_status("not_installed", "Requiere instalación manual.")
    else:
        # En modo config, el wrapper debe saber que está "listo"
        installed = True

    sat_proc = None
    
    def handle_stop(signum, frame):
        log("Cierre solicitado. Limpiando procesos...")
        if sat_proc:
            try: os.killpg(os.getpgid(sat_proc.pid), signal.SIGTERM)
            except: pass
        if web_proc:
            try: os.killpg(os.getpgid(web_proc.pid), signal.SIGTERM)
            except: pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    
    while True:
        try:
            # A. Instalacion Manual (Solo si NO es modo config)
            if not config_mode and not installed and INSTALL_FLAG.exists():
                log("Trigger de instalación recibido.")
                INSTALL_FLAG.unlink()
                try:
                    update_status("installing", "Iniciando descarga...", 5)
                    node_bin, env = setup_fnm()
                    threading.Thread(target=install_satellite_thread, args=(node_bin, env)).start()
                except Exception as e:
                    update_status("error", f"Fallo al iniciar setup: {e}")

            # B. Detectar fin de instalacion
            if not config_mode and not installed:
                try:
                    if (CODE_DIR / "satellite" / "dist" / "main.js").exists():
                        node_bin, env = setup_fnm()
                        setup_runtime_config()
                        installed = True
                        log("Instalación completada y detectada.")
                except: pass

            # C. Gestionar Proceso Satellite (Solo si NO es modo config e instalado)
            if not config_mode and installed:
                if RESTART_FLAG.exists():
                    log("[WRAPPER] Petición de reinicio de Satellite (Cambio de config).")
                    RESTART_FLAG.unlink()
                    if sat_proc:
                        try: os.killpg(os.getpgid(sat_proc.pid), signal.SIGTERM)
                        except: pass
                        sat_proc.wait(timeout=2)
                    sat_proc = None

                if sat_proc is None or sat_proc.poll() is not None:
                    if sat_proc is not None:
                        log(f"[WRAPPER] Satellite se detuvo (code: {sat_proc.returncode}). Reiniciando...")
                        time.sleep(3)
                    
                    setup_runtime_config() # Asegurar que tenemos la última config antes de lanzar
                    try:
                        sat_proc = run_satellite(node_bin, env)
                        update_status("running", "Activo")
                    except Exception as e:
                        log(f"Error lanzando Satellite: {e}")
                        time.sleep(5)
            
            # D. Verificar Web Wrapper
            if web_proc and web_proc.poll() is not None:
                log("[WRAPPER] Web Wrapper se cerró inesperadamente. Reiniciando...")
                web_proc = run_web_wrapper()
                
        except Exception as e:
            log(f"Error en loop de servicio: {e}")
            
        time.sleep(1)

if __name__ == "__main__":
    main()
