# AppHandler.py
from __future__ import annotations
import sys
import time
import subprocess
from pathlib import Path
from typing import Optional

from jsonconfig import STANDBY_SERVICE, discover_services

BASE_SERVICES_DIR = (Path(__file__).resolve().parent / "servicios").resolve()
ENTRYPOINT = "service.py"

_proc: Optional[subprocess.Popen] = None
_name: Optional[str] = None
_logical: Optional[str] = STANDBY_SERVICE


def _service_path(name: str) -> Path:
    return (BASE_SERVICES_DIR / name / ENTRYPOINT).resolve()


def _is_running() -> bool:
    return _proc is not None and _proc.poll() is None


def list_available_services() -> list[str]:
    return [name for name in discover_services() if name != STANDBY_SERVICE]


def get_active_service(logical: bool = False) -> Optional[str]:
    if logical:
        return _logical
    return _name if _is_running() else None


def start_service(name: str) -> bool:
    global _proc, _name, _logical

    if name == STANDBY_SERVICE:
        if _is_running():
            stop_service()
        _logical = STANDBY_SERVICE
        return True

    script = _service_path(name)
    if not script.exists():
        print(f"[AppHandler] No existe: {script}")
        return False

    if _is_running() and _name == name:
        print(f"[AppHandler] '{name}' ya está en ejecución (pid={_proc.pid})")
        _logical = name
        return True

    if _is_running():
        stop_service()

    try:
        args = [sys.executable, str(script)]
        print(f"[AppHandler] Lanzando: {args}")
        _proc = subprocess.Popen(args)
        _name = name
        _logical = name
        print(f"[AppHandler] Servicio '{name}' iniciado (pid={_proc.pid})")
        return True
    except Exception as e:
        _proc = None
        _name = None
        _logical = STANDBY_SERVICE
        print(f"[AppHandler] Error iniciando '{name}': {e}")
        return False


def stop_service(timeout: float = 5.0) -> bool:
    global _proc, _name, _logical
    if not _is_running():
        _proc = None
        _name = None
        _logical = STANDBY_SERVICE
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
        _logical = STANDBY_SERVICE
        return True
    except Exception as e:
        print(f"[AppHandler] Error al detener '{_name}': {e}")
        _proc = None
        _name = None
        _logical = STANDBY_SERVICE
        return False


def current_logical_service() -> Optional[str]:
    return _logical
