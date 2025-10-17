#!/usr/bin/env python3
"""Launcher para el servicio MIDI."""
from __future__ import annotations

import runpy
import sys
import traceback
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
CORE_PATH = THIS_DIR / "omimidi_core.py"


if __name__ == "__main__":
    try:
        runpy.run_path(str(CORE_PATH), run_name="__main__")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[service] Error lanzando omimidi_core: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc()
        raise
