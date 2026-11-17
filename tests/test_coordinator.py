import asyncio
import time

import pytest

from omi.client.coordinator import Coordinator
from omi.library import Library
from omi.models import Command, EditorRequest, NetworkProfile, PresetConfig, SavePreset, Target
from omi.store import Store


class Host:
    def __init__(self, events):
        self.events = events
        self.state = {"health": "standby"}
        self.editor = None
        self.learn = None
        self.gate = None
        self.fail = False

    async def start(self, config):
        self.events.append("start:" + config.service)
        if self.gate:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("MIDI device unavailable")
        self.state = {"health": "ready"}
        return self.state

    async def stop(self):
        self.events.append("stop")
        self.state = {"health": "standby"}

    async def apply(self, config):
        self.events.append("hot")

    def status(self):
        return self.state.copy()

    async def open_editor(self, identifier, config):
        self.editor = identifier

    async def close_editor(self, identifier):
        self.editor = None

    async def arm_learn(self, identifier):
        self.learn = {"kind": "note", "number": 60, "channel": 1}

    def learn_result(self, identifier):
        return self.learn


class Network:
    def __init__(self, events):
        self.events = events
        self.fail = False

    async def cleanup(self):
        self.events.append("remove_vlan")

    async def prepare(self, profile):
        self.events.append("network_ip_ready")
        if self.fail:
            raise TimeoutError("DHCP deadline expired")
        return {"vlan": profile.vlan}


@pytest.fixture
async def rig(tmp_path):
    store = Store(tmp_path / "omi.sqlite3")
    library = Library(store)
    events = []
    host, network = Host(events), Network(events)
    coordinator = Coordinator(store, library, host, network)
    yield coordinator, library, events, host, network
    await coordinator.close()
    store.close()


def preset(library, service="midi", name="A", network=None):
    return library.save(SavePreset(name=name, config=PresetConfig(service=service, network=network or NetworkProfile())))


def apply(revision, **kwargs):
    return Command(action="apply", target=Target(preset_id=revision.preset_id, revision=revision.id), **kwargs)


async def execute(coordinator, command):
    accepted = coordinator.submit(command)
    await coordinator.task
    return coordinator.operation(accepted.id)


async def test_order_concurrency_duplicates_and_expiry(rig):
    coordinator, library, events, host, network = rig
    revision = preset(library)
    host.gate = asyncio.Event()
    command = apply(revision)
    first = coordinator.submit(command)
    assert first.status == "accepted"
    assert coordinator.submit(command).id == first.id
    with pytest.raises(RuntimeError, match="still running"):
        coordinator.submit(Command(action="standby"))
    await asyncio.sleep(.01)
    assert events == ["stop", "remove_vlan", "network_ip_ready", "start:midi"]
    host.gate.set()
    await coordinator.task
    assert coordinator.submit(command).status == "succeeded"
    assert events.count("start:midi") == 1
    with pytest.raises(RuntimeError, match="different arguments"):
        coordinator.submit(Command(id=command.id, action="standby"))
    with pytest.raises(ValueError, match="expired"):
        coordinator.submit(Command(action="standby", expires_at=time.time()-1))


async def test_failure_standby_persisted_and_no_previous_rollback(rig):
    coordinator, library, events, host, network = rig
    first = preset(library)
    await execute(coordinator, apply(first))
    events.clear()
    second = preset(library, "satellite", "B", NetworkProfile(vlan=100))
    network.fail = True
    result = await execute(coordinator, apply(second))
    assert result.status == "failed" and "DHCP" in result.error
    assert "start:satellite" not in events and "start:midi" not in events
    assert coordinator.state.target is None and coordinator.state.health == "error"
    assert coordinator.store.get("execution")["target"] is None
    await coordinator.restore()
    assert coordinator.state.target is None


async def test_hot_save_and_vlan_restart_keep_editor_muted(rig):
    coordinator, library, events, host, network = rig
    original = preset(library)
    await execute(coordinator, apply(original))
    editor = await coordinator.open_editor(EditorRequest(preset_id=original.preset_id, revision=original.id, route_editor=True))
    events.clear()
    config = original.config.model_copy(deep=True)
    config.midi.deadline_ms = 8
    revision = coordinator.save_preset(SavePreset(preset_id=original.preset_id, parents=[original.id], name="A", config=config, session_id=editor["id"]), "Pi")
    await coordinator.task
    assert events == ["hot"]
    assert host.editor == editor["id"]
    config.network.vlan = 100
    coordinator.save_preset(SavePreset(preset_id=revision.preset_id, parents=[revision.id], name="A", config=config, session_id=editor["id"]), "Pi")
    await coordinator.task
    assert events[1:] == ["stop", "remove_vlan", "network_ip_ready", "start:midi"]
    assert host.editor == editor["id"]
    await coordinator.close_editor(editor["id"])
    assert host.editor is None


async def test_library_update_does_not_apply_restore_pins_previous(rig):
    coordinator, library, events, host, network = rig
    original = preset(library)
    await execute(coordinator, apply(original))
    library.save(SavePreset(preset_id=original.preset_id, parents=[original.id], name="A newer", config=original.config))
    await coordinator.close()
    assert coordinator.store.get("execution")["target"]["revision"] == original.id
    fresh = Coordinator(coordinator.store, library, host, network)
    await fresh.restore()
    assert fresh.state.target.revision == original.id
    await fresh.close()


async def test_standby_edits_stay_standby_and_active_restriction(rig):
    coordinator, library, events, host, network = rig
    first = preset(library)
    second = preset(library, "satellite", "B")
    coordinator.save_preset(SavePreset(preset_id=second.preset_id, parents=[second.id], name="B", config=second.config), "local")
    assert not events and coordinator.state.target is None
    await execute(coordinator, apply(first))
    with pytest.raises(RuntimeError, match="running preset"):
        await coordinator.open_editor(EditorRequest(preset_id=second.preset_id, revision=second.id))
    with pytest.raises(RuntimeError, match="running preset"):
        coordinator.save_preset(SavePreset(preset_id=second.preset_id, parents=[second.id], name="B", config=second.config), "local")


async def test_operation_timeout_cleanup_not_connected_status(rig):
    coordinator, library, events, host, network = rig
    coordinator.operation_timeout = .02
    host.gate = asyncio.Event()
    result = await execute(coordinator, apply(preset(library)))
    assert result.status == "failed" and result.error == "TimeoutError"
    assert coordinator.state.health == "error"
    assert events[-2:] == ["stop", "remove_vlan"]
    assert not coordinator.busy


async def test_editor_lease_and_runtime_device_wait(rig):
    coordinator, library, events, host, network = rig
    await coordinator.restore()
    original = preset(library)
    await execute(coordinator, apply(original))
    host.state = {"health": "waiting", "detail": "Configured controller disconnected"}
    await asyncio.sleep(.3)
    assert coordinator.snapshot().health == "waiting"
    assert coordinator.state.target is not None
    editor = await coordinator.open_editor(EditorRequest(preset_id=original.preset_id, revision=original.id, route_editor=True))
    coordinator.editor["expires_at"] = 0
    await asyncio.sleep(.3)
    assert coordinator.editor is None and host.editor is None


async def test_power_keeps_restore_target_after_ordered_cleanup(rig):
    coordinator, library, events, host, network = rig
    async def power(action):
        events.append(action)
    coordinator.power = power
    original = preset(library)
    await execute(coordinator, apply(original))
    events.clear()
    result = await execute(coordinator, Command(action="reboot"))
    assert result.status == "succeeded"
    assert events == ["stop", "remove_vlan", "reboot"]
    assert coordinator.store.get("execution")["target"]["revision"] == original.id
