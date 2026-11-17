#!/usr/bin/env python3
"""Run the prepared real Linux Satellite offline, without USB or host network edits."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import socket
import sys

from omi.models import PresetConfig, SatelliteConfig
from omi.services.host import ServiceHost
from omi.services.satellite import SATELLITE_VERSION


def free_port() -> int:
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        return stream.getsockname()[1]


async def check(directory: Path, data_dir: Path) -> None:
    host = ServiceHost(data_dir, satellite_dir=directory)
    config = PresetConfig(service="satellite", satellite=SatelliteConfig(host="127.0.0.1", port=free_port(), rest_port=free_port()))
    try:
        first = await host.start(config)
        assert first["ready"] and first["health"] == "waiting", first
        assert first["version"] == SATELLITE_VERSION
        await asyncio.sleep(2)
        assert host.status()["health"] == "waiting", host.status()
        assert host.status()["restarts"] == 0, host.status()
        await host.stop()
        config.satellite.port = free_port()
        second = await host.start(config)
        assert second["ready"] and second["health"] == "waiting", second
        await asyncio.sleep(2)
        assert host.status()["health"] == "waiting", host.status()
        assert host.status()["restarts"] == 0, host.status()
        print(json.dumps({"ok": True, "version": second["version"], "build": second["build"],
                          "first_start": first["health"], "restart": second["health"],
                          "companion_connected": second["companion_connected"]}))
    finally:
        await host.stop()
    assert host.status()["health"] == "standby"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--satellite-dir", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "linux":
        raise SystemExit("This smoke check executes the real prepared Linux binary")
    asyncio.run(check(args.satellite_dir, args.data_dir))
