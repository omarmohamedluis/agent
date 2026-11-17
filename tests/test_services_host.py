import asyncio
import socket

import pytest

from omi.models import MidiConfig, MidiRoute, OscDestination, PresetConfig
from omi.services.host import ServiceHost
from omi.services.midi import encode_osc


def preset(port=7001, address="/fader"):
    return PresetConfig(service="midi", midi=MidiConfig(input="Simulated MIDI", deadline_ms=100,
            destinations=[OscDestination(host="127.0.0.1", port=port)],
            routes=[MidiRoute(kind="cc", number=7, address=address)]))


@pytest.fixture
def receiver():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    yield sock
    sock.close()


async def receive(sock, timeout=0.5):
    return await asyncio.wait_for(asyncio.get_running_loop().sock_recv(sock, 4096), timeout)


async def test_real_subprocess_udp_hot_apply_and_editor_survives_restart(tmp_path, receiver):
    host = ServiceHost(tmp_path, simulated=True)
    cfg = preset(receiver.getsockname()[1])
    message = {"type": "control_change", "channel": 2, "cc": 7, "value": 8}
    try:
        await host.start(cfg)
        pid = host._process.pid
        assert host.status()["ready"]
        await host.inject_midi(message)
        assert await receive(receiver) == encode_osc("/fader", 8)
        await host.open_editor("session", cfg)
        await host.inject_midi(message)  # muted before arming Learn
        await host.arm_learn("session")
        await host.inject_midi(message)
        await host.inject_midi({**message, "value": 90})
        assert host.learn_result("session")["value"] == 8
        updated = preset(receiver.getsockname()[1], "/new")
        await host.apply(updated)
        assert host._process.pid == pid  # routes genuinely apply without restart
        await host.inject_midi(message)
        await host.stop()
        await host.start(updated)
        assert host._process.pid != pid
        assert host.status()["muted"]
        assert host.learn_result("session")["value"] == 8
        await host.inject_midi(message)
        with pytest.raises(asyncio.TimeoutError):
            await receive(receiver, 0.1)
        await host.close_editor("session")
        with pytest.raises(asyncio.TimeoutError):
            await receive(receiver, 0.1)  # no replay on close
        await host.inject_midi({**message, "value": 12})
        assert await receive(receiver) == encode_osc("/new", 12)
    finally:
        await host.close_editor("session")
        await host.stop()
    assert host._process is None and host._monitor is None


async def test_standby_capture_never_becomes_operational_or_emits(tmp_path, receiver):
    host = ServiceHost(tmp_path, simulated=True)
    cfg = preset(receiver.getsockname()[1])
    try:
        await host.open_editor("capture", cfg)
        assert host.status()["capture_only"]
        assert host.status()["service"] is None
        await host.arm_learn("capture")
        await host.inject_midi({"type": "note_on", "channel": 15, "note": 80, "velocity": 77})
        assert host.learn_result("capture")["note"] == 80
        with pytest.raises(RuntimeError, match="Another"):
            await host.open_editor("second", cfg)
        await host.close_editor("wrong")
        assert host.status()["editor"] == "capture"
        await host.close_editor("capture")
        assert host._process is None
        assert host.status()["health"] == "standby"
        with pytest.raises(asyncio.TimeoutError):
            await receive(receiver, 0.1)
    finally:
        await host.stop()


async def test_start_twice_stops_old_child_and_stop_is_idempotent(tmp_path):
    host = ServiceHost(tmp_path, simulated=True)
    try:
        await host.start(preset())
        old = host._process
        await host.start(preset())
        assert old.returncode is not None
        assert host._process.pid != old.pid
    finally:
        await host.stop()
        await host.stop()


async def test_armed_learn_survives_coordinated_worker_restart(tmp_path):
    host = ServiceHost(tmp_path, simulated=True)
    cfg = preset()
    try:
        await host.start(cfg)
        await host.open_editor("session", cfg)
        await host.arm_learn("session")
        await host.stop()
        await host.start(cfg)
        await host.inject_midi({"type": "control_change", "cc": 7, "value": 45})
        assert host.learn_result("session")["value"] == 45
        assert host.status()["muted"]
    finally:
        await host.close_editor("session")
        await host.stop()


async def test_incompatible_hot_apply_rejected_and_input_never_changed(tmp_path):
    host = ServiceHost(tmp_path, simulated=True)
    cfg = preset()
    try:
        await host.start(cfg)
        changed = cfg.model_copy(deep=True)
        changed.midi.input = "Other controller"
        with pytest.raises(ValueError, match="restart"):
            await host.apply(changed)
        assert host.status()["input"] == "Simulated MIDI"
        assert await host.inputs() == ["Simulated MIDI"]
    finally:
        await host.stop()


async def test_worker_crash_is_visible_and_bounded_cleanup(tmp_path):
    host = ServiceHost(tmp_path, simulated=True)
    await host.start(preset())
    process = host._process
    process.kill()
    await process.wait()
    for _ in range(20):
        if host.status()["health"] == "error":
            break
        await asyncio.sleep(0.05)
    assert host.status()["health"] == "error"
    await asyncio.wait_for(host.stop(), 5)


async def test_production_rejects_injection_without_opening_hardware(tmp_path):
    host = ServiceHost(tmp_path)
    with pytest.raises(ValueError, match="simulation"):
        await host.inject_midi({"type": "note_on", "note": 60})
