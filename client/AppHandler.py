# AppHandler.py
from __future__ import annotations
import logging
import os
import sys
import time
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, TextIO

from jsonconfig import (
    STANDBY_SERVICE,
    discover_services,
    get_service_definition,
)
from logger import default_service_log_paths, get_agent_logger, get_service_logger, resolve_log_path

BASE_SERVICES_DIR = (Path(__file__).resolve().parent / "servicios").resolve()
ENTRYPOINT = "service.py"

_proc: Optional[subprocess.Popen] = None
_name: Optional[str] = None
_logical: Optional[str] = STANDBY_SERVICE
_stdout_thread: Optional[threading.Thread] = None
_stderr_thread: Optional[threading.Thread] = None
_stdout_stream: Optional[TextIO] = None
_stderr_stream: Optional[TextIO] = None
_last_error: Optional[str] = None
_last_command: list[str] | None = None
_last_env: Dict[str, str] | None = None
_last_cwd: Optional[Path] = None
_last_returncode: Optional[int] = None
_runtime_env: Dict[str, str] = {}
_spool_stop: Optional[threading.Event] = None

_logger = get_agent_logger()


def _service_path(name: str) -> Path:
    return (BASE_SERVICES_DIR / name / ENTRYPOINT).resolve()


def _is_running() -> bool:
    global _last_returncode, _last_error
    if _proc is None:
        return False
    rc = _proc.poll()
    if rc is None:
        _last_returncode = None
        return True
    _last_returncode = rc
    if _last_error is None:
        _last_error = f"return code {rc}"
    return False


def list_available_services(include_logical: bool = False) -> list[str]:
    services = discover_services()
    if include_logical:
        return services
    return [name for name in services if name != STANDBY_SERVICE]


def set_runtime_env(extra_env: Dict[str, str]) -> None:
    global _runtime_env
    _runtime_env = dict(extra_env)
    for key, value in _runtime_env.items():
        if value is None:
            continue
        os.environ[key] = value


def get_active_service(logical: bool = False) -> Optional[str]:
    if logical:
        return _logical
    return _name if _is_running() else None


def _resolve_definition(name: str) -> dict:
    definition = get_service_definition(name) or {}
    return dict(definition)


def _resolve_paths(definition: dict, name: str) -> tuple[list[str], Path, Dict[str, str], Path]:
    service_dir = (BASE_SERVICES_DIR / name).resolve()
    cwd = definition.get("cwd")
    if isinstance(cwd, str) and cwd:
        cwd_path = (BASE_SERVICES_DIR.parent / cwd).resolve()
    else:
        cwd_path = service_dir

    entry = definition.get("entry")
    if isinstance(entry, list) and entry:
        command = []
        for token in entry:
            if token == "${PYTHON}":
                command.append(sys.executable)
            elif isinstance(token, str):
                command.append(token)
            else:
                command.append(str(token))
    else:
        script = definition.get("path")
        if isinstance(script, str) and script:
            script_path = (BASE_SERVICES_DIR.parent / script).resolve()
        else:
            script_path = _service_path(name)
        command = [sys.executable, str(script_path)]

    env = {}
    raw_env = definition.get("env") or {}
    if isinstance(raw_env, dict):
        env = {str(k): str(v) for k, v in raw_env.items()}

    logs_conf = definition.get("logs") if isinstance(definition.get("logs"), dict) else {}
    stdout_path = resolve_log_path(logs_conf.get("stdout")) if logs_conf else None
    stderr_path = resolve_log_path(logs_conf.get("stderr")) if logs_conf else None
    if stdout_path and stderr_path and stdout_path != stderr_path:
        _logger.warning(
            "La configuración de logs de %s especifica stdout/stderr distintos; se usará %s como log combinado",
            name,
            stdout_path,
        )
    log_path = stdout_path or stderr_path
    if log_path is None:
        defaults = default_service_log_paths(name)
        log_path = defaults["stdout"]

    return command, cwd_path, env, log_path


def _stop_stream_threads() -> None:
    global _stdout_thread, _stderr_thread, _stdout_stream, _stderr_stream, _spool_stop
    if _spool_stop:
        _spool_stop.set()
    if _stdout_stream:
        try:
            _stdout_stream.close()
        except Exception:
            pass
    if _stderr_stream:
        try:
            _stderr_stream.close()
        except Exception:
            pass
    for thread in (_stdout_thread, _stderr_thread):
        if thread and thread.is_alive():
            thread.join(timeout=1.0)
    _stdout_thread = None
    _stderr_thread = None
    _stdout_stream = None
    _stderr_stream = None
    _spool_stop = None


def _pump_stream(stream: Optional[TextIO], service_name: str, stream_label: str, service_logger: logging.Logger) -> None:
    if stream is None:
        return
    try:
        while True:
            if _spool_stop and _spool_stop.is_set():
                break
            line = stream.readline()
            if not line:
                break
            text = line.rstrip("\n")
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            formatted = f"[{stamp}] {stream_label.upper()} {text}"
            service_logger.info(formatted)
            _logger.debug("%s %s: %s", service_name, stream_label, text)
            if service_name.upper() == "MIDI" and "error" in text.lower():
                _logger.error("MIDI ERROR: %s", text)
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _start_stream_threads(proc: subprocess.Popen[str], service_name: str, log_path: Path) -> None:
    global _stdout_thread, _stderr_thread, _stdout_stream, _stderr_stream, _spool_stop
    _spool_stop = threading.Event()
    _stdout_stream = proc.stdout
    _stderr_stream = proc.stderr
    service_logger = get_service_logger(service_name, path=log_path)
    _stdout_thread = threading.Thread(
        target=_pump_stream,
        args=(_stdout_stream, service_name, "stdout", service_logger),
        daemon=True,
    )
    _stderr_thread = threading.Thread(
        target=_pump_stream,
        args=(_stderr_stream, service_name, "stderr", service_logger),
        daemon=True,
    )
    _stdout_thread.start()
    _stderr_thread.start()


def start_service(name: str) -> bool:
    global _proc, _name, _logical, _last_error, _last_command, _last_env, _last_cwd, _last_returncode

    if name == STANDBY_SERVICE:
        if _is_running():
            stop_service()
        else:
            _stop_stream_threads()
        _logical = STANDBY_SERVICE
        _last_error = None
        _last_command = None
        _last_env = None
        _last_cwd = None
        _last_returncode = None
        return True

    definition = _resolve_definition(name)
    if definition.get("type") == "logical":
        return start_service(STANDBY_SERVICE)

    command, cwd_path, env, log_path = _resolve_paths(definition, name)

    if _is_running() and _name == name:
        _logger.info("Servicio '%s' ya en ejecución (pid=%s)", name, _proc.pid)
        _logical = name
        return True

    if _is_running():
        stop_service()

    try:
        env_map = os.environ.copy()
        env_map.update(_runtime_env)
        env_map.update(env)
        env_map.setdefault("PYTHONUNBUFFERED", "1")
        env_map["OMI_SERVICE_ID"] = name
        _stop_stream_threads()
        _logger.info("Lanzando servicio '%s' → %s (cwd=%s)", name, command, cwd_path)
        _proc = subprocess.Popen(
            command,
            cwd=str(cwd_path),
            env=env_map,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        _name = name
        _logical = name
        _last_error = None
        _last_command = command
        _last_env = env_map
        _last_cwd = cwd_path
        _last_returncode = None
        _start_stream_threads(_proc, name, log_path)
        _logger.info("Servicio '%s' iniciado (pid=%s)", name, _proc.pid)
        return True
    except Exception as e:
        _proc = None
        _name = None
        _logical = STANDBY_SERVICE
        _last_error = str(e)
        _last_returncode = None
        _logger.error("Error iniciando '%s': %s", name, e)
        _stop_stream_threads()
        return False


def stop_service(timeout: float = 5.0) -> bool:
    global _proc, _name, _logical, _last_error, _last_returncode
    if not _is_running():
        _proc = None
        _name = None
        _logical = STANDBY_SERVICE
        _last_error = None
        _last_returncode = None
        _stop_stream_threads()
        _logger.info("No hay servicio en ejecución.")
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
        _logger.info("Servicio '%s' detenido (rc=%s)", _name, rc)
        _proc = None
        _name = None
        _logical = STANDBY_SERVICE
        _last_error = None
        _last_returncode = rc
        _stop_stream_threads()
        return True
    except Exception as e:
        _logger.error("Error al detener '%s': %s", _name, e)
        _proc = None
        _name = None
        _logical = STANDBY_SERVICE
        _last_error = str(e)
        _last_returncode = None
        _stop_stream_threads()
        return False


def current_logical_service() -> Optional[str]:
    return _logical


def get_status() -> Dict[str, Any]:
    running = _is_running()
    pid = int(_proc.pid) if running and _proc else None
    return {
        "name": _name,
        "logical": _logical,
        "running": running,
        "pid": pid,
        "last_error": _last_error,
        "returncode": _last_returncode,
        "command": list(_last_command) if _last_command else None,
        "cwd": str(_last_cwd) if _last_cwd else None,
    }
