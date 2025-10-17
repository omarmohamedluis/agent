"""Logging helpers for the OMI control server."""
from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent
LOG_ROOT = SERVER_ROOT / "logs"
LOG_ROOT.mkdir(parents=True, exist_ok=True)

_MAX_BYTES = 2_000_000
_BACKUPS = 3


def get_server_logger() -> logging.Logger:
    logger = logging.getLogger("omi.server")
    if logger.handlers:
        return logger

    file_handler = RotatingFileHandler(LOG_ROOT / "server.log", maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    return logger
