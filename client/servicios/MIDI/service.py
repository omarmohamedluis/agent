#!/usr/bin/env python3
"""Launcher para el servicio MIDI."""
from __future__ import annotations

import runpy
import sys
import traceback
import logging
from pathlib import Path

import os

# Configurar logging usando el nuevo módulo centralizado
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))
from omimidi_logger import get_logger, set_log_level

# Determinar nivel de log inicial
DEBUG_MODE = "-debug" in sys.argv or "--debug" in sys.argv
LOG_LEVEL = logging.DEBUG if DEBUG_MODE else logging.INFO

LOGGER = get_logger("omimidi.service", level=LOG_LEVEL)

THIS_DIR = Path(__file__).resolve().parent
CORE_PATH = THIS_DIR / "src" / "omimidi_core.py"

if __name__ == "__main__":
    if DEBUG_MODE:
        LOGGER.info("Modo DEBUG activado")
    LOGGER.info("Iniciando servicio MIDI...")
    LOGGER.info(f"Directorio actual: {THIS_DIR}")
    LOGGER.info(f"Cargando core desde: {CORE_PATH}")
    
    try:
        LOGGER.info("Ejecutando omimidi_core...")
        runpy.run_path(str(CORE_PATH), run_name="__main__")
    except KeyboardInterrupt:
        LOGGER.info("\n¡Hasta luego! Servicio MIDI detenido por el usuario.")
        sys.exit(0)
    except Exception as exc:
        LOGGER.error(f"Error lanzando omimidi_core: {exc}", exc_info=True)
        raise
