from __future__ import annotations

import json
import logging
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Optional

from .. import db
from .registry import DeviceRegistry
from .settings import Settings
from .errors import api_error

LOGGER = logging.getLogger("omi.server.broadcast")


class PendingRequest:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.payload: Optional[Dict] = None

    def update(self, payload: Dict) -> None:
        self.payload = payload

    def set(self, payload: Dict) -> None:
        self.payload = payload
        self.event.set()

    def wait(self, timeout: float) -> Dict:
        if not self.event.wait(timeout):
            raise TimeoutError
        return self.payload or {}


class BroadcastManager:
    def __init__(self, settings: Settings, registry: DeviceRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self.stop_evt = threading.Event()
        self.broadcast_thread: Optional[threading.Thread] = None
        self.listen_thread: Optional[threading.Thread] = None
        self.command_socket: Optional[socket.socket] = None
        self.pending: Dict[str, PendingRequest] = {}
        self.pending_lock = threading.Lock()
        self.pending_index: set[str] = set()

    def start(self) -> None:
        if self.broadcast_thread and self.broadcast_thread.is_alive():
            return
        self.stop_evt.clear()
        self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.command_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.command_socket.bind(("", 0))

        self.broadcast_thread = threading.Thread(target=self._broadcast_loop, name="omi-broadcast", daemon=True)
        self.listen_thread = threading.Thread(target=self._listen_loop, name="omi-listen", daemon=True)
        self.broadcast_thread.start()
        self.listen_thread.start()

    def stop(self) -> None:
        self.stop_evt.set()
        if self.broadcast_thread:
            self.broadcast_thread.join(timeout=1.5)
        if self.listen_thread:
            self.listen_thread.join(timeout=1.5)
        if self.command_socket:
            try:
                self.command_socket.close()
            except Exception:
                pass
        with self.pending_lock:
            for pending in self.pending.values():
                pending.set({"ok": False, "error": "shutdown"})
            self.pending.clear()
            self.pending_index.clear()

    def _broadcast_loop(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            while not self.stop_evt.is_set():
                payload = {
                    "type": "DISCOVER",
                    "server_ip": self._local_ip(),
                    "reply_port": self.settings.reply_port,
                    "http_port": self.settings.http_port,
                    "ts": time.time(),
                }
                try:
                    s.sendto(json.dumps(payload).encode("utf-8"), (self.settings.broadcast_ip, self.settings.broadcast_port))
                    LOGGER.debug("Broadcast DISCOVER → %s:%s", self.settings.broadcast_ip, self.settings.broadcast_port)
                except Exception as exc:
                    LOGGER.error("Error enviando broadcast: %s", exc)
                for _ in range(int(self.settings.discover_interval * 10)):
                    if self.stop_evt.is_set():
                        break
                    time.sleep(0.1)
        finally:
            s.close()

    def _listen_loop(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", self.settings.reply_port))
        s.settimeout(0.5)
        try:
            while not self.stop_evt.is_set():
                try:
                    data, addr = s.recvfrom(4096)
                except socket.timeout:
                    continue
                except Exception as exc:
                    LOGGER.error("Error de socket en listener: %s", exc)
                    continue

                try:
                    payload = json.loads(data.decode("utf-8", "ignore"))
                except Exception:
                    LOGGER.warning("JSON inválido recibido: %r", data)
                    continue

                msg_type = payload.get("type")

                if msg_type == "AGENT_STATUS":
                    assigned_index, reported_index = self.registry.update_from_status(payload, addr)
                    serial = payload.get("serial") or addr[0]
                    LOGGER.info("Estado recibido de %s", serial)
                    if assigned_index is not None and payload.get("serial") and assigned_index != reported_index:
                        try:
                            self.request_index_update(payload["serial"], assigned_index)
                        except Exception as exc:
                            LOGGER.error("No se pudo actualizar índice de %s: %s", payload["serial"], exc)

                elif msg_type == "SERVICE_ACK":
                    self._handle_service_ack(payload)

                elif msg_type == "POWER_ACK":
                    request_id = payload.get("request_id")
                    if request_id:
                        with self.pending_lock:
                            pending = self.pending.pop(request_id, None)
                        if pending:
                            pending.set(payload)
                    serial = payload.get("serial")
                    if serial:
                        LOGGER.info(
                            "ACK de energía (%s) recibido de %s (ok=%s)",
                            payload.get("action"),
                            serial,
                            payload.get("ok"),
                        )

                elif msg_type == "INDEX_ACK":
                    request_id = payload.get("request_id")
                    if request_id:
                        with self.pending_lock:
                            pending = self.pending.pop(request_id, None)
                        if pending:
                            pending.set(payload)
                    serial = payload.get("serial")
                    if serial:
                        with self.pending_lock:
                            self.pending_index.discard(serial)
                        self.registry.update_index(serial, payload.get("index"))
                        LOGGER.info(
                            "ACK de índice recibido de %s (index=%s, ok=%s)",
                            serial,
                            payload.get("index"),
                            payload.get("ok"),
                        )
                else:
                    LOGGER.debug("Mensaje desconocido de %s: %s", addr[0], payload)
        finally:
            s.close()

    def _handle_service_ack(self, payload: Dict) -> None:
        request_id = payload.get("request_id")
        transition = bool(payload.get("transition"))
        serial = payload.get("serial")
        stage = payload.get("stage")
        ok_flag = payload.get("ok")
        progress = payload.get("progress")
        if request_id:
            if transition:
                with self.pending_lock:
                    pending = self.pending.get(request_id)
                if pending:
                    pending.update(payload)
            else:
                with self.pending_lock:
                    pending = self.pending.pop(request_id, None)
                if pending:
                    pending.set(payload)
        if serial:
            self.registry.update_services(
                serial,
                payload.get("services"),
                payload.get("service_state"),
                transition=transition,
                progress=progress,
                stage=stage,
            )
            LOGGER.info(
                "ACK de servicio recibido de %s (ok=%s, transition=%s, stage=%s)",
                serial,
                ok_flag,
                transition,
                stage,
            )

    def _local_ip(self) -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"
        finally:
            s.close()

    def request_service_change(self, serial: str, service: str, *, config: Optional[str] = None, timeout: float = 25.0) -> Dict[str, Any]:
        device = self.registry.get_device(serial)
        if not device:
            api_error(
                400,
                "dispositivo desconocido",
                logger=LOGGER,
                context={"serial": serial, "operation": "request_service_change"},
            )
        if not device.get("ip"):
            api_error(
                400,
                "no se conoce la IP del dispositivo",
                logger=LOGGER,
                context={"serial": serial, "operation": "request_service_change"},
            )

        request_id = str(uuid.uuid4())
        message = {
            "type": "SET_SERVICE",
            "service": service,
            "request_id": request_id,
            "reply_port": self.settings.reply_port,
        }
        if config:
            message["config"] = config

        pending = PendingRequest()
        with self.pending_lock:
            self.pending[request_id] = pending

        self._send_command(device["ip"], message, serial, f"SET_SERVICE({service})")

        try:
            reply = pending.wait(timeout)
        except TimeoutError:
            with self.pending_lock:
                self.pending.pop(request_id, None)
            api_error(
                504,
                "el agente no respondió al cambio de servicio",
                logger=LOGGER,
                context={"serial": serial, "service": service},
            )

        if not reply.get("ok"):
            reason = reply.get("error") or "error desconocido"
            api_error(
                400,
                reason,
                logger=LOGGER,
                context={"serial": serial, "service": service},
            )

        db.upsert_device(serial, desired_service=service, desired_config=config)
        return reply

    def request_power_action(self, serial: str, action: str, timeout: float = 10.0) -> Dict[str, Any]:
        action = (action or "").lower()
        if action not in {"shutdown", "reboot"}:
            api_error(
                400,
                "acción de energía no soportada",
                logger=LOGGER,
                context={"serial": serial, "action": action},
            )

        device = self.registry.get_device(serial)
        if not device:
            api_error(
                400,
                "dispositivo desconocido",
                logger=LOGGER,
                context={"serial": serial, "operation": "request_power_action"},
            )
        if not device.get("ip"):
            api_error(
                400,
                "no se conoce la IP del dispositivo",
                logger=LOGGER,
                context={"serial": serial, "operation": "request_power_action"},
            )

        request_id = str(uuid.uuid4())
        message = {
            "type": "POWER",
            "action": action,
            "request_id": request_id,
            "reply_port": self.settings.reply_port,
        }

        pending = PendingRequest()
        with self.pending_lock:
            self.pending[request_id] = pending

        self._send_command(device["ip"], message, serial, f"POWER({action})")

        try:
            reply = pending.wait(timeout)
        except TimeoutError:
            with self.pending_lock:
                self.pending.pop(request_id, None)
            api_error(
                504,
                "el agente no confirmó la orden de energía",
                logger=LOGGER,
                context={"serial": serial, "action": action},
            )

        return reply or {"ok": False, "error": "sin respuesta"}

    def request_index_update(self, serial: str, index: int, timeout: float = 10.0) -> Dict[str, Any]:
        device = self.registry.get_device(serial)
        if not device:
            api_error(
                400,
                "dispositivo desconocido",
                logger=LOGGER,
                context={"serial": serial, "operation": "request_index_update"},
            )
        if not device.get("ip"):
            api_error(
                400,
                "no se conoce la IP del dispositivo",
                logger=LOGGER,
                context={"serial": serial, "operation": "request_index_update"},
            )

        with self.pending_lock:
            if serial in self.pending_index:
                return {"ok": True, "pending": True}

        request_id = str(uuid.uuid4())
        message = {
            "type": "SET_INDEX",
            "index": int(index),
            "request_id": request_id,
            "reply_port": self.settings.reply_port,
        }

        pending = PendingRequest()
        with self.pending_lock:
            self.pending[request_id] = pending
            self.pending_index.add(serial)

        self._send_command(device["ip"], message, serial, f"SET_INDEX({index})")

        try:
            reply = pending.wait(timeout)
        except TimeoutError:
            with self.pending_lock:
                self.pending.pop(request_id, None)
                self.pending_index.discard(serial)
            api_error(
                504,
                "el agente no confirmó la actualización de índice",
                logger=LOGGER,
                context={"serial": serial, "index": index},
            )

        with self.pending_lock:
            self.pending_index.discard(serial)
        return reply or {"ok": False, "error": "sin respuesta"}

    def _send_command(self, ip: str, message: Dict[str, Any], serial: str, label: str) -> None:
        if not self.command_socket:
            api_error(
                500,
                "socket de comando no disponible",
                logger=LOGGER,
                context={"serial": serial, "label": label},
            )
        try:
            self.command_socket.sendto(json.dumps(message).encode("utf-8"), (ip, self.settings.broadcast_port))
            LOGGER.info("Comando %s → %s", label, serial)
        except Exception as exc:
            with self.pending_lock:
                for req_id, pending in list(self.pending.items()):
                    if pending.payload is None:
                        pending.set({"ok": False, "error": str(exc)})
                        self.pending.pop(req_id, None)
            api_error(
                500,
                "error enviando comando",
                logger=LOGGER,
                context={"serial": serial, "label": label, "error": str(exc)},
            )
