"""Small libasound sequencer binding with kernel ingress timestamps.

See https://www.alsa-project.org/alsa-doc/alsa-lib/seq.html (timestamped
subscriptions). No RtMidi/Mido queue is interposed. MIDI pre-ALSA hardware/USB
latency is outside the observable deadline boundary.
"""
from __future__ import annotations

import ctypes as C
import errno
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .midi import MidiEvent


class AlsaError(RuntimeError):
    pass


class ClockUncertain(AlsaError):
    """The queue clock sample spanned a scheduling stall and cannot date input."""


@dataclass(frozen=True)
class Port:
    client: int
    port: int
    client_name: str
    port_name: str
    hardware_id: str = ""

    @property
    def name(self):
        # Client numbers change on USB reconnect. Names and port names survive.
        suffix = f" [{self.hardware_id}]" if self.hardware_id else ""
        return f"{self.client_name} :: {self.port_name}{suffix}"


def hardware_identity(card: int, sound_root: Path = Path("/sys/class/sound")) -> str:
    """USB serial when available; physical USB socket for serial-less devices.

    The suffix disambiguates identical controllers and survives ALSA card/client
    renumbering. Without a device serial, moving to another USB socket is an
    explicit new input selection, because device identity cannot be inferred.
    """
    if card < 0:
        return ""
    try:
        device = (sound_root / f"card{card}" / "device").resolve(strict=True)
        for parent in (device, *device.parents):
            vendor, product = parent / "idVendor", parent / "idProduct"
            if vendor.is_file() and product.is_file():
                model = f"{vendor.read_text().strip()}:{product.read_text().strip()}"
                serial_path = parent / "serial"
                serial = serial_path.read_text().strip() if serial_path.is_file() else ""
                return f"usb:{model}:{serial[:64]}" if serial else f"usb:{model}@{parent.name}"
    except OSError:
        pass
    return ""


def select_port(selection: str, ports: list[Port]) -> Port | None:
    matching = [port for port in ports if port.name == selection]
    if len(matching) > 1:
        raise AlsaError(f"Ambiguous MIDI input {selection!r}; give identical devices unique ALSA names")
    return matching[0] if matching else None


class Alsa:
    def __init__(self):
        if not sys.platform.startswith("linux"):
            raise AlsaError("Production MIDI requires Linux ALSA; use --simulate for development")
        try:
            self.lib = C.CDLL("libasound.so.2")
        except OSError as exc:
            raise AlsaError("libasound.so.2 is not installed") from exc
        self.handle = C.c_void_p()
        self.queue = -1
        self.port = -1
        self.selected: Port | None = None
        self._bind()
        self._check(self.lib.snd_seq_open(C.byref(self.handle), b"default", 3, 1))
        try:
            self._check(self.lib.snd_seq_set_client_name(self.handle, b"OMI MIDI OSC"))
            self._check(self.lib.snd_seq_set_client_pool_input(self.handle, 64))
            self._check(self.lib.snd_seq_set_input_buffer_size(self.handle, 1024))
        except BaseException:
            self.close()
            raise

    def _bind(self):
        p, i, s = C.c_void_p, C.c_int, C.c_char_p
        signatures = {
            "snd_seq_open": ([C.POINTER(p), s, i, i], i),
            "snd_seq_close": ([p], i),
            "snd_seq_set_client_name": ([p, s], i),
            "snd_seq_set_client_pool_input": ([p, C.c_size_t], i),
            "snd_seq_set_input_buffer_size": ([p, C.c_size_t], i),
            "snd_seq_client_id": ([p], i),
            "snd_seq_create_simple_port": ([p, s, C.c_uint, C.c_uint], i),
            "snd_seq_alloc_named_queue": ([p, s], i),
            "snd_seq_free_queue": ([p, i], i),
            "snd_seq_control_queue": ([p, i, i, i, p], i),
            "snd_seq_drain_output": ([p], i),
            "snd_seq_drop_input": ([p], i),
            "snd_seq_event_input": ([p, C.POINTER(p)], i),
            "snd_seq_subscribe_port": ([p, p], i),
            "snd_seq_port_subscribe_set_sender": ([p, p], None),
            "snd_seq_port_subscribe_set_dest": ([p, p], None),
            "snd_seq_port_subscribe_set_queue": ([p, i], None),
            "snd_seq_port_subscribe_set_time_update": ([p, i], None),
            "snd_seq_port_subscribe_set_time_real": ([p, i], None),
            "snd_seq_query_next_client": ([p, p], i),
            "snd_seq_query_next_port": ([p, p], i),
            "snd_seq_get_any_port_info": ([p, i, i, p], i),
            "snd_seq_client_info_set_client": ([p, i], None),
            "snd_seq_client_info_get_client": ([p], i),
            "snd_seq_client_info_get_name": ([p], s),
            "snd_seq_client_info_get_card": ([p], i),
            "snd_seq_port_info_set_client": ([p, i], None),
            "snd_seq_port_info_set_port": ([p, i], None),
            "snd_seq_port_info_get_port": ([p], i),
            "snd_seq_port_info_get_capability": ([p], C.c_uint),
            "snd_seq_port_info_get_type": ([p], C.c_uint),
            "snd_seq_port_info_get_name": ([p], s),
            "snd_seq_get_queue_status": ([p, i, p], i),
            "snd_seq_queue_status_get_real_time": ([p], C.POINTER(C.c_uint)),
            "snd_strerror": ([i], s),
        }
        for kind in ("client_info", "port_info", "port_subscribe", "queue_status"):
            signatures[f"snd_seq_{kind}_malloc"] = ([C.POINTER(p)], i)
            signatures[f"snd_seq_{kind}_free"] = ([p], None)
        for name, (args, result) in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, result

    def _check(self, result):
        if result < 0:
            raise AlsaError(self.lib.snd_strerror(result).decode("utf-8", "replace"))
        return result

    def _allocate(self, kind):
        value = C.c_void_p()
        self._check(getattr(self.lib, f"snd_seq_{kind}_malloc")(C.byref(value)))
        return value

    def inputs(self) -> list[Port]:
        ci, pi = self._allocate("client_info"), self._allocate("port_info")
        result = []
        try:
            self.lib.snd_seq_client_info_set_client(ci, -1)
            while self.lib.snd_seq_query_next_client(self.handle, ci) >= 0:
                client = self.lib.snd_seq_client_info_get_client(ci)
                if client == self.lib.snd_seq_client_id(self.handle):
                    continue
                client_name = self.lib.snd_seq_client_info_get_name(ci).decode("utf-8", "replace")
                identity = hardware_identity(self.lib.snd_seq_client_info_get_card(ci))
                self.lib.snd_seq_port_info_set_client(pi, client)
                self.lib.snd_seq_port_info_set_port(pi, -1)
                while self.lib.snd_seq_query_next_port(self.handle, pi) >= 0:
                    caps = self.lib.snd_seq_port_info_get_capability(pi)
                    types = self.lib.snd_seq_port_info_get_type(pi)
                    if caps & 0x21 != 0x21 or not types & 0x02:  # READ + SUBS_READ, MIDI_GENERIC
                        continue
                    result.append(Port(client, self.lib.snd_seq_port_info_get_port(pi), client_name,
                                       self.lib.snd_seq_port_info_get_name(pi).decode("utf-8", "replace"), identity))
        finally:
            self.lib.snd_seq_client_info_free(ci)
            self.lib.snd_seq_port_info_free(pi)
        return result

    def connect(self, selection: str):
        target = select_port(selection, self.inputs())
        if target is None:
            raise AlsaError(f"MIDI input not found: {selection!r}")
        self.port = self._check(self.lib.snd_seq_create_simple_port(self.handle, b"capture", 0x02 | 0x40, 0x02 | 0x100000))
        self.queue = self._check(self.lib.snd_seq_alloc_named_queue(self.handle, b"OMI ingress timestamps"))
        self._check(self.lib.snd_seq_control_queue(self.handle, self.queue, 30, 0, None))
        self._check(self.lib.snd_seq_drain_output(self.handle))
        sub = self._allocate("port_subscribe")
        try:
            sender = (C.c_ubyte * 2)(target.client, target.port)
            receiver = (C.c_ubyte * 2)(self.lib.snd_seq_client_id(self.handle), self.port)
            self.lib.snd_seq_port_subscribe_set_sender(sub, sender)
            self.lib.snd_seq_port_subscribe_set_dest(sub, receiver)
            self.lib.snd_seq_port_subscribe_set_queue(sub, self.queue)
            self.lib.snd_seq_port_subscribe_set_time_update(sub, 1)
            self.lib.snd_seq_port_subscribe_set_time_real(sub, 1)
            self._check(self.lib.snd_seq_subscribe_port(self.handle, sub))
            self.selected = target
        finally:
            self.lib.snd_seq_port_subscribe_free(sub)
        self.drop()

    def connected(self) -> bool:
        if self.selected is None:
            return False
        # Compare names as well as address: ALSA can reuse a disappeared client ID.
        return self.selected in self.inputs()

    def drop(self):
        self._check(self.lib.snd_seq_drop_input(self.handle))

    def clock_origin(self) -> int:
        status = self._allocate("queue_status")
        try:
            sampled_before = time.monotonic_ns()
            self._check(self.lib.snd_seq_get_queue_status(self.handle, self.queue, status))
            value = self.lib.snd_seq_queue_status_get_real_time(status)
            queue_ns = value[0] * 1_000_000_000 + value[1]
            sampled_after = time.monotonic_ns()
            if sampled_after - sampled_before > 1_000_000:
                # Do not let a stall inside GET_QUEUE_STATUS poison every future
                # event. Discard the uncertain batch and calibrate afresh next
                # iteration. ALSA subscription stamps and queue status both use
                # the same interpolated sequencer clock.
                raise ClockUncertain("ALSA clock sampling exceeded 1 ms; input discarded")
            # The kernel sampled between before/after. Using the lower bracket
            # bound conservatively ages this batch by at most its uncertainty.
            return sampled_before - queue_ns
        finally:
            self.lib.snd_seq_queue_status_free(status)

    def read(self, origin_ns: int) -> MidiEvent | None | bool:
        pointer = C.c_void_p()
        result = self.lib.snd_seq_event_input(self.handle, C.byref(pointer))
        if result == -errno.EAGAIN:
            return None
        if result == -errno.ENOSPC:
            self.drop()
            raise OverflowError("ALSA input overrun; pending input discarded")
        self._check(result)
        raw = C.string_at(pointer, 28)  # Linux snd_seq_event_t fixed header/data layout.
        if raw[0] not in (6, 7, 10):
            return False
        if not raw[1] & 1:  # Never assign a fresh timestamp to an unstamped backlog.
            return False
        seconds, nanos = struct.unpack_from("=II", raw, 4)
        received_ns = origin_ns + seconds * 1_000_000_000 + nanos
        if raw[0] in (6, 7):
            return MidiEvent("note_on" if raw[0] == 6 else "note_off", raw[16], raw[17], raw[18], received_ns)
        number, value = struct.unpack_from("=Ii", raw, 20)
        if not 0 <= number <= 127 or not 0 <= value <= 127:
            return False
        return MidiEvent("control_change", raw[16], number, value, received_ns)

    def close(self):
        if self.handle.value:
            self.lib.snd_seq_close(self.handle)
            self.handle = C.c_void_p()
