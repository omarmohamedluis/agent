from __future__ import annotations

import logging
from typing import Callable, Optional

LOGGER = logging.getLogger("omi.agent.hardware")

_shutdown_callback: Optional[Callable[[], None]] = None


def register_shutdown_callback(callback: Callable[[], None]) -> None:
    """Register a callback to be invoked when the hardware shutdown button is pressed (stub)."""
    global _shutdown_callback
    _shutdown_callback = callback
    LOGGER.info("Shutdown callback registrado (stub, sin integración física).")


def clear_callbacks() -> None:
    """Reset any registered hardware callbacks (no-op placeholder)."""
    global _shutdown_callback
    _shutdown_callback = None
    LOGGER.info("Callbacks de hardware limpiados (stub).")


def simulate_shutdown_press() -> None:
    """Helper for tests to simulate a hardware button press."""
    if _shutdown_callback:
        LOGGER.info("Simulación de pulsación de botón de apagado.")
        _shutdown_callback()
