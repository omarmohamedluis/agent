#!/usr/bin/env python3
from __future__ import annotations
import os, json, socket, ipaddress, time, asyncio, tempfile, html, logging
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Form, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pythonosc.udp_client import SimpleUDPClient
from datetime import datetime
import mido

from omimidi_core import push_map_to_server

LOGGER = logging.getLogger("omimidi.webui")

BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
MAP_FILE         = os.path.join(BASE_DIR, "OMIMIDI_map.json")
LEARN_REQ_FILE   = os.path.join(BASE_DIR, "OMIMIDI_learn_request.json")
STATE_FILE       = os.path.join(BASE_DIR, "OMIMIDI_state.json")
RESTART_REQ_FILE = os.path.join(BASE_DIR, "OMIMIDI_restart.flag")

# Backend fijo (no editable)
mido.set_backend("mido.backends.rtmidi")

app = FastAPI(title="OMIMIDI Web UI", version="0.6")

# Static files
STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Templates
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "web" / "templates"))

STRUCTURE_PATH = Path(__file__).resolve().parents[2] / "agent_pi" / "data" / "structure.json"

# ---------- utils JSON ----------
def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: str, data: Any) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory or None, prefix=os.path.basename(path) + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        except Exception as exc:
            LOGGER.debug("No se pudo borrar archivo temporal %s: %s", tmp_path, exc)

def get_map() -> Dict[str, Any]:
    return load_json(MAP_FILE, {
        "midi_input": "",
        "osc_port": 1024,
        "osc_ips": ["127.0.0.1"],
        "ui_port": 9001,
        "routes": [],
        "config_name": "default",
    })

def persist_map(data: Dict[str, Any]) -> None:
    config_name = str(data.get("config_name") or "").strip() or "default"
    data["config_name"] = config_name
    data["osc_port"] = int(data.get("osc_port", 1024))
    data["ui_port"] = int(data.get("ui_port", 9001))
    data["osc_ips"] = list(data.get("osc_ips", ["127.0.0.1"]))
    save_json(MAP_FILE, data)
    push_map_to_server(data, source="midiwebui")
    LOGGER.info(
        "Mapa MIDI guardado (config=%s, midi_input=%s, osc_port=%s, ui_port=%s, osc_ips=%s)",
        config_name,
        data.get("midi_input"),
        data.get("osc_port"),
        data.get("ui_port"),
        ", ".join(data.get("osc_ips") or []),
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
    for i, r in enumerate(data.get("routes", [])):
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
        rows.append(
            (
                "<tr>"
                f"<td>{i}</td>"
                f"<td>{midi_esc}</td>"
                f"<td>{osc_esc}</td>"
                f"<td>{vtype_esc}</td>"
                f"<td><span data-route='{i}' data-osc='{osc_esc}'>–</span></td>"
                "<td>"
                "<form method='post' action='/delete_route' style='display:inline;'>"
                f"<input type='hidden' name='idx' value='{i}'/>"
                "<button class='btn'>Eliminar</button>"
                "</form>"
                "</td>"
                "</tr>"
            )
        )
    if not rows:
        return "<tr><td colspan='6' class='muted' style='text-align:center;'>No hay rutas configuradas.</td></tr>"
    return "".join(rows)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    data = get_map()
    context = get_template_context(active="home")
    context["request"] = request
    # Prepare BODY_HTML by loading the index fragment and injecting rendered rows
    tmpl_path = Path(__file__).resolve().parent / "web" / "templates" / "index.html"
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
        "current_midi": data.get("midi_input", ""),
        "config_name": data.get("config_name", "default"),
        "osc_port": data.get("osc_port", 1024),
        "osc_ips": ",".join(data.get("osc_ips", ["127.0.0.1"])),
        "ui_port": data.get("ui_port", 9001),
        "vlan": data.get("vlan", 100),
        "routes": data.get("routes", [])
    })
    
    # Build local variables for template formatting
    config_name = context.get('config_name', '')
    vlan = context.get('vlan', '')
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
        <input type="number" name="vlan" value="{vlan}" min="1" max="4094">
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

  <section class="card stack">
    <div class="section-title">
      <h2>Mapeo MIDI <button type="button" class="btn primary" id="addBtn">+ Añadir Mapeo</button></h2>
    </div>

    <!-- Formulario de Mapeo (oculto por defecto) -->
    <div id="mappingForm" class="mapping-form" style="display:none;">
      <div class="form-grid">
        <div>
          <label>Tipo</label>
          <select name="map_type">
            <option value="note">Note</option>
            <option value="cc">CC</option>
          </select>
        </div>
        <div>
          <label>Nota/CC</label>
          <input type="number" name="map_num" min="0" max="127">
        </div>
        <div>
          <label>Canal</label>
          <input type="number" name="map_channel" min="0" max="15">
        </div>
        <div class="full">
          <label>Ruta OSC</label>
          <input type="text" name="map_osc" placeholder="/ruta/osc">
        </div>
        <div>
          <label>Tipo de valor</label>
          <select name="map_vtype">
            <option value="float">Float (0-1)</option>
            <option value="int">Int (0-127)</option>
            <option value="bool">Bool</option>
            <option value="const">Const</option>
          </select>
        </div>
        <div id="constValueField" style="display:none;">
          <label>Valor constante</label>
          <input type="text" name="map_const" placeholder="1.0">
        </div>
      </div>
      <div class="form-actions">
        <button type="button" class="btn" id="learnBtn">LEARN</button>
        <button type="button" class="btn" onclick="cancelMapping()">Cancelar</button>
      </div>
    </div>

    <!-- Lista de Mapeos -->
    <div class="table-wrap">
      <table class="routes-table">
        <tr>
          <th>#</th>
          <th>MIDI</th>
          <th>OSC Path</th>
          <th>Valor</th>
          <th>Último</th>
          <th></th>
        </tr>
        {render_routes_rows(data)}
      </table>
    </div>
  </section>

  <div class="global-actions">
    <button type="submit" class="btn primary">Guardar Todos los Cambios</button>
    <button type="button" class="btn" id="pingBtn">Ping OSC</button>
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
    context = get_template_context(active="add")
    context["request"] = request
    tmpl_path = Path(__file__).resolve().parent / "web" / "templates" / "add.html"
    try:
        raw = tmpl_path.read_text(encoding='utf-8')
    except Exception:
        raw = ''
    brand = f"<strong>OMIMIDI</strong> — {get_identity_host()}"
    nav_links = ' '.join([f"<a class=\"nav-link{' active' if item[0]==context.get('active') else ''}\" href=\"{item[2]}\">{item[1]}</a>" for item in context.get('nav_items', [])])
    context.update({
        'BODY_HTML': raw,
        'PAGE_TITLE': context.get('title'),
        'PAGE_ID': context.get('active'),
        'BRAND_HTML': brand,
        'NAV_LINKS': nav_links,
        'EXTRA_HEAD': '',
        'EXTRA_JS': ''
    })
    return templates.TemplateResponse('layout.html', context)

@app.get("/add/manual", response_class=HTMLResponse)
async def add_route_manual_page(request: Request):
    context = get_template_context(active="add")
    context["request"] = request
    tmpl_path = Path(__file__).resolve().parent / "web" / "templates" / "add_manual.html"
    try:
        raw = tmpl_path.read_text(encoding='utf-8')
    except Exception:
        raw = ''
    brand = f"<strong>OMIMIDI</strong> — {get_identity_host()}"
    nav_links = ' '.join([f"<a class=\"nav-link{' active' if item[0]==context.get('active') else ''}\" href=\"{item[2]}\">{item[1]}</a>" for item in context.get('nav_items', [])])
    context.update({
        'BODY_HTML': raw,
        'PAGE_TITLE': context.get('title'),
        'PAGE_ID': context.get('active'),
        'BRAND_HTML': brand,
        'NAV_LINKS': nav_links,
        'EXTRA_HEAD': '',
        'EXTRA_JS': ''
    })
    return templates.TemplateResponse('layout.html', context)

@app.get("/add/learn", response_class=HTMLResponse)
async def add_route_learn_page(request: Request):
    context = get_template_context(active="add")
    context["request"] = request
    tmpl_path = Path(__file__).resolve().parent / "web" / "templates" / "add_learn.html"
    try:
        raw = tmpl_path.read_text(encoding='utf-8')
    except Exception:
        raw = ''
    brand = f"<strong>OMIMIDI</strong> — {get_identity_host()}"
    nav_links = ' '.join([f"<a class=\"nav-link{' active' if item[0]==context.get('active') else ''}\" href=\"{item[2]}\">{item[1]}</a>" for item in context.get('nav_items', [])])
    context.update({
        'BODY_HTML': raw,
        'PAGE_TITLE': context.get('title'),
        'PAGE_ID': context.get('active'),
        'BRAND_HTML': brand,
        'NAV_LINKS': nav_links,
        'EXTRA_HEAD': '',
        'EXTRA_JS': ''
    })
    return templates.TemplateResponse('layout.html', context)
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

@app.post("/push_state")
async def push_state(request: Request):
    """Recibe {route_idx?, path, value, ts, route?} del core y lo refleja en el estado + websockets."""
    try:
        payload = await request.json()
        path = str(payload.get("path") or "")
        value = payload.get("value")
        ts = payload.get("ts") or datetime.utcnow().isoformat() + "Z"
        route_idx = payload.get("route_idx")
        route_meta = payload.get("route") or {}

        st = load_json(STATE_FILE, {})
        if route_idx is not None:
            st[str(route_idx)] = {
                "path": path,
                "value": value,
                "ts": ts,
                "route": route_meta,
            }
        else:
            st[path] = {"value": value, "ts": ts}
        save_json(STATE_FILE, st)

        broadcast_payload = {"path": path, "value": value, "ts": ts}
        if route_idx is not None:
            broadcast_payload["route_idx"] = str(route_idx)
            broadcast_payload["route"] = route_meta
        await ws_manager.broadcast_json(broadcast_payload)
        return JSONResponse({"ok": True})
    except Exception as e:
        LOGGER.exception("Error procesando push_state: %s", e)
        return JSONResponse({"ok": False, "err": str(e)}, status_code=400)

@app.get("/state")
def state():
    return JSONResponse(load_json(STATE_FILE, {}))

# ---------- Learn ----------
@app.get("/learn_state")
def learn_state():
    raw = read_learn_state()
    resp: Dict[str, Any] = {
        "armed": bool(raw.get("armed")),
        "osc": raw.get("osc", "/learn"),
        "vtype": raw.get("vtype", "float"),
        "candidate": raw.get("candidate"),
        "result": raw.get("result"),
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
def arm_learn(osc: str = Form(...), vtype: str = Form(...), const: str = Form("")):
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

def restart_page(message: str = "Reiniciando servicio OMIMIDI…", request: Request = None) -> HTMLResponse:
    context = {
        "request": request,
        "message": message
    }
    return templates.TemplateResponse("restart.html", context)

@app.post("/config/save")
def save_config(
    midi_input: str = Form(""), 
    osc_port: str = Form(""),
    osc_ips: str = Form(""), 
    ui_port: str = Form(""), 
    config_name: str = Form(""),
    vlan: str = Form(""),
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
    data["midi_input"] = midi_input.strip()
    data["config_name"] = config_name.strip() or data.get("config_name", "default")

    # Validar y guardar VLAN
    try:
        vlan_num = int(vlan)
        if 1 <= vlan_num <= 4094:
            data["vlan"] = vlan_num
    except ValueError:
        data["vlan"] = 100

    # Validar y guardar puerto OSC
    try:
        osc_port_num = int(osc_port)
        if 1 <= osc_port_num <= 65535:
            data["osc_port"] = osc_port_num
    except ValueError:
        data["osc_port"] = 1024

    # Validar y guardar puerto UI
    try:
        ui_port_num = int(ui_port)
        if 1 <= ui_port_num <= 65535:
            data["ui_port"] = ui_port_num
    except ValueError:
        data["ui_port"] = 9001

    # Validar y guardar IPs OSC
    valid_ips = []
    for ip in [ip.strip() for ip in osc_ips.split(",") if ip.strip()]:
        try:
            ipaddress.ip_address(ip)
            valid_ips.append(ip)
        except ValueError:
            LOGGER.warning(f"IP inválida ignorada: {ip}")
            continue
    data["osc_ips"] = valid_ips or ["127.0.0.1"]

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

@app.post("/ping_osc")
def ping_osc():
    data = get_map()
    port = int(data.get("osc_port", 1024))
    ips = data.get("osc_ips", ["127.0.0.1"])
    ts = datetime.utcnow().isoformat() + "Z"
    for ip in ips:
        try:
            c = SimpleUDPClient(ip, port)
            c._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            c.send_message("/omimidi/ping", ts)
            LOGGER.info("Ping OSC enviado a %s:%s", ip, port)
        except Exception as exc:
            LOGGER.warning("No se pudo enviar ping OSC a %s:%s → %s", ip, port, exc)
    return RedirectResponse("/config", status_code=303)

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
@app.post("/restart")
def restart():
    request_restart_flag()
    return restart_page()
