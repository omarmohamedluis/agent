from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from ..db import upsert_device, ensure_device_index, list_devices as db_list_devices, get_device as db_get_device


class DeviceRegistry:
    """Thread-safe runtime registry of discovered agents."""

    def __init__(self, status_ttl: float) -> None:
        self._ttl = status_ttl
        self._devices: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def update_from_status(self, payload: Dict[str, Any], addr) -> Tuple[Optional[int], Optional[int]]:
        serial = payload.get("serial")
        if not serial:
            return None, None
        assigned_index = ensure_device_index(serial)
        info = {
            "serial": serial,
            "host": payload.get("host"),
            "name": payload.get("name"),
            "index": assigned_index,
            "version": payload.get("version"),
            "services": payload.get("services", []),
            "available_services": payload.get("available_services", []),
            "heartbeat": payload.get("heartbeat", {}),
            "service_state": payload.get("service_state"),
            "logical_service": payload.get("logical_service"),
            "last_seen": time.time(),
            "ip": addr[0],
        }
        with self._lock:
            self._devices[serial] = info
        upsert_device(serial, host=info.get("host"), device_index=assigned_index)
        return assigned_index, payload.get("index")

    def update_services(
        self,
        serial: str,
        services: Optional[List[Dict[str, Any]]],
        service_state: Optional[Dict[str, Any]] = None,
        *,
        transition: Optional[bool] = None,
        progress: Optional[int] = None,
        stage: Optional[str] = None,
    ) -> None:
        if services is None and service_state is None and transition is None:
            return
        with self._lock:
            if serial not in self._devices:
                return
            if services is not None:
                self._devices[serial]["services"] = services
            if service_state is not None:
                self._devices[serial]["service_state"] = service_state
            elif transition is not None or progress is not None or stage is not None:
                state = dict(self._devices[serial].get("service_state") or {})
                if transition is not None:
                    state["transition"] = bool(transition)
                if progress is not None:
                    state["progress"] = progress
                if stage is not None:
                    state["stage"] = stage
                self._devices[serial]["service_state"] = state
            self._devices[serial]["last_seen"] = time.time()

    def update_index(self, serial: str, index: Optional[int]) -> None:
        if index is None:
            return
        with self._lock:
            if serial in self._devices:
                self._devices[serial]["index"] = int(index)

    def list_devices(self) -> List[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            devices = []
            for dev in self._devices.values():
                copy_dev = dict(dev)
                copy_dev["online"] = (now - dev.get("last_seen", 0.0)) < self._ttl
                devices.append(copy_dev)
            return devices

    def get_device(self, serial: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            dev = self._devices.get(serial)
            return dict(dev) if dev else None

    @staticmethod
    def desired_devices() -> Dict[str, Dict[str, Any]]:
        return {d["serial"]: d for d in db_list_devices()}

    @staticmethod
    def db_device(serial: str) -> Optional[Dict[str, Any]]:
        return db_get_device(serial)
