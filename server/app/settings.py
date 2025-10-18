from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    """Static configuration for the control server."""

    http_port: int = 6982
    broadcast_ip: str = "255.255.255.255"
    broadcast_port: int = 37020
    reply_port: int = 37021
    discover_interval: float = 3.0
    status_ttl: float = 6.0

    @property
    def web_root(self) -> Path:
        return Path(__file__).resolve().parents[1] / "web"

    @property
    def static_root(self) -> Path:
        return self.web_root / "static"
