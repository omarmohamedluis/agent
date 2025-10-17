#!/usr/bin/env python3
"""Launcher para el servicio MIDI."""
from __future__ import annotations
import runpy
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
CORE_PATH = THIS_DIR / 'omimidi_core.py'

if __name__ == '__main__':
    runpy.run_path(str(CORE_PATH))
