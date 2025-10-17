#!/usr/bin/env python3
from __future__ import annotations
import os, json, socket, ipaddress, time, asyncio, tempfile, html
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Form, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pythonosc.udp_client import SimpleUDPClient
from datetime import datetime
import mido

from omimidi_core import push_map_to_server

BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
MAP_FILE         = os.path.join(BASE_DIR, "OMIMIDI_map.json")
LEARN_REQ_FILE   = os.path.join(BASE_DIR, "OMIMIDI_learn_request.json")
STATE_FILE       = os.path.join(BASE_DIR, "OMIMIDI_state.json")
RESTART_REQ_FILE = os.path.join(BASE_DIR, "OMIMIDI_restart.flag")

# Backend fijo (no editable)
mido.set_backend("mido.backends.rtmidi")

app = FastAPI(title="OMIMIDI Web UI", version="0.6")

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
    html_doc = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{page_title_html}</title>
<style>
:root {{
  --bg:#111;
  --card:#1b1b1b;
  --text:#eaeaea;
  --muted:#a7a7a7;
  --accent:#2ea043;
  --danger:#e53e3e;
  --danger-dim:#8f2929;
  --line:#2a2a2a;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial;
  background: var(--bg);
  color: var(--text);
}}
a {{ color: inherit; }}
.topbar {{
  display:flex;
  align-items:center;
  justify-content:space-between;
  padding:16px 20px;
  border-bottom:1px solid var(--line);
  background:#141414;
}}
.brand {{ font-weight:600; letter-spacing:0.04em; }}
.nav {{ display:flex; gap:8px; flex-wrap:wrap; }}
.nav-link {{
  padding:7px 14px;
  border-radius:20px;
  border:1px solid transparent;
  text-decoration:none;
  color:var(--muted);
  background:transparent;
}}
.nav-link:hover {{ color:var(--text); }}
.nav-link.active {{
  color:#0d170d;
  background:var(--accent);
  border-color:var(--accent);
  font-weight:600;
}}
.container {{ max-width:960px; margin:0 auto; padding:28px 20px 40px; }}
.stack > * + * {{ margin-top:18px; }}
.card {{
  background: var(--card);
  border:1px solid var(--line);
  border-radius:12px;
  padding:20px;
}}
.card-header {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; }}
.card-header h2 {{ margin:0; font-size:22px; }}
.section-title h2 {{ margin:0; font-size:22px; }}
.section-title p {{ margin:6px 0 0 0; color:var(--muted); font-size:14px; }}
.btn {{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  gap:6px;
  padding:8px 14px;
  border-radius:10px;
  border:1px solid var(--line);
  background:#222;
  color:var(--text);
  text-decoration:none;
  cursor:pointer;
  font-size:14px;
}}
.btn:hover {{ filter:brightness(1.12); }}
.btn.primary {{ background:var(--accent); border-color:var(--accent); color:#0d170d; }}
.btn.danger {{ background:var(--danger); border-color:#7a2020; }}
.btn.ghost {{ background:transparent; }}
.table-wrap {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; }}
th, td {{ border-bottom:1px solid var(--line); padding:10px 8px; text-align:left; }}
th {{ color:var(--muted); font-size:13px; text-transform:uppercase; letter-spacing:0.06em; }}
small, .muted {{ color:var(--muted); }}
input[type=text], select {{
  width:100%;
  padding:8px 10px;
  border:1px solid var(--line);
  border-radius:10px;
  background:#121212;
  color:var(--text);
}}
.form-grid {{ display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); }}
.form-grid .full {{ grid-column:1 / -1; }}
.option-grid {{ display:grid; gap:18px; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); }}
.option-card {{
  display:block;
  background:var(--card);
  border:1px solid var(--line);
  border-radius:12px;
  padding:18px;
  text-decoration:none;
  transition:transform 0.15s ease, border-color 0.15s ease;
}}
.option-card:hover {{ transform:translateY(-3px); border-color:var(--accent); }}
.option-card h3 {{ margin:0 0 8px 0; font-size:20px; }}
.option-card p {{ margin:0; color:var(--muted); line-height:1.4; }}
.info-block {{ background:#131313; border:1px dashed var(--line); border-radius:10px; padding:14px; }}
.actions {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:16px; }}
.badge {{ display:inline-block; padding:3px 8px; border-radius:999px; font-size:12px; border:1px solid var(--line); color:#ddd; }}
</style>
{extra_head}
</head>
<body>
<header class="topbar">
  <div class="brand">{brand_html}</div>
  <nav class="nav">{''.join(nav_links)}</nav>
</header>
<main class="container stack">
{body_html}
</main>
{extra_js}
</body>
</html>
"""
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
                f"<td><span data-osc='{osc_esc}'>–</span></td>"
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
    extra_js = """
<script>
(function(){
  function formatValue(v){
    if (typeof v === 'number'){
      if (Math.abs(v) >= 1000) return v.toFixed(0);
      return Math.round(v * 1000) / 1000;
    }
    return String(v);
  }
  function applyValue(path, value){
    const nodes = document.querySelectorAll('[data-osc="' + path + '"]');
    nodes.forEach(el => { el.textContent = formatValue(value); });
  }
  fetch('/state').then(r=>r.json()).then(st=>{
    Object.entries(st).forEach(([path, obj])=>{
      if (obj && 'value' in obj){
        applyValue(path, obj.value);
      }
    });
  }).catch(()=>{});
  function setupWS(){
    const proto = (location.protocol === 'https:') ? 'wss' : 'ws';
    const ws = new WebSocket(proto + '://' + location.host + '/ws');
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg && msg.path !== undefined){
          applyValue(msg.path, msg.value);
        }
      } catch(e){}
    };
    ws.onclose = () => {
      setTimeout(setupWS, 1000);
    };
  }
  setupWS();
})();
</script>
"""
    return render_layout(body, active="home", extra_js=extra_js)


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
      <h2>Dispositivo MIDI</h2>
      <p class="muted">Selecciona la entrada MIDI disponible.</p>
    </div>
    <select name="midi_input">{''.join(options)}</select>
  </section>

  <section class="card stack">
    <div class="section-title">
      <h2>Preset</h2>
      <p class="muted">Nombre identificador para guardar y compartir esta configuración.</p>
    </div>
    <input type="text" name="config_name" value="{config_name}" maxlength="64">
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

    extra_js = """
<script>
(function(){
  const form = document.getElementById('settingsForm');
  if (form){
    form.addEventListener('submit', function(ev){
      if (!confirm('Se reiniciará el servicio, ¿desea continuar?')){
        ev.preventDefault();
      }
    });
  }
  const pingBtn = document.getElementById('pingBtn');
  const pingStatus = document.getElementById('pingStatus');
  if (pingBtn && pingStatus){
    pingBtn.addEventListener('click', async function(){
      const original = pingBtn.textContent;
      pingBtn.disabled = true;
      pingBtn.textContent = 'Enviando…';
      try {
        const res = await fetch('/ping_osc', {method:'POST'});
        pingStatus.style.display = 'block';
        pingStatus.textContent = res.ok ? 'Ping enviado a los targets OSC.' : 'Error enviando ping OSC.';
      } catch (e){
        pingStatus.style.display = 'block';
        pingStatus.textContent = 'Error enviando ping OSC.';
      } finally {
        setTimeout(()=>{ pingStatus.style.display = 'none'; }, 3500);
        pingBtn.disabled = false;
        pingBtn.textContent = original;
      }
    });
  }
})();
</script>
"""

    response = render_layout(body, active="settings", extra_js=extra_js)
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
  <form method="post" action="/add_route" class="stack">
    <div class="form-grid">
      <div>
        <label>Tipo</label>
        <select name="rtype"><option value="note">note</option><option value="cc">cc</option></select>
      </div>
      <div>
        <label>Nota o CC (0..127)</label>
        <input type="text" name="num">
      </div>
      <div>
        <label>Canal (solo CC, opcional)</label>
        <input type="text" name="channel" placeholder="(opcional)">
      </div>
      <div class="full">
        <label>OSC Path</label>
        <input type="text" name="osc" value="/D3/x">
      </div>
      <div>
        <label>Tipo de valor OSC</label>
        <select name="vtype">
          <option value="float">float (0..1)</option>
          <option value="int">int (0..127)</option>
          <option value="bool">bool</option>
          <option value="const">const</option>
        </select>
      </div>
      <div>
        <label>Const (si vtype=const)</label>
        <input type="text" name="const" placeholder="ej: 1.0">
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
    <p class="muted">Activa LEARN y mueve un control para crear la ruta automáticamente.</p>
  </div>
  <form method="post" action="/arm_learn" id="learnForm" class="stack">
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
    <div class="actions">
      <button class="btn ghost" id="learnToggle" type="submit">LEARN DISABLED</button>
      <a class="btn ghost" href="/cancel_learn" id="cancelLink" style="display:none;">Cancelar</a>
    </div>
    <small>El próximo evento MIDI creará la ruta y te llevará de vuelta a Home.</small>
  </form>
  <div class="info-block" id="learnResult" style="display:none;">
    <div class="muted">Último LEARN</div>
    <code id="resultCode" style="display:block; margin-top:6px; white-space:pre-wrap;"></code>
    <form method="post" action="/clear_learn_result" style="margin-top:12px;">
      <button class="btn" type="submit">Ocultar resultado</button>
    </form>
  </div>
</section>
"""
    extra_js = """
<script>
(function(){
  const vtypeSel = document.getElementById('vtypeInput');
  const constRow = document.getElementById('constRow');
  if (vtypeSel){
    const toggleConst = () => { constRow.style.display = (vtypeSel.value === 'const') ? 'block' : 'none'; };
    vtypeSel.addEventListener('change', toggleConst);
    toggleConst();
  }
  let prevArmed = null;
  async function refreshLearnUI(){
    try {
      const res = await fetch('/learn_state');
      const st = await res.json();
      const toggle = document.getElementById('learnToggle');
      const cancelLink = document.getElementById('cancelLink');
      if (st.armed){
        toggle.textContent = 'LEARN ENABLED';
        toggle.classList.add('danger');
        toggle.classList.remove('ghost');
        cancelLink.style.display = 'inline-flex';
      } else {
        toggle.textContent = 'LEARN DISABLED';
        toggle.classList.remove('danger');
        toggle.classList.add('ghost');
        cancelLink.style.display = 'none';
      }
      const lr = document.getElementById('learnResult');
      const rc = document.getElementById('resultCode');
      if (st.result){
        lr.style.display = 'block';
        rc.textContent = JSON.stringify(st.result, null, 2);
      } else {
        lr.style.display = 'none';
      }
      if (prevArmed === true && st.armed === false && st.result){
        window.location.href = '/';
      }
      prevArmed = st.armed;
    } catch(e){}
  }
  refreshLearnUI();
  setInterval(refreshLearnUI, 600);
})();
</script>
"""
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
    """Recibe {path, value, ts} del core y lo reenvía por websockets; guarda en STATE_FILE también."""
    try:
        payload = await request.json()
        path = str(payload.get("path"))
        value = payload.get("value")
        ts = payload.get("ts") or datetime.utcnow().isoformat() + "Z"
        # Persistimos en STATE_FILE por si se conecta tarde la UI
        st = load_json(STATE_FILE, {})
        st[path] = {"value": value, "ts": ts}
        save_json(STATE_FILE, st)
        # Broadcast a los clientes
        await ws_manager.broadcast_json({"path": path, "value": value, "ts": ts})
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "err": str(e)}, status_code=400)

@app.get("/state")
def state():
    return JSONResponse(load_json(STATE_FILE, {}))

# ---------- Learn ----------
@app.get("/learn_state")
def learn_state():
    return JSONResponse(load_json(LEARN_REQ_FILE, {}))

@app.post("/clear_learn_result")
def clear_learn_result():
    st = load_json(LEARN_REQ_FILE, {})
    st.pop("result", None)
    save_json(LEARN_REQ_FILE, st)
    return RedirectResponse("/", status_code=303)


@app.post("/arm_learn")
def arm_learn(osc: str = Form(...), vtype: str = Form(...), const: str = Form("")):
    osc_path = osc.strip() or "/learn"
    payload: Dict[str, Any] = {
        "armed": True,
        "osc": osc_path,
        "vtype": vtype
    }
    if vtype == "const":
        try:
            payload["const"] = float(const)
        except ValueError:
            payload["const"] = 1.0
    payload.pop("result", None)
    save_json(LEARN_REQ_FILE, payload)
    return RedirectResponse("/", status_code=303)

@app.get("/cancel_learn")
def cancel_learn():
    st = load_json(LEARN_REQ_FILE, {})
    st["armed"] = False
    st.pop("result", None)
    save_json(LEARN_REQ_FILE, st)
    return RedirectResponse("/", status_code=303)


# ---------- MIDI / OSC / UI settings ----------
def request_restart_flag() -> None:
    with open(RESTART_REQ_FILE, "w") as f:
        f.write("restart")

def restart_page(message: str = "Reiniciando servicio OMIMIDI…") -> HTMLResponse:
    template = """<!doctype html><html><head><meta charset='utf-8'>
    <title>Reiniciando…</title></head>
    <body style="font-family:system-ui; background:#111; color:#eee; display:flex; align-items:center; justify-content:center; height:100vh;">
    <div>
      <h2>🔄 {{MESSAGE}}</h2>
      <p>La página intentará reconectar automáticamente.</p>
      <script>
      (function retry(){
        fetch('/')
          .then(()=>{ window.location.href='/'; })
          .catch(()=>{ setTimeout(retry, 800); });
      })();
      </script>
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
        except Exception:
            pass
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
