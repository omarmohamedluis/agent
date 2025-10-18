from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .. import db


class NetworkManager:
    """Placeholder for future network orchestration between server and agents."""

    def __init__(self) -> None:
        self.logger = logging.getLogger("omi.server.network")

    def record_desired_profile(self, serial: str, profile: Optional[Dict[str, Any]]) -> None:
        """Store the desired network profile for a device (no orchestration yet)."""
        db.save_device_network_profile(serial, profile)
        if profile:
            self.logger.info("Perfil de red deseado actualizado para %s → %s", serial, profile)
        else:
            self.logger.info("Perfil de red desactivado para %s", serial)

    def acknowledge_profile(self, serial: str, payload: Dict[str, Any]) -> None:
        """Stub for agent acknowledgment when a network profile is applied."""
        applied = payload.get("applied")
        message = payload.get("message")
        self.logger.info(
            "ACK de red recibido de %s (applied=%s, message=%s)",
            serial,
            applied,
            message,
        )
