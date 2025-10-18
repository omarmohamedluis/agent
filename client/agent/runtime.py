from __future__ import annotations

import copy
import json
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from AppHandler import (
    current_logical_service,
    get_status as get_service_runtime_status,
    set_runtime_env,
    start_service,
)
from heartbeat import UPDATEHB
from jsonconfig import (
    STANDBY_SERVICE,
    discover_services,
    ensure_config,
    get_enabled_service,
    read_config,
    set_active_service,
    set_device_index,
)
from logger import get_agent_logger
from ui import EstandardUse, LoadingUI, ErrorUI, ErrorUIBlink, UIOFF, SyncingUI, UIShutdownProceess
from . import hardware


class AgentRuntime:
    """Encapsulates the OMI agent lifecycle."""

    SOFVERSION = "0.0.1"
    BCAST_PORT = 37020
    SERVER_REPLY_PORT = 37021
    SERVER_TIMEOUT_S = 5.0
    SERVICE_MONITOR_INTERVAL_S = 2.0
    STRUCTURE_PATH = Path(__file__).resolve().parents[1] / "agent_pi" / "data" / "structure.json"
    SERVER_INFO_PATH = Path(__file__).resolve().parents[1] / "agent_pi" / "data" / "server.json"
    SERVER_HTTP_PORT_DEFAULT = 8000

    def __init__(self) -> None:
        self.logger = get_agent_logger()
        self.server_api_base: Optional[str] = None

        self.snapshot_lock = threading.Lock()
        self.config_lock = threading.Lock()
        self.service_lock = threading.RLock()

        self.current_snapshot: Optional[Dict[str, Any]] = None
        self.cfg: Dict[str, Any] = {}

        self.server_online = False
        self.last_server_contact = 0.0

        self.stop_refresh = threading.Event()
        self.refresh_thread: Optional[threading.Thread] = None

        self.service_monitor_stop = threading.Event()
        self.service_monitor_thread: Optional[threading.Thread] = None

        self.service_status: Dict[str, Any] = {
            "expected": STANDBY_SERVICE,
            "actual": None,
            "logical": STANDBY_SERVICE,
            "running": False,
            "pid": None,
            "last_error": None,
            "returncode": None,
            "config_name": None,
            "web_url": None,
            "timestamp": 0.0,
            "error": None,
            "transition": False,
            "progress": 0,
            "stage": None,
        }
        self.service_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _identity_serial(self) -> Optional[str]:
        identity = (self._current_config().get("identity", {}) or {}) if self.cfg else {}
        serial = identity.get("serial")
        return serial

    def _build_ack(self, kind: str, request_id: Optional[str], **extra: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "type": kind,
            "request_id": request_id,
            "serial": self._identity_serial(),
            "timestamp": time.time(),
        }
        payload.update(extra)
        return payload

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------
    def bootstrap(self) -> None:
        self._reset_server_status()
        LoadingUI(0, "INICIANDO")
        time.sleep(1)
        LoadingUI(30, "LEYENDO")

        self.logger.info("Inicializando agente OMI")
        cfg_boot = ensure_config(self.STRUCTURE_PATH, version=self.SOFVERSION)
        self._set_config(cfg_boot)
        identity_boot = cfg_boot.get("identity", {})
        set_runtime_env(
            {
                "OMI_AGENT_SERIAL": identity_boot.get("serial", ""),
                "OMI_AGENT_HOST": identity_boot.get("host", ""),
            }
        )
        try:
            hardware.register_shutdown_callback(self._on_shutdown_button)
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.warning("No se pudo registrar callback de hardware: %s", exc)
        self._update_service_status()

        initial_service = get_enabled_service(cfg_boot) or STANDBY_SERVICE
        try:
            self._apply_active_service(initial_service)
        except Exception as exc:  # pragma: no cover - defensive
            self._set_service_error(f"Inicio fallido: {exc}")

        snap = UPDATEHB(self.STRUCTURE_PATH)
        self._set_snapshot(snap)
        LoadingUI(40, "CARGADO")

    def listen_and_reply(self) -> None:
        self._start_refresh_thread()
        self._start_service_monitor()

        s_listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s_listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s_listen.bind(("", self.BCAST_PORT))
        s_listen.settimeout(0.5)
        self.logger.info("Escuchando broadcast en :%s", self.BCAST_PORT)

        s_reply = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        try:
            while True:
                try:
                    data, addr = s_listen.recvfrom(4096)
                except socket.timeout:
                    continue

                try:
                    payload = json.loads(data.decode("utf-8", "ignore"))
                except Exception as exc:
                    self.logger.warning("Mensaje inválido desde %s: %s", addr[0], exc)
                    continue

                msg_type = payload.get("type")

                if msg_type == "DISCOVER":
                    server_ip = payload.get("server_ip") or addr[0]
                    reply_port = int(payload.get("reply_port", self.SERVER_REPLY_PORT))
                    self._update_server_api(server_ip, payload.get("http_port"))
                    snap = self._get_snapshot()
                    reply = self._build_status_payload(snap)
                    try:
                        s_reply.sendto(json.dumps(reply).encode("utf-8"), (server_ip, reply_port))
                        self.logger.info("Estado enviado a %s:%s", server_ip, reply_port)
                        self._mark_server_seen()
                    except Exception as exc:
                        self.logger.error("Error enviando estado al servidor: %s", exc)

                elif msg_type == "SET_SERVICE":
                    self.logger.info("Solicitud de cambio de servicio desde %s → %s", addr[0], payload.get("service"))
                    self._handle_service_command(payload, s_reply, addr)
                    self._mark_server_seen()

                elif msg_type == "POWER":
                    self.logger.info("Comando de energía '%s' desde %s", payload.get("action"), addr[0])
                    self._handle_power_command(payload, s_reply, addr)
                    self._mark_server_seen()

                elif msg_type == "SET_INDEX":
                    self.logger.info("Actualización de índice → %s (desde %s)", payload.get("index"), addr[0])
                    self._handle_index_command(payload, s_reply, addr)
                    self._mark_server_seen()

                else:
                    self.logger.debug("Mensaje no reconocido de %s: %s", addr[0], msg_type)

        except KeyboardInterrupt:
            self.logger.info("Agente detenido por usuario")
        finally:
            self._stop_service_monitor()
            self._stop_refresh_thread()
            try:
                s_listen.close()
            except Exception:
                pass
            try:
                s_reply.close()
            except Exception:
                pass
            try:
                UIOFF()
            except Exception:
                pass
            try:
                hardware.clear_callbacks()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Snapshot/config helpers
    # ------------------------------------------------------------------
    def _set_config(self, data: Dict[str, Any]) -> None:
        with self.config_lock:
            self.cfg = data

    def _current_config(self) -> Dict[str, Any]:
        with self.config_lock:
            return copy.deepcopy(self.cfg)

    def _update_server_api(self, server_ip: str, http_port: Optional[int]) -> None:
        port = http_port or self.SERVER_HTTP_PORT_DEFAULT
        self.server_api_base = f"http://{server_ip}:{port}"
        identity = self._current_config().get("identity", {}) if self.cfg else {}
        info = {
            "api": self.server_api_base,
            "serial": identity.get("serial", ""),
            "host": identity.get("host", ""),
        }
        set_runtime_env(
            {
                "OMI_SERVER_API": info["api"],
                "OMI_AGENT_SERIAL": info["serial"],
                "OMI_AGENT_HOST": info["host"],
            }
        )
        self._write_server_info(info)
        self._upload_midi_config_to_server()
        self.logger.info("Servidor API detectado en %s", self.server_api_base)

    def _write_server_info(self, info: Dict[str, Any]) -> None:
        try:
            self.SERVER_INFO_PATH.parent.mkdir(parents=True, exist_ok=True)
            self.SERVER_INFO_PATH.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self.logger.warning("No se pudo escribir server.json: %s", exc)

    def _reset_server_status(self) -> None:
        with self.snapshot_lock:
            self.server_online = False
            self.last_server_contact = 0.0

    def _mark_server_seen(self) -> None:
        with self.snapshot_lock:
            self.server_online = True
            self.last_server_contact = time.time()

    def _server_is_online(self) -> bool:
        with self.snapshot_lock:
            if not self.server_online or self.last_server_contact == 0.0:
                return False
            if (time.time() - self.last_server_contact) > self.SERVER_TIMEOUT_S:
                self.server_online = False
                return False
            return True

    def _set_snapshot(self, data: Dict[str, Any]) -> None:
        with self.snapshot_lock:
            self.current_snapshot = data

    def _current_snapshot_copy(self) -> Optional[Dict[str, Any]]:
        with self.snapshot_lock:
            return copy.deepcopy(self.current_snapshot)

    def _get_snapshot(self, use_fallback: bool = True) -> Dict[str, Any]:
        snap = self._current_snapshot_copy()
        if snap is None and use_fallback:
            snap = UPDATEHB(self.STRUCTURE_PATH)
            self._set_snapshot(snap)
        return snap.copy() if isinstance(snap, dict) else {}

    # ------------------------------------------------------------------
    # MIDI config helpers
    # ------------------------------------------------------------------
    def _midi_map_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "servicios" / "MIDI" / "OMIMIDI_map.json"

    def _read_midi_config(self) -> Dict[str, Any]:
        try:
            return json.loads(self._midi_map_path().read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_midi_config(self, data: Dict[str, Any]) -> None:
        self._midi_map_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _upload_midi_config_to_server(self) -> None:
        if not self.server_api_base:
            return
        data = self._read_midi_config()
        if not data:
            return
        info = {
            "name": data.get("config_name", "default"),
            "data": data,
            "serial": self._current_config().get("identity", {}).get("serial", ""),
            "host": self._current_config().get("identity", {}).get("host", ""),
            "source": "client_sync",
            "overwrite": True,
        }
        try:
            payload = json.dumps(info).encode("utf-8")
            req = urllib.request.Request(
                f"{self.server_api_base}/api/configs/MIDI",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            self.logger.info("Preset MIDI sincronizado con el servidor")
        except Exception as exc:
            self.logger.warning("No se pudo sincronizar preset local con servidor: %s", exc)

    def _download_service_config(self, service: str, config_name: str) -> None:
        if not self.server_api_base:
            raise RuntimeError("sin servidor API disponible")
        url = f"{self.server_api_base}/api/configs/{service}/{config_name}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.logger.warning("Preset %s/%s no existe en servidor, se mantiene configuración local", service, config_name)
                return
            raise RuntimeError(f"configuración '{config_name}' no disponible ({exc.code})") from exc
        except Exception as exc:
            raise RuntimeError(f"no se pudo descargar la configuración '{config_name}'") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if service == "MIDI":
            if not isinstance(data, dict):
                raise RuntimeError("datos de configuración MIDI inválidos")
            if config_name and not data.get("config_name"):
                data["config_name"] = config_name
            self._write_midi_config(data)
        else:
            raise RuntimeError(f"descarga de configuración no soportada para {service}")

    # ------------------------------------------------------------------
    # Status management
    # ------------------------------------------------------------------
    def _primary_ip(self, snapshot: Optional[Dict[str, Any]]) -> Optional[str]:
        if not snapshot:
            return None
        ifaces = snapshot.get("ifaces") or []
        wifi = [i for i in ifaces if isinstance(i.get("iface"), str) and i["iface"].lower().startswith("wl")]
        candidates = wifi or ifaces
        for iface in candidates:
            ip = iface.get("ip")
            if ip and not ip.startswith("127."):
                return ip
        return None

    def _update_service_status(self, *, expected: Optional[str] = None, runtime: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if runtime is None:
            runtime = get_service_runtime_status()
        if expected is None:
            expected = get_enabled_service(self._current_config()) or STANDBY_SERVICE

        config_name = None
        web_url = None
        if expected == "MIDI":
            midi_cfg = self._read_midi_config()
            config_name = midi_cfg.get("config_name")
            if bool(runtime.get("running")):
                snap = self._get_snapshot()
                primary_ip = self._primary_ip(snap)
                port = midi_cfg.get("ui_port", 9001)
                if primary_ip:
                    web_url = f"http://{primary_ip}:{port}"

        state = {
            "expected": expected,
            "actual": runtime.get("name"),
            "logical": runtime.get("logical"),
            "running": bool(runtime.get("running")),
            "pid": runtime.get("pid"),
            "last_error": runtime.get("last_error"),
            "returncode": runtime.get("returncode"),
            "config_name": config_name,
            "web_url": web_url,
            "timestamp": time.time(),
            "error": self.service_error,
        }

        with self.service_lock:
            state.setdefault("transition", self.service_status.get("transition", False))
            state.setdefault("progress", self.service_status.get("progress", 0))
            state.setdefault("stage", self.service_status.get("stage"))
            self.service_status.update(state)
            return copy.deepcopy(self.service_status)

    def _get_service_state(self) -> Dict[str, Any]:
        with self.service_lock:
            return copy.deepcopy(self.service_status)

    def _set_service_transition(self, active: bool, *, stage: Optional[str] = None, progress: Optional[int] = None) -> Dict[str, Any]:
        with self.service_lock:
            self.service_status["transition"] = bool(active)
            if stage is not None:
                self.service_status["stage"] = stage
            if progress is not None:
                self.service_status["progress"] = max(0, min(100, int(progress)))
            self.service_status["timestamp"] = time.time()
            return copy.deepcopy(self.service_status)

    def _set_service_error(self, message: str) -> None:
        self.service_error = message
        self.logger.warning(message)
        self._set_service_transition(False, stage="error", progress=100)
        state = self._get_service_state()
        service_label = (state.get("expected") or "").upper()
        ui_label = f"{service_label[:10]} ERR" if service_label else "ERROR"
        try:
            ErrorUIBlink(ui_label)
        except Exception:
            try:
                ErrorUI(ui_label)
            except Exception:
                pass
        self._update_service_status()

    def _clear_service_error(self) -> None:
        if self.service_error:
            self.service_error = None
            self._update_service_status()

    def _render_ui(self, snapshot: Dict[str, Any], state: Dict[str, Any]) -> None:
        """Render the physical UI according to the latest state."""
        try:
            if state.get("transition"):
                stage = state.get("stage") or "Synking"
                progress = state.get("progress") if isinstance(state.get("progress"), int) else 0
                SyncingUI(progress or 0, stage)
            else:
                EstandardUse(snapshot, server_online=self._server_is_online(), json_path=self.STRUCTURE_PATH)
        except Exception:
            # No queremos romper el hilo de refresco por errores del display.
            self.logger.debug("Fallo renderizando UI", exc_info=True)

    def _on_shutdown_button(self) -> None:
        """Callback for future hardware button integration."""
        self.logger.info("Pulsación de botón de apagado detectada (stub).")

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------
    def _refresh_loop(self) -> None:
        """Refresh snapshot + UI at a fixed cadence."""
        interval = 1.0
        while not self.stop_refresh.is_set():
            start = time.time()
            try:
                snapshot = UPDATEHB(self.STRUCTURE_PATH)
                self._set_snapshot(snapshot)
                state = self._get_service_state()
                self._render_ui(snapshot, state)
            except Exception as exc:
                self.logger.exception("Error actualizando snapshot/UI: %s", exc)
            remaining = interval - (time.time() - start)
            sleep_for = remaining if remaining > 0 else 0.1
            self.stop_refresh.wait(sleep_for)

    def _start_refresh_thread(self) -> None:
        if self.refresh_thread and self.refresh_thread.is_alive():
            return
        self.stop_refresh.clear()
        self.refresh_thread = threading.Thread(target=self._refresh_loop, name="omi-refresh", daemon=True)
        self.refresh_thread.start()

    def _stop_refresh_thread(self) -> None:
        self.stop_refresh.set()
        if self.refresh_thread and self.refresh_thread.is_alive():
            self.refresh_thread.join(timeout=1.5)
        self.refresh_thread = None

    def _service_monitor_loop(self) -> None:
        while not self.service_monitor_stop.is_set():
            try:
                cfg = self._current_config()
                expected = get_enabled_service(cfg) or STANDBY_SERVICE
                runtime = get_service_runtime_status()
                state = self._update_service_status(expected=expected, runtime=runtime)
                current_config_name = state.get("config_name") if isinstance(state, dict) else None

                if expected != STANDBY_SERVICE and not runtime.get("running"):
                    rc = runtime.get("returncode")
                    label = (expected or "").upper()
                    message = f"{label} ERROR (rc={rc})" if rc is not None else f"{label} ERROR"
                    self._set_service_error(message)
                    try:
                        self._apply_active_service(expected, config_name=current_config_name)
                        self.logger.info("Servicio '%s' relanzado después de una caída (rc=%s)", expected, rc)
                    except Exception as exc:
                        self.logger.error("No se pudo relanzar '%s': %s", expected, exc)
                        try:
                            self._apply_active_service(STANDBY_SERVICE)
                        except Exception as inner:
                            self.logger.error("No se pudo forzar standby tras fallo: %s", inner)
                elif runtime.get("running") and self.service_error:
                    self._clear_service_error()
            except Exception as exc:
                self.logger.exception("Error en monitor de servicios: %s", exc)
            finally:
                self.service_monitor_stop.wait(self.SERVICE_MONITOR_INTERVAL_S)

    def _start_service_monitor(self) -> None:
        if self.service_monitor_thread and self.service_monitor_thread.is_alive():
            return
        self.service_monitor_stop.clear()
        self.service_monitor_thread = threading.Thread(target=self._service_monitor_loop, name="omi-service-monitor", daemon=True)
        self.service_monitor_thread.start()

    def _stop_service_monitor(self) -> None:
        self.service_monitor_stop.set()
        if self.service_monitor_thread and self.service_monitor_thread.is_alive():
            self.service_monitor_thread.join(timeout=1.5)
        self.service_monitor_thread = None

    # ------------------------------------------------------------------
    # Service orchestration
    # ------------------------------------------------------------------
    def _apply_active_service(self, service: str, *, config_name: Optional[str] = None) -> Dict[str, Any]:
        service = (service or "").strip()
        if not service:
            raise ValueError("nombre de servicio vacío")

        available = discover_services()
        if service not in available:
            raise ValueError(f"servicio desconocido: {service}")

        with self.service_lock:
            snapshot = self._current_config() or read_config(self.STRUCTURE_PATH)
            previous = get_enabled_service(snapshot) or STANDBY_SERVICE
            runtime = get_service_runtime_status()
            running_same = runtime.get("running") and runtime.get("name") == service

            if service == previous and running_same:
                if config_name and service != STANDBY_SERVICE:
                    self._download_service_config(service, config_name)
                cfg = set_active_service(self.STRUCTURE_PATH, service)
                self._set_config(cfg)
                self._update_service_status(expected=service, runtime=runtime)
                return cfg

            if service == STANDBY_SERVICE:
                start_service(STANDBY_SERVICE)
                cfg = set_active_service(self.STRUCTURE_PATH, service)
                self._set_config(cfg)
                self._update_service_status(expected=service)
                self._clear_service_error()
                return cfg

            if config_name:
                self._download_service_config(service, config_name)

            if not start_service(service):
                raise RuntimeError(f"no se pudo iniciar el servicio '{service}'")

            try:
                cfg = set_active_service(self.STRUCTURE_PATH, service)
            except Exception as exc:
                start_service(previous)
                raise exc

            self._set_config(cfg)
            self._update_service_status(expected=service)
            self._clear_service_error()
            return cfg

    def _handle_service_command(self, payload: Dict[str, Any], s_reply: socket.socket, addr) -> None:
        request_id = payload.get("request_id")
        service = payload.get("service")
        config_target = payload.get("config")
        reply_port = int(payload.get("reply_port", addr[1])) if payload.get("reply_port") else self.SERVER_REPLY_PORT
        reply_ip = addr[0]
        def snapshot_state() -> Dict[str, Any]:
            return {
                "services": self._current_config().get("services"),
                "service_state": self._get_service_state(),
            }

        def send_ack(*, ok: bool, transition: bool, stage: str, progress: Optional[int] = None, error: Optional[str] = None):
            snap = snapshot_state()
            payload_ack = self._build_ack(
                "SERVICE_ACK",
                request_id,
                service=service,
                ok=ok,
                error=error,
                services=snap.get("services"),
                service_state=snap.get("service_state"),
                transition=transition,
                stage=stage,
                config=config_target,
            )
            if progress is not None:
                payload_ack["progress"] = max(0, min(100, int(progress)))
            if config_target is None:
                payload_ack.pop("config", None)
            try:
                s_reply.sendto(json.dumps(payload_ack).encode("utf-8"), (reply_ip, reply_port))
            except Exception as exc:
                self.logger.error("Error enviando ACK al servidor: %s", exc)

        def run_transition() -> None:
            try:
                self._apply_active_service(service, config_name=config_target)

                self._set_service_transition(True, stage="abriendo", progress=80)
                SyncingUI(80, "Abriendo")
                send_ack(ok=True, transition=True, stage="abriendo", progress=80)

                self._set_service_transition(False, stage="completado", progress=100)
                SyncingUI(100, f"{service} OK")
                send_ack(ok=True, transition=False, stage="completado", progress=100)
                self.logger.info("Servicio activo cambiado a '%s' por petición de %s", service, addr[0])
            except Exception as exc:
                message = str(exc)
                self._set_service_error(message)
                self._set_service_transition(False, stage="error", progress=100)
                SyncingUI(100, "Error")
                send_ack(ok=False, transition=False, stage="error", progress=100, error=message)
                self.logger.error("Error cambiando servicio a '%s': %s", service, exc)

        self._set_service_transition(True, stage="cerrando", progress=5)
        SyncingUI(10, "Cerrando")
        send_ack(ok=True, transition=True, stage="cerrando", progress=5)

        threading.Thread(target=run_transition, name="omi-service-transition", daemon=True).start()

    def _handle_power_command(self, payload: Dict[str, Any], s_reply: socket.socket, addr) -> None:
        request_id = payload.get("request_id")
        action = (payload.get("action") or "").lower()
        reply_port = int(payload.get("reply_port", addr[1])) if payload.get("reply_port") else self.SERVER_REPLY_PORT
        reply_ip = addr[0]
        ack = self._build_ack("POWER_ACK", request_id, action=action, ok=False, error=None)

        try:
            if action not in {"shutdown", "reboot"}:
                raise ValueError("acción de energía desconocida")

            success = False
            if action == "shutdown":
                UIShutdownProceess(20, "Apagando")
                success = self._run_power_command([
                    ["sudo", "shutdown", "-h", "now"],
                    ["sudo", "/sbin/shutdown", "-h", "now"],
                    ["shutdown", "-h", "now"],
                ])
            else:
                SyncingUI(20, "Reinicio")
                success = self._run_power_command([
                    ["sudo", "reboot"],
                    ["sudo", "/sbin/reboot"],
                    ["reboot"],
                ])

            if not success:
                raise RuntimeError("no se pudo ejecutar el comando")

            ack["ok"] = True
        except Exception as exc:
            ack["error"] = str(exc)
            self.logger.error("Error procesando comando de energía '%s': %s", action, exc)

        try:
            s_reply.sendto(json.dumps(ack).encode("utf-8"), (reply_ip, reply_port))
        except Exception as exc:
            self.logger.error("Error enviando POWER_ACK al servidor: %s", exc)

        if ack["ok"]:
            if action == "shutdown":
                UIShutdownProceess(90, "Apagando")
            else:
                SyncingUI(90, "Reinicio")

    def _handle_index_command(self, payload: Dict[str, Any], s_reply: socket.socket, addr) -> None:
        request_id = payload.get("request_id")
        reply_port = int(payload.get("reply_port", addr[1])) if payload.get("reply_port") else self.SERVER_REPLY_PORT
        reply_ip = addr[0]
        serial = (self._current_config().get("identity", {}) or {}).get("serial")
        new_index = payload.get("index")

        ack = self._build_ack("INDEX_ACK", request_id, index=new_index, ok=False, error=None)

        try:
            if new_index is None:
                raise ValueError("índice no proporcionado")
            cfg = set_device_index(self.STRUCTURE_PATH, int(new_index))
            self._set_config(cfg)
            self._update_service_status()
            ack.update({"ok": True, "index": int(new_index)})
            LoadingUI(60, f"Index #{new_index}")
        except Exception as exc:
            ack["error"] = str(exc)
            self.logger.error("No se pudo actualizar índice: %s", exc)

        try:
            s_reply.sendto(json.dumps(ack).encode("utf-8"), (reply_ip, reply_port))
        except Exception as exc:
            self.logger.error("Error enviando INDEX_ACK al servidor: %s", exc)

    def _run_power_command(self, command_variants: List[List[str]]) -> bool:
        for cmd in command_variants:
            try:
                subprocess.Popen(cmd)
                return True
            except FileNotFoundError:
                continue
            except Exception as exc:
                self.logger.error("Fallo ejecutando %s: %s", cmd, exc)
        return False

    def _build_status_payload(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        cfg = self._current_config()
        identity = cfg.get("identity", {})
        state = self._update_service_status()
        return {
            "type": "AGENT_STATUS",
            "serial": identity.get("serial") or "pi-unknown",
            "index": identity.get("index"),
            "name": identity.get("name"),
            "host": identity.get("host") or "unknown-host",
            "version": cfg.get("version", {}).get("version", self.SOFVERSION),
            "services": cfg.get("services", []),
            "available_services": discover_services(),
            "service_state": state,
            "server_api": self.server_api_base,
            "heartbeat": {
                "cpu": snapshot.get("cpu"),
                "temp": snapshot.get("temp"),
                "ifaces": snapshot.get("ifaces"),
            },
            "logical_service": current_logical_service(),
        }
