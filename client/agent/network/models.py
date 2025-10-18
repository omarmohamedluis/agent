from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(slots=True)
class ServiceNetworkProfile:
    """Future network configuration to apply when a service is active."""

    vlan_id: Optional[int] = None
    interface: Optional[str] = None
    dhcp: bool = True
    static_ip: Optional[str] = None
    gateway: Optional[str] = None
    dns: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.vlan_id is None:
            return "default LAN"
        return f"VLAN {self.vlan_id} on {self.interface or 'iface'}"
