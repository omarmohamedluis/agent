import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import socket
import sys
import threading

import httpx
import pytest

from omi.client.app import create_app
from omi.client.runtime import ClientRuntime, Settings
from omi.discovery import local_ipv4
from omi.logging import apply_log_budget
from omi.models import HeartbeatReply, MidiRoute
from omi.store import Store


def test_no_beacon_cli_never_requires_default_route(monkeypatch, tmp_path):
    from omi import cli
    import omi.discovery
    import omi.server.app
    import uvicorn
    monkeypatch.setattr(sys, "argv", ["omi-server", "--no-beacon", "--simulate", "--data-dir", str(tmp_path)])
    monkeypatch.setattr(omi.discovery, "local_ipv4", lambda: (_ for _ in ()).throw(OSError("No route")))
    monkeypatch.setattr(cli, "configure_logging", lambda *a, **k: None)
    captured = {}
    monkeypatch.setattr(omi.server.app, "create_app", lambda *a, **kwargs: captured.update(kwargs) or "app")
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kwargs: None)
    cli.server_main()
    assert captured["beacon_enabled"] is False and captured["advertise"] == ""


def test_isolated_linux_lan_address_without_default_route(monkeypatch):
    import omi.discovery
    class NoRouteSocket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def connect(self, address):
            raise OSError("No default route")
    monkeypatch.setattr(omi.discovery.socket, "socket", lambda *a: NoRouteSocket())
    monkeypatch.setattr(omi.discovery.sys, "platform", "linux")
    class Result:
        stdout = json.dumps([{"ifname": "eth0", "addr_info": [{"local": "192.168.50.2"}]}])
    monkeypatch.setattr(omi.discovery.subprocess, "run", lambda *a, **k: Result())
    assert local_ipv4() == "192.168.50.2"


async def test_endpoint_dns_wait_is_off_control_event_loop_and_only_one_job(tmp_path):
    runtime = ClientRuntime(tmp_path, simulated=True, serial="test")
    # Construction in simulation avoids all physical identity/network adapters.
    runtime.simulated = False
    release, entered = threading.Event(), threading.Event()
    count = 0
    def resolve():
        nonlocal count
        count += 1
        entered.set()
        release.wait(timeout=3)
        return "http://192.168.1.2:8081"
    runtime._agent_endpoint = resolve
    try:
        pending = asyncio.create_task(runtime._resolve_endpoint())
        for _ in range(20):
            if entered.is_set():
                break
            await asyncio.sleep(.01)
        assert entered.is_set()
        # API stays available while the native resolver is stalled.
        app = create_app(runtime, manage_lifecycle=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://pi") as client:
            response = await asyncio.wait_for(client.get("/api/status"), .2)
            assert response.status_code == 200
        release.set()
        assert await pending == "http://192.168.1.2:8081"
        assert await runtime._resolve_endpoint() == "http://192.168.1.2:8081"
        assert count == 1
    finally:
        release.set()
        await runtime.close()


async def test_identity_survives_offline_restart_and_reset_applies_live(tmp_path, monkeypatch):
    from omi.discovery import BeaconListener
    async def inert_start(self):
        pass
    monkeypatch.setattr(BeaconListener, "start", inert_start)
    store = Store(tmp_path / "omi.sqlite3")
    store.put("central.last_reply", HeartbeatReply(onboarded=True, device_id=12, name="FOH backup").model_dump())
    store.put("settings", {"server_url": "http://old-server:8080", "operation_timeout": 120, "interface": "enp1"})
    store.close()
    runtime = ClientRuntime(tmp_path, simulated=True, serial="physical-equivalent")
    runtime.booting = False
    try:
        assert runtime.status()["device_id"] == 12
        assert runtime.status()["name"] == "FOH backup"
        assert not runtime.connected and runtime.local_control
        app = create_app(runtime, manage_lifecycle=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://pi") as client:
            response = await client.post("/api/maintenance/factory-reset", json={"confirm": True, "backup": False})
            assert response.status_code == 200, response.text
            assert (await client.get("/api/settings")).json() == Settings().model_dump()
        assert runtime.server_url is None
        assert runtime.coordinator.operation_timeout == Settings().operation_timeout
        assert runtime.serial == "physical-equivalent"
        assert runtime.store.items() == {}
        backups = list((tmp_path / "backups").glob("*.sqlite3"))
        assert len(backups) == 1
        recovered = Store(backups[0])
        assert recovered.get("central.last_reply")["device_id"] == 12
        recovered.close()
    finally:
        await runtime.close()


def test_factory_cli_creates_recoverable_backup_without_optional_path(tmp_path, monkeypatch, capsys):
    from omi.cli import maintain_main
    store = Store(tmp_path / "omi.sqlite3")
    store.put("settings", {"interface": "eth0"})
    store.put("execution", {"target": None, "error": "Previous failure"})
    store.close()
    monkeypatch.setattr(sys, "argv", ["omi-maintain", "--data-dir", str(tmp_path), "factory-reset", "--confirm"])
    maintain_main()
    result = json.loads(capsys.readouterr().out)
    recovered = Store(Path(result["backup"]))
    try:
        assert recovered.get("settings") == {"interface": "eth0"}
        assert recovered.get("execution")["error"] == "Previous failure"
    finally:
        recovered.close()
    cleared = Store(tmp_path / "omi.sqlite3")
    try:
        assert cleared.items() == {}
    finally:
        cleared.close()


def test_log_budget_reconfigures_and_removes_oversized_rotated_logs(tmp_path):
    path = tmp_path / "logs/omi.log"
    path.parent.mkdir()
    handler = RotatingFileHandler(path, maxBytes=40 * 1024 * 1024, backupCount=4)
    logger = logging.getLogger()
    logger.addHandler(handler)
    try:
        path.write_bytes(b"x" * (2 * 1024 * 1024))
        apply_log_budget(tmp_path, 1)
        assert handler.maxBytes == 1024 * 1024 // 5
        assert sum(p.stat().st_size for p in path.parent.iterdir()) <= 1024 * 1024
    finally:
        logger.removeHandler(handler)
        handler.close()


@pytest.mark.parametrize("constant", [float("nan"), float("inf"), 2**31, -(2**31)-1, "\0", "x"*2049])
def test_invalid_osc_constants_rejected_before_realtime_path(constant):
    with pytest.raises(ValueError):
        MidiRoute(constant=constant)
