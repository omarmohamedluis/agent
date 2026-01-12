#!/usr/bin/env python3
from __future__ import annotations
import os, sys, json, socket, ipaddress, time, asyncio, tempfile, html, logging, concurrent.futures
from pathlib import Path
from typing import Any, Dict

# Asegurar que el directorio src esté en el path para encontrar omimidi_core
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Form, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pythonosc.udp_client import SimpleUDPClient
from datetime import datetime
import mido

from omimidi_core import push_map_to_server
from omimidi_utils import load_json, save_json

from omimidi_logger import get_logger
LOGGER = get_logger("omimidi.webui")

BASE_DIR         = Path(__file__).resolve().parents[1]
MAP_FILE         = BASE_DIR / "OMIMIDI_map.json"
LEARN_REQ_FILE   = BASE_DIR / "OMIMIDI_learn_request.json"
STATE_FILE       = BASE_DIR / "OMIMIDI_state.json"
RESTART_REQ_FILE = BASE_DIR / "OMIMIDI_restart.flag"

# Backend fijo (no editable)
mido.set_backend("mido.backends.rtmidi")

# Estado en memoria para evitar I/O excesivo
MEMORY_STATE = {}
LAST_EVENT_STATE = {}

app = FastAPI(title="OMIMIDI Web UI", version="0.6")

# Static files
STATIC_DIR = BASE_DIR / "web" / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Templates
templates = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))

STRUCTURE_PATH = Path(__file__).resolve().parents[3] / "data" / "structure.json"

# load_json y save_json ahora se importan de omimidi_utils

def get_map() -> Dict[str, Any]:
    return load_json(MAP_FILE, {
        "file_info": {
            "name": "default",
            "ui_port": 9001
        },
        "source": {
            "midi_input": ""
        },
        "net": {
            "vlan": 100,
            "vlan_active": False
        },
        "osc": {
            "ips": ["127.0.0.1"],
            "port": 1024
        },
        "routes": []
    })

import threading

def persist_map(data: Dict[str, Any]) -> None:
    # Asegurar estructura
    if "file_info" not in data: data["file_info"] = {}
    if "source" not in data: data["source"] = {}
    if "net" not in data: data["net"] = {}
    if "osc" not in data: data["osc"] = {}
    
    data["file_info"]["name"] = str(data["file_info"].get("name") or "default").strip()
    data["file_info"]["ui_port"] = int(data["file_info"].get("ui_port", 9001))
    data["net"]["vlan"] = int(data["net"].get("vlan", 100))
    data["net"]["vlan_active"] = bool(data["net"].get("vlan_active", False))
    data["osc"]["port"] = int(data["osc"].get("port", 1024))
    data["osc"]["ips"] = list(data["osc"].get("ips", ["127.0.0.1"]))
    
    # 1. Guardado local (bloqueante para asegurar consistencia)
    save_json(MAP_FILE, data)

    # 2. Tareas lentas (red, subprocess) en hilo secundario
    def _background_sync():
        try:
            push_map_to_server(data, source="midiwebui")
            
            # Notificar al cliente (OMI Agent) que la configuración ha cambiado
            import subprocess
            # El script de sincronización está en client/src/structure_sync.py
            sync_script = Path(__file__).resolve().parents[3] / "src" / "structure_sync.py"
            if sync_script.exists():
                subprocess.run(["python3", str(sync_script), "MIDI"], 
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            LOGGER.warning("Error en sincronización en segundo plano: %s", e)

    threading.Thread(target=_background_sync, daemon=True).start()

    LOGGER.info(
        "Mapa MIDI guardado (config=%s, midi_input=%s, VLAN=%s, VLAN_ACTIVE=%s, osc_port=%s, ui_port=%s, osc_ips=%s)",
        data["file_info"]["name"],
        data["source"].get("midi_input"),
        data["net"]["vlan"],
        data["net"]["vlan_active"],
        data["osc"]["port"],
        data["file_info"]["ui_port"],
        ", ".join(data["osc"]["ips"]),
    )


def read_learn_state() -> Dict[str, Any]:
    return load_json(LEARN_REQ_FILE, {})


def write_learn_state(data: Dict[str, Any]) -> None:
    save_json(LEARN_REQ_FILE, data)

# ---------- WS manager ----------
class WSManager:
    def __init__(self):
        self.active: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.active.add(ws)

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self.active.discard(ws)

    async def broadcast_json(self, data: dict):
        async with self._lock:
            dead = []
            for ws in self.active:
                try:
                    await ws.send_json(data)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.active.discard(ws)

ws_manager = WSManager()


# ---------- HTML ----------
def get_identity_host() -> str:
    data = load_json(str(STRUCTURE_PATH), {})
    host = data.get("identity", {}).get("host")
    if isinstance(host, str) and host.strip():
        return host.strip()
    return "unknown-host"


def get_template_context(active: str = "home", extra_data: dict = None) -> dict:
    """Genera el contexto base para las plantillas."""
    host_label = get_identity_host()
    context = {
        "request": None,  # Se reemplazará en cada ruta
        "active": active,
        "host": host_label,
        "title": f"OMIMIDI @ {host_label} Web UI",
        "nav_items": [
            ("home", "Home", "/"),
            ("config", "Configuración", "/config")
        ]
    }
    if extra_data:
        context.update(extra_data)
    return context


def render_routes_rows(data: Dict[str, Any]) -> str:
    rows = []
    routes = data.get("routes", [])
    total = len(routes)
    
    for i, r in enumerate(routes):
        midi_desc = "?"
        if r.get("type") == "note":
            midi_desc = f"NOTE {r.get('note')}"
        elif r.get("type") == "cc":
            ch = r.get("channel", "any")
            midi_desc = f"CC {r.get('cc')} ch {ch}"
        vtype = r.get("vtype", "float")
        extra = ""
        if vtype == "const" and "const" in r:
            extra = f" const={r['const']}"
        osc = str(r.get("osc", ""))
        osc_esc = html.escape(osc, quote=True)
        midi_esc = html.escape(str(midi_desc))
        vtype_esc = html.escape(f"{vtype}{extra}")
        
        # Move Buttons Logic
        move_buttons = ""
        if total > 1:
            up_disabled = "disabled" if i == 0 else ""
            down_disabled = "disabled" if i == total - 1 else ""
            
            move_buttons = (
                "<div style='display:flex; flex-direction:column; gap:2px; margin-right:5px;'>"
                f"<form method='post' action='/move_route' style='margin:0;'>"
                f"<input type='hidden' name='idx' value='{i}'/>"
                f"<input type='hidden' name='direction' value='up'/>"
                f"<button class='btn-icon small' {up_disabled} title='Subir'>▲</button>"
                "</form>"
                f"<form method='post' action='/move_route' style='margin:0;'>"
                f"<input type='hidden' name='idx' value='{i}'/>"
                f"<input type='hidden' name='direction' value='down'/>"
                f"<button class='btn-icon small' {down_disabled} title='Bajar'>▼</button>"
                "</form>"
                "</div>"
            )

        rows.append(
            (
                "<tr>"
                f"<td style='width:1px; white-space:nowrap;'>{move_buttons}</td>"
                f"<td>{i}</td>"
                f"<td>{midi_esc}</td>"
                f"<td>{osc_esc}</td>"
                f"<td>{vtype_esc}</td>"
                f"<td><span data-route='{i}' data-osc='{osc_esc}'>–</span></td>"
                "<td class='actions-cell'>"
                f"<a href='/edit/{i}' class='btn ghost' title='Editar'><span class='icon'>✏️</span></a>"
                f"<form method='post' action='/delete_route' style='display:inline; margin:0;' onsubmit=\"return confirm('¿Estás seguro de que quieres eliminar esta ruta?');\">"
                f"<input type='hidden' name='idx' value='{i}'/>"
                "<button class='btn ghost danger-text' title='Eliminar'><span class='icon'>🗑️</span></button>"
                "</form>"
                "</td>"
                "</tr>"
            )
        )
    if not rows:
        return "<tr><td colspan='7' class='muted' style='text-align:center;'>No hay rutas configuradas.</td></tr>"
    return "".join(rows)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    data = get_map()
    context = get_template_context(active="home")
    context["request"] = request
    # Prepare BODY_HTML by loading the index fragment and injecting rendered rows
    tmpl_path = BASE_DIR / "web" / "templates" / "index.html"
    try:
        raw = tmpl_path.read_text(encoding='utf-8')
    except Exception:
        raw = ''
    rows_html = render_routes_rows(data)
    body = raw.replace('{{ROUTES_HTML}}', rows_html)

    # Build brand and nav HTML
    brand = f"<strong>OMIMIDI</strong> — {get_identity_host()}"
    nav_links = ' '.join([f"<a class=\"nav-link{' active' if item[0]==context.get('active') else ''}\" href=\"{item[2]}\">{item[1]}</a>" for item in context.get('nav_items', [])])

    context.update({
        'BODY_HTML': body,
        'PAGE_TITLE': context.get('title'),
        'PAGE_ID': context.get('active'),
        'BRAND_HTML': brand,
        'NAV_LINKS': nav_links,
        'EXTRA_HEAD': '',
        'EXTRA_JS': ''
    })
    return templates.TemplateResponse('layout.html', context)


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    data = get_map()
    context = get_template_context(active="config")
    context.update({
        "request": request,
        "midi_inputs": mido.get_input_names(),
        "current_midi": data.get("source", {}).get("midi_input", ""),
        "config_name": data.get("file_info", {}).get("name", "default"),
        "osc_port": data.get("osc", {}).get("port", 1024),
        "osc_ips": ",".join(data.get("osc", {}).get("ips", ["127.0.0.1"])),
        "ui_port": data.get("file_info", {}).get("ui_port", 9001),
        "vlan": data.get("net", {}).get("vlan", 100),
        "vlan_active": data.get("net", {}).get("vlan_active", False)
    })
    
    # Build local variables for template formatting
    config_name = context.get('config_name', '')
    vlan = context.get('vlan', '')
    vlan_active = context.get('vlan_active', False)
    osc_port = context.get('osc_port', 1024)
    ui_port = context.get('ui_port', 9001)
    osc_ips = context.get('osc_ips', '')
    midi_inputs = context.get('midi_inputs', [])
    current_midi = context.get('current_midi', '')
    options = []
    for m in midi_inputs:
        sel = ' selected' if m == current_midi else ''
        options.append(f"<option value=\"{html.escape(m)}\"{sel}>{html.escape(m)}</option>")
    options = ''.join(options)

    body = f"""
<form id="configForm" method="post" action="/config/save" class="stack">
  <section class="card stack">
    <div class="section-title">
      <h2>Configuración General</h2>
    </div>
    
    <div class="config-grid">
      <div class="form-group">
        <label>Nombre del Preset</label>
        <input type="text" name="config_name" value="{config_name}" maxlength="64">
      </div>

      <div class="form-group">
        <label>VLAN</label>
        <div style="display: flex; align-items: center; gap: 12px;">
            <input type="number" name="vlan" value="{vlan}" min="1" max="4094" style="width: 80px;">
            <label class="checkbox-container" style="display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;">
                <input type="checkbox" name="vlan_active" value="true" {'checked' if vlan_active else ''} style="width: auto; margin: 0;">
                <span style="font-size: 14px; font-weight: 500;">Activar VLAN</span>
            </label>
        </div>
      </div>

                <div class="form-group full">
                    <label>Dispositivo MIDI</label>
                    <select name="midi_input">{options}</select>
            </div>

                <div class="form-group">
                <label>Puerto OSC</label>
                <input type="number" name="osc_port" value="{osc_port}" min="1" max="65535">
            </div>

            <div class="form-group">
                <label>Puerto WebUI</label>
                <input type="number" name="ui_port" value="{ui_port}" min="1" max="65535">
            </div>

            <div class="form-group full">
                <label>IPs OSC</label>
                <input type="text" name="osc_ips" value="{osc_ips}" placeholder="127.0.0.1, 192.168.1.100">
            </div>
    </div>
  </section>

  <div class="global-actions">
    <button type="submit" class="btn primary">Guardar Configuración</button>
    <button type="button" class="btn" id="pingBtn">Ping OSC (IGMP)</button>
    <button type="button" class="btn danger" id="reiniciarBtn">Reiniciar Servicio</button>
  </div>
</form>

<style>
.config-grid {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 15px;
}}
.full {{
  grid-column: 1 / -1;
}}
.form-group {{
  margin-bottom: 10px;
}}
.form-group label {{
  display: block;
  margin-bottom: 5px;
  font-weight: 500;
}}
.checkbox-container:hover {{
    color: var(--accent);
}}
.section-title {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}}
.mapping-form {{
  background: #f5f5f5;
  padding: 15px;
  border-radius: 5px;
  margin-bottom: 20px;
}}
.global-actions {{
  margin-top: 20px;
  padding: 15px;
  border-top: 1px solid #eee;
  text-align: center;
}}
.form-actions {{
  margin-top: 15px;
  display: flex;
  gap: 10px;
}}
.danger {{
  background: #dc3545;
  color: white;
}}
</style>
"""
    # Inject the built body into the shared layout so styles/scripts are applied
    brand = f"<strong>OMIMIDI</strong> — {get_identity_host()}"
    nav_links = ' '.join([f"<a class=\"nav-link{' active' if item[0]==context.get('active') else ''}\" href=\"{item[2]}\">{item[1]}</a>" for item in context.get('nav_items', [])])
    context.update({
        'BODY_HTML': body,
        'PAGE_TITLE': context.get('title'),
        'PAGE_ID': context.get('active'),
        'BRAND_HTML': brand,
        'NAV_LINKS': nav_links,
        'EXTRA_HEAD': '',
        'EXTRA_JS': ''
    })
    resp = templates.TemplateResponse('layout.html', context)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@app.get("/add", response_class=HTMLResponse)
async def add_route_landing(request: Request):
    return RedirectResponse("/add/manual", status_code=303)

@app.get("/add/manual", response_class=HTMLResponse)
async def add_route_manual_page(request: Request):
    context = get_template_context(active="add")
    context["request"] = request
    tmpl_path = BASE_DIR / "web" / "templates" / "add_manual.html"
    try:
        raw = tmpl_path.read_text(encoding='utf-8')
    except Exception:
        raw = ''
    brand = f"<strong>OMIMIDI</strong> — {get_identity_host()}"
    nav_links = ' '.join([f"<a class=\"nav-link{' active' if item[0]==context.get('active') else ''}\" href=\"{item[2]}\">{item[1]}</a>" for item in context.get('nav_items', [])])
    context.update({
        'BODY_HTML': raw,
        'PAGE_TITLE': context.get('title'),
        'PAGE_ID': "add",
        'BRAND_HTML': brand,
        'NAV_LINKS': nav_links,
        'EXTRA_HEAD': '',
        'EXTRA_JS': ''
    })
    return templates.TemplateResponse('layout.html', context)

@app.get("/add/learn", response_class=HTMLResponse)
async def add_route_learn_page(request: Request):
    return RedirectResponse("/add/manual", status_code=303)

@app.get("/edit/{idx}", response_class=HTMLResponse)
async def edit_route_page(idx: int, request: Request):
    data = get_map()
    routes = data.get("routes", [])
    if idx < 0 or idx >= len(routes):
        return RedirectResponse("/", status_code=303)
    
    route = routes[idx]
    context = get_template_context(active="edit")
    context.update({
        "request": request,
        "idx": idx,
        "route": route,
        "vtype": route.get("vtype", "float"),
        "rtype": route.get("type", "note"),
        "num": route.get("note") if route.get("type") == "note" else route.get("cc"),
        "channel": route.get("channel", ""),
        "osc": route.get("osc", ""),
        "const": route.get("const", "")
    })
    
    # Use add_manual.html but with edit context
    tmpl_path = BASE_DIR / "web" / "templates" / "add_manual.html"
    try:
        raw = tmpl_path.read_text(encoding='utf-8')
    except Exception:
        raw = ''
    
    # Simple template injection for edit mode
    raw = raw.replace('action="/add_route"', f'action="/edit/save/{idx}"')
    raw = raw.replace('id="formTitle">Añadir mapeo manual</h2>', f'id="formTitle">Editar mapeo #{idx}</h2>')
    
    # Prefill values
    raw = raw.replace('id="rtypeInput"', f'id="rtypeInput" data-value="{context["rtype"]}"')
    raw = raw.replace('id="numInput"', f'id="numInput" value="{context["num"]}"')
    raw = raw.replace('id="channelInput"', f'id="channelInput" value="{context["channel"]}"')
    raw = raw.replace('id="oscInput"', f'id="oscInput" value="{context["osc"]}"')
    raw = raw.replace('id="vtypeInput"', f'id="vtypeInput" data-value="{context["vtype"]}"')
    raw = raw.replace('id="constInput"', f'id="constInput" value="{context["const"]}"')
    
    brand = f"<strong>OMIMIDI</strong> — {get_identity_host()}"
    nav_links = ' '.join([f"<a class=\"nav-link{' active' if item[0]==context.get('active') else ''}\" href=\"{item[2]}\">{item[1]}</a>" for item in context.get('nav_items', [])])
    context.update({
        'BODY_HTML': raw,
        'PAGE_TITLE': f"Editar Mapeo #{idx}",
        'PAGE_ID': "edit",
        'BRAND_HTML': brand,
        'NAV_LINKS': nav_links,
        'EXTRA_HEAD': '',
        'EXTRA_JS': '<script>onReady(() => { document.querySelectorAll("select[data-value]").forEach(s => s.value = s.dataset.value); });</script>'
    })
    return templates.TemplateResponse('layout.html', context)

@app.post("/edit/save/{idx}")
async def edit_route_save(
    idx: int,
    rtype: str = Form(...),
    num: int = Form(...),
    channel: str = Form(""),
    osc: str = Form(...),
    vtype: str = Form(...),
    const: str = Form("")
):
    data = get_map()
    if idx < 0 or idx >= len(data["routes"]):
        return RedirectResponse("/", status_code=303)
    
    new_route = {
        "type": rtype,
        "osc": osc.strip(),
        "vtype": vtype
    }
    if rtype == "note":
        new_route["note"] = num
    else:
        new_route["cc"] = num
    
    if channel.strip():
        try:
            new_route["channel"] = int(channel)
        except ValueError:
            pass
            
    if vtype == "const" and const.strip():
        try:
            new_route["const"] = float(const)
        except ValueError:
            new_route["const"] = 1.0

    data["routes"][idx] = new_route
    persist_map(data)
    return RedirectResponse("/", status_code=303)
# ---------- WS / Push ----------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # no esperamos nada; mantener vivo
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)
    except Exception:
        await ws_manager.disconnect(ws)

@app.post("/push_batch")
async def push_batch(request: Request):
    """Recibe un lote de estados del core y los refleja en memoria + websockets."""
    try:
        data = await request.json()
        batch = data.get("batch", [])
        if not batch:
            return JSONResponse({"ok": True})

        for update in batch:
            route_idx = update.get("route_idx")
            if route_idx is not None:
                MEMORY_STATE[str(route_idx)] = update
            else:
                path = update.get("path")
                if path:
                    MEMORY_STATE[path] = update

        # Broadcast del lote completo
        await ws_manager.broadcast_json({"batch": batch})
        return JSONResponse({"ok": True})
    except Exception as e:
        LOGGER.exception("Error procesando push_batch: %s", e)
        return JSONResponse({"ok": False, "err": str(e)}, status_code=400)

@app.post("/push_state")
async def push_state(request: Request):
    """Mantenido por compatibilidad, redirige a push_batch."""
    try:
        payload = await request.json()
        return await push_batch_internal([payload])
    except Exception as e:
        return JSONResponse({"ok": False, "err": str(e)}, status_code=400)

async def push_batch_internal(batch: list):
    for update in batch:
        route_idx = update.get("route_idx")
        if route_idx is not None:
            MEMORY_STATE[str(route_idx)] = update
        else:
            path = update.get("path")
            if path:
                MEMORY_STATE[path] = update
    await ws_manager.broadcast_json({"batch": batch})
    return JSONResponse({"ok": True})

@app.get("/state")
def state():
    return JSONResponse(MEMORY_STATE)

# ---------- Learn ----------
@app.post("/push_last_event")
async def push_last_event(request: Request):
    global LAST_EVENT_STATE
    data = await request.json()
    event = data.get("event", {})
    LAST_EVENT_STATE = event
    # Opcional: Notificar vía WebSocket si se desea tiempo real extremo
    return {"ok": True}

@app.get("/learn_state")
async def learn_state():
    raw = read_learn_state()
    resp: Dict[str, Any] = {
        "armed": bool(raw.get("armed")),
        "osc": raw.get("osc", "/learn"),
        "vtype": raw.get("vtype", "float"),
        "candidate": raw.get("candidate"),
        "result": raw.get("result"),
        "last_event": LAST_EVENT_STATE # Add last_event to the response
    }
    if resp["vtype"] == "const":
        try:
            resp["const"] = float(raw.get("const", 1.0))
        except (TypeError, ValueError):
            resp["const"] = 1.0
    return JSONResponse(resp)

@app.post("/clear_learn_result")
def clear_learn_result():
    st = read_learn_state()
    st.pop("result", None)
    write_learn_state(st)
    return RedirectResponse("/add/learn", status_code=303)


@app.post("/arm_learn")
def arm_learn(osc: str = Form("/learn"), vtype: str = Form("float"), const: str = Form("")):
    existing = read_learn_state()
    prev_armed = bool(existing.get("armed"))
    osc_path = osc.strip() or "/learn"

    existing["armed"] = True
    existing["osc"] = osc_path
    existing["vtype"] = vtype
    if vtype == "const":
        try:
            existing["const"] = float(const)
        except (TypeError, ValueError):
            existing["const"] = 1.0
    else:
        existing.pop("const", None)
    existing.pop("result", None)
    if not prev_armed:
        existing.pop("candidate", None)

    write_learn_state(existing)
    return JSONResponse({"ok": True, "armed": True})


@app.post("/commit_learn")
def commit_learn(osc: str = Form(...), vtype: str = Form(...), const: str = Form(""), confirm: str = Form("")):
    st = read_learn_state()
    candidate = st.get("candidate")
    if not candidate:
        return JSONResponse({"ok": False, "reason": "no_candidate"}, status_code=400)

    osc_path = osc.strip() or "/learn"
    data = get_map()

    duplicates = [r for r in data.get("routes", []) if str(r.get("osc", "")).strip() == osc_path]
    confirmed = str(confirm or "").strip() == "1"
    if duplicates and not confirmed:
        return JSONResponse(
            {"ok": False, "reason": "duplicate", "osc": osc_path, "count": len(duplicates)},
            status_code=409
        )

    if candidate.get("type") == "note":
        route: Dict[str, Any] = {
            "type": "note",
            "note": int(candidate.get("note", 0)),
            "osc": osc_path,
            "vtype": vtype,
        }
    else:
        route = {
            "type": "cc",
            "cc": int(candidate.get("cc", 0)),
            "osc": osc_path,
            "vtype": vtype,
        }
        ch = candidate.get("channel")
        if ch is not None:
            try:
                route["channel"] = int(ch)
            except (TypeError, ValueError):
                pass

    if vtype == "const":
        try:
            route["const"] = float(const)
            st["const"] = float(const)
        except (TypeError, ValueError):
            route["const"] = 1.0
            st["const"] = 1.0
    else:
        st.pop("const", None)

    data["routes"].append(route)
    persist_map(data)

    st["armed"] = False
    st["osc"] = osc_path
    st["vtype"] = vtype
    st["result"] = {
        "label": candidate.get("label"),
        "route": route,
    }
    st.pop("candidate", None)
    write_learn_state(st)

    return JSONResponse({"ok": True, "redirect": "/"})


@app.get("/cancel_learn")
def cancel_learn():
    st = read_learn_state()
    st["armed"] = False
    st.pop("candidate", None)
    st.pop("result", None)
    write_learn_state(st)
    return RedirectResponse("/", status_code=303)


# ---------- MIDI / OSC / UI settings ----------
def request_restart_flag() -> None:
    with open(RESTART_REQ_FILE, "w") as f:
        f.write("restart")
    LOGGER.info("Se solicitó reinicio del servicio OMIMIDI.")

def restart_page(request: Request, message: str = "Reiniciando servicio OMIMIDI…") -> HTMLResponse:
    context = get_template_context(active="restart")
    context["request"] = request
    context["message"] = message
    
    tmpl_path = BASE_DIR / "web" / "templates" / "restart.html"
    try:
        raw = tmpl_path.read_text(encoding='utf-8')
        # Reemplazar variables manuales si no usamos Jinja para el fragmento
        body = raw.replace("{{MESSAGE}}", message)
    except Exception:
        body = f"<h2>{message}</h2><p>La página se actualizará automáticamente.</p>"

    brand = f"<strong>OMIMIDI</strong> — {get_identity_host()}"
    nav_links = ' '.join([f"<a class=\"nav-link{' active' if item[0]==context.get('active') else ''}\" href=\"{item[2]}\">{item[1]}</a>" for item in context.get('nav_items', [])])
    
    context.update({
        'BODY_HTML': body,
        'PAGE_TITLE': "Reiniciando...",
        'PAGE_ID': "restart",
        'BRAND_HTML': brand,
        'NAV_LINKS': nav_links,
        'EXTRA_HEAD': '',
        'EXTRA_JS': ''
    })
    
    resp = templates.TemplateResponse('layout.html', context)
    resp.headers['Cache-Control'] = 'no-store'
    return resp

@app.post("/config/save")
def save_config(
    midi_input: str = Form(""), 
    osc_port: str = Form(""),
    osc_ips: str = Form(""), 
    ui_port: str = Form(""), 
    config_name: str = Form(""),
    vlan: str = Form(""),
    vlan_active: bool = Form(False),
    map_type: str = Form(""),
    map_num: str = Form(""),
    map_channel: str = Form(""),
    map_osc: str = Form(""),
    map_vtype: str = Form(""),
    map_const: str = Form("")
):
    """Guarda la configuración general y opcionalmente añade un nuevo mapeo."""
    data = get_map()
    
    # Guardar configuración general
    if "source" not in data: data["source"] = {}
    if "file_info" not in data: data["file_info"] = {}
    if "net" not in data: data["net"] = {}
    if "osc" not in data: data["osc"] = {}

    data["source"]["midi_input"] = midi_input.strip()
    data["file_info"]["name"] = config_name.strip() or data["file_info"].get("name", "default")
    data["net"]["vlan_active"] = vlan_active

    # Validar y guardar VLAN
    try:
        vlan_num = int(vlan)
        if 1 <= vlan_num <= 4094:
            data["net"]["vlan"] = vlan_num
    except ValueError:
        data["net"]["vlan"] = 100

    # Validar y guardar puerto OSC
    try:
        osc_port_num = int(osc_port)
        if 1 <= osc_port_num <= 65535:
            data["osc"]["port"] = osc_port_num
    except ValueError:
        data["osc"]["port"] = 1024

    # Validar y guardar puerto UI
    try:
        ui_port_num = int(ui_port)
        if 1 <= ui_port_num <= 65535:
            data["file_info"]["ui_port"] = ui_port_num
    except ValueError:
        data["file_info"]["ui_port"] = 9001

    # Validar y guardar IPs OSC
    valid_ips = []
    for ip in [ip.strip() for ip in osc_ips.split(",") if ip.strip()]:
        try:
            ipaddress.ip_address(ip)
            valid_ips.append(ip)
        except ValueError:
            LOGGER.warning(f"IP inválida ignorada: {ip}")
            continue
    data["osc"]["ips"] = valid_ips or ["127.0.0.1"]

    # Procesar nuevo mapeo si se proporcionaron los datos necesarios
    if all([map_type, map_num, map_osc]):
        try:
            map_num_int = int(map_num)
            if not (0 <= map_num_int <= 127):
                raise ValueError("Número MIDI fuera de rango (0-127)")

            new_route = {
                "type": map_type,
                "osc": map_osc.strip(),
                "vtype": map_vtype or "float"
            }

            if map_type == "note":
                new_route["note"] = map_num_int
            else:
                new_route["cc"] = map_num_int

            # Procesar canal MIDI si se especificó
            if map_channel.strip():
                channel = int(map_channel)
                if 0 <= channel <= 15:
                    new_route["channel"] = channel

            # Procesar valor constante si el tipo es 'const'
            if map_vtype == "const" and map_const.strip():
                try:
                    new_route["const"] = float(map_const)
                except ValueError:
                    new_route["const"] = 1.0
                    LOGGER.warning(f"Valor constante inválido: {map_const}, usando 1.0")

            data["routes"].append(new_route)
            LOGGER.info("Nuevo mapeo añadido: %s", new_route)

        except ValueError as e:
            LOGGER.error("Error añadiendo mapeo: %s", e)

    # Guardar todos los cambios
    persist_map(data)
    
    # Solicitar reinicio si cambió el puerto UI
    if ui_port_num != get_map().get("ui_port", 9001):
        request_restart_flag()
        return restart_page("Reiniciando servicio con nueva configuración...")

    return RedirectResponse("/config", status_code=303)

    LOGGER.info("Solicitando reinicio del servicio...")
    request_restart_flag()
    return restart_page("Aplicando cambios y reiniciando...")

def scan_subnet(base_ip: str) -> List[str]:
    """Escanea una subred /24 buscando hosts activos."""
    from omimidi_core import is_reachable
    prefix = ".".join(base_ip.split(".")[:-1]) + "."
    found = []
    
    def check_ip(i):
        target = f"{prefix}{i}"
        if is_reachable(target, timeout=0.2):
            return target
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(check_ip, i) for i in range(1, 255)]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                found.append(res)
    return sorted(found)

@app.post("/ping_osc")
async def ping_osc():
    from omimidi_core import is_reachable
    data = get_map()
    osc_cfg = data.get("osc", {})
    ips = osc_cfg.get("ips", [])
    port = osc_cfg.get("port", 1024)
    
    results = []
    success_count = 0
    
    all_targets = []
    for ip_str in ips:
        try:
            addr = ipaddress.ip_address(ip_str)
            # Detección simple de broadcast (termina en .255 o es global)
            if ip_str.endswith(".255") or ip_str == "255.255.255.255":
                results.append({"ip": ip_str, "type": "broadcast", "info": "Detectado broadcast, iniciando descubrimiento..."})
                discovered = scan_subnet(ip_str)
                for d_ip in discovered:
                    if d_ip not in all_targets:
                        all_targets.append((d_ip, True)) # True = discovered
            else:
                all_targets.append((ip_str, False))
        except ValueError:
            all_targets.append((ip_str, False))

    for ip, is_discovered in all_targets:
        reachable = is_reachable(ip)
        if not reachable:
            results.append({"ip": ip, "ok": False, "error": "IP no alcanzable", "discovered": is_discovered})
            continue
            
        try:
            client = SimpleUDPClient(ip, port)
            client.send_message("/ping", [1.0])
            results.append({"ip": ip, "ok": True, "discovered": is_discovered})
            success_count += 1
        except Exception as e:
            results.append({"ip": ip, "ok": False, "error": str(e), "discovered": is_discovered})
            
    return {
        "ok": success_count > 0,
        "total": len(all_targets),
        "success": success_count,
        "details": results
    }

# ---------- Mapping CRUD ----------
@app.post("/add_route")
def add_route(rtype: str = Form(...), num: str = Form(...), channel: str = Form(""),
              osc: str = Form(...), vtype: str = Form(...), const: str = Form("")):
    data = get_map()
    try:
        n = int(num)
        if not (0 <= n <= 127):
            raise ValueError
    except ValueError:
        return RedirectResponse("/", status_code=303)

    if rtype == "note":
        r = {"type":"note", "note": n, "osc": osc, "vtype": vtype}
    else:
        r = {"type":"cc", "cc": n, "osc": osc, "vtype": vtype}
        if channel.strip() != "":
            try:
                ch = int(channel)
                if 0 <= ch <= 15:
                    r["channel"] = ch
            except ValueError:
                pass
    if vtype == "const" and const.strip() != "":
        try:
            r["const"] = float(const)
        except ValueError:
            r["const"] = 1.0

    data["routes"].append(r)
    persist_map(data)
    return RedirectResponse("/", status_code=303)

@app.post("/delete_route")
def delete_route(idx: int = Form(...)):
    data = get_map()
    try:
        data["routes"].pop(int(idx))
    except Exception:
        pass
    persist_map(data)
    return RedirectResponse("/", status_code=303)

# ---------- Restart ----------
@app.get("/restart")
async def restart_get():
    return RedirectResponse("/", status_code=303)

@app.post("/restart")
def restart(request: Request):
    request_restart_flag()
    return restart_page(request)

@app.post("/move_route")
def move_route(idx: int = Form(...), direction: str = Form(...)):
    data = get_map()
    routes = data.get("routes", [])
    idx = int(idx)
    
    if 0 <= idx < len(routes):
        if direction == "up" and idx > 0:
            routes[idx], routes[idx-1] = routes[idx-1], routes[idx]
            persist_map(data)
        elif direction == "down" and idx < len(routes) - 1:
            routes[idx], routes[idx+1] = routes[idx+1], routes[idx]
            persist_map(data)
            
    return RedirectResponse("/", status_code=303)
