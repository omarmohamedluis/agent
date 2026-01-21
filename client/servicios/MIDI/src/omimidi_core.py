"""
Núcleo del Servicio MIDI (OmiMidiCore).
Gestiona la entrada MIDI, el mapeo a mensajes OSC y el envío a clientes configurados.
También maneja la recarga en caliente (hot-reload) y la comunicación de estado con la WebUI.
"""
#!/usr/bin/env python3
from __future__ import annotations
import os
import json
import time
import threading
import socket
import ipaddress
import sys
import signal
import tempfile
import atexit
import queue
import subprocess
import urllib.request
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from pathlib import Path

import logging
# Asegurar que el directorio src esté en el path para encontrar midiwebui
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import mido
from pythonosc.udp_client import SimpleUDPClient
from omimidi_utils import load_json, save_json

class RestartRequest(Exception):
    """Excepción interna para solicitar un reinicio suave (soft-restart) del core."""
    pass

# ==== Archivos ====
BASE_DIR         = Path(__file__).resolve().parents[1]
# Usar ruta de configuración desde variable de entorno, o fallback a configs/Default.json
_env_config = os.environ.get("OMI_CONFIG_PATH")
if _env_config:
    MAP_FILE = str(Path(_env_config).resolve())
else:
    MAP_FILE = os.path.join(BASE_DIR, "configs", "Default.json")
    
# Asegurar que el directorio configs existe si usamos el fallback
if not os.path.exists(os.path.dirname(MAP_FILE)):
    os.makedirs(os.path.dirname(MAP_FILE), exist_ok=True)
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
    """Envía la configuración actual al servidor central para sincronización."""
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
        LOGGER.warning(f"Falló sincronización de preset '{config_name}': {exc}")

def cleanup_runtime_files():
    """Elimina archivos temporales generados durante la ejecución."""
    for path in CLEANUP_FILES:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception as exc:
            LOGGER.debug(f"No se pudo eliminar archivo temporal {path}: {exc}")

atexit.register(cleanup_runtime_files)

CHECK_INTERVAL = 0.5   # s (hot-reload)
STATE_FLUSH_MS = 20    # ms (frecuencia de volcado de estado para WebUI)

# ---- Backend fijo (no editable) ----
mido.set_backend("mido.backends.rtmidi")

# ---- Utilidades de Validación ----
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
        # Soporte para estructura plana y anidada
        osc_section = config.get("osc", {})
        osc_port = int(config.get("osc_port") or osc_section.get("port") or 0)
        
        if not (1 <= osc_port <= 65535):
            errors.append(f"Puerto OSC inválido: {osc_port}")
    except (TypeError, ValueError):
        errors.append("Puerto OSC debe ser un número")

    try:
        # Soporte para estructura plana y anidada
        info_section = config.get("file_info", {})
        ui_port = int(config.get("ui_port") or info_section.get("ui_port") or 0)
        
        if not (1 <= ui_port <= 65535):
            errors.append(f"Puerto UI inválido: {ui_port}")
    except (TypeError, ValueError):
        errors.append("Puerto UI debe ser un número")

    # Validar IPs
    osc_section = config.get("osc", {})
    osc_ips = config.get("osc_ips") or osc_section.get("ips") or []
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
    def __init__(self, address, port, bind_address="0.0.0.0"):
        super().__init__(address, port)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            # Bind to specific interface to ensure broadcast goes out correctly
            self._sock.bind((bind_address, 0))
        except Exception as e:
            LOGGER.warning(f"No se pudo vincular socket a {bind_address}: {e}")

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
        self.net_ip: str = ""

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
        mm.net_ip = str(net.get("ip") or "").strip()
        
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
            LOGGER.error(f"Errores en la configuración:\n{error_msg}")
            LOGGER.debug(f"Payload inválido al persistir map: {payload}")

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
                        LOGGER.info(f"Renombrando ruta OSC duplicada '{osc}' → '{new_osc}'")
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
                    LOGGER.error(f"Reparación automática falló: {error_msg2}")
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
    
    # Determinar IP de origen (Bind IP)
    # Si la VLAN está activa, usamos su IP para asegurar que el broadcast salga por esa interfaz
    bind_ip = "0.0.0.0"
    if map_obj.vlan_active and map_obj.net_ip:
        bind_ip = map_obj.net_ip
        
    for ip in map_obj.osc_ips:
        try:
            ipaddress.ip_address(ip)
            clients.append(BroadcastUDPClient(ip, map_obj.osc_port, bind_address=bind_ip))
        except Exception:
            LOGGER.warning(f"IP inválida ignorada: {ip}")
    LOGGER.info(f"OSC → {len(clients)} targets @ port {map_obj.osc_port} (Bound to: {bind_ip})")
    return clients

# ---- Notificación WebUI (push de valores) ----
# Cola para eventos de WebUI (evita crear hilos por cada mensaje)
_WEBUI_QUEUE = queue.Queue(maxsize=100)  # Backpressure: si la UI es lenta, descartamos eventos viejos
_WEBUI_WORKER_THREAD = None
_WEBUI_STOP_EVENT = threading.Event()

def _webui_worker_loop(ui_port: int):
    """Worker que consume eventos de la cola y los envía a la WebUI."""
    while not _WEBUI_STOP_EVENT.is_set():
        try:
            # Esperamos items (bloqueante con timeout para checkear stop_event)
            item = _WEBUI_QUEUE.get(timeout=0.5)
        except queue.Empty:
            continue

        try:
            # item es (tipo, payload)
            # tipo: 'batch' o 'event'
            msg_type, payload_data = item
            
            endpoint = "/push_batch" if msg_type == 'batch' else "/push_last_event"
            payload_dict = {"batch": payload_data} if msg_type == 'batch' else {"event": payload_data}
            
            payload_bytes = json.dumps(payload_dict).encode("utf-8")
            req = urllib.request.Request(
                url=f"http://127.0.0.1:{ui_port}{endpoint}",
                data=payload_bytes,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req, timeout=0.2).read()
        except Exception:
            # No logueamos cada error para no saturar, solo debug
            pass
        finally:
            _WEBUI_QUEUE.task_done()

def _ensure_webui_worker(ui_port: int):
    global _WEBUI_WORKER_THREAD
    if _WEBUI_WORKER_THREAD is None or not _WEBUI_WORKER_THREAD.is_alive():
        _WEBUI_STOP_EVENT.clear()
        _WEBUI_WORKER_THREAD = threading.Thread(target=_webui_worker_loop, args=(ui_port,), name="MidiWebUIWorker", daemon=True)
        _WEBUI_WORKER_THREAD.start()

def _notify_webui_batch_async(batch: List[Dict[str, Any]], ui_port: int):
    """Encola un lote de estados para la WebUI."""
    if not batch:
        return
    _ensure_webui_worker(ui_port)
    try:
        _WEBUI_QUEUE.put_nowait(('batch', batch))
    except queue.Full:
        pass # Drop si la cola está llena

def _notify_last_event_async(msg: mido.Message, ui_port: int):
    """Encola el último evento MIDI para la WebUI."""
    _ensure_webui_worker(ui_port)
    
    d: Dict[str, Any] = {"type": msg.type, "ts": datetime.now(timezone.utc).isoformat()}
    if msg.type in ("note_on", "note_off"):
        d.update({"note": msg.note, "velocity": msg.velocity, "channel": getattr(msg, "channel", 0)})
    elif msg.type == "control_change":
        d.update({"cc": msg.control, "value": msg.value, "channel": msg.channel})
    
    try:
        _WEBUI_QUEUE.put_nowait(('event', d))
    except queue.Full:
        pass # Drop si la cola está llena


# ---- Core ----
class OmiMidiCore:
    def __init__(self) -> None:
        # Auto-restaurar mapa desde plantilla si falta
        if not os.path.exists(MAP_FILE):
            template_file = MAP_FILE + ".template"
            if os.path.exists(template_file):
                LOGGER.info(f"Restaurando {os.path.basename(MAP_FILE)} desde plantilla...")
                import shutil
                try:
                    shutil.copy(template_file, MAP_FILE)
                except Exception as e:
                    LOGGER.error(f"Error restaurando plantilla: {e}")

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

        LOGGER.info(f"MIDI IN ← '{name}'")
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
            LOGGER.debug(f"No se pudo finalizar WebUI previa: {exc}")
        if os.path.exists(WEBUI_PID_FILE):
            try:
                os.remove(WEBUI_PID_FILE)
            except Exception as exc:
                LOGGER.debug(f"No se pudo borrar PID file de WebUI: {exc}")

    def _check_restart_flag(self):
        """Si hay flag de reinicio, mata WebUI y solicita soft-restart."""
        if os.path.exists(RESTART_REQ_FILE):
            try:
                os.remove(RESTART_REQ_FILE)
            except Exception as exc:
                LOGGER.warning(f"No se pudo limpiar flag de reinicio: {exc}")
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
                        LOGGER.info(f"Cambió dispositivo MIDI: '{self.map.midi_input_name}' → '{new_map.midi_input_name}'")
                        self.map = new_map
                        try:
                            if self.inport:
                                self.inport.close()
                            self.open_input()
                        except Exception as e:
                            LOGGER.error(f"Error reabriendo MIDI input: {e}")
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
            LOGGER.error(f"Ruta OSC inválida: {path}")
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
                LOGGER.warning(f"Timeout enviando OSC a {c._address}:{c._port}")
                failed_clients.append(c)
            except Exception as e:
                LOGGER.warning(f"Error enviando OSC a {c._address}:{c._port} → {e}")
                failed_clients.append(c)
                
        # Remover clientes que fallaron
        if failed_clients:
            for c in failed_clients:
                if c in self.clients:
                    self.clients.remove(c)
            LOGGER.info(f"Se removieron {len(failed_clients)} clientes OSC con fallas")

    def stop(self) -> None:
        self._stop.set()
        try:
            if self.inport:
                self.inport.close()
        except Exception as exc:
            LOGGER.debug(f"Error cerrando puerto MIDI al detener: {exc}")

    def run(self) -> None:
        LOGGER.info("🎹 OMIMIDI Core — MIDI→OSC")
        self.open_input()
        t_reload = threading.Thread(target=self.hot_reload_loop, name="MidiHotReload", daemon=True)
        t_reload.start()
        t_state = threading.Thread(target=self._flush_state_periodically, name="MidiStateFlush", daemon=True)
        t_state.start()

        try:
            LOGGER.info("Bucle MIDI iniciado. Esperando mensajes...")
            while not self._stop.is_set():
                # poll() es no bloqueante
                msg = self.inport.poll()
                if msg:
                    LOGGER.debug(f"MIDI Recibido: {msg}")
                    self.write_last_event(msg)
                    self._maybe_consume_learn(msg)
                    routes = self.map.match(msg)
                    if routes:
                        LOGGER.debug(f"  -> Coincide con {len(routes)} rutas")
                    for idx, r in routes:
                        val = self.value_from_msg(msg, r)
                        self.send_osc(idx, r, val)
                else:
                    time.sleep(0.005) # Un poco más rápido para RPi
        except Exception as exc:
            LOGGER.error(f"Error crítico en bucle MIDI: {exc}", exc_info=True)
        finally:
            self._stop.set()
            try:
                if self.inport:
                    self.inport.close()
            except Exception as exc:
                LOGGER.debug(f"Error cerrando puerto MIDI al finalizar: {exc}")
            cleanup_runtime_files()
            LOGGER.info("Bye!")
            
        if self._restart_requested:
            raise RestartRequest()

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
    
    display_host = host
    if host == "0.0.0.0" or host == "127.0.0.1":
        try:
            display_host = socket.gethostname()
        except Exception:
            pass
            
    LOGGER.info(f"🌐 WebUI en http://{display_host}:{port}")
    return p

def main() -> None:
    LOGGER.info(f"Iniciando OmiMidiCore (Process ID: {os.getpid()})...")
    
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
        LOGGER.info(f"\nRecibida señal de parada ({signum})")
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_stop)

    try:
        while True:
            try:
                # Verificar modo configuración
                if os.environ.get("OMI_CONFIG_MODE") == "1":
                    LOGGER.info("⚠️ MODO CONFIGURACIÓN DETECTADO: Saltando inicialización del Core MIDI/OSC")
                    
                    # Cargar mapa solo para obtener el puerto UI
                    try:
                        temp_map = MidiMap.from_file(MAP_FILE)
                        ui_port = temp_map.ui_port
                    except Exception:
                        ui_port = 9001
                    
                    if web_proc is None or not web_proc.is_alive():
                        LOGGER.info(f"Iniciando WebUI (Solo Config) en puerto {ui_port}...")
                        web_proc = start_webui(port=ui_port)
                    
                    # Mantener el proceso vivo
                    while True:
                        time.sleep(1)
                
                else:
                    # Verificar disponibilidad de dispositivos MIDI
                    inputs = mido.get_input_names()
                    LOGGER.info(f"Dispositivos MIDI disponibles: {inputs}")
                    
                    # Crear instancia del core
                    LOGGER.info("Creando instancia del core...")
                    core = OmiMidiCore()
                    
                    # Iniciar WebUI si no está corriendo o si cambió el puerto
                    if web_proc is None or not web_proc.is_alive():
                        LOGGER.info(f"Iniciando WebUI en puerto {core.map.ui_port}...")
                        web_proc = start_webui(port=core.map.ui_port)
                    
                    LOGGER.info("Iniciando bucle principal...")
                    core.run()
                    
                    # Si run() termina normalmente (sin excepción), salimos del loop
                    break
                
            except RestartRequest:
                LOGGER.info("--- REINICIANDO CORE (Soft Restart) ---")
                continue
                
            except Exception as e:
                LOGGER.error(f"Error en el bucle principal: {e}", exc_info=True)
                break
    except KeyboardInterrupt:
        LOGGER.info("\n¡Hasta luego! Servicio MIDI detenido por el usuario.")
    finally:
        cleanup()

if __name__ == "__main__":
    main()
