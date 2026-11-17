import ctypes as C
import errno
import struct
import threading
import time

import pytest

from omi.models import MidiConfig
from omi.services.alsa import Alsa, AlsaError, ClockUncertain, hardware_identity
from omi.services.worker import MidiWorker


class FakeAlsa:
    present = True
    connections = 0
    closed = 0

    def __init__(self):
        self.target = None

    def connect(self, selected):
        if not self.present:
            raise AlsaError("Chosen controller is not present")
        self.target = selected
        type(self).connections += 1

    def connected(self):
        return self.present

    def read(self, origin):
        return None

    def clock_origin(self):
        return time.monotonic_ns()

    def close(self):
        type(self).closed += 1


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_runtime_disconnect_waits_indefinitely_for_same_controller(monkeypatch):
    class Backend(FakeAlsa):
        pass
    monkeypatch.setattr("omi.services.worker.Alsa", Backend)
    worker = MidiWorker(MidiConfig(input="Chosen :: MIDI"))
    try:
        worker.start()
        assert worker.health == "ready"
        Backend.present = False
        wait_for(lambda: worker.health == "waiting")
        # Exceed the complete initial startup retry budget. Runtime remains live.
        time.sleep(3)
        assert worker.health == "waiting" and worker.thread.is_alive()
        assert worker.config.input == "Chosen :: MIDI"
        Backend.present = True
        wait_for(lambda: worker.health == "ready")
        assert Backend.connections == 2
        assert worker.backend.target == "Chosen :: MIDI"
    finally:
        worker.close()
    assert not worker.thread.is_alive()


def test_initial_absent_controller_fails_after_six_checks_preserving_selection(monkeypatch):
    class Backend(FakeAlsa):
        present = False
        attempts = 0
        def connect(self, selected):
            type(self).attempts += 1
            super().connect(selected)
    monkeypatch.setattr("omi.services.worker.Alsa", Backend)
    worker = MidiWorker(MidiConfig(input="Absent :: Port"))
    try:
        with pytest.raises(RuntimeError, match="not present"):
            worker.start()
        assert Backend.attempts == 6
        assert worker.config.input == "Absent :: Port"
        assert worker.health == "error"
    finally:
        worker.close()


def test_worker_flushes_uncertain_clock_batch_and_continues_capture(monkeypatch):
    class Backend(FakeAlsa):
        sampled = 0
        dropped = 0
        def clock_origin(self):
            type(self).sampled += 1
            if type(self).sampled == 1:
                raise ClockUncertain("Injected scheduling stall")
            return time.monotonic_ns()
        def drop(self):
            type(self).dropped += 1
    monkeypatch.setattr("omi.services.worker.Alsa", Backend)
    worker = MidiWorker(MidiConfig(input="Chosen :: MIDI"))
    try:
        worker.start()
        wait_for(lambda: Backend.sampled > 2)
        assert Backend.dropped == 1
        assert worker.engine.metrics["clock_dropped"] == 1
        assert worker.health == "ready" and worker.thread.is_alive()
    finally:
        worker.close()


class NativeInput:
    def __init__(self, raw=None, code=0):
        self.buffer = C.create_string_buffer(bytes(raw or bytes(28)))
        self.code = code

    def snd_seq_event_input(self, handle, target):
        C.cast(target, C.POINTER(C.c_void_p))[0] = C.addressof(self.buffer)
        return self.code


def native_backend(raw=None, code=0):
    backend = object.__new__(Alsa)
    backend.lib = NativeInput(raw, code)
    backend.handle = C.c_void_p(1)
    backend._check = lambda value: value
    return backend


@pytest.mark.parametrize("event_type,expected", [(6, "note_on"), (7, "note_off"), (10, "control_change")])
def test_linux_alsa_event_abi_preserves_driver_ingress_timestamp(event_type, expected):
    raw = bytearray(28)
    raw[0], raw[1] = event_type, 1
    struct.pack_into("=II", raw, 4, 3, 123)
    raw[16] = 4
    if event_type == 10:
        struct.pack_into("=Ii", raw, 20, 7, 101)
    else:
        raw[17], raw[18] = 7, 101
    backend = native_backend(raw)
    event = backend.read(90_000_000_000)
    assert event.type == expected and event.channel == 4
    assert event.number == 7 and event.value == 101
    assert event.received_ns == 93_000_000_123


def test_alsa_never_restamps_old_or_unstamped_events_as_fresh():
    raw = bytearray(28)
    raw[0], raw[16], raw[17], raw[18] = 6, 0, 60, 127
    assert native_backend(raw).read(time.monotonic_ns()) is False
    assert native_backend(code=-errno.EAGAIN).read(0) is None
    backend = native_backend(code=-errno.ENOSPC)
    dropped = []
    backend.drop = lambda: dropped.append(True)
    with pytest.raises(OverflowError):
        backend.read(0)
    assert dropped == [True]


def test_alsa_clock_sampling_stall_discards_uncertainty_and_recovers_fresh_input(monkeypatch):
    backend = object.__new__(Alsa)
    backend.handle, backend.queue = C.c_void_p(1), 1
    backend._allocate = lambda kind: None
    backend._check = lambda result: result
    class Lib:
        def snd_seq_get_queue_status(self, *args): return 0
        current = (1, 20_000_000)
        def snd_seq_queue_status_get_real_time(self, status): return self.current
        def snd_seq_queue_status_free(self, status): pass
    backend.lib = Lib()
    samples = iter([1_000_000_000, 1_020_100_000])
    monkeypatch.setattr("omi.services.alsa.time.monotonic_ns", lambda: next(samples))
    with pytest.raises(ClockUncertain):
        backend.clock_origin()
    # A later clean sample must recover instead of permanently keeping -20 ms.
    backend.lib.current = (2, 0)
    samples = iter([2_000_000_000, 2_000_100_000])
    origin = backend.clock_origin()
    assert origin == 0
    from omi.services.midi import MidiEngine, MidiEvent
    from omi.models import MidiRoute, OscDestination
    class Output:
        sent = []
        def resolve(self, config): return ("destination",)
        def send(self, payload, endpoint): self.sent.append(payload); return True
    output = Output()
    engine = MidiEngine(MidiConfig(routes=[MidiRoute(kind="cc", number=7)],
                                  destinations=[OscDestination(host="127.0.0.1")]),
                        output, clock=lambda: 2_000_000_000)
    engine.process(MidiEvent("control_change", 0, 7, 1, origin + 1_000_000_000))
    engine.process(MidiEvent("control_change", 0, 7, 2, origin + 2_000_000_000))
    assert engine.metrics["expired"] == 1 and engine.metrics["sent"] == 1


def test_sysfs_usb_identity_uses_serial_and_physical_socket_without_serial(tmp_path):
    card = tmp_path / "card2"
    (card / "device").mkdir(parents=True)
    (card / "idVendor").write_text("1234\n")
    (card / "idProduct").write_text("abcd\n")
    (card / "serial").write_text("unique-controller\n")
    assert hardware_identity(2, tmp_path) == "usb:1234:abcd:unique-controller"
    (card / "serial").unlink()
    assert hardware_identity(2, tmp_path) == "usb:1234:abcd@card2"
    assert hardware_identity(-1, tmp_path) == ""
