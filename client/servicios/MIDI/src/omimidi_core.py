#!/usr/bin/env python3
from __future__ import annotations
import os, json, time, threading, socket, ipaddress, sys, signal, tempfile, atexit, queue, subprocess
import urllib.request
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import urllib.request
from pathlib import Path

import logging
# Asegurar que el directorio src esté en el path para encontrar midiwebui
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import mido
from pythonosc.udp_client import SimpleUDPClient
from omimidi_utils import load_json, save_json

class RestartRequest(Exception):
    """Excepción interna para solicitar un soft-restart del core."""
    pass

# ==== Archivos ====
BASE_DIR         = Path(__file__).resolve().parents[1]
MAP_FILE         = os.path.join(BASE_DIR, "OMIMIDI_map.json")
# LAST_EVENT_FILE eliminado para evitar I/O innecesario y problemas de permisos
LEARN_REQ_FILE   = os.path.join(BASE_DIR, "OMIMIDI_learn_request.json")   # WebUI arma LEARN; el core lo consume
STATE_FILE       = os.path.join(BASE_DIR, "OMIMIDI_state.json")           # último valor por ruta OSC
RESTART_REQ_FILE = os.path.join(BASE_DIR, "OMIMIDI_restart.flag")         # WebUI solicita reinicio; el core se re-ejecuta
WEBUI_PID_FILE   = os.path.join(BASE_DIR, "OMIMIDI_webui.pid")            # PID de la WebUI para poder matarla
SERVER_INFO_PATH = Path(__file__).resolve().parents[4] / 'client' / 'agent_pi' / 'data' / 'server.json'


from omimidi_logger import get_logger
LOGGER = get_logger("omimidi.core")


CLEANUP_FILES = [
    LEARN_REQ_FILE,
    STATE_FILE,
    WEBUI_PID_FILE,
]


def _load_server_info() -> Dict[str, Any]:
    try:
        with SERVER_INFO_PATH.open('r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}

def push_map_to_server(map_data: Dict[str, Any], *, source: str = "omimidi_core") -> None:
    info = _load_server_info()
    server_api = info.get("api")
    serial = info.get("serial")
    host = info.get("host")
    if not server_api or not serial:
        return
    config_name = str(map_data.get("file_info", {}).get("name") or "default")
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
        LOGGER.warning("Falló sincronización de preset '%s': %s", config_name, exc)
def cleanup_runtime_files():
    for path in CLEANUP_FILES:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception as exc:
            LOGGER.debug("No se pudo eliminar archivo temporal %s: %s", path, exc)

atexit.register(cleanup_runtime_files)

CHECK_INTERVAL = 0.5   # s (hot-reload)
STATE_FLUSH_MS = 20    # ms (frecuencia de volcado de estado para WebUI)

# ---- Backend fijo (no editable) ----
mido.set_backend("mido.backends.rtmidi")

# load_json y save_json ahora se importan de omimidi_utils

# ---- Validation Utils ----
def validate_osc_path(path: str) -> bool:
    """Valida que una ruta OSC sea válida."""
    if not path:
        return False
    if not path.startswith("/"):
        return False
    return True

def validate_midi_config(config: Dict[str, Any]) -> List[str]:
    """Valida la configuración MIDI y retorna lista de errores."""
    errors = []
    
    # Validar puertos
    try:
        osc_port = int(config.get("osc_port", 0))
        if not (1 <= osc_port <= 65535):
            errors.append(f"Puerto OSC inválido: {osc_port}")
    except (TypeError, ValueError):
        errors.append("Puerto OSC debe ser un número")

    try:
        ui_port = int(config.get("ui_port", 0))
        if not (1 <= ui_port <= 65535):
            errors.append(f"Puerto UI inválido: {ui_port}")
    except (TypeError, ValueError):
        errors.append("Puerto UI debe ser un número")

    # Validar IPs
    osc_ips = config.get("osc_ips", [])
    if not isinstance(osc_ips, list):
        errors.append("osc_ips debe ser una lista")
    else:
        for ip in osc_ips:
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                errors.append(f"IP inválida: {ip}")

    # Validar rutas
    routes = config.get("routes", [])
    if not isinstance(routes, list):
        errors.append("routes debe ser una lista")
    else:
        seen_routes = set()
        for i, route in enumerate(routes):
            if not isinstance(route, dict):
                errors.append(f"Ruta {i} inválida: debe ser un objeto")
                continue
                
            # Validar tipo
            rtype = route.get("type")
            if rtype not in ("note", "cc"):
                errors.append(f"Ruta {i}: tipo inválido {rtype}")
                continue

            # Validar número según tipo
            if rtype == "note":
                try:
                    note = int(route["note"])
                    if not (0 <= note <= 127):
                        errors.append(f"Ruta {i}: nota fuera de rango (0-127)")
                except (KeyError, ValueError, TypeError):
                    errors.append(f"Ruta {i}: nota inválida")
            else:  # cc
                try:
                    cc = int(route["cc"])
                    if not (0 <= cc <= 127):
                        errors.append(f"Ruta {i}: CC fuera de rango (0-127)")
                except (KeyError, ValueError, TypeError):
                    errors.append(f"Ruta {i}: CC inválido")

            # Validar canal MIDI (opcional)
            channel = route.get("channel")
            if channel is not None:
                try:
                    ch = int(channel)
                    if not (0 <= ch <= 15):
                        errors.append(f"Ruta {i}: canal fuera de rango (0-15)")
                except (ValueError, TypeError):
                    errors.append(f"Ruta {i}: canal inválido")

            # Validar ruta OSC
            osc_path = route.get("osc", "")
            if not validate_osc_path(osc_path):
                errors.append(f"Ruta {i}: path OSC inválido {osc_path}")
            elif osc_path in seen_routes:
                errors.append(f"Ruta {i}: path OSC duplicado {osc_path}")
            else:
                seen_routes.add(osc_path)

            # Validar tipo de valor
            vtype = route.get("vtype", "float")
            if vtype not in ("float", "int", "bool", "const"):
                errors.append(f"Ruta {i}: tipo de valor inválido {vtype}")
            elif vtype == "const":
                try:
                    float(route.get("const", 0))
                except (ValueError, TypeError):
                    errors.append(f"Ruta {i}: valor constante inválido")

    return errors

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
        self.vlan: int = 100
        self.vlan_active: bool = False

    @classmethod
    def from_file(cls, path: str) -> "MidiMap":
        data = load_json(path, {})
        mm = cls()
        
        # file_info
        info = data.get("file_info", {})
        mm.config_name = str(info.get("name") or data.get("config_name") or "default")
        mm.ui_port = int(info.get("ui_port") or data.get("ui_port") or 9001)
        
        # source
        mm.midi_input_name = data.get("source", {}).get("midi_input") or data.get("midi_input") or ""
        
        # net
        net = data.get("net", {})
        mm.vlan = int(net.get("vlan") or data.get("vlan") or 100)
        mm.vlan_active = bool(net.get("vlan_active") or data.get("vlan_active") or False)
        
        # osc
        osc = data.get("osc", {})
        mm.osc_port = int(osc.get("port") or data.get("osc_port") or 1024)
        mm.osc_ips = osc.get("ips") or data.get("osc_ips") or ["127.0.0.1"]
        
        # routes
        mm.routes = data.get("routes", [])
        
        return mm

    def persist(self) -> None:
        payload = {
            "file_info": {
                "name": self.config_name,
                "ui_port": self.ui_port
            },
            "source": {
                "midi_input": self.midi_input_name
            },
            "net": {
                "vlan": self.vlan,
                "vlan_active": self.vlan_active
            },
            "osc": {
                "ips": self.osc_ips,
                "port": self.osc_port
            },
            "routes": self.routes
        }
        
        # Validar configuración antes de guardar
        errors = validate_midi_config(payload)
        if errors:
            error_msg = "\n".join(errors)
            LOGGER.error("Errores en la configuración:\n%s", error_msg)
            LOGGER.debug("Payload inválido al persistir map: %s", payload)

            # Intento de reparación automática para duplicados de rutas OSC:
            dup_errors = [e for e in errors if "path OSC duplicado" in e]
            if dup_errors and len(dup_errors) == len(errors):
                # Solo hay errores por duplicados: intentamos deduplicar agregando sufijos
                LOGGER.info("Intentando reparación automática de rutas OSC duplicadas...")
                seen = set()
                for idx, r in enumerate(payload.get("routes", [])):
                    osc = r.get("osc", "")
                    if not osc:
                        continue
                    if osc in seen:
                        # Añadir sufijo numérico para hacerlo único
                        new_osc = f"{osc}/{idx}"
                        LOGGER.info("Renombrando ruta OSC duplicada '%s' → '%s'", osc, new_osc)
                        r["osc"] = new_osc
                    seen.add(r.get("osc", ""))

                # Revalidar
                errors2 = validate_midi_config(payload)
                if not errors2:
                    LOGGER.info("Reparación automática exitosa — persistiendo mapa corregido")
                    save_json(MAP_FILE, payload)
                    push_map_to_server(payload)
                    return
                else:
                    error_msg2 = "\n".join(errors2)
                    LOGGER.error("Reparación automática falló: %s", error_msg2)
                    raise ValueError(f"Configuración inválida tras intento de reparación:\n{error_msg2}")

            # Propagar detalles de validación para facilitar depuración
            raise ValueError(f"Configuración inválida:\n{error_msg}")
            
        save_json(MAP_FILE, payload)
        push_map_to_server(payload)

    def match(self, msg: mido.Message) -> List[tuple[int, Dict[str, Any]]]:
        """Devuelve lista de (idx, ruta) que aplican (normalmente 0 o 1)."""
        hits: List[tuple[int, Dict[str, Any]]] = []
        if msg.type in ("note_on", "note_off"):
            note = msg.note
            for idx, r in enumerate(self.routes):
                if r.get("type") == "note" and int(r.get("note", -1)) == note:
                    hits.append((idx, r))
        elif msg.type == "control_change":
            cc = msg.control
            ch = msg.channel
            for idx, r in enumerate(self.routes):
                if r.get("type") == "cc" and int(r.get("cc", -1)) == cc:
                    rc = r.get("channel", None)
                    if rc is None or int(rc) == ch:
                        hits.append((idx, r))
        return hits

def build_osc_clients_from_map(map_obj: MidiMap) -> List[BroadcastUDPClient]:
    clients: List[BroadcastUDPClient] = []
    for ip in map_obj.osc_ips:
        try:
            ipaddress.ip_address(ip)
            clients.append(BroadcastUDPClient(ip, map_obj.osc_port))
        except Exception:
            LOGGER.warning("IP inválida ignorada: %s", ip)
    LOGGER.info("OSC → %s targets @ port %s", len(clients), map_obj.osc_port)
    return clients

# ---- Notificación WebUI (push de valores) ----
def _notify_webui_batch_async(batch: List[Dict[str, Any]], ui_port: int):
    """POST no bloqueante a la WebUI para empujar un lote de estados."""
    if not batch:
        return
        
    def _post():
        try:
            payload = json.dumps({"batch": batch}).encode("utf-8")
            req = urllib.request.Request(
                url=f"http://127.0.0.1:{ui_port}/push_batch",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req, timeout=0.5).read()
        except Exception as exc:
            LOGGER.debug("No se pudo notificar lote a la WebUI: %s", exc)
    threading.Thread(target=_post, daemon=True).start()

def _notify_last_event_async(msg: mido.Message, ui_port: int):
    """Notifica el último evento MIDI a la WebUI para visualización en vivo."""
    def _post():
        try:
            d: Dict[str, Any] = {"type": msg.type, "ts": datetime.now(timezone.utc).isoformat()}
            if msg.type in ("note_on", "note_off"):
                d.update({"note": msg.note, "velocity": msg.velocity, "channel": getattr(msg, "channel", 0)})
            elif msg.type == "control_change":
                d.update({"cc": msg.control, "value": msg.value, "channel": msg.channel})
            
            payload = json.dumps({"event": d}).encode("utf-8")
            req = urllib.request.Request(
                url=f"http://127.0.0.1:{ui_port}/push_last_event",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req, timeout=0.1).read()
        except Exception:
            pass
    threading.Thread(target=_post, daemon=True).start()

def is_reachable(ip: str, timeout: float = 0.5) -> bool:
    """Comprueba si una IP es alcanzable usando el comando ping del sistema."""
    try:
        # -c 1: enviar 1 paquete
        # -W timeout: esperar respuesta (segundos)
        # -n: sin resolución DNS
        res = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), "-n", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return res.returncode == 0
    except Exception:
        return False

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
        self._state: Dict[str, Dict[str, Any]] = {} # Estado solo en memoria
        self._state_queue = queue.Queue()
        self._last_state_flush = 0.0
        self._restart_requested = False

    def open_input(self) -> None:
        inputs = mido.get_input_names()
        if not inputs:
            LOGGER.error("No hay dispositivos MIDI. Conecta uno y reinicia.")
            sys.exit(1)

        name = self.map.midi_input_name if self.map.midi_input_name in inputs else inputs[0]
        if name != self.map.midi_input_name:
            self.map.midi_input_name = name
            self.map.persist()

        LOGGER.info("MIDI IN ← '%s'", name)
        self.inport = mido.open_input(name)

    def _rebuild_clients_if_needed(self, new_map: MidiMap):
        ports_changed = (new_map.osc_port != self.map.osc_port)
        ips_changed = (sorted(new_map.osc_ips) != sorted(self.map.osc_ips))
        if ports_changed or ips_changed:
            LOGGER.info("Cambió configuración OSC → reconstruyendo clientes…")
            self.clients = build_osc_clients_from_map(new_map)

    def _flush_state_periodically(self):
        """Procesa la cola de estados y envía lotes a la WebUI cada STATE_FLUSH_MS."""
        while not self._stop.is_set():
            now = time.time() * 1000.0
            if (now - self._last_state_flush) >= STATE_FLUSH_MS:
                batch = []
                # Vaciar la cola
                while not self._state_queue.empty():
                    try:
                        update = self._state_queue.get_nowait()
                        batch.append(update)
                        # Actualizar estado interno
                        key = str(update["route_idx"])
                        with self._state_lock:
                            self._state[key] = update
                    except queue.Empty:
                        break
                
                if batch:
                    _notify_webui_batch_async(batch, ui_port=self.map.ui_port)
                
                self._last_state_flush = now
            time.sleep(0.01)

    def _kill_existing_webui(self):
        """Mata el proceso de WebUI si hay PID guardado."""
        if not os.path.exists(WEBUI_PID_FILE):
            return
        try:
            with open(WEBUI_PID_FILE, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGKILL)
            # breve espera
            time.sleep(0.2)
        except Exception as exc:
            LOGGER.debug("No se pudo finalizar WebUI previa: %s", exc)
        if os.path.exists(WEBUI_PID_FILE):
            try:
                os.remove(WEBUI_PID_FILE)
            except Exception as exc:
                LOGGER.debug("No se pudo borrar PID file de WebUI: %s", exc)

    def _check_restart_flag(self):
        """Si hay flag de reinicio, mata WebUI y solicita soft-restart."""
        if os.path.exists(RESTART_REQ_FILE):
            try:
                os.remove(RESTART_REQ_FILE)
            except Exception as exc:
                LOGGER.warning("No se pudo limpiar flag de reinicio: %s", exc)
            # Pequeño retraso para que la WebUI pueda enviar la respuesta al navegador
            time.sleep(1.0)
            
            # Mata la WebUI existente
            self._kill_existing_webui()

            LOGGER.info("Solicitando soft-restart del core…")
            self._restart_requested = True
            self._stop.set()

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
                        LOGGER.info("Cambió dispositivo MIDI: '%s' → '%s'", self.map.midi_input_name, new_map.midi_input_name)
                        self.map = new_map
                        try:
                            if self.inport:
                                self.inport.close()
                            self.open_input()
                        except Exception as e:
                            LOGGER.error("Error reabriendo MIDI input: %s", e)
                    else:
                        self._rebuild_clients_if_needed(new_map)
                        # Si cambió el UI port, no lo aplicamos en caliente (requiere restart); se leerá tras reinicio
                        self.map = new_map
                        LOGGER.info("Mapa recargado.")
            except FileNotFoundError:
                pass
            time.sleep(CHECK_INTERVAL)

    # ---- LEARN: consume petición y crea ruta con el siguiente evento MIDI ----
    def _maybe_consume_learn(self, msg: mido.Message) -> None:
        req = load_json(LEARN_REQ_FILE, {})
        if not req.get("armed"):
            return

        candidate: Dict[str, Any] | None = None
        if msg.type in ("note_on", "note_off"):
            candidate = {
                "type": "note",
                "note": int(msg.note),
                "velocity": int(getattr(msg, "velocity", 0)),
                "channel": getattr(msg, "channel", None),
                "message_type": msg.type,
            }
        elif msg.type == "control_change":
            candidate = {
                "type": "cc",
                "cc": int(msg.control),
                "value": int(msg.value),
                "channel": int(msg.channel),
            }

        if not candidate:
            return

        # Etiqueta amigable para la WebUI
        if candidate["type"] == "note":
            note_val = candidate["note"]
            candidate["label"] = f"nota {note_val}"
        else:
            cc_val = candidate["cc"]
            ch = candidate.get("channel")
            chan_part = f" canal {ch}" if ch is not None else ""
            candidate["label"] = f"cc {cc_val}{chan_part}"

        candidate["captured_at"] = datetime.now(timezone.utc).isoformat()

        req["candidate"] = candidate
        # Limpiamos último resultado para que la UI no muestre datos antiguos
        req.pop("result", None)
        save_json(LEARN_REQ_FILE, req)

    def _update_state(self, route_idx: int, path: str, value: Any, route_meta: Dict[str, Any]) -> None:
        update = {
            "route_idx": route_idx,
            "path": path,
            "value": value,
            "ts": datetime.now(timezone.utc).isoformat(),
            "route": {
                "type": route_meta.get("type"),
                "note": route_meta.get("note"),
                "cc": route_meta.get("cc"),
                "channel": route_meta.get("channel"),
            },
        }
        self._state_queue.put(update)

    def write_last_event(self, msg: mido.Message) -> None:
        # Ahora solo notifica a la WebUI en memoria
        _notify_last_event_async(msg, ui_port=self.map.ui_port)

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

    def send_osc(self, route_idx: int, route: Dict[str, Any], value: Any) -> None:
        path = str(route.get("osc", ""))
        if not path.startswith("/"):
            LOGGER.error("Ruta OSC inválida: %s", path)
            return
            
        # Actualiza estado visible por WebUI (vía cola)
        self._update_state(route_idx, path, value, route_meta=route)
        
        # Envía a todos los targets con timeout
        failed_clients = []
        for c in self.clients:
            try:
                # Agregar timeout para evitar bloqueos
                c._sock.settimeout(0.1)
                c.send_message(path, value)
            except socket.timeout:
                LOGGER.warning("Timeout enviando OSC a %s:%s", c._address, c._port)
                failed_clients.append(c)
            except Exception as e:
                LOGGER.warning("Error enviando OSC a %s:%s → %s", c._address, c._port, e)
                failed_clients.append(c)
                
        # Remover clientes que fallaron
        if failed_clients:
            for c in failed_clients:
                if c in self.clients:
                    self.clients.remove(c)
            LOGGER.info("Se removieron %d clientes OSC con fallas", len(failed_clients))

    def stop(self) -> None:
        self._stop.set()
        try:
            if self.inport:
                self.inport.close()
        except Exception as exc:
            LOGGER.debug("Error cerrando puerto MIDI al detener: %s", exc)

    def run(self) -> None:
        LOGGER.info("🎹 OMIMIDI Core — MIDI→OSC")
        self.open_input()
        t_reload = threading.Thread(target=self.hot_reload_loop, daemon=True)
        t_reload.start()
        t_state = threading.Thread(target=self._flush_state_periodically, daemon=True)
        t_state.start()

        try:
            LOGGER.info("Bucle MIDI iniciado. Esperando mensajes...")
            while not self._stop.is_set():
                # poll() es no bloqueante
                msg = self.inport.poll()
                if msg:
                    LOGGER.debug("MIDI Recibido: %s", msg)
                    self.write_last_event(msg)
                    self._maybe_consume_learn(msg)
                    routes = self.map.match(msg)
                    if routes:
                        LOGGER.debug("  -> Coincide con %s rutas", len(routes))
                    for idx, r in routes:
                        val = self.value_from_msg(msg, r)
                        self.send_osc(idx, r, val)
                else:
                    time.sleep(0.005) # Un poco más rápido para RPi
        except Exception as exc:
            LOGGER.error("Error crítico en bucle MIDI: %s", exc, exc_info=True)
        finally:
            self._stop.set()
            try:
                if self.inport:
                    self.inport.close()
            except Exception as exc:
                LOGGER.debug("Error cerrando puerto MIDI al finalizar: %s", exc)
            cleanup_runtime_files()
            LOGGER.info("Bye!")
            
        if self._restart_requested:
            raise RestartRequest()

# ---- Arranque WebUI desde el core ----
# ---- Arranque WebUI desde el core ----
def start_webui(host: str = "0.0.0.0", port: int = 9001):
    print("iniciando webui")
    import uvicorn, multiprocessing
    def run_server():
        # Guardar PID del worker para poder “matarlo” en reinicio
        with open(WEBUI_PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        from midiwebui import app
        uvicorn.run(app, host=host, port=port, log_level="warning")
    p = multiprocessing.Process(target=run_server, daemon=False)
    p.start()
    LOGGER.info("🌐 WebUI en http://%s:%s", host, port)
    return p

def main() -> None:
    LOGGER.info("Iniciando OmiMidiCore (Process ID: %s)...", os.getpid())
    
    web_proc = None
    
    def cleanup():
        """Limpieza al salir"""
        nonlocal web_proc
        if web_proc and web_proc.is_alive():
            LOGGER.info("Deteniendo WebUI...")
            web_proc.terminate()
            web_proc.join(timeout=2.0)
        cleanup_runtime_files()
        
    # Configurar manejo de señales (solo SIGTERM, SIGINT lo maneja KeyboardInterrupt)
    def handle_stop(signum, frame):
        LOGGER.info("\nRecibida señal de parada (%s)", signum)
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_stop)

    try:
        while True:
            try:
                # Verificar disponibilidad de dispositivos MIDI
                inputs = mido.get_input_names()
                LOGGER.info("Dispositivos MIDI disponibles: %s", inputs)
                
                # Crear instancia del core
                LOGGER.info("Creando instancia del core...")
                core = OmiMidiCore()
                
                # Iniciar WebUI si no está corriendo o si cambió el puerto
                if web_proc is None or not web_proc.is_alive():
                    LOGGER.info("Iniciando WebUI en puerto %s...", core.map.ui_port)
                    web_proc = start_webui(port=core.map.ui_port)
                
                LOGGER.info("Iniciando bucle principal...")
                core.run()
                
                # Si run() termina normalmente (sin excepción), salimos del loop
                break
                
            except RestartRequest:
                LOGGER.info("--- REINICIANDO CORE (Soft Restart) ---")
                continue
                
            except Exception as e:
                LOGGER.error("Error en el bucle principal: %s", e, exc_info=True)
                break
    except KeyboardInterrupt:
        LOGGER.info("\n¡Hasta luego! Servicio MIDI detenido por el usuario.")
    finally:
        cleanup()
        LOGGER.info("Servicio finalizado.")


if __name__ == "__main__":
    main()
