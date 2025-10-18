from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class NetworkInterfaceConfig(BaseModel):
    """Placeholder for future VLAN/DHCP configuration per service."""

    vlan_id: Optional[int] = Field(default=None, ge=1, description="VLAN tag to enable when the service is active")
    interface: Optional[str] = Field(default=None, description="Physical interface name, e.g., eth0")
    dhcp: bool = True
    static_ip: Optional[str] = None
    gateway: Optional[str] = None
    dns: List[str] = Field(default_factory=list)


class ServiceRequest(BaseModel):
    service: str
    config: Optional[str] = None


class PowerRequest(BaseModel):
    action: Literal["shutdown", "reboot"]


class ConfigPayload(BaseModel):
    name: str
    data: dict
    serial: Optional[str] = None
    overwrite: bool = False


class DeviceDesiredPayload(BaseModel):
    desired_service: Optional[str] = None
    desired_config: Optional[str] = None
    network: Optional[NetworkInterfaceConfig] = None


class ServiceProfile(BaseModel):
    service: str
    config: Optional[str] = None
    network: Optional[NetworkInterfaceConfig] = None


class NetworkAckPayload(BaseModel):
    applied: bool = False
    message: Optional[str] = None
