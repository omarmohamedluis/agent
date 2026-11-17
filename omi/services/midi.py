"""Immediate MIDI/OSC conversion; no event queue, retry, logging or HTTP here."""
from __future__ import annotations

import array
import ipaddress
import socket
import struct
import sys
import time
import threading
from dataclasses import dataclass
from typing import Callable

from omi.models import MidiConfig, MidiRoute
from .neighbours import NeighbourGate


@dataclass(frozen=True, slots=True)
class MidiEvent:
    type: str
    channel: int
    number: int
    value: int
    received_ns: int

    @classmethod
    def from_dict(cls, message: dict, now_ns: int | None = None) -> MidiEvent:
        kind = message.get("type")
        if kind not in ("note_on", "note_off", "control_change"):
            raise ValueError("Only note_on, note_off and control_change are supported")
        channel = message.get("channel", 0)
        number = message.get("note") if kind != "control_change" else message.get("control", message.get("cc"))
        value = message.get("velocity", 0) if kind != "control_change" else message.get("value")
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (channel, number, value)):
            raise ValueError("MIDI channel, number and value must be integers")
        if not 0 <= channel < 16 or not 0 <= number < 128 or not 0 <= value < 128:
            raise ValueError("MIDI channel/number/value out of range")
        return cls(kind, channel, number, value, time.monotonic_ns() if now_ns is None else now_ns)

    def as_dict(self) -> dict:
        base = {"type": self.type, "channel": self.channel}
        if self.type == "control_change":
            return {**base, "control": self.number, "cc": self.number, "value": self.value}
        return {**base, "note": self.number, "velocity": self.value}


def route_value(event: MidiEvent, route: MidiRoute):
    """Preserve legacy conversion, including note-off release velocity."""
    if route.value_type == "const":
        return route.constant
    if route.value_type == "bool":
        return event.value > 0 and event.type != "note_off"
    if route.value_type == "int":
        return event.value
    return event.value / 127.0


def _osc_string(value: str) -> bytes:
    data = value.encode("utf-8") + b"\0"
    return data + b"\0" * (-len(data) % 4)


def encode_osc(address: str, value) -> bytes:
    if isinstance(value, bool):
        tag, data = (",T" if value else ",F"), b""
    elif isinstance(value, int):
        tag, data = ",i", struct.pack(">i", value)
    elif isinstance(value, float):
        tag, data = ",f", struct.pack(">f", value)
    else:
        tag, data = ",s", _osc_string(str(value))
    return _osc_string(address) + _osc_string(tag) + data


class UdpOutput:
    """Nonblocking send admission. Destinations survive every transient failure.

    SIOCOUTQ rejects new events while their destination socket owns a previous
    datagram. It does not see every packet orphaned to a qdisc/driver queue.
    UDP acceptance cannot promise delivery time after a packet enters the kernel
    or network; that physical boundary is explicitly included in hardware tests.
    """
    def __init__(self, gate=None):
        self.sockets: dict[tuple, socket.socket] = {}
        self.gate = gate if gate is not None else NeighbourGate()
        self._dns_names = ()
        self._dns_cache = {}
        self._dns_stop = threading.Event()
        self._dns_wake = threading.Event()
        self._dns_thread = None

    def _resolve_names(self):
        while not self._dns_stop.is_set():
            self._dns_wake.clear()
            for host in self._dns_names:
                if self._dns_stop.is_set():
                    return
                try:
                    family, _, _, _, address = socket.getaddrinfo(host, 0, type=socket.SOCK_DGRAM)[0]
                    self._dns_cache[host] = (family, address[0])
                except OSError:
                    # Keep last known resolution; failed packets are never saved.
                    pass
            self._dns_wake.wait(5)

    def resolve(self, config: MidiConfig) -> tuple:
        endpoints = []
        names = []
        for target in config.destinations:
            try:
                try:
                    numeric = ipaddress.ip_address(target.host)
                    family = socket.AF_INET if numeric.version == 4 else socket.AF_INET6
                    host = str(numeric)
                except ValueError:
                    names.append(target.host)
                    resolved = self._dns_cache.get(target.host)
                    if resolved is None:
                        endpoints.append((None, (target.host, target.port)))
                        continue
                    family, host = resolved
                address = (host, target.port) if family == socket.AF_INET else (host, target.port, 0, 0)
                key = (family, address)
                if key not in self.sockets:
                    sock = socket.socket(family, socket.SOCK_DGRAM)
                    sock.setblocking(False)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2048)
                    if family == socket.AF_INET:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    self.sockets[key] = sock
                endpoints.append((family, address))
            except OSError:
                # Retain the original configuration; control thread refreshes DNS.
                endpoints.append((None, (target.host, target.port)))
        selected_names = tuple(dict.fromkeys(names))
        if selected_names != self._dns_names:
            self._dns_names = selected_names
            self._dns_cache = {host: value for host, value in self._dns_cache.items() if host in selected_names}
            self._dns_wake.set()
        if selected_names and self._dns_thread is None:
            self._dns_thread = threading.Thread(target=self._resolve_names, name="omi-osc-dns", daemon=True)
            self._dns_thread.start()
        self.gate.configure(endpoints)
        current = set(endpoints)
        for key in tuple(self.sockets):
            if key not in current:
                self.sockets.pop(key).close()
        return tuple(endpoints)

    def send(self, payload: bytes, endpoint) -> bool:
        family, address = endpoint
        if family is None or not self.gate.admits(endpoint):
            return False
        try:
            sock = self.sockets[endpoint]
            if sys.platform.startswith("linux"):
                import fcntl
                pending = array.array("i", [0])
                fcntl.ioctl(sock.fileno(), 0x5411, pending, True)  # SIOCOUTQ
                if pending[0]:
                    return False
            return sock.sendto(payload, address) == len(payload)
        except (OSError, KeyError):
            return False

    def close(self):
        self._dns_stop.set()
        self._dns_wake.set()
        self.gate.close()
        for sock in self.sockets.values():
            sock.close()
        self.sockets.clear()


@dataclass(frozen=True, slots=True)
class Routing:
    routes: dict
    endpoints: tuple
    deadline_ns: int


class MidiEngine:
    """One producer calls process; control atomically replaces immutable state.

    Metrics are counters, and Learn is one overwrite-protected slot. Neither
    produces a stream for a slower UI consumer to drain later.
    """
    def __init__(self, config: MidiConfig, output=None, clock: Callable[[], int] = time.monotonic_ns):
        self.output = output if output is not None else UdpOutput()
        self.clock = clock
        self.muted = False
        self.learn_armed = False
        self.learn_after_ns = -1
        self.discard_before_ns = -1
        self.learn = None
        self.metrics = {key: 0 for key in ("received", "sent", "expired", "muted", "send_dropped", "overruns", "invalid", "clock_dropped")}
        self.apply(config)

    def apply(self, config: MidiConfig):
        routes: dict[tuple[str, int, int | None], list] = {}
        for route in config.routes:
            routes.setdefault((route.kind, route.number, route.channel), []).append(route)
        endpoints = self.output.resolve(config)
        self.routing = Routing({key: tuple(value) for key, value in routes.items()}, endpoints, int(config.deadline_ms * 1_000_000))

    def refresh_destinations(self, config: MidiConfig):
        table = self.routing
        self.routing = Routing(table.routes, self.output.resolve(config), table.deadline_ns)

    def set_editor(self, opened: bool):
        self.muted = opened
        if not opened:
            # Kernel input may still contain events received during the editor.
            # Discard by ingress timestamp even if they are within deadline.
            self.discard_before_ns = self.clock()
            self.learn_armed = False
            self.learn = None

    def arm(self):
        if not self.muted:
            raise RuntimeError("Learn requires an open route editor")
        self.learn = None
        self.learn_after_ns = self.clock()
        self.learn_armed = True

    def process(self, event: MidiEvent):
        self.metrics["received"] += 1
        table = self.routing
        deadline = event.received_ns + table.deadline_ns
        if self.clock() > deadline or event.received_ns < self.discard_before_ns:
            self.metrics["expired"] += 1
            return
        if self.learn_armed and event.received_ns >= self.learn_after_ns:
            self.learn_armed = False
            self.learn = event.as_dict()
        if self.muted:
            self.metrics["muted"] += 1
            return
        kind = "cc" if event.type == "control_change" else "note"
        matched = table.routes.get((kind, event.number, None), ()) + table.routes.get((kind, event.number, event.channel), ())
        for route in matched:
            if self.muted:
                return
            try:
                payload = encode_osc(route.address, route_value(event, route))
            except (OverflowError, struct.error, ValueError):
                self.metrics["invalid"] += 1
                continue
            for endpoint in table.endpoints:
                if self.muted:
                    return
                if self.clock() > deadline:
                    self.metrics["expired"] += 1
                    return
                if self.output.send(payload, endpoint):
                    self.metrics["sent"] += 1
                else:
                    self.metrics["send_dropped"] += 1

    def close(self):
        self.output.close()
