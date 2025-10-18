#!/usr/bin/env python3
from __future__ import annotations
import os, json, socket, ipaddress, time, asyncio, tempfile, html, logging
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Form, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
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

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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


NAV_ITEMS = [
    ("home", "Home", "/"),
    ("settings", "Ajustes", "/settings"),
    ("add", "Añadir", "/add")
]

LAYOUT_PATH = Path(__file__).resolve().parent / "web" / "layout.html"
LAYOUT_TEMPLATE = LAYOUT_PATH.read_text(encoding="utf-8")

def render_layout(body_html: str, *, title: str = "OMIMIDI Web UI", active: str = "home", extra_head: str = "", extra_js: str = "") -> HTMLResponse:
    host_label = get_identity_host()
    brand_text = f"OMIMIDI @ {host_label} Web UI"
    brand_html = html.escape(brand_text, quote=True)
    page_title = title if title != "OMIMIDI Web UI" else brand_text
    page_title_html = html.escape(page_title, quote=True)
    nav_links = []
    for key, label, href in NAV_ITEMS:
        cls = "nav-link"
        if key == active:
            cls += " active"
        nav_links.append(f'<a class="{cls}" href="{href}">{label}</a>')
    html_doc = LAYOUT_TEMPLATE
    replacements = {
        "PAGE_TITLE": page_title_html,
        "BRAND_HTML": brand_html,
        "NAV_LINKS": "".join(nav_links),
        "BODY_HTML": body_html,
        "EXTRA_HEAD": extra_head,
        "EXTRA_JS": extra_js,
        "PAGE_ID": html.escape(active, quote=True),
    }
    for token, value in replacements.items():
        html_doc = html_doc.replace(f"{{{{{token}}}}}", value)
    return HTMLResponse(html_doc)


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
def index():
    data = get_map()
    rows_html = render_routes_rows(data)
    body = f"""
<section class="card">
  <div class="card-header">
    <h2>Rutas MIDI → OSC</h2>
    <a class="btn primary" href="/add">+ Añadir ruta</a>
  </div>
  <div class="table-wrap">
    <table class="routes-table">
      <tr><th>#</th><th>MIDI</th><th>OSC Path</th><th>Valor</th><th>Último</th><th></th></tr>
      {rows_html}
    </table>
  </div>
</section>
"""
    return render_layout(body, active="home")


@app.get("/settings", response_class=HTMLResponse)
def settings_page():
    data = get_map()
    inputs = mido.get_input_names()
    current = data.get("midi_input", "")
    options = ["<option value=''>(sin seleccionar)</option>"]
    for name in inputs:
        sel = " selected" if name == current else ""
        options.append(f"<option{sel}>{html.escape(name, quote=True)}</option>")
    osc_port = html.escape(str(data.get("osc_port", 1024)), quote=True)
    osc_ips = html.escape(",".join(data.get("osc_ips", ["127.0.0.1"])), quote=True)
    ui_port = html.escape(str(data.get("ui_port", 9001)), quote=True)
    config_name = html.escape(str(data.get("config_name", "default")), quote=True)

    body = f"""
<form id="settingsForm" method="post" action="/settings/save" class="stack">
  <section class="card stack">
    <div class="section-title">
      <h2>Preset name</h2>
      <p class="muted">Nombre identificador para guardar y compartir esta configuración.</p>
    </div>
    <input type="text" name="config_name" value="{config_name}" maxlength="64">
  </section>

  <section class="card stack">
    <div class="section-title">
      <h2>Dispositivo MIDI</h2>
      <p class="muted">Selecciona la entrada MIDI disponible.</p>
    </div>
    <select name="midi_input">{''.join(options)}</select>
  </section>

  <section class="card stack">
    <div class="section-title">
      <h2>Targets OSC</h2>
      <p class="muted">Define el puerto y las IPs destino (separadas por coma).</p>
    </div>
    <div class="form-grid">
      <div>
        <label>Puerto</label>
        <input type="text" name="osc_port" value="{osc_port}">
      </div>
      <div class="full">
        <label>IPs</label>
        <input type="text" name="osc_ips" value="{osc_ips}">
      </div>
    </div>
    <small class="muted">Ejemplo: 192.168.0.52, 127.0.0.1</small>
  </section>

  <section class="card stack">
    <div class="section-title">
      <h2>Web UI</h2>
      <p class="muted">Al guardar se reiniciará el servicio.</p>
    </div>
    <div class="form-grid">
      <div>
        <label>Puerto WebUI</label>
        <input type="text" name="ui_port" value="{ui_port}">
      </div>
    </div>
    <small>Actual: <code>http://&lt;host&gt;:{ui_port}</code></small>
  </section>

  <div class="actions">
    <button class="btn primary" type="submit">Guardar cambios</button>
    <button class="btn" type="button" id="pingBtn">Ping OSC</button>
    <a class="btn" href="/">Cancelar</a>
  </div>
</form>
<div class="small muted" id="pingStatus" style="display:none;"></div>
"""

    response = render_layout(body, active="settings")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/add", response_class=HTMLResponse)
def add_route_landing():
    body = """
<section class="card stack">
  <div class="section-title">
    <h2>Añadir ruta</h2>
    <p class="muted">Elige cómo quieres crear la ruta MIDI → OSC.</p>
  </div>
  <div class="option-grid">
    <a class="option-card" href="/add/manual">
      <h3>Manual</h3>
      <p>Introduce nota/CC, canal y ruta OSC a mano.</p>
    </a>
    <a class="option-card" href="/add/learn">
      <h3>LEARN automático</h3>
      <p>Activa LEARN y toca tu controlador para mapearlo al instante.</p>
    </a>
  </div>
</section>
"""
    return render_layout(body, active="add")

@app.get("/add/manual", response_class=HTMLResponse)
def add_route_manual_page():
    body = """
<section class="card stack">
  <div class="section-title">
    <h2>Añadir ruta manual</h2>
    <p class="muted">Configura la ruta MIDI → OSC rellenando los campos.</p>
  </div>
  <form method="post" action="/add_route" class="stack" id="manualForm">
    <div class="form-grid">
      <div>
        <label>Tipo</label>
        <select name="rtype"><option value="note">note</option><option value="cc">cc</option></select>
      </div>
      <div>
        <label>Nota o CC (0..127)</label>
        <input type="text" name="num">
      </div>
      <div class="full">
        <label>OSC Path</label>
        <input type="text" name="osc" value="/D3/x">
      </div>
      <div>
        <label>Tipo de valor OSC</label>
        <select name="vtype" id="manualVType">
          <option value="float">float (0..1)</option>
          <option value="int">int (0..127)</option>
          <option value="bool">bool</option>
          <option value="const">const</option>
        </select>
      </div>
      <div id="manualConstRow" style="display:none;">
        <label>Const (si vtype=const)</label>
        <input type="text" name="const" id="manualConstInput" placeholder="ej: 1.0">
      </div>
    </div>
    <div class="actions">
      <button class="btn primary" type="submit">Añadir ruta</button>
      <a class="btn" href="/">Cancelar</a>
    </div>
  </form>
</section>
"""
    return render_layout(body, active="add")

@app.get("/add/learn", response_class=HTMLResponse)
def add_route_learn_page():
    body = """
<section class="card stack">
  <div class="section-title">
    <h2>LEARN automático</h2>
    <p class="muted">Mueve un control MIDI, revisa el último mensaje detectado y pulsa aceptar para crear la ruta.</p>
  </div>
  <form method="post" action="/commit_learn" id="learnForm" class="stack">
    <div class="form-grid">
      <div class="full">
        <label>OSC Path</label>
        <input type="text" name="osc" value="/D3/learn" id="oscInput">
      </div>
      <div>
        <label>Tipo de valor OSC</label>
        <select name="vtype" id="vtypeInput">
          <option value="float">float (0..1)</option>
          <option value="int">int (0..127)</option>
          <option value="bool">bool</option>
          <option value="const">const</option>
        </select>
      </div>
      <div id="constRow" style="display:none;">
        <label>Const (si vtype=const)</label>
        <input type="text" name="const" id="constInput" placeholder="ej: 1.0">
      </div>
    </div>
    <div class="info-block" id="livePreview">
      <div><strong>Ruta OSC:</strong> <code id="summaryOsc">/D3/learn</code></div>
      <div><strong>Tipo de valor:</strong> <span id="summaryType">float (0..1)</span></div>
      <div><strong>Tipo de mensaje:</strong> <span id="summaryKind">Esperando…</span></div>
      <div><strong>Último mensaje:</strong> <span id="summaryCandidate">Esperando evento MIDI…</span></div>
      <div class="muted" id="summaryDetails" style="margin-top:4px; display:none;"></div>
    </div>
    <div class="actions">
      <button class="btn primary" id="learnAccept" type="button" disabled>Aceptar</button>
      <button class="btn" type="button" id="learnCancel">Cancelar</button>
    </div>
    <div class="muted" id="learnMessage" style="display:none;"></div>
    <small>En esta pantalla el aprendizaje se inicia automáticamente y se muestra siempre el último mensaje recibido.</small>
  </form>
  <div class="info-block" id="learnResult" style="display:none;">
    <div class="muted">Última ruta creada</div>
    <div id="resultSummary" style="margin-top:6px;"></div>
    <form method="post" action="/clear_learn_result" style="margin-top:12px;">
      <button class="btn" type="submit">Ocultar resultado</button>
    </form>
  </div>
</section>
"""
    extra_js = '\n<script src="/static/learn.js"></script>\n'
    return render_layout(body, active="add", extra_js=extra_js)
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

def restart_page(message: str = "Reiniciando servicio OMIMIDI…") -> HTMLResponse:
    template = """<!doctype html><html><head><meta charset='utf-8'>
    <title>Reiniciando…</title></head>
    <body style="font-family:system-ui; background:#111; color:#eee; display:flex; align-items:center; justify-content:center; height:100vh;">
    <div>
      <h2>🔄 {{MESSAGE}}</h2>
      <p>La página intentará reconectar automáticamente.</p>
      <script src="/static/restart.js"></script>
    </div>
    </body></html>"""
    return HTMLResponse(template.replace("{{MESSAGE}}", message))

@app.post("/settings/save")
def save_settings(midi_input: str = Form(""), osc_port: str = Form(""),
                 osc_ips: str = Form(""), ui_port: str = Form(""), config_name: str = Form("")):
    data = get_map()
    data["midi_input"] = midi_input.strip()

    try:
        port = int(osc_port)
        if not (1 <= port <= 65535):
            raise ValueError
        data["osc_port"] = port
    except ValueError:
        data["osc_port"] = 1024

    ips_in = [ip.strip() for ip in osc_ips.split(",") if ip.strip()]
    valid_ips = []
    for ip in ips_in:
        try:
            ipaddress.ip_address(ip)
            valid_ips.append(ip)
        except Exception:
            pass
    data["osc_ips"] = valid_ips or ["127.0.0.1"]

    try:
        uport = int(ui_port)
        if not (1 <= uport <= 65535):
            raise ValueError
        data["ui_port"] = uport
    except ValueError:
        data["ui_port"] = 9001

    data["config_name"] = config_name.strip() or data.get("config_name", "default")

    persist_map(data)
    request_restart_flag()
    LOGGER.info(
        "Configuración actualizada; se reiniciará el servicio (midi_input=%s, osc_port=%s, ui_port=%s, ips=%s)",
        data.get("midi_input"),
        data.get("osc_port"),
        data.get("ui_port"),
        ", ".join(data.get("osc_ips") or []),
    )
    return restart_page("Aplicando cambios y reiniciando…")

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
    return RedirectResponse("/settings", status_code=303)

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
