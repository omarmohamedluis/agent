import asyncio
import time

import httpx
import pytest

from omi.client.app import create_app as client_app
from omi.client.runtime import ClientRuntime
from omi.models import Command, PresetConfig, SavePreset, Target
from omi.server.app import create_app as server_app


class Router(httpx.AsyncBaseTransport):
    def __init__(self):
        self.apps = {}
        self.connected = True

    async def handle_async_request(self, request):
        if not self.connected:
            raise httpx.ConnectError("Simulated cable/server loss", request=request)
        return await httpx.ASGITransport(app=self.apps[request.url.host]).handle_async_request(request)


class Listener:
    def __init__(self, url="http://central"):
        self.url = url

    async def start(self):
        pass

    async def wait(self, timeout):
        return self.url

    async def close(self):
        pass


async def eventually(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Condition did not become true before deadline")
        await asyncio.sleep(.03)


@pytest.fixture
async def system(tmp_path):
    router = Router()
    server = server_app(tmp_path / "server", transport=router, simulated=True, offline_after=.5)
    runtime = ClientRuntime(tmp_path / "client", simulated=True, serial="pi1", transport=router,
                            endpoint="http://pi1", listener=Listener(),
                            settings={"heartbeat_interval": .1, "offline_after": .5, "discovery_wait": 0, "display": False})
    client = client_app(runtime, manage_lifecycle=False)
    router.apps = {"central": server, "pi1": client}
    central_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://central")
    local_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=client), base_url="http://pi1")
    try:
        yield runtime, server.state.central, central_http, local_http, router
    finally:
        await runtime.close()
        await server.state.central.close()
        await central_http.aclose()
        await local_http.aclose()


def initial_preset(runtime):
    config = PresetConfig(service="midi")
    config.midi.input = "Simulated MIDI"
    revision = runtime.library.save(SavePreset(name="Show A", config=config))
    target = Target(preset_id=revision.preset_id, revision=revision.id)
    runtime.store.put("execution", {"target": target.model_dump(), "error": ""})
    return revision, target


async def test_boot_with_server_restores_local_then_library_sync_does_not_apply(system):
    runtime, central, server, local, router = system
    original, target = initial_preset(runtime)
    await runtime.start()
    await eventually(lambda: not runtime.booting)
    assert runtime.coordinator.state.target == target
    assert central.agents["pi1"]["onboarded"] is False
    response = await server.post("/api/agents/pi1/onboard", json={"name": "FOH", "import_presets": True})
    assert response.status_code == 200, response.text
    await eventually(lambda: runtime.server.onboarded)
    await server.post("/api/agents/pi1/accept")
    await eventually(lambda: not runtime.local_control)
    newer = central.library.save(SavePreset(preset_id=original.preset_id, parents=[original.id], name="Show A", config=original.config))
    await eventually(lambda: any(r["id"] == newer.id for r in runtime.library.export()))
    assert runtime.coordinator.state.target == target
    assert not central.view(central.agents["pi1"])["discrepancy"]
    assert (await local.get("/")).status_code == 423
    assert (await local.get("/api/status")).status_code == 200
    # The always-on control channel remains usable after human UI is disabled.
    command = Command(action="apply", target=Target(preset_id=original.preset_id, revision=newer.id))
    response = await server.post("/api/agents/pi1/commands", json=command.model_dump())
    assert response.status_code == 200, response.text
    await eventually(lambda: runtime.coordinator.state.target.revision == newer.id)


async def test_offline_edit_conflict_reconnect_and_local_session_kept_until_close(system):
    runtime, central, server, local, router = system
    original, target = initial_preset(runtime)
    await runtime.start()
    await eventually(lambda: not runtime.booting)
    await server.post("/api/agents/pi1/onboard", json={"import_presets": True})
    await server.post("/api/agents/pi1/accept")
    await eventually(lambda: not runtime.local_control)
    router.connected = False
    await eventually(lambda: runtime.local_control)
    assert runtime.coordinator.state.target == target
    editor_response = await local.post("/api/editors", json={"preset_id": original.preset_id, "revision": original.id})
    assert editor_response.status_code == 200, editor_response.text
    editor = editor_response.json()
    config = original.config.model_copy(deep=True)
    config.midi.deadline_ms = 8
    saved = await local.post("/api/presets", json=SavePreset(preset_id=original.preset_id, parents=[original.id], name="Show A", config=config, session_id=editor["id"]).model_dump())
    assert saved.status_code == 200, saved.text
    await runtime.coordinator.task
    offline_revision = saved.json()["id"]
    central.library.save(SavePreset(preset_id=original.preset_id, parents=[original.id], name="Show A", config=original.config))
    router.connected = True
    await eventually(lambda: runtime.connected and runtime.server.discrepancy)
    await eventually(lambda: central.library.list()[0]["conflict"])
    assert runtime.coordinator.state.target.revision == offline_revision
    await server.post("/api/agents/pi1/accept")
    await eventually(lambda: not runtime.server.discrepancy)
    assert runtime.local_control  # operator's existing draft must not disappear
    await local.delete(f"/api/editors/{editor['id']}")
    assert not runtime.local_control


async def test_remote_editor_learn_silence_and_hot_save_entire_http_path(system):
    runtime, central, server, local, router = system
    original, target = initial_preset(runtime)
    await runtime.start()
    await eventually(lambda: not runtime.booting)
    await server.post("/api/agents/pi1/onboard", json={"import_presets": True})
    await server.post("/api/agents/pi1/accept")
    await eventually(lambda: not runtime.local_control)
    prefix = "/api/agents/pi1"
    response = await server.post(prefix + "/editors", json={"preset_id": original.preset_id, "revision": original.id, "route_editor": True})
    assert response.status_code == 200, response.text
    editor = response.json()
    assert runtime.host.status()["muted"]
    assert (await server.post(prefix + f"/editors/{editor['id']}/learn")).status_code == 200
    await runtime.host.inject_midi({"type": "note_on", "note": 60, "channel": 0, "velocity": 100})
    await eventually(lambda: runtime.host.learn_result(editor["id"]) is not None)
    preview = (await server.get(prefix + f"/editors/{editor['id']}")).json()["learn"]
    assert preview is not None
    config = original.config.model_copy(deep=True)
    config.midi.deadline_ms = 9
    saved = await server.post(prefix + "/presets", json=SavePreset(preset_id=original.preset_id, parents=[original.id], name="Show A", config=config, session_id=editor["id"]).model_dump())
    assert saved.status_code == 200, saved.text
    await runtime.coordinator.task
    assert runtime.host.status()["muted"]
    assert (await server.delete(prefix + f"/editors/{editor['id']}")).status_code == 200
    assert runtime.host.status()["muted"] is False


async def test_no_server_boot_recovers_local_and_standby_stays_standby(tmp_path):
    runtime = ClientRuntime(tmp_path, simulated=True, serial="alone", listener=Listener(None),
                            settings={"discovery_wait": 0, "display": False})
    original, target = initial_preset(runtime)
    try:
        await runtime.start()
        await eventually(lambda: not runtime.booting)
        assert runtime.coordinator.state.target == target
        assert not runtime.connected and runtime.local_control
        runtime.coordinator.submit(Command(action="standby"))
        await runtime.coordinator.task
    finally:
        await runtime.close()
    restarted = ClientRuntime(tmp_path, simulated=True, serial="alone", listener=Listener(None), settings={"discovery_wait": 0, "display": False})
    try:
        await restarted.start()
        await eventually(lambda: not restarted.booting)
        assert restarted.coordinator.state.target is None
        assert restarted.coordinator.state.health == "standby"
    finally:
        await restarted.close()


async def test_heartbeat_continues_while_network_operation_is_blocked(system):
    runtime, central, server, local, router = system
    original, target = initial_preset(runtime)
    await runtime.start()
    await eventually(lambda: not runtime.booting)
    await server.post("/api/agents/pi1/onboard", json={"import_presets": True})
    await server.post("/api/agents/pi1/accept")
    await eventually(lambda: not runtime.local_control)
    gate = asyncio.Event()
    real_prepare = runtime.network.prepare
    async def blocked_prepare(profile):
        await gate.wait()
        return await real_prepare(profile)
    runtime.network.prepare = blocked_prepare
    config = original.config.model_copy(deep=True)
    config.network.vlan = 100
    newer = central.library.save(SavePreset(preset_id=original.preset_id, parents=[original.id], name="Show A", config=config))
    response = await server.post("/api/agents/pi1/commands", json=Command(action="apply", target=Target(preset_id=newer.preset_id, revision=newer.id)).model_dump())
    assert response.status_code == 200
    await asyncio.sleep(.8)  # longer than both roles' configured offline threshold
    observed = central.view(central.agents["pi1"])
    assert observed["online"] and observed["busy"]
    assert runtime.connected
    rejected = await server.post("/api/agents/pi1/commands", json=Command(action="standby").model_dump())
    assert rejected.status_code == 409
    gate.set()
    await runtime.coordinator.task
    await eventually(lambda: not central.busy(central.agents["pi1"]))
    assert runtime.coordinator.state.target.revision == newer.id
