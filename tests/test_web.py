"""Practical Chromium checks using real loopback HTTP and simulated service workers.

Run: python -m playwright install chromium; python -m pytest tests/test_web.py -q
No tests change host networking, contact show equipment, or request real power actions.
"""
from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest

from omi.models import PresetConfig, Revision
from omi.store import Store


def free_port():
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        return stream.getsockname()[1]


def wait_for(callback, *, timeout=20):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = callback()
            if last:
                return last
        except (httpx.HTTPError, KeyError, ValueError):
            pass
        time.sleep(.1)
    raise AssertionError(f"Condition not reached; last result: {last}")


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        try:
            instance = driver.chromium.launch(headless=True)
        except playwright.Error as exc:
            try:
                instance = driver.chromium.launch(headless=True,channel="chrome")
            except playwright.Error:
                pytest.skip(f"Chromium/Chrome is not installed: {exc}")
        yield instance
        instance.close()


@pytest.fixture
def installation(tmp_path, request):
    central_port, a_port, b_port = free_port(), free_port(), free_port()
    central_url = f"http://127.0.0.1:{central_port}"
    urls = {"central": central_url, "pi-a": f"http://127.0.0.1:{a_port}", "pi-b": f"http://127.0.0.1:{b_port}"}
    processes = []
    streams = []
    def cleanup():
        # Registered before startup so a failed readiness check also cleans up.
        for process in reversed(processes):
            process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in streams:
            stream.close()
    request.addfinalizer(cleanup)
    for role, port, serial in (("server", central_port, None), ("client", a_port, "pi-a"), ("client", b_port, "pi-b")):
        data = tmp_path / (serial or "central")
        data.mkdir()
        store = Store(data / "omi.sqlite3")
        if serial:
            store.put("settings", {"server_url": central_url, "discovery_wait": 0,
                                    "discovery_port": free_port(), "heartbeat_interval": .2,
                                    "offline_after": .5, "display": False})
        store.close()
        stream = (data / "test-process.log").open("w", encoding="utf-8")
        streams.append(stream)
        if serial:
            # Listener is deliberately inert: all communication in this test is loopback HTTP.
            code = (
                "import uvicorn; from pathlib import Path; "
                "from omi.client.runtime import ClientRuntime; from omi.client.app import create_app; "
                "from omi.discovery import BeaconListener; "
                "BeaconListener.start=lambda self: __import__('asyncio').sleep(0); "
                f"r=ClientRuntime(Path({str(data)!r}),simulated=True,serial={serial!r},endpoint={urls[serial]!r},port={port}); "
                f"uvicorn.run(create_app(r),host='127.0.0.1',port={port},access_log=False)"
            )
        else:
            code = ("import uvicorn; from pathlib import Path; from omi.server.app import create_app; "
                    f"uvicorn.run(create_app(Path({str(data)!r}),simulated=True),host='127.0.0.1',port={port},access_log=False)")
        process = subprocess.Popen([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                                   stdout=stream, stderr=subprocess.STDOUT)
        processes.append(process)
        wait_for(lambda: httpx.get(urls[serial or "central"]+"/api/status").status_code == 200)
    wait_for(lambda: len(httpx.get(central_url+"/api/agents").json()) == 2)
    yield urls, tmp_path


def assert_no_browser_errors(errors):
    assert errors == [], "\n".join(errors)


def test_central_browser_preset_remote_learn_save_muted_selective_apply_and_drafts(browser, installation):
    from playwright.sync_api import expect
    urls, directory = installation
    page = browser.new_page(viewport={"width": 1380, "height": 950})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(urls["central"])
        page.locator('[data-serial="pi-a"] [data-action="onboard"]').click()
        # Empty central library asks before importing; cancel means onboard without import.
        page.locator('#confirm-cancel').click()
        expect(page.locator('#agent-list [data-serial="pi-a"]')).to_be_visible()
        page.locator('[data-serial="pi-b"] [data-action="onboard"]').click()
        page.locator('#confirm-cancel').click()
        expect(page.locator('#agent-list [data-serial="pi-b"]')).to_be_visible()
        page.locator('[data-tab="library"]').click()
        page.locator('#new-preset').click()
        page.locator('#preset-name').fill('Ensayo MIDI')
        page.locator('#midi-input').fill('Simulated MIDI')
        page.locator('#add-destination').click()
        page.locator('#save-preset').click()
        expect(page.locator('#draft-status')).to_contain_text('Guardado')
        page.locator('#capture-agent').select_option('pi-a')
        page.locator('#refresh-inputs').click()
        page.locator('#add-route').click()
        expect(page.locator('#mute-status')).to_contain_text('Toda la salida OSC')
        page.locator('#learn').click()
        page.locator('#simulate-midi').click()
        expect(page.locator('#learn-result')).to_contain_text('Capturado: nota 60')
        # A second event cannot replace the first captured event.
        page.request.post(urls['central']+'/api/agents/pi-a/simulate/midi', data={"type":"control_change","control":12,"value":99,"channel":3})
        expect(page.locator('#route-number')).to_have_value('60')
        page.locator('#route-address').fill('/ensayo/go')
        session = page.evaluate('state.session.id')
        page.locator('#save-route-preset').click()
        expect(page.locator('#draft-status')).to_contain_text('Guardado')
        assert page.evaluate('state.routeOpen') is True
        assert page.evaluate('state.session.id') == session
        editor = page.request.get(urls['central']+f'/api/agents/pi-a/editors/{session}').json()
        assert editor['route_editor'] is True
        page.locator('#route-form button[type="submit"]').click()
        expect(page.locator('#route-editor')).not_to_be_visible()
        page.locator('#preset-name').fill('Borrador conservado')
        # Trigger state and library polling without replacing form controls.
        page.evaluate('poll()')
        expect(page.locator('#preset-name')).to_have_value('Borrador conservado')
        page.locator('#preset-name').fill('Ensayo MIDI')
        page.locator('summary').filter(has_text='Revisiones y aplicación').click()
        page.locator('[data-apply-target="pi-a"]').check()
        page.locator('#save-apply').click()
        expect(page.locator('#notice')).to_contain_text('Preset guardado')
        wait_for(lambda: next(a for a in httpx.get(urls['central']+'/api/agents').json() if a['serial']=='pi-a')['state']['health']=='ready')
        agents = {a['serial']:a for a in httpx.get(urls['central']+'/api/agents').json()}
        assert agents['pi-a']['state']['target'] is not None
        assert agents['pi-b']['state']['target'] is None
        page.locator('#cancel-editor').click()
        page.locator('[data-tab="devices"]').click()
        # Choosing a preset on a card survives the next operational poll.
        revision = next(p for p in httpx.get(urls['central']+'/api/presets').json() if p['name']=='Ensayo MIDI')['revision']['id']
        choice = page.locator('[data-serial="pi-b"] [data-preset]')
        choice.select_option(revision)
        page.evaluate('poll()')
        expect(choice).to_have_value(revision)
        page.locator('[data-serial="pi-a"] [data-action="edit-current"]').click()
        page.locator('#network-vlan').fill('31')
        page.locator('#save-preset').click()
        expect(page.locator('#draft-status')).to_contain_text('Guardado')
        wait_for(lambda: next(a for a in httpx.get(urls['central']+'/api/agents').json() if a['serial']=='pi-a')['state']['health']=='ready')
        row=next(a for a in httpx.get(urls['central']+'/api/agents').json() if a['serial']=='pi-a')
        assert row['expected']==row['observed'] and row['discrepancy'] is False
        page.locator('#cancel-editor').click()
        page.screenshot(path=str(directory/'central-browser.png'),full_page=True)
        assert_no_browser_errors(errors)
    finally:
        page.close()


def test_local_browser_satellite_edit_restriction_and_maintenance(browser, installation):
    from playwright.sync_api import expect
    urls, directory = installation
    page = browser.new_page(viewport={"width": 900, "height": 900})
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    try:
        # Unonboarded Pi remains autonomous despite an available server.
        page.goto(urls['pi-a'])
        page.locator('[data-tab="library"]').click()
        page.locator('#new-preset').click()
        page.locator('#preset-name').fill('Stream Deck ensayo')
        page.locator('#preset-service').select_option('satellite')
        page.locator('#satellite-host').fill('127.0.0.1')
        page.locator('#satellite-port').fill('16622')
        page.locator('#network-vlan').fill('30')
        page.locator('#network-mode').select_option('static')
        page.locator('#network-address').fill('192.168.30.10/24')
        page.locator('#save-preset').click()
        expect(page.locator('#draft-status')).to_contain_text('Guardado')
        page.locator('#cancel-editor').click()
        page.locator('[data-tab="devices"]').click()
        revision=httpx.get(urls['pi-a']+'/api/presets').json()[0]['revision']['id']
        page.locator('[data-preset]').select_option(revision)
        page.locator('[data-action="apply"]').click()
        wait_for(lambda: httpx.get(urls['pi-a']+'/api/status').json()['state']['health']=='ready')
        page.locator('[data-action="edit-current"]').click()
        page.locator('#satellite-port').fill('16623')
        page.locator('#save-preset').click()
        expect(page.locator('#draft-status')).to_contain_text('Guardado')
        page.locator('#cancel-editor').click()
        wait_for(lambda: httpx.get(urls['pi-a']+'/api/status').json()['state']['health']=='ready')
        page.locator('[data-tab="maintenance"]').click()
        page.locator('#load-settings').click()
        wait_for(lambda: 'server_url' in page.locator('#settings-json').input_value())
        page.locator('#read-logs').click()
        page.locator('#management-mode').select_option('static')
        page.locator('#management-address').fill('192.168.99.2/24')
        page.locator('#management-form button[type="submit"]').click()
        page.locator('#confirm-form button[type="submit"]').click()
        wait_for(lambda: (httpx.get(urls['pi-a']+'/api/status').json()['state'].get('operation') or {}).get('action')=='management')
        wait_for(lambda: httpx.get(urls['pi-a']+'/api/status').json()['state']['operation']['status']=='succeeded')
        assert httpx.get(urls['pi-a']+'/api/status').json()['state']['target'] is not None
        with page.expect_download() as download:
            page.locator('#backup').click()
        assert download.value.suggested_filename.endswith('.sqlite3')
        page.screenshot(path=str(directory/'local-browser.png'),full_page=True)
        assert_no_browser_errors(errors)
    finally:
        page.close()


def test_new_local_draft_survives_onboard_and_atomic_route_lease_switch(browser, installation):
    from playwright.sync_api import expect
    urls, directory = installation
    page = browser.new_page(viewport={"width": 1100, "height": 950})
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    try:
        page.goto(urls['pi-a'])
        page.locator('[data-tab="library"]').click()
        page.locator('#new-preset').click()
        page.locator('#preset-name').fill('Borrador antes de onboard')
        page.locator('#midi-input').fill('Simulated MIDI')
        first_session=page.evaluate('state.session.id')
        response=httpx.post(urls['central']+'/api/agents/pi-a/onboard',json={})
        assert response.status_code == 200
        wait_for(lambda: httpx.get(urls['pi-a']+'/api/status').json()['device_id']==1)
        page.evaluate('poll()')
        expect(page.locator('#preset-name')).to_have_value('Borrador antes de onboard')
        page.locator('#add-route').click()
        expect(page.locator('#mute-status')).to_contain_text('Toda la salida OSC')
        assert page.evaluate('state.session.id') == first_session
        page.locator('#learn').click()
        page.request.post(urls['pi-a']+'/api/simulate/midi',data={"type":"note_on","note":52,"velocity":80,"channel":1})
        wait_for(lambda: page.locator('#route-number').input_value()=='52')
        page.locator('#route-form button[type="submit"]').click()
        assert page.evaluate('state.session.id') == first_session
        page.locator('#save-preset').click()
        expect(page.locator('#draft-status')).to_contain_text('Guardado')
        assert httpx.get(urls['pi-a']+'/api/status').json()['state']['target'] is None
        page.locator('#cancel-editor').click()
        wait_for(lambda: httpx.get(urls['pi-a']+'/api/status').json()['local_control'] is False)
        page.locator('[data-tab="devices"]').click()
        page.evaluate('poll()')
        expect(page.locator('#connection')).to_have_text('Control central disponible')
        assert_no_browser_errors(errors)
    finally:
        page.close()


def test_central_browser_conflict_import_rename_duplicate_export_and_reset(browser, installation):
    from playwright.sync_api import expect
    urls, directory = installation
    central=urls['central']
    base=httpx.post(central+'/api/presets',json={"name":"Base de conflicto","config":{"service":"midi","midi":{"input":"Simulated MIDI"}}}).json()
    left=Revision(preset_id=base['preset_id'],parents=[base['id']],name='Rama izquierda',config=PresetConfig(service='midi'))
    right=Revision(preset_id=base['preset_id'],parents=[base['id']],name='Rama derecha',config=PresetConfig(service='midi'))
    incoming=directory/'conflicting-library.json'
    incoming.write_text(json.dumps({"revisions":[left.model_dump(),right.model_dump()]}),encoding='utf-8')
    page=browser.new_page(viewport={"width":1200,"height":950})
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    try:
        page.goto(central)
        page.locator('[data-tab="library"]').click()
        page.locator('#import-library').set_input_files(incoming)
        expect(page.locator('#preset-list .badge')).to_have_text('Conflicto')
        page.locator('[data-action="resolve"]').click()
        page.locator(f'[data-resolve-id="{right.id}"]').click()
        expect(page.locator('#preset-list h2')).to_have_text('Rama derecha')
        page.once('dialog',lambda dialog:dialog.accept('Renombrado'))
        page.locator('[data-action="rename"]').click()
        expect(page.locator('#preset-list h2')).to_have_text('Renombrado')
        assert httpx.get(central+'/api/presets').json()[0]['preset_id']==base['preset_id']
        page.locator('[data-action="duplicate"]').click()
        page.locator('#preset-name').fill('Copia independiente')
        page.locator('#save-preset').click()
        expect(page.locator('#draft-status')).to_contain_text('Guardado')
        page.locator('#cancel-editor').click()
        expect(page.locator('#preset-list article')).to_have_count(2)
        with page.expect_download() as downloaded:
            page.locator('#export-library').click()
        assert downloaded.value.suggested_filename=='omi-biblioteca.json'
        copy=page.locator('#preset-list article').filter(has=page.locator('h2',has_text='Copia independiente'))
        copy.locator('[data-action="delete"]').click()
        page.locator('#confirm-form button[type="submit"]').click()
        expect(page.locator('#preset-list article')).to_have_count(1)
        for serial in ('pi-a','pi-b'):
            assert httpx.post(central+f'/api/agents/{serial}/onboard',json={}).status_code==200
        page.locator('[data-tab="maintenance"]').click()
        page.locator('#load-settings').click()
        wait_for(lambda:'offline_after' in page.locator('#settings-json').input_value())
        settings=json.loads(page.locator('#settings-json').input_value())
        settings['offline_after']=9
        page.locator('#settings-json').fill(json.dumps(settings))
        page.locator('#save-settings').click()
        expect(page.locator('#notice')).to_have_text('Ajustes guardados.')
        page.locator('#reset-installation').click()
        page.locator('#confirm-phrase').fill('reset-installation')
        page.locator('#confirm-form button[type="submit"]').click()
        expect(page.locator('#maintenance-result')).to_contain_text('"ok": true')
        assert all(not a['onboarded'] for a in httpx.get(central+'/api/agents').json())
        assert len([p for p in httpx.get(central+'/api/presets').json() if not p['deleted']])==1
        assert_no_browser_errors(errors)
    finally:
        page.close()
