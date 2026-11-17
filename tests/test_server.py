from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import time

import httpx
import pytest

from omi.models import Command, PresetConfig, Revision, SavePreset
from omi.server.app import create_app


class AgentTransport:
    """Loopback protocol peer; no production endpoint or host setting is changed."""
    def __init__(self):
        self.revisions = []
        self.operations = {}
        self.commands = []
        self.calls = []
        self.fail_commands = False
        self.command_gate = None
        self.command_entered = asyncio.Event()
        self.state = {"target": None, "health": "standby", "operation": None}
        self.after_save_state = None

    async def __call__(self, request):
        self.calls.append((request.method, request.url.path, request.headers.get("X-OMI-Control")))
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        if path == "/api/sync":
            existing = {r["id"]: r for r in self.revisions}
            existing.update({r["id"]: r for r in body["revisions"]})
            self.revisions = list(existing.values())
            return httpx.Response(200, json={"revisions": self.revisions, "digest": "fake"})
        if path == "/api/presets/export":
            return httpx.Response(200, json={"revisions": self.revisions})
        if path == "/api/status":
            return httpx.Response(200, json={"state": self.state})
        if path == "/api/presets" and request.method == "POST":
            saved = Revision(preset_id=body['preset_id'],parents=body['parents'],name=body['name'],config=body['config']).model_dump(mode='json')
            self.revisions.append(saved)
            if self.after_save_state is not None:
                self.state=copy.deepcopy(self.after_save_state)
            return httpx.Response(200,json=saved)
        if path == "/api/commands":
            self.commands.append(body)
            self.command_entered.set()
            if self.command_gate:
                await self.command_gate.wait()
            operation = {"id": body["id"], "action": body["action"], "status": "accepted", "phase": "accepted"}
            self.operations[body["id"]] = operation
            if self.fail_commands:
                raise httpx.ReadTimeout("lost response", request=request)
            return httpx.Response(200, json=operation)
        if path.startswith("/api/operations/"):
            operation = self.operations.get(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json=operation) if operation else httpx.Response(404, json={"detail": "Unknown operation"})
        if path == "/api/midi/inputs":
            return httpx.Response(200, json={"inputs": ["Controlador elegido"]})
        if path == "/api/editors":
            return httpx.Response(200, json={"id": "editor-1", "expires_at": time.time()+30, "learn": None})
        return httpx.Response(404, json={"detail": "Unknown mock route"})


@pytest.fixture
async def central(tmp_path):
    peer = AgentTransport()
    clock = [time.time()]
    app = create_app(tmp_path, transport=httpx.MockTransport(peer), clock=lambda: clock[0], simulated=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://central") as client:
        yield app, client, peer, clock
    await app.state.central.close()


def heartbeat(serial="pi-a", *, target=None, operation=None, boot="boot-a", health="standby"):
    return {"serial": serial, "hostname": f"host-{serial}", "endpoint": f"http://{serial}:8081", "boot_id": boot,
            "state": {"target": target, "health": health, "operation": operation}, "library_digest": "client-has-library"}


async def onboard(client, serial="pi-a", **kwargs):
    response = await client.post("/api/heartbeat", json=heartbeat(serial))
    assert response.status_code == 200
    response = await client.post(f"/api/agents/{serial}/onboard", json=kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def save_revision(app, *, name="Show", parents=None, preset_id=None):
    return app.state.library.save(SavePreset(name=name, config=PresetConfig(service="midi"),
                                              parents=parents or [], preset_id=preset_id)).model_dump(mode="json")


async def test_discovery_manual_onboard_edit_and_atomic_swap(central):
    app, client, peer, clock = central
    response = await client.post("/api/heartbeat", json=heartbeat())
    assert response.json()["onboarded"] is False
    assert (await client.post("/api/agents/pi-a/commands", json={"action": "standby"})).status_code == 404
    a = await onboard(client)
    b = await onboard(client, "pi-b")
    assert (a["device_id"], b["device_id"]) == (1, 2)
    assert (await client.patch("/api/agents/pi-a", json={"device_id": 20, "name": "FOH"})).status_code == 200
    await client.patch("/api/agents/pi-a", json={"device_id": 2})
    rows = {a["serial"]: a for a in (await client.get("/api/agents")).json()}
    assert rows["pi-a"]["device_id"] == 2 and rows["pi-b"]["device_id"] == 20
    assert rows["pi-a"]["name"] == "FOH"
    c = await onboard(client, "pi-c")
    assert c["device_id"] == 1
    assert (await client.patch("/api/agents/pi-a", json={"device_id": 0})).status_code == 422
    assert len(peer.commands) == 0


async def test_registry_persists_but_restart_does_not_fabricate_online(tmp_path):
    peer = AgentTransport()
    first = create_app(tmp_path, transport=httpx.MockTransport(peer))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://central") as client:
        await onboard(client, name="FOH")
    await first.state.central.close()
    second = create_app(tmp_path, transport=httpx.MockTransport(peer))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second), base_url="http://central") as client:
        row = (await client.get("/api/agents")).json()[0]
        assert row["name"] == "FOH" and row["device_id"] == 1 and row["online"] is False
    await second.state.central.close()


async def test_server_restart_during_sending_recovers_404_without_permanent_busy_or_replay(tmp_path):
    peer=AgentTransport()
    first=create_app(tmp_path,transport=httpx.MockTransport(peer))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first),base_url='http://central') as client:
        await onboard(client)
        command=Command(action='standby').model_dump(mode='json')
        record={"id":command['id'],"command":command,"status":"sending","acknowledged":False,"sent_at":time.time(),"operation":None}
        first.state.central.agents['pi-a']['pending']=record
        first.state.store.put(f"server.command.pi-a.{command['id']}",record)
        first.state.central.persist()
    await first.state.central.close()
    second=create_app(tmp_path,transport=httpx.MockTransport(peer))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second),base_url='http://central') as client:
        assert second.state.central.agents['pi-a']['pending']['status']=='unknown'
        assert (await client.post('/api/heartbeat',json=heartbeat())).status_code==200
        await asyncio.gather(*tuple(second.state.central.tasks))
        row=(await client.get('/api/agents')).json()[0]
        assert row['busy'] is False
        assert row['last_command']['status']=='interrupted'
        assert peer.commands==[]
    await second.state.central.close()


async def test_expected_observed_accept_and_new_revision_is_not_discrepancy(central):
    app, client, peer, clock = central
    first = save_revision(app)
    target = {"preset_id": first["preset_id"], "revision": first["id"]}
    await client.post("/api/heartbeat", json=heartbeat(target=target, health="ready"))
    await client.post("/api/agents/pi-a/onboard", json={})
    second = save_revision(app, parents=[first["id"]], preset_id=first["preset_id"])
    response = await client.post("/api/heartbeat", json=heartbeat(target=target, health="ready"))
    assert response.json()["discrepancy"] is False
    response = await client.post("/api/heartbeat", json=heartbeat())
    assert response.json()["discrepancy"] is True
    assert peer.commands == []  # Reconnect never changes execution.
    response = await client.post("/api/agents/pi-a/accept")
    assert response.json()["expected"] is None and response.json()["discrepancy"] is False


async def test_offline_commands_and_group_have_no_queue(central):
    app, client, peer, clock = central
    await onboard(client)
    await onboard(client, "pi-b")
    app.state.central.agents["pi-b"]["last_seen"] -= 20
    command = Command(action="standby").model_dump(mode="json")
    assert (await client.post("/api/agents/pi-b/commands", json=command)).status_code == 503
    assert app.state.store.get(f"server.command.pi-b.{command['id']}") is None
    response = await client.post("/api/groups/commands", json={"serials": ["pi-a", "pi-b"], "command": command})
    results = response.json()["results"]
    assert results[0]["result"]["status"] == "accepted" and results[1]["skipped"] is True
    await client.post("/api/heartbeat", json=heartbeat("pi-b"))
    assert len(peer.commands) == 1


async def test_pending_blocks_before_network_ack_while_heartbeats_continue(central):
    app, client, peer, clock = central
    await onboard(client)
    peer.command_gate = asyncio.Event()
    command = Command(action="standby").model_dump(mode="json")
    sending = asyncio.create_task(client.post("/api/agents/pi-a/commands", json=command))
    await peer.command_entered.wait()
    hb = await client.post("/api/heartbeat", json=heartbeat())
    assert hb.status_code == 200
    assert (await client.get("/api/agents")).json()[0]["busy"] is True
    response = await client.post("/api/agents/pi-a/commands", json={"action": "reboot"})
    assert response.status_code == 409
    peer.command_gate.set()
    assert (await sending).json()["status"] == "accepted"
    assert len(peer.commands) == 1


async def test_duplicate_ids_and_mismatched_content(central):
    app, client, peer, clock = central
    await onboard(client)
    command = Command(action="standby").model_dump(mode="json")
    first = await client.post("/api/agents/pi-a/commands", json=command)
    second = await client.post("/api/agents/pi-a/commands", json=command)
    assert first.json() == second.json() and len(peer.commands) == 1
    command["action"] = "reboot"
    assert (await client.post("/api/agents/pi-a/commands", json=command)).status_code == 409


async def test_duplicate_id_without_explicit_expiry_is_still_idempotent(central):
    app, client, peer, clock = central
    await onboard(client)
    first = await client.post("/api/agents/pi-a/commands", json={"id": "operator-double-click", "action": "standby"})
    second = await client.post("/api/agents/pi-a/commands", json={"id": "operator-double-click", "action": "standby"})
    assert first.json() == second.json() and len(peer.commands) == 1


async def test_timeout_unknown_heartbeat_recovers_result_without_resend(central):
    app, client, peer, clock = central
    await onboard(client)
    revision = save_revision(app)
    target = {"preset_id": revision["preset_id"], "revision": revision["id"]}
    peer.fail_commands = True
    command = Command(action="apply", target=target).model_dump(mode="json")
    response = await client.post("/api/agents/pi-a/commands", json=command)
    assert response.json()["status"] == "unknown"
    assert (await client.get("/api/agents")).json()[0]["busy"] is True
    assert app.state.central.agents["pi-a"]["expected"] is None  # No acknowledgement yet.
    op = {**peer.operations[command["id"]], "status": "succeeded", "phase": "ready"}
    response = await client.post("/api/heartbeat", json=heartbeat(target=target, operation=op, health="ready"))
    assert response.json()["expected"] == target and response.json()["discrepancy"] is False
    row = (await client.get("/api/agents")).json()[0]
    assert row["busy"] is False and row["last_command"]["status"] == "succeeded"
    assert len(peer.commands) == 1


async def test_failed_apply_preserves_expected_and_deletion_blocks_offline(central):
    app, client, peer, clock = central
    await onboard(client)
    revision = save_revision(app)
    target = {"preset_id": revision["preset_id"], "revision": revision["id"]}
    command = Command(action="apply", target=target).model_dump(mode="json")
    assert (await client.post("/api/agents/pi-a/commands", json=command)).json()["status"] == "accepted"
    op = {**peer.operations[command["id"]], "status": "failed", "phase": "error", "error": "No DHCP"}
    response = await client.post("/api/heartbeat", json=heartbeat(operation=op, health="error"))
    assert response.json()["expected"] == target and response.json()["discrepancy"] is True
    clock[0] += 20
    response = await client.delete(f"/api/presets/{revision['preset_id']}")
    assert response.status_code == 409 and "pi-a" in response.text


async def test_empty_server_requires_explicit_import_and_never_wipes_client(central):
    app, client, peer, clock = central
    revision = Revision(name="Local", config=PresetConfig(service="satellite")).model_dump(mode="json")
    peer.revisions = [revision]
    await onboard(client)
    response = await client.post("/api/sync", json={"serial": "pi-a", "revisions": peer.revisions})
    assert response.json()["revisions"] == []
    assert peer.revisions == [revision]
    response = await client.post("/api/agents/pi-a/onboard", json={"import_presets": True})
    assert response.status_code == 200
    assert app.state.library.get(revision["id"]).name == "Local"
    assert peer.revisions == [revision]


async def test_replication_preserves_conflicts_and_explicit_resolution(central):
    app, client, peer, clock = central
    base = save_revision(app)
    left = Revision(preset_id=base["preset_id"], parents=[base["id"]], name="Izquierda", config=PresetConfig(service="midi"))
    right = Revision(preset_id=base["preset_id"], parents=[base["id"]], name="Derecha", config=PresetConfig(service="midi"))
    response = await client.post("/api/presets/import", json={"revisions": [left.model_dump(), right.model_dump()]})
    assert response.status_code == 200
    assert (await client.get("/api/presets")).json()[0]["conflict"] is True
    assert len((await client.get("/api/presets/export")).json()["revisions"]) == 3
    response = await client.post(f"/api/presets/{base['preset_id']}/resolve", json={"revision": right.id})
    assert set(response.json()["parents"]) == {left.id, right.id}
    assert response.json()["name"] == "Derecha"
    assert (await client.get("/api/presets")).json()[0]["conflict"] is False
    assert peer.commands == []


async def test_import_sync_and_replication_tombstones_cannot_delete_known_assignments(central):
    app, client, peer, clock = central
    base = save_revision(app)
    target = {"preset_id": base["preset_id"], "revision": base["id"]}
    await client.post('/api/heartbeat',json=heartbeat(target=target))
    await client.post('/api/agents/pi-a/onboard',json={})
    await onboard(client,'pi-b')
    tombstone = Revision(preset_id=base['preset_id'],parents=[base['id']],name='Show',config=PresetConfig(service='midi'),deleted=True).model_dump(mode='json')
    clock[0] += 20  # The assignment is protected even while its Pi is offline.
    response = await client.post('/api/presets/import',json={"revisions":[tombstone]})
    assert response.status_code == 409 and 'pi-a' in response.text
    response = await client.post('/api/sync',json={"serial":"pi-b","revisions":[tombstone]})
    assert response.status_code == 409
    assert len(app.state.library.export()) == 1
    peer.revisions = [base,tombstone]
    await client.post('/api/heartbeat',json=heartbeat('pi-b'))
    with pytest.raises(RuntimeError,match='pi-a'):
        await app.state.central.replicate(app.state.central.agents['pi-b'])
    assert len(app.state.library.export()) == 1
    # After the assignment and observed execution are both cleared, the same
    # immutable deletion is eligible for normal union replication.
    await client.post('/api/heartbeat',json=heartbeat())
    await client.post('/api/agents/pi-a/accept')
    response = await client.post('/api/presets/import',json={"revisions":[tombstone]})
    assert response.status_code == 200


async def test_resolving_deleted_conflict_head_is_blocked_if_preset_became_assigned(central):
    app, client, peer, clock = central
    base = save_revision(app)
    deleted = Revision(preset_id=base['preset_id'],parents=[base['id']],name='Show',config=PresetConfig(service='midi'),deleted=True)
    other = Revision(preset_id=base['preset_id'],parents=[base['id']],name='Show',config=PresetConfig(service='midi'))
    assert (await client.post('/api/presets/import',json={"revisions":[deleted.model_dump(),other.model_dump()]})).status_code == 200
    target={"preset_id":base['preset_id'],"revision":base['id']}
    await client.post('/api/heartbeat',json=heartbeat(target=target))
    await client.post('/api/agents/pi-a/onboard',json={})
    response=await client.post(f"/api/presets/{base['preset_id']}/resolve",json={"revision":deleted.id})
    assert response.status_code == 409 and 'pi-a' in response.text
    assert (await client.get('/api/presets')).json()[0]['conflict'] is True


async def test_reset_installation_preserves_library_and_factory_backup_has_revisions(central, tmp_path):
    app, client, peer, clock = central
    revision = save_revision(app)
    await onboard(client)
    assert (await client.post("/api/maintenance/reset-installation", json={})).status_code == 422
    response = await client.post("/api/maintenance/reset-installation", json={"confirm": True})
    assert response.status_code == 200
    assert (await client.get("/api/agents")).json() == []
    assert len(app.state.library.export()) == 1
    assert (await client.put('/api/settings',json={"offline_after":25,"request_timeout":45})).status_code==200
    response = await client.post("/api/maintenance/factory-reset", json={"confirm": True, "backup": False})
    assert response.status_code == 200
    assert response.json()["backup"] is not None
    assert app.state.library.export() == []
    assert app.state.central.offline_after==8
    assert app.state.central.client.timeout.read==10
    assert (await client.get('/api/settings')).json()['offline_after']==8
    with sqlite3.connect(response.json()["backup"]) as database:
        assert database.execute("SELECT count(*) FROM revisions").fetchone()[0] == 1
    assert peer.commands == []


async def test_whitelisted_remote_editor_and_inputs_use_control_header(central):
    app, client, peer, clock = central
    await onboard(client)
    revision = save_revision(app)
    response = await client.get("/api/agents/pi-a/midi/inputs")
    assert response.json()["inputs"] == ["Controlador elegido"]
    response = await client.post("/api/agents/pi-a/editors", json={"preset_id": revision["preset_id"], "revision": revision["id"], "route_editor": True})
    assert response.json()["id"] == "editor-1"
    assert all(header == "central" for method, path, header in peer.calls)
    assert (await client.get("/api/agents/pi-a/arbitrary-path")).status_code == 404


@pytest.mark.parametrize('failed',[False,True])
async def test_contextual_save_updates_expected_even_when_restart_temporarily_has_no_target(central,failed):
    app,client,peer,clock=central
    base=save_revision(app)
    target={"preset_id":base['preset_id'],"revision":base['id']}
    peer.state={"target":target,"health":"ready","operation":None}
    await client.post('/api/heartbeat',json=heartbeat(target=target,health='ready'))
    await client.post('/api/agents/pi-a/onboard',json={})
    # Network preparation has stopped the previous process and cleared its target.
    # The save is acknowledged while the accepted apply is still running, or has failed.
    peer.after_save_state={"target":None,"health":"error" if failed else "starting","busy":not failed,
                           "operation":{"id":"context-save-apply","action":"apply","status":"failed" if failed else "running","phase":"network preparation"}}
    response=await client.post('/api/agents/pi-a/presets',json={"preset_id":base['preset_id'],"parents":[base['id']],"name":"Show","config":{"service":"midi","network":{"vlan":30}}})
    assert response.status_code==200,response.text
    saved=response.json()
    row=(await client.get('/api/agents')).json()[0]
    assert row['expected']=={"preset_id":base['preset_id'],"revision":saved['id']}
    assert row['observed'] is None
    assert row['busy'] is (not failed)


async def test_endpoint_validation_and_expired_commands(central):
    app, client, peer, clock = central
    bad = heartbeat()
    bad["endpoint"] = "http://user:password@pi-a:8081"
    assert (await client.post("/api/heartbeat", json=bad)).status_code == 422
    await onboard(client)
    assert (await client.post("/api/agents/pi-a/commands", json={"action": "standby", "expires_at": 1})).status_code == 422
    assert peer.commands == []


async def test_failed_operation_with_incomplete_cleanup_remains_busy(central):
    app, client, peer, clock = central
    await onboard(client)
    data=heartbeat(operation={"id":"failed-cleanup","action":"apply","status":"failed","error":"cleanup failed"},health="error")
    data['state']['busy']=True
    assert (await client.post('/api/heartbeat',json=data)).status_code == 200
    assert (await client.get('/api/agents')).json()[0]['busy'] is True
    assert (await client.post('/api/agents/pi-a/commands',json={"action":"standby"})).status_code == 409


async def test_web_and_backup_are_served_without_external_assets(central):
    app, client, peer, clock = central
    html = await client.get("/")
    assert html.status_code == 200 and 'lang="es"' in html.text
    assert '<iframe' not in html.text and 'https://' not in html.text
    assert (await client.get("/assets/app.js")).status_code == 200
    save_revision(app)
    response = await client.get("/api/backup")
    assert response.status_code == 200 and response.content.startswith(b"SQLite format 3")


async def test_simulated_maintenance_never_calls_power_or_updater(central):
    app, client, peer, clock = central
    response = await client.post("/api/maintenance/update", json={})
    assert response.json()["simulated"] is True


async def test_settings_reload_restores_effective_values_and_constructor_overrides(tmp_path):
    settings={"offline_after":17,"request_timeout":23,"advertise":"http://saved:8080","beacon_interval":4}
    first=create_app(tmp_path,transport=httpx.MockTransport(AgentTransport()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first),base_url='http://central') as client:
        assert (await client.put('/api/settings',json=settings)).json()==settings
    await first.state.central.close()
    second=create_app(tmp_path,transport=httpx.MockTransport(AgentTransport()))
    assert second.state.central.offline_after==17
    assert second.state.central.client.timeout.read==23
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second),base_url='http://central') as client:
        assert (await client.get('/api/settings')).json()==settings
    await second.state.central.close()
    explicit=create_app(tmp_path,transport=httpx.MockTransport(AgentTransport()),offline_after=.5,
                        request_timeout=7,advertise='http://explicit:9000/',beacon_interval=3)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=explicit),base_url='http://central') as client:
        actual=(await client.get('/api/settings')).json()
        assert actual=={"offline_after":.5,"request_timeout":7,"advertise":"http://explicit:9000","beacon_interval":3}
        assert explicit.state.store.get('settings')==settings
        assert (await client.put('/api/settings',json={"advertise":"http://other:8080"})).status_code==409
        assert explicit.state.store.get('settings')==settings
    await explicit.state.central.close()


async def test_settings_validate_all_fields_before_mutating_live_values(central):
    app,client,peer,clock=central
    defaults=(await client.get('/api/settings')).json()
    assert set(defaults)=={'offline_after','request_timeout','advertise','beacon_interval'}
    invalid=[{"unknown_setting":1},{"offline_after":0},{"request_timeout":0},
             {"beacon_interval":0},{"request_timeout":True},{"offline_after":"NaN"},
             {"advertise":"https://central:8080"},{"advertise":"http://central:invalid"},
             {"advertise":"http://host with spaces:8080"},
             {"advertise":"http://user:secret@central:8080"},
             {"offline_after":20,"request_timeout":999}]
    for patch in invalid:
        response=await client.put('/api/settings',json=patch)
        assert response.status_code==422,(patch,response.text)
        assert (await client.get('/api/settings')).json()==defaults
        assert app.state.central.offline_after==8 and app.state.central.client.timeout.read==10
        assert app.state.store.get('settings') is None
    assert (await client.put('/api/settings',json={"offline_after":19})).status_code==200
    assert (await client.get('/api/settings')).json()=={**defaults,"offline_after":19}


async def test_no_beacon_never_resolves_an_advertise_address(tmp_path,monkeypatch):
    def forbidden():
        raise AssertionError('Disabled beacon must not inspect host addresses')
    monkeypatch.setattr('omi.discovery.local_ipv4',forbidden)
    app=create_app(tmp_path,beacon_enabled=False)
    async with app.router.lifespan_context(app):
        assert app.state.central.broadcaster is None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://central') as client:
            assert (await client.put('/api/settings',json={"advertise":"http://saved:8080","beacon_interval":3})).status_code==200
            assert (await client.post('/api/maintenance/factory-reset',json={"confirm":True,"backup":False})).status_code==200


async def test_beacon_saved_settings_live_reconfiguration_and_factory_defaults(tmp_path,monkeypatch):
    created=[]
    lookups=[]
    class FakeBeacon:
        def __init__(self,url,*,port,interval):
            self.url,self.port,self.interval=url,port,interval
            self.closed=False
            created.append(self)
        async def start(self):
            self.started=True
        async def close(self):
            self.closed=True
    monkeypatch.setattr('omi.discovery.BeaconBroadcaster',FakeBeacon)
    monkeypatch.setattr('omi.discovery.local_ipv4',lambda:lookups.append(True) or '127.0.0.1')
    seed=create_app(tmp_path)
    await seed.state.central.update_settings({"advertise":"http://saved:8765","beacon_interval":5,
                                              "offline_after":21,"request_timeout":30})
    await seed.state.central.close()
    app=create_app(tmp_path,beacon_enabled=True,beacon_port=19000,http_port=8765)
    async with app.router.lifespan_context(app):
        assert lookups==[]
        assert created[-1].url=='http://saved:8765' and created[-1].interval==5
        assert created[-1].port==19000
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://central') as client:
            response=await client.put('/api/settings',json={"advertise":"http://changed:8765","beacon_interval":7,"request_timeout":11})
            assert response.status_code==200
            assert created[0].closed is True
            assert created[-1].url=='http://changed:8765' and created[-1].interval==7
            assert app.state.central.client.timeout.read==11
            assert (await client.post('/api/maintenance/reset-installation',json={"confirm":True,"backup":False})).status_code==200
            assert created[-1].url=='http://changed:8765' and len(created)==2
            assert (await client.post('/api/maintenance/factory-reset',json={"confirm":True,"backup":False})).status_code==200
            assert (await client.get('/api/settings')).json()=={"offline_after":8,"request_timeout":10,"advertise":"","beacon_interval":2}
            assert created[1].closed is True
            assert created[-1].url=='http://127.0.0.1:8765' and created[-1].interval==2
            assert app.state.central.offline_after==8 and app.state.central.client.timeout.read==10
            assert lookups==[True]
    assert created[-1].closed is True


async def test_invalid_persisted_settings_are_reported_without_replacing_database(tmp_path):
    from omi.store import Store
    path=tmp_path/'omi.sqlite3'
    store=Store(path)
    store.put('settings',{"request_timeout":-1})
    store.close()
    with pytest.raises(ValueError):
        create_app(tmp_path)
    store=Store(path)
    assert store.get('settings')=={"request_timeout":-1}
    store.close()
