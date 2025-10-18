from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from ..db import list_devices as db_list_devices, get_device
from .registry import DeviceRegistry


def build_devices_payload(registry: DeviceRegistry) -> List[Dict[str, Any]]:
    runtime_devices = registry.list_devices()
    desired_devices = {d["serial"]: d for d in db_list_devices()}
    result: List[Dict[str, Any]] = []
    seen = set()

    for dev in runtime_devices:
        serial = dev.get("serial")
        extra = desired_devices.get(serial or "") if serial else None
        payload = dict(dev)
        last_seen = payload.get("last_seen")
        if isinstance(last_seen, (int, float)):
            try:
                payload["last_seen"] = datetime.fromtimestamp(last_seen).isoformat()
            except Exception:
                payload["last_seen"] = None
        if extra:
            payload["desired_service"] = extra.get("desired_service")
            payload["desired_config"] = extra.get("desired_config")
            if extra.get("network_profile") is not None:
                payload["desired_network"] = extra.get("network_profile")
            if extra.get("device_index") is not None:
                payload["index"] = extra.get("device_index")
                payload["device_index"] = extra.get("device_index")
        result.append(payload)
        if serial:
            seen.add(serial)

    for serial, extra in desired_devices.items():
        if serial in seen:
            continue
        result.append(
            {
                "serial": serial,
                "host": extra.get("host"),
                "services": [],
                "available_services": [],
                "heartbeat": {},
                "logical_service": None,
                "last_seen": extra.get("updated_at"),
                "ip": None,
                "online": False,
                "desired_service": extra.get("desired_service"),
                "desired_config": extra.get("desired_config"),
                "desired_network": extra.get("network_profile"),
                "service_state": None,
                "index": extra.get("device_index"),
                "device_index": extra.get("device_index"),
            }
        )

    return result
