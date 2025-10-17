#!/usr/bin/env python3
from __future__ import annotations
import os, json, socket, ipaddress, time, asyncio, tempfile
from typing import Any, Dict

from fastapi import FastAPI, Form, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pythonosc.udp_client import SimpleUDPClient
from datetime import datetime
import mido

BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
MAP_FILE         = os.path.join(BASE_DIR, "OMIMIDI_map.json")
LEARN_REQ_FILE   = os.path.join(BASE_DIR, "OMIMIDI_learn_request.json")
STATE_FILE       = os.path.join(BASE_DIR, "OMIMIDI_state.json")
RESTART_REQ_FILE = os.path.join(BASE_DIR, "OMIMIDI_restart.flag")

# Backend fijo (no editable)
mido.set_backend("mido.backends.rtmidi")

app = FastAPI(title="OMIMIDI Web UI", version="0.6")

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
        "routes": []
    })

def persist_map(data: Dict[str, Any]) -> None:
    data["osc_port"] = int(data.get("osc_port", 1024))
    data["ui_port"] = int(data.get("ui_port", 9001))
    data["osc_ips"] = list(data.get("osc_ips", ["127.0.0.1"]))
    save_json(MAP_FILE, data)

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
def page(title: str, body_html: str) -> HTMLResponse:
    html = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{{TITLE}}</title>
<style>
:root {
  --bg:#111;
  --card:#1b1b1b;
  --text:#eaeaea;
  --muted:#aaaaaa;
  --ok:#2ea043;
  --warn:#f0ad4e;
  --danger:#e53e3e;
  --danger-dim:#8f2929;
  --line:#2a2a2a;
}
* { box-sizing: border-box; }
body {
  margin: 20px;
  font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial;
  background: var(--bg);
  color: var(--text);
}
h1 { margin: 0 0 18px 0; font-size: 22px; }
.card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 14px;
}
.grid {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 16px;
  align-items: start;
}
.row { margin-bottom: 10px; }
label { display:block; margin-bottom:6px; color: var(--muted); }
input[type=text], select {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #121212;
  color: var(--text);
}
table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 8px;
}
td, th {
  border-bottom: 1px solid var(--line);
  padding: 8px 6px;
  text-align: left;
}
th { color: var(--muted); font-weight: 600; }
.btn {
  display:inline-block;
  padding: 8px 12px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: #222;
  color: var(--text);
  text-decoration: none;
  cursor: pointer;
  margin: 2px 0;
}
.btn:hover { filter: brightness(1.1); }
.btn-danger { background: var(--danger); border-color: #7a2020; }
.btn-danger.dim { background: var(--danger-dim); }
.btn-ok { background: var(--ok); border-color: #2b6b34; }
.stack > * + * { margin-top: 12px; }
.badge {
  display:inline-block; padding:3px 8px; border-radius:999px; font-size:12px; border:1px solid var(--line); color:#ddd;
}
.small { font-size: 12px; color: var(--muted); }
.header {
  display:flex; align-items:center; justify-content:space-between; margin-bottom:10px;
}
hr.sep { border:none; border-top:1px solid var(--line); margin:12px 0; }
</style>
</head>
<body>
  <div class="header">
    <h1>OMIMIDI Web UI</h1>
    <span class="small">Frontend minimal • listo para maquetar</span>
  </div>

  <div class="grid">
    <div>
      <!-- Columna izquierda: Dispositivo, OSC y Rutas -->
      <div class="card stack">
        <div>
          <h2 style="margin:0 0 8px 0; font-size:18px;">Dispositivo MIDI</h2>
          <form method="post" action="/set_device">
            <div class="row">
              <label>Entrada MIDI</label>
              {{MIDI_SELECT}}
            </div>
            <button class="btn" type="submit">Guardar dispositivo</button>
            <a class="btn" href="/">Refrescar</a>
          </form>
        </div>

        <hr class="sep">

        <div>
          <h2 style="margin:0 0 8px 0; font-size:18px;">Targets OSC</h2>
          <form method="post" action="/set_osc">
            <div class="row">
              <label>Puerto</label>
              <input type="text" name="osc_port" value="{{OSC_PORT}}">
            </div>
            <div class="row">
              <label>IPs (separadas por coma)</label>
              <input type="text" name="osc_ips" value="{{OSC_IPS}}">
            </div>
            <button class="btn" type="submit">Guardar OSC</button>
            <button class="btn" formaction="/ping_osc" formmethod="post" title="Envia /omimidi/ping a todos los targets">Ping OSC</button>
          </form>
        </div>

        <hr class="sep">

        <div>
          <div style="display:flex;align-items:center;gap:8px;">
            <h2 style="margin:0; font-size:18px;">Rutas MIDI → OSC</h2>
            <span class="small">(LEARN para añadir automáticamente; la columna <b>Último</b> muestra el último valor)</span>
          </div>
          <table>
            <tr><th>#</th><th>MIDI</th><th>OSC Path</th><th>Valor</th><th>Último</th><th></th></tr>
            {{ROUTES_ROWS}}
          </table>

          <h3 style="margin:16px 0 6px 0; font-size:16px;">Añadir ruta (manual)</h3>
          <form method="post" action="/add_route">
            <div class="row">
              <label>Tipo</label>
              <select name="rtype"><option value="note">note</option><option value="cc">cc</option></select>
            </div>
            <div class="row">
              <label>Nota o CC (0..127)</label>
              <input type="text" name="num">
            </div>
            <div class="row">
              <label>Canal (solo CC, 0..15, opcional)</label>
              <input type="text" name="channel" placeholder="(opcional)">
            </div>
            <div class="row">
              <label>OSC Path</label>
              <input type="text" name="osc" value="/D3/x">
            </div>
            <div class="row">
              <label>Tipo de valor OSC</label>
              <select name="vtype">
                <option value="float">float (0..1)</option>
                <option value="int">int (0..127)</option>
                <option value="bool">bool</option>
                <option value="const">const</option>
              </select>
            </div>
            <div class="row">
              <label>Const (si vtype=const)</label>
              <input type="text" name="const" placeholder="ej: 1.0">
            </div>
            <button class="btn" type="submit">Añadir</button>
          </form>
        </div>

        <hr class="sep">

        <div>
          <h2 style="margin:0 0 8px 0; font-size:18px;">Web UI</h2>
          <form method="post" action="/set_ui">
            <div class="row">
              <label>Puerto WebUI (requiere reinicio)</label>
              <input type="text" name="ui_port" value="{{UI_PORT}}">
            </div>
            <button class="btn" type="submit">Guardar WebUI</button>
            <button class="btn" formaction="/restart" formmethod="post" title="Reinicia todo (core + WebUI)">Reiniciar servicio</button>
          </form>
          <div class="small">Actual: <code>http://&lt;host&gt;:{{UI_PORT}}</code></div>
        </div>
      </div>
    </div>

    <div>
      <!-- Sidebar derecha: LEARN -->
      <div class="card">
        <h2 style="margin:0 0 12px 0; font-size:18px;">LEARN</h2>

        <form method="post" action="/arm_learn" id="learnForm">
          <div class="row">
            <label>OSC Path</label>
            <input type="text" name="osc" value="/D3/learn" id="oscInput">
          </div>
          <div class="row">
            <label>Tipo de valor OSC</label>
            <select name="vtype" id="vtypeInput">
              <option value="float">float (0..1)</option>
              <option value="int">int (0..127)</option>
              <option value="bool">bool</option>
              <option value="const">const</option>
            </select>
          </div>
          <div class="row" id="constRow" style="display:none;">
            <label>Const (si vtype=const)</label>
            <input type="text" name="const" id="constInput" placeholder="ej: 1.0">
          </div>

          <div class="row">
            <!-- Estado LEARN -->
            <button class="btn btn-danger dim" id="learnToggle" type="submit" title="Activa LEARN y toca/mueve algo en tu controlador">
              LEARN DISABLED
            </button>
            <a class="btn" href="/cancel_learn" id="cancelLink" style="display:none;">Cancelar</a>
          </div>
          <div class="small">Cuando esté <b>LEARN ENABLED</b>, el próximo evento MIDI creará la ruta y la página se actualizará sola.</div>
        </form>

        <hr class="sep">

        <div id="learnResult" style="display:none;">
          <div class="small" style="margin-bottom:6px;">Último LEARN</div>
          <code id="resultCode" style="display:block; white-space:pre-wrap;"></code>
          <form method="post" action="/clear_learn_result" style="margin-top:8px;">
            <button class="btn" type="submit">Ocultar resultado</button>
          </form>
        </div>
      </div>
    </div>
  </div>

<script>
(function(){
  /* Mostrar/ocultar input const en función de vtype */
  const vtypeSel = document.getElementById('vtypeInput');
  const constRow = document.getElementById('constRow');
  vtypeSel.addEventListener('change', () => {
    constRow.style.display = (vtypeSel.value === 'const') ? 'block' : 'none';
  });

  /* Polling de LEARN */
  let prevArmed = null;
  async function refreshLearnUI() {
    try {
      const res = await fetch('/learn_state');
      const st = await res.json();

      const toggle = document.getElementById('learnToggle');
      const cancelLink = document.getElementById('cancelLink');

      if (st.armed) {
        toggle.classList.remove('dim');
        toggle.textContent = 'LEARN ENABLED';
        cancelLink.style.display = 'inline-block';
      } else {
        toggle.classList.add('dim');
        toggle.textContent = 'LEARN DISABLED';
        cancelLink.style.display = 'none';
      }

      /* Mostrar último resultado si existe */
      const lr = document.getElementById('learnResult');
      const rc = document.getElementById('resultCode');
      if (st.result) {
        lr.style.display = 'block';
        rc.textContent = JSON.stringify(st.result, null, 2);
      } else {
        lr.style.display = 'none';
      }

      /* Auto-recargar si veníamos de armed=true y pasa a false con result */
      if (prevArmed === true && st.armed === false && st.result) {
        window.location.reload();
      }
      prevArmed = st.armed;
    } catch(e) {}
  }

  /* WebSocket: actualiza la columna "Último" en tiempo real */
  function setupWS() {
    const proto = (location.protocol === 'https:') ? 'wss' : 'ws';
    const ws = new WebSocket(proto + '://' + location.host + '/ws');

    ws.onopen = () => {
      /* Carga inicial de valores por si llegamos tarde */
      fetch('/state').then(r=>r.json()).then(st=>{
        Object.entries(st).forEach(([k, o])=>{
          const nodes = document.querySelectorAll('[data-osc="' + k + '"]');
          nodes.forEach(el => {
            const v = o.value;
            el.textContent = (typeof v === 'number')
              ? ((Math.abs(v) >= 1000) ? v.toFixed(0) : (Math.round(v*1000)/1000))
              : String(v);
          });
        });
      }).catch(()=>{});
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data); // {path, value, ts}
        const nodes = document.querySelectorAll('[data-osc="' + msg.path + '"]');
        nodes.forEach(el => {
          const v = msg.value;
          el.textContent = (typeof v === 'number')
            ? ((Math.abs(v) >= 1000) ? v.toFixed(0) : (Math.round(v*1000)/1000))
            : String(v);
        });
      } catch(e) {}
    };

    ws.onclose = () => {
      /* Reintentar conexión */
      setTimeout(setupWS, 1000);
    };
  }

  /* Primer pintado y timers */
  refreshLearnUI();
  setInterval(refreshLearnUI, 600);
  setupWS();
})();
</script>

</body>
</html>"""
    html = html.replace("{{TITLE}}", title)
    html = html.replace("__BODY__", body_html)
    return HTMLResponse(html)

# ---------- Rutas HTML ----------
@app.get("/", response_class=HTMLResponse)
def index():
    data = get_map()
    inputs = mido.get_input_names()
    current = data.get("midi_input", "")

    # tabla rutas (incluye celda de "Último" con data-osc)
    rows = ""
    for i, r in enumerate(data.get("routes", [])):
        midi_desc = "?"
        if r.get("type") == "note":
            midi_desc = f"NOTE {r.get('note')}"
        elif r.get("type") == "cc":
            midi_desc = f"CC {r.get('cc')} ch {r.get('channel','any')}"
        vtype = r.get("vtype", "float")
        extra = f" const={r.get('const')}" if vtype == "const" else ""
        osc = r.get("osc")
        rows += (
            f"<tr>"
            f"<td>{i}</td>"
            f"<td>{midi_desc}</td>"
            f"<td>{osc}</td>"
            f"<td>{vtype}{extra}</td>"
            f"<td><span data-osc=\"{osc}\">–</span></td>"
            f"<td>"
            f"<form method='post' action='/delete_route' style='display:inline;'>"
            f"<input type='hidden' name='idx' value='{i}'/>"
            f"<button class='btn'>Eliminar</button>"
            f"</form>"
            f"</td>"
            f"</tr>"
        )

    # select MIDI
    options = ["<option value=''>(sin seleccionar)</option>"]
    for n in inputs:
        sel = " selected" if n == current else ""
        options.append(f"<option{sel}>{n}</option>")
    midi_select = f"<select name='midi_input'>{''.join(options)}</select>"

    # construye HTML base
    body = page("OMIMIDI Web UI", "__BODY__").body.decode("utf-8")

    # reemplazos
    body = body.replace("{{MIDI_SELECT}}", midi_select)
    body = body.replace("{{OSC_PORT}}", str(data.get("osc_port", 1024)))
    body = body.replace("{{OSC_IPS}}", ",".join(data.get("osc_ips", ["127.0.0.1"])))
    body = body.replace("{{UI_PORT}}", str(data.get("ui_port", 9001)))
    body = body.replace("{{ROUTES_ROWS}}", rows if rows else "<tr><td colspan='6' class='small'>(sin rutas)</td></tr>")

    return HTMLResponse(body)

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
@app.post("/set_device")
def set_device(midi_input: str = Form("")):
    data = get_map()
    data["midi_input"] = midi_input.strip()
    persist_map(data)
    return RedirectResponse("/", status_code=303)

@app.post("/set_osc")
def set_osc(osc_port: str = Form(...), osc_ips: str = Form(...)):
    data = get_map()
    try:
        data["osc_port"] = int(osc_port)
    except ValueError:
        data["osc_port"] = 1024

    ips_in = [ip.strip() for ip in osc_ips.split(",") if ip.strip()]
    valid_ips = []
    for ip in ips_in:
        try:
            ipaddress.ip_address(ip); valid_ips.append(ip)
        except Exception:
            pass
    data["osc_ips"] = valid_ips or ["127.0.0.1"]
    persist_map(data)
    return RedirectResponse("/", status_code=303)

@app.post("/set_ui")
def set_ui(ui_port: str = Form(...)):
    data = get_map()
    try:
        data["ui_port"] = int(ui_port)
        if not (1 <= data["ui_port"] <= 65535):
            raise ValueError
    except ValueError:
        data["ui_port"] = 9001
    persist_map(data)
    return RedirectResponse("/", status_code=303)

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
    return RedirectResponse("/", status_code=303)

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
    # Crear flag para que el core mate la WebUI y se auto-reinicie
    with open(RESTART_REQ_FILE, "w") as f:
        f.write("restart")
    # Pantalla de "Reiniciando..."
    html = """<!doctype html><html><head><meta charset="utf-8">
    <title>Reiniciando…</title></head>
    <body style="font-family:system-ui; background:#111; color:#eee; display:flex; align-items:center; justify-content:center; height:100vh;">
    <div>
      <h2>🔄 Reiniciando servicio OMIMIDI…</h2>
      <p>La página intentará reconectar automáticamente.</p>
      <script>
      (function retry(){
        fetch('/').then(()=>{ window.location.href='/' }).catch(()=>{ setTimeout(retry, 800); });
      })();
      </script>
    </div>
    </body></html>"""
    return HTMLResponse(html)
