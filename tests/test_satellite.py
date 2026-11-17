import asyncio
import json
import os
import signal
import socket
import sys
from pathlib import Path

import pytest

from omi.models import SatelliteConfig
from omi.services.satellite import (SATELLITE_BUILD, SATELLITE_VERSION, SatelliteSupervisor,
                                    prepared_command, runtime_config)


def test_runtime_config_owns_central_target_and_stream_deck_only():
    value = runtime_config(SatelliteConfig(host="192.0.2.3", port=16625, rest_port=9099),
                           {"machineId": "retained", "installationName": "OMI FOH", "remoteIp": "wrong"})
    assert value["remoteIp"] == "192.0.2.3" and value["remotePort"] == 16625
    assert value["restPort"] == 9099 and value["restEnabled"]
    assert value["machineId"] == "retained" and value["installationName"] == "OMI FOH"
    assert not value["detailedUsageEnabled"]
    assert [name for name, enabled in value["surfacePluginsEnabled"].items() if enabled] == ["elgato-stream-deck"]


def test_prepared_version_is_required_without_runtime_install(tmp_path):
    with pytest.raises(RuntimeError, match="installer"):
        prepared_command(tmp_path, tmp_path / "config.json")
    manifest = tmp_path / "omi-satellite.json"
    manifest.write_text(json.dumps({"version": "3.7.0", "build": "old"}))
    with pytest.raises(RuntimeError, match="pinned"):
        prepared_command(tmp_path, tmp_path / "config.json")
    manifest.write_text(json.dumps({"version": SATELLITE_VERSION, "build": SATELLITE_BUILD,
                                    "executable": "../outside"}))
    with pytest.raises(RuntimeError, match="outside"):
        prepared_command(tmp_path, tmp_path / "config.json")
    launcher = tmp_path / "companion-satellite"
    launcher.write_text("prepared")
    manifest.write_text(json.dumps({"version": SATELLITE_VERSION, "build": SATELLITE_BUILD,
                                    "executable": "companion-satellite"}))
    assert prepared_command(tmp_path, tmp_path / "config.json") == [str(launcher.resolve()), str((tmp_path / "config.json").resolve())]


FAKE_SATELLITE = '''
import json,os,sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
path=Path(sys.argv[1]); config=json.loads(path.read_text())
path.with_suffix('.pid').write_text(str(os.getpid()))
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def do_GET(self):
  if path.with_suffix('.health-fail').exists():
   self.send_error(503); return
  data=({'host':config['remoteIp'],'port':config['remotePort'],'protocol':'tcp'}
        if self.path=='/api/config' else {'connected':path.with_suffix('.connected').exists(),'companionVersion':'test'})
  body=json.dumps(data).encode(); self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
ThreadingHTTPServer(('127.0.0.1',config['restPort']),Handler).serve_forever()
'''


@pytest.fixture
def satellite(tmp_path, monkeypatch):
    script = tmp_path / "fake_satellite.py"
    script.write_text(FAKE_SATELLITE, encoding="utf-8")
    monkeypatch.setattr("omi.services.satellite.prepared_command", lambda directory, config: [sys.executable, str(script), str(config)])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = SatelliteConfig(host="127.0.0.7", rest_port=port)
    return SatelliteSupervisor(tmp_path / "runtime", tmp_path), config


async def until(predicate, timeout=6):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("Expected asynchronous state was not reached")
        await asyncio.sleep(0.05)


async def test_real_subprocess_readiness_companion_loss_and_health_are_separate(satellite):
    supervisor, config = satellite
    try:
        state = await supervisor.start(config)
        assert state["ready"] and state["health"] == "waiting"
        pid = supervisor.process.pid
        connected = supervisor.data_dir / "satellite.connected"
        connected.touch()
        await until(lambda: supervisor.status()["health"] == "ready")
        connected.unlink()
        await until(lambda: supervisor.status()["companion_connected"] is False)
        assert supervisor.status()["health"] == "waiting"
        assert supervisor.process.pid == pid and supervisor.restarts == 0
        (supervisor.data_dir / "satellite.health-fail").touch()
        await until(lambda: "health unavailable" in supervisor.status()["detail"])
        assert supervisor.process.pid == pid and supervisor.restarts == 0
        assert supervisor.status()["ready"]
    finally:
        await supervisor.stop()
    assert supervisor.process is None and supervisor.task is None and supervisor.reader is None


async def test_real_child_crashes_have_limited_recovery_without_network_restart(satellite):
    supervisor, config = satellite
    try:
        await supervisor.start(config)
        for attempt in range(4):
            pid_path = supervisor.data_dir / "satellite.pid"
            pid = int(pid_path.read_text())
            os.kill(pid, signal.SIGTERM)
            if attempt < 3:
                try:
                    await until(lambda: pid_path.exists() and int(pid_path.read_text()) != pid and supervisor.status()["ready"])
                except AssertionError as exc:
                    raise AssertionError(supervisor.status()) from exc
                assert supervisor.restarts == attempt + 1
            else:
                await until(lambda: supervisor.status()["health"] == "error")
        assert supervisor.restarts == 3
        assert not supervisor.status()["process_alive"]
        assert "limit" in supervisor.status()["detail"]
    finally:
        await supervisor.stop()


async def test_foreign_listener_cannot_satisfy_child_readiness(satellite):
    supervisor, config = satellite
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", config.rest_port))
        listener.listen()
        with pytest.raises(RuntimeError, match="already in use"):
            await supervisor.start(config)
        assert supervisor.process is None


async def test_agent_lifetime_pipe_eof_cleans_satellite(satellite):
    supervisor, config = satellite
    try:
        await supervisor.start(config)
        process = supervisor.process
        # Exactly what happens when the agent disappears without stop().
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 6)
        assert process.returncode is not None
    finally:
        await supervisor.stop()


async def test_known_incompatible_companion_api_fails_startup_immediately(satellite, monkeypatch):
    supervisor, config = satellite
    monkeypatch.setattr("omi.services.satellite.probe", lambda cfg: {
        "health": "error", "ready": True, "detail": "Companion API version is unsupported",
        "companion_unsupported": True})
    with pytest.raises(RuntimeError, match="unsupported"):
        await supervisor.start(config)
    assert supervisor.process is None
    assert supervisor.task is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux REST port TIME_WAIT reuse")
async def test_recent_rest_connection_time_wait_does_not_block_restart(satellite):
    supervisor, config = satellite
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", config.rest_port))
        listener.listen(1)
        with socket.create_connection(("127.0.0.1", config.rest_port)) as client:
            accepted, _ = listener.accept()
            accepted.shutdown(socket.SHUT_WR)
            accepted.close()
            assert client.recv(1) == b""
    try:
        assert (await supervisor.start(config))["ready"]
    finally:
        await supervisor.stop()
