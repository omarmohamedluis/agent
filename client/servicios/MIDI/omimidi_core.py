#!/usr/bin/env python3
from __future__ import annotations
import os, json, time, threading, socket, ipaddress, sys, signal, tempfile, atexit
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import urllib.request

import mido
from pythonosc.udp_client import SimpleUDPClient

# ==== Archivos ====
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
MAP_FILE         = os.path.join(BASE_DIR, "OMIMIDI_map.json")
LAST_EVENT_FILE  = os.path.join(BASE_DIR, "OMIMIDI_last_event.json")      # opcional
LEARN_REQ_FILE   = os.path.join(BASE_DIR, "OMIMIDI_learn_request.json")   # WebUI arma LEARN; el core lo consume
STATE_FILE       = os.path.join(BASE_DIR, "OMIMIDI_state.json")           # último valor por ruta OSC
RESTART_REQ_FILE = os.path.join(BASE_DIR, "OMIMIDI_restart.flag")         # WebUI solicita reinicio; el core se re-ejecuta
WEBUI_PID_FILE   = os.path.join(BASE_DIR, "OMIMIDI_webui.pid")            # PID de la WebUI para poder matarla

CLEANUP_FILES = [
    LAST_EVENT_FILE,
    LEARN_REQ_FILE,
    STATE_FILE,
    WEBUI_PID_FILE,
]

def cleanup_runtime_files():
    for path in CLEANUP_FILES:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception:
            pass

atexit.register(cleanup_runtime_files)

CHECK_INTERVAL = 0.5   # s (hot-reload)
STATE_FLUSH_MS = 200   # ms (frecuencia de volcado de estado)

# ---- Backend fijo (no editable) ----
mido.set_backend("mido.backends.rtmidi")

# ---- Helpers JSON ----
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

# ---- OSC ----
class BroadcastUDPClient(SimpleUDPClient):
    def __init__(self, address, port):
        super().__init__(address, port)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

# ---- Mapa ----
class MidiMap:
    def __init__(self) -> None:
        self.midi_input_name: str = ""
        self.osc_port: int = 1024
        self.osc_ips: List[str] = ["127.0.0.1"]
        self.ui_port: int = 9001
        self.routes: List[Dict[str, Any]] = []
        self.config_name: str = "default"

    @classmethod
    def from_file(cls, path: str) -> "MidiMap":
        data = load_json(path, {})
        mm = cls()
        mm.midi_input_name = data.get("midi_input", "")
        mm.osc_port = int(data.get("osc_port", 1024))
        mm.osc_ips = data.get("osc_ips", ["127.0.0.1"])
        mm.ui_port = int(data.get("ui_port", 9001))
        mm.routes = data.get("routes", [])
        mm.config_name = str(data.get("config_name") or "default")
        return mm

    def persist(self) -> None:
        payload = {
            "midi_input": self.midi_input_name,
            "osc_port": self.osc_port,
            "osc_ips": self.osc_ips,
            "ui_port": self.ui_port,
            "routes": self.routes,
            "config_name": self.config_name,
        }
        save_json(MAP_FILE, payload)
        push_map_to_server(payload)

    def match(self, msg: mido.Message) -> List[Dict[str, Any]]:
        """Devuelve lista de rutas que aplican (normalmente 0 o 1)."""
        hits: List[Dict[str, Any]] = []
        if msg.type in ("note_on", "note_off"):
            note = msg.note
            for r in self.routes:
                if r.get("type") == "note" and int(r.get("note", -1)) == note:
                    hits.append(r)
        elif msg.type == "control_change":
            cc = msg.control
            ch = msg.channel
            for r in self.routes:
                if r.get("type") == "cc" and int(r.get("cc", -1)) == cc:
                    rc = r.get("channel", None)
                    if rc is None or int(rc) == ch:
                        hits.append(r)
        return hits

def build_osc_clients_from_map(map_obj: MidiMap) -> List[BroadcastUDPClient]:
    clients: List[BroadcastUDPClient] = []
    for ip in map_obj.osc_ips:
        try:
            ipaddress.ip_address(ip)
            clients.append(BroadcastUDPClient(ip, map_obj.osc_port))
        except Exception:
            print(f"[WARN] IP inválida ignorada: {ip}")
    print(f"[CORE] OSC → {len(clients)} targets @ port {map_obj.osc_port}")
    return clients

# ---- Notificación WebUI (push de valores) ----
def _notify_webui_async(path: str, value: Any, ui_port: int):
    """POST no bloqueante a la WebUI para empujar estados a los clientes."""
    def _post():
        try:
            payload = json.dumps({
                "path": path,
                "value": value,
                "ts": datetime.now(timezone.utc).isoformat()
            }).encode("utf-8")
            req = urllib.request.Request(
                url=f"http://127.0.0.1:{ui_port}/push_state",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req, timeout=0.2).read()
        except Exception:
            # Si no hay WebUI escuchando, seguimos sin bloquear
            pass
    threading.Thread(target=_post, daemon=True).start()

# ---- Core ----
class OmiMidiCore:
    def __init__(self) -> None:
        self.map = MidiMap.from_file(MAP_FILE)
        self._map_mtime = os.path.getmtime(MAP_FILE) if os.path.exists(MAP_FILE) else 0.0
        self.clients = build_osc_clients_from_map(self.map)
        self.inport: Optional[mido.ports.BaseInput] = None
        self._stop = threading.Event()

        # Estado de rutas (para WebUI)
        self._state_lock = threading.Lock()
        self._state: Dict[str, Dict[str, Any]] = load_json(STATE_FILE, {})
        self._state_dirty = False
        self._last_state_flush = 0.0

    def open_input(self) -> None:
        inputs = mido.get_input_names()
        if not inputs:
            print("[ERROR] No hay dispositivos MIDI. Conecta uno y reinicia.")
            sys.exit(1)

        name = self.map.midi_input_name if self.map.midi_input_name in inputs else inputs[0]
        if name != self.map.midi_input_name:
            self.map.midi_input_name = name
            self.map.persist()

        print(f"[CORE] MIDI IN ← '{name}'")
        self.inport = mido.open_input(name)

    def _rebuild_clients_if_needed(self, new_map: MidiMap):
        ports_changed = (new_map.osc_port != self.map.osc_port)
        ips_changed = (sorted(new_map.osc_ips) != sorted(self.map.osc_ips))
        if ports_changed or ips_changed:
            print("[CORE] Cambió configuración OSC → reconstruyendo clientes...")
            self.clients = build_osc_clients_from_map(new_map)

    def _flush_state_periodically(self):
        """Escribe STATE_FILE como mucho cada STATE_FLUSH_MS, si hay cambios."""
        while not self._stop.is_set():
            now = time.time() * 1000.0
            if self._state_dirty and (now - self._last_state_flush) >= STATE_FLUSH_MS:
                with self._state_lock:
                    save_json(STATE_FILE, self._state)
                    self._state_dirty = False
                    self._last_state_flush = now
            time.sleep(0.05)

    def _kill_existing_webui(self):
        """Mata el proceso de WebUI si hay PID guardado."""
        if not os.path.exists(WEBUI_PID_FILE):
            return
        try:
            with open(WEBUI_PID_FILE, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)
            # breve espera
            time.sleep(0.5)
        except Exception:
            pass
        try:
            os.remove(WEBUI_PID_FILE)
        except Exception:
            pass

    def _check_restart_flag(self):
        """Si hay flag de reinicio, mata WebUI y re-ejecuta el core."""
        if os.path.exists(RESTART_REQ_FILE):
            try:
                os.remove(RESTART_REQ_FILE)
            except Exception:
                pass
            print("[CORE] Reiniciando proceso...")

            # Cerrar recursos antes de re-ejecutar
            try:
                if self.inport:
                    self.inport.close()
            except Exception:
                pass

            # Mata la WebUI existente
            self._kill_existing_webui()

            # Re-ejecuta el mismo script
            python = sys.executable
            script = os.path.abspath(__file__)
            os.execv(python, [python, script])

    def hot_reload_loop(self) -> None:
        while not self._stop.is_set():
            # restart?
            self._check_restart_flag()

            # hot reload del mapa
            try:
                cur = os.path.getmtime(MAP_FILE)
                if cur != self._map_mtime:
                    self._map_mtime = cur
                    new_map = MidiMap.from_file(MAP_FILE)
                    # Reabrir MIDI si cambió el dispositivo
                    if new_map.midi_input_name != self.map.midi_input_name:
                        print(f"[CORE] Cambió dispositivo MIDI: '{self.map.midi_input_name}' → '{new_map.midi_input_name}'")
                        self.map = new_map
                        try:
                            if self.inport:
                                self.inport.close()
                            self.open_input()
                        except Exception as e:
                            print(f"[ERROR] Reabriendo MIDI input: {e}")
                    else:
                        self._rebuild_clients_if_needed(new_map)
                        # Si cambió el UI port, no lo aplicamos en caliente (requiere restart); se leerá tras reinicio
                        self.map = new_map
                        print("[CORE] Mapa recargado.")
            except FileNotFoundError:
                pass
            time.sleep(CHECK_INTERVAL)

    # ---- LEARN: consume petición y crea ruta con el siguiente evento MIDI ----
    def _maybe_consume_learn(self, msg: mido.Message) -> None:
        req = load_json(LEARN_REQ_FILE, {})
        if not req.get("armed"):
            return
        osc = req.get("osc", "/learn")
        vtype = req.get("vtype", "float")
        # Construir ruta desde msg
        if msg.type in ("note_on", "note_off"):
            new_route = {"type": "note", "note": int(msg.note), "osc": osc, "vtype": vtype}
        elif msg.type == "control_change":
            new_route = {"type": "cc", "cc": int(msg.control), "channel": int(msg.channel), "osc": osc, "vtype": vtype}
        else:
            return
        if vtype == "const":
            new_route["const"] = float(req.get("const", 1.0))

        self.map.routes.append(new_route)
        self.map.persist()
        # Marcar completado
        save_json(LEARN_REQ_FILE, {"armed": False, "result": new_route})
        print(f"[CORE][LEARN] Añadida ruta: {new_route}")

    def _update_state(self, path: str, value: Any) -> None:
        with self._state_lock:
            self._state[path] = {
                "value": value,
                "ts": datetime.now(timezone.utc).isoformat()
            }
            self._state_dirty = True

    def write_last_event(self, msg: mido.Message) -> None:
        d: Dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(), "type": msg.type}
        if msg.type in ("note_on", "note_off"):
            d.update({"note": msg.note, "velocity": msg.velocity, "channel": getattr(msg, "channel", None)})
        elif msg.type == "control_change":
            d.update({"cc": msg.control, "value": msg.value, "channel": msg.channel})
        save_json(LAST_EVENT_FILE, d)

    def value_from_msg(self, msg: mido.Message, route: Dict[str, Any]) -> Any:
        vtype = route.get("vtype", "float")
        if vtype == "const":
            return route.get("const", 1.0)
        if msg.type in ("note_on", "note_off"):
            vel = int(getattr(msg, "velocity", 0))
            if vtype == "bool":
                return bool(vel > 0 and msg.type == "note_on")
            if vtype == "int":
                return vel
            return vel / 127.0
        elif msg.type == "control_change":
            val = int(msg.value)
            if vtype == "bool":
                return bool(val > 0)
            if vtype == "int":
                return val
            return val / 127.0
        return 0.0 if vtype != "bool" else False

    def send_osc(self, path: str, value: Any) -> None:
        # Actualiza estado visible por WebUI
        self._update_state(path, value)
        # Notifica a la WebUI (empuje para websockets) usando puerto configurado
        _notify_webui_async(path, value, ui_port=self.map.ui_port)
        # Envía a todos los targets
        for c in self.clients:
            try:
                c.send_message(path, value)
            except Exception as e:
                print(f"[WARN] Error enviando OSC a {c._address}:{c._port} → {e}")

    def stop(self) -> None:
        self._stop.set()
        try:
            if self.inport:
                self.inport.close()
        except Exception:
            pass

    def run(self) -> None:
        print("🎹 OMIMIDI Core — MIDI→OSC")
        self.open_input()
        t_reload = threading.Thread(target=self.hot_reload_loop, daemon=True)
        t_reload.start()
        t_state = threading.Thread(target=self._flush_state_periodically, daemon=True)
        t_state.start()

        try:
            for msg in self.inport:  # bloqueante
                self.write_last_event(msg)
                self._maybe_consume_learn(msg)
                routes = self.map.match(msg)
                for r in routes:
                    val = self.value_from_msg(msg, r)
                    self.send_osc(r["osc"], val)
        except KeyboardInterrupt:
            self._stop.set()
        finally:
            self._stop.set()
            if self.inport:
                self.inport.close()
            cleanup_runtime_files()
            print("[CORE] Bye!")

# ---- Arranque WebUI desde el core ----
def start_webui(host: str = "0.0.0.0", port: int = 9001):
    import uvicorn, multiprocessing
    def run_server():
        # Guardar PID del worker para poder “matarlo” en reinicio
        with open(WEBUI_PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        from midiwebui import app
        uvicorn.run(app, host=host, port=port, log_level="warning")
    p = multiprocessing.Process(target=run_server, daemon=False)
    p.start()
    print(f"[CORE] 🌐 WebUI en http://{host}:{port}")
    return p

def main() -> None:
    core = OmiMidiCore()

    def handle_stop(signum, frame):
        core.stop()

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    web_proc = start_webui(port=core.map.ui_port)
    try:
        core.run()
    finally:
        if web_proc.is_alive():
            web_proc.terminate()
            web_proc.join(timeout=2.0)
        cleanup_runtime_files()


if __name__ == "__main__":
    main()
def push_map_to_server(map_data: Dict[str, Any], *, source: str = "omimidi_core") -> None:
    server_api = os.environ.get("OMI_SERVER_API")
    serial = os.environ.get("OMI_AGENT_SERIAL")
    host = os.environ.get("OMI_AGENT_HOST")
    if not server_api or not serial:
        return
    config_name = str(map_data.get("config_name") or "default")
    payload = json.dumps(
        {
            "name": config_name,
            "data": map_data,
            "serial": serial,
            "host": host,
            "source": source,
            "overwrite": True,
        }
    ).encode("utf-8")
    url = f"{server_api}/api/configs/MIDI"
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:
        print(f"[WARN] Falló sincronización de preset '{config_name}': {exc}")
