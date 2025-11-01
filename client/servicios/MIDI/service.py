#!/usr/bin/env python3
"""Launcher para el servicio MIDI."""
from __future__ import annotations

import runpy
import sys
import traceback
import logging
from pathlib import Path

# Configurar logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    stream=sys.stdout
)

LOGGER = logging.getLogger("omimidi.service")

THIS_DIR = Path(__file__).resolve().parent
CORE_PATH = THIS_DIR / "omimidi_core.py"

if __name__ == "__main__":
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
