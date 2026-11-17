"""Validated, versioned contracts shared by agent, server and service workers."""
from __future__ import annotations

import ipaddress
import math
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def new_id() -> str:
    return str(uuid.uuid4())


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class NetworkProfile(Model):
    vlan: int | None = Field(default=None, ge=1, le=4094)
    mode: Literal["dhcp", "static"] = "dhcp"
    address: str = ""  # IPv4/prefix, e.g. 192.168.30.10/24
    gateway: str = ""
    dns: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def valid_addresses(self) -> NetworkProfile:
        if self.mode == "static":
            ipaddress.IPv4Interface(self.address)
        if self.gateway:
            ipaddress.IPv4Address(self.gateway)
        for address in self.dns:
            ipaddress.IPv4Address(address)
        return self


class OscDestination(Model):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=7001, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def valid_host(cls, value: str) -> str:
        if any(c.isspace() or c in "/\\\x00" for c in value):
            raise ValueError("Invalid OSC host")
        return value


class MidiRoute(Model):
    id: str = Field(default_factory=new_id, min_length=1, max_length=80)
    kind: Literal["note", "cc"] = "note"
    number: int = Field(default=60, ge=0, le=127)
    channel: int | None = Field(default=None, ge=0, le=15)
    address: str = Field(default="/go", min_length=1, max_length=512)
    value_type: Literal["int", "float", "bool", "const"] = "int"
    constant: int | float | bool | str = 1

    @field_validator("address")
    @classmethod
    def valid_osc_address(cls, value: str) -> str:
        if not value.startswith("/") or any(c.isspace() or c == "\x00" for c in value):
            raise ValueError("OSC address must start with / and contain no whitespace")
        return value

    @field_validator("constant")
    @classmethod
    def valid_constant(cls, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and not -(2**31) <= value < 2**31:
            raise ValueError("OSC integer constants must fit signed 32 bits")
        if isinstance(value, float) and (not math.isfinite(value) or abs(value) > 3.4028235e38):
            raise ValueError("OSC float constants must be finite 32-bit values")
        if isinstance(value, str) and (len(value.encode("utf-8")) > 2048 or "\x00" in value):
            raise ValueError("OSC string constants must be at most 2048 UTF-8 bytes without NUL")
        return value


class MidiConfig(Model):
    input: str = Field(default="", max_length=256)
    destinations: list[OscDestination] = Field(default_factory=list, max_length=32)
    routes: list[MidiRoute] = Field(default_factory=list, max_length=2048)
    deadline_ms: float = Field(default=10.0, ge=1, le=100)


class SatelliteConfig(Model):
    host: str = Field(default="127.0.0.1", min_length=1, max_length=253)
    port: int = Field(default=16622, ge=1, le=65535)
    rest_port: int = Field(default=9999, ge=1, le=65535)


class PresetConfig(Model):
    service: Literal["midi", "satellite"]
    network: NetworkProfile = Field(default_factory=NetworkProfile)
    midi: MidiConfig = Field(default_factory=MidiConfig)
    satellite: SatelliteConfig = Field(default_factory=SatelliteConfig)


class Revision(Model):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id, min_length=1, max_length=80)
    preset_id: str = Field(default_factory=new_id, min_length=1, max_length=80)
    parents: list[str] = Field(default_factory=list, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    config: PresetConfig
    deleted: bool = False
    author: str = Field(default="local", max_length=128)
    created_at: float = Field(default_factory=time.time)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Preset needs a printable name")
        return value


class Target(Model):
    preset_id: str = Field(min_length=1, max_length=80)
    revision: str = Field(min_length=1, max_length=80)


class Command(Model):
    id: str = Field(default_factory=new_id, min_length=1, max_length=80)
    action: Literal["apply", "standby", "reboot", "poweroff", "management", "update"]
    target: Target | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    expires_at: float = Field(default_factory=lambda: time.time() + 15)


class Operation(Model):
    id: str
    action: str
    status: Literal["accepted", "running", "succeeded", "failed", "interrupted"] = "accepted"
    phase: str = "accepted"
    started_at: float = Field(default_factory=time.time)
    finished_at: float | None = None
    error: str | None = None
    progress: list[str] = Field(default_factory=list)


class RuntimeState(Model):
    target: Target | None = None
    health: Literal["standby", "starting", "ready", "waiting", "error", "stopping"] = "standby"
    detail: str = ""
    operation: Operation | None = None
    service_info: dict[str, Any] = Field(default_factory=dict)
    network: dict[str, Any] = Field(default_factory=dict)
    busy: bool = False


class Heartbeat(Model):
    serial: str = Field(min_length=1, max_length=128)
    hostname: str = Field(max_length=253)
    endpoint: str
    state: RuntimeState
    restoring: Target | None = None
    boot_id: str
    library_digest: str = ""
    version: str = "3.0.0.dev0"


class HeartbeatReply(Model):
    onboarded: bool = False
    device_id: int | None = None
    name: str = ""
    expected: Target | None = None
    discrepancy: bool = False
    library_digest: str = ""


class EditorRequest(Model):
    preset_id: str
    revision: str
    route_editor: bool = False
    input: str | None = None
    config: PresetConfig | None = None  # unsaved draft, allowed only from standby
    session_id: str | None = None


class SavePreset(Model):
    preset_id: str | None = None
    parents: list[str] = Field(default_factory=list)
    name: str
    config: PresetConfig
    session_id: str | None = None


def change_requires_restart(old: PresetConfig, new: PresetConfig) -> bool:
    if old.service != new.service or old.network != new.network:
        return True
    if new.service == "satellite":
        return old.satellite != new.satellite
    return old.midi.input != new.midi.input
