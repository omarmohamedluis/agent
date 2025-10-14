# AppHandler.py
from __future__ import annotations
import sys, time, subprocess
from pathlib import Path
from typing import Optional

# Carpeta de servicios ABSOLUTA (junto a este archivo)
BASE_SERVICES_DIR = (Path(__file__).resolve().parent / "servicios").resolve()
ENTRYPOINT = "service.py"

_proc: Optional[subprocess.Popen] = None
_name: Optional[str] = None

def _service_path(name: str) -> Path:
    # /abs/.../servicios/<name>/service.py
    return (BASE_SERVICES_DIR / name / ENTRYPOINT).resolve()

def _is_running() -> bool:
    return _proc is not None and _proc.poll() is None

def get_active_service() -> Optional[str]:
    return _name if _is_running() else None

def start_service(name: str) -> bool:
    """Arranca servicios/<name>/service.py (ruta ABSOLUTA, sin cambiar cwd)."""
    global _proc, _name

    script = _service_path(name)
    if not script.exists():
        print(f"[AppHandler] No existe: {script}")
        return False

    if _is_running() and _name == name:
        print(f"[AppHandler] '{name}' ya está en ejecución (pid={_proc.pid})")
        return True

    if _is_running():
        stop_service()

    try:
        # ✅ Ruta ABSOLUTA; no se pasa cwd
        args = [sys.executable, str(script)]
        print(f"[AppHandler] Lanzando: {args}")
        _proc = subprocess.Popen(args)  # stdout/stderr heredan del padre
        _name = name
        print(f"[AppHandler] Servicio '{name}' iniciado (pid={_proc.pid})")
        return True
    except Exception as e:
        _proc = None
        _name = None
        print(f"[AppHandler] Error iniciando '{name}': {e}")
        return False

def stop_service(timeout: float = 5.0) -> bool:
    """Termina el servicio activo (terminate → espera → kill si hace falta)."""
    global _proc, _name
    if not _is_running():
        _proc = None
        _name = None
        print("[AppHandler] No hay servicio en ejecución.")
        return True

    try:
        _proc.terminate()
        t0 = time.time()
        while time.time() - t0 < timeout:
            if _proc.poll() is not None:
                break
            time.sleep(0.1)
        if _proc.poll() is None:
            _proc.kill()
        rc = _proc.poll()
        print(f"[AppHandler] '{_name}' detenido. rc={rc}")
        _proc = None
        _name = None
        return True
    except Exception as e:
        print(f"[AppHandler] Error al detener '{_name}': {e}")
        _proc = None
        _name = None
        return False
