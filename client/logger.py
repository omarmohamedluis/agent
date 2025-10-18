"""Logging helpers for the OMI agent."""
from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Optional

CLIENT_ROOT = Path(__file__).resolve().parent
LOG_ROOT = CLIENT_ROOT / "logs"
COMPONENT_LOG_DIR = LOG_ROOT / "components"
SERVICE_LOG_DIR = LOG_ROOT / "services"

_DEFAULT_MAX_BYTES = 2_000_000
_DEFAULT_BACKUPS = 3


def _ensure_dirs() -> None:
    for path in (LOG_ROOT, COMPONENT_LOG_DIR, SERVICE_LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _build_file_handler(path: Path) -> RotatingFileHandler:
    handler = RotatingFileHandler(path, maxBytes=_DEFAULT_MAX_BYTES, backupCount=_DEFAULT_BACKUPS, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    return handler


def get_agent_logger() -> logging.Logger:
    """Return the main agent logger (logs to client/logs/agent.log and console)."""
    _ensure_dirs()
    logger = logging.getLogger("omi.agent")
    if logger.handlers:
        return logger

    file_handler = _build_file_handler(LOG_ROOT / "agent.log")
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    return logger


def get_component_logger(name: str, filename: Optional[str] = None) -> logging.Logger:
    """Return a logger for a specific component, writing to components/<filename>."""
    _ensure_dirs()
    if filename is None:
        filename = f"{name}.log"
    path = COMPONENT_LOG_DIR / filename

    logger = logging.getLogger(f"omi.agent.{name}")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        logger.addHandler(_build_file_handler(path))
        logger.propagate = False
    return logger


def resolve_log_path(relative_path: Optional[str]) -> Optional[Path]:
    if not relative_path:
        return None
    path = (CLIENT_ROOT / relative_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def default_service_log_paths(service_id: str) -> Dict[str, Path]:
    """Provide default stdout/stderr paths when manifest omits them."""
    _ensure_dirs()
    base = SERVICE_LOG_DIR / service_id.lower()
    return {
        "stdout": base.with_suffix(".log"),
        "stderr": base.with_suffix(".log"),
    }


def get_service_logger(service_id: str, *, path: Optional[Path] = None) -> logging.Logger:
    """Return a rotating logger to aggregate stdout/stderr of a service."""
    _ensure_dirs()
    logger_name = f"omi.agent.service.{service_id.lower()}"
    logger = logging.getLogger(logger_name)
    if logger.handlers:
        return logger

    if path is None:
        path = SERVICE_LOG_DIR / f"{service_id.lower()}.log"
    handler = _build_file_handler(path)

    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger
