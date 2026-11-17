import struct
import time
import socket
import threading

import pytest

from omi.models import MidiConfig, MidiRoute, OscDestination
from omi.services.alsa import AlsaError, Port, select_port
from omi.services.midi import MidiEngine, MidiEvent, UdpOutput, encode_osc, route_value


class Output:
    def __init__(self, callback=None):
        self.sent = []
        self.callback = callback
        self.fail = False

    def resolve(self, config):
        return tuple((x.host, x.port) for x in config.destinations)

    def send(self, payload, target):
        if self.callback:
            self.callback()
        if self.fail:
            return False
        self.sent.append((payload, target))
        return True

    def close(self):
        pass


def config(routes=None):
    return MidiConfig(input="Controller :: MIDI", destinations=[OscDestination(host="127.0.0.1")],
                      routes=routes or [MidiRoute(kind="cc", number=7, address="/fader")])


@pytest.mark.parametrize("kind,value,vtype,expected", [
    ("note_on", 127, "float", 1.0), ("note_on", 64, "int", 64),
    ("note_off", 53, "int", 53), ("note_off", 53, "float", 53 / 127),
    ("note_off", 53, "bool", False), ("note_on", 0, "bool", False),
    ("note_on", 100, "bool", True), ("control_change", 0, "bool", False),
    ("control_change", 1, "bool", True), ("control_change", 64, "float", 64 / 127),
    ("note_off", 0, "const", "trigger"),
])
def test_preserves_legacy_value_conversion(kind, value, vtype, expected):
    event = MidiEvent(kind, 0, 60, value, 0)
    route = MidiRoute(value_type=vtype, constant="trigger")
    assert route_value(event, route) == expected


@pytest.mark.parametrize("kind,event_type", [("note", "note_on"), ("note", "note_off"), ("cc", "control_change")])
def test_all_matching_routes_and_channel_filter_for_notes_and_cc(kind, event_type):
    routes = [MidiRoute(kind=kind, number=7, channel=None, address="/same"),
              MidiRoute(kind=kind, number=7, channel=3, address="/same"),
              MidiRoute(kind=kind, number=7, channel=4, address="/wrong")]
    output = Output()
    engine = MidiEngine(config(routes), output, clock=lambda: 0)
    engine.process(MidiEvent(event_type, 3, 7, 70, 0))
    assert [payload for payload, _ in output.sent] == [encode_osc("/same", 70)] * 2


def test_editor_mutes_before_learn_after_capture_and_hot_apply_without_replay():
    output = Output()
    engine = MidiEngine(config(), output, clock=lambda: 0)
    event = MidiEvent("control_change", 2, 7, 21, 0)
    engine.set_editor(True)
    engine.process(event)
    assert engine.learn is None
    engine.arm()
    engine.process(event)
    engine.process(MidiEvent("control_change", 2, 7, 99, 0))
    assert engine.learn["value"] == 21
    updated = config([MidiRoute(kind="cc", number=7, address="/new")])
    engine.apply(updated)
    engine.process(event)
    assert output.sent == []
    engine.set_editor(False)
    assert output.sent == []
    engine.process(event)
    assert output.sent[0][0] == encode_osc("/new", 21)


def test_learn_rearm_replaces_preview_only_on_next_event():
    engine = MidiEngine(config(), Output(), clock=lambda: 0)
    engine.set_editor(True)
    engine.arm()
    engine.process(MidiEvent("note_on", 1, 60, 22, 0))
    assert engine.learn["note"] == 60
    engine.arm()
    assert engine.learn is None
    engine.process(MidiEvent("control_change", 4, 5, 6, 0))
    assert engine.learn == {"type": "control_change", "channel": 4, "control": 5, "cc": 5, "value": 6}


def test_kernel_backlog_from_editor_is_discarded_even_inside_event_deadline():
    now = [100]
    output = Output()
    engine = MidiEngine(config(), output, clock=lambda: now[0])
    engine.set_editor(True)
    before_arm = MidiEvent("control_change", 0, 7, 1, 110)
    now[0] = 120
    engine.arm()
    engine.process(before_arm)
    assert engine.learn is None
    muted_backlog = MidiEvent("control_change", 0, 7, 2, 125)
    now[0] = 130
    engine.set_editor(False)
    engine.process(muted_backlog)
    assert output.sent == []
    engine.process(MidiEvent("control_change", 0, 7, 3, 140))
    assert output.sent[0][0] == encode_osc("/fader", 3)


def test_send_failure_does_not_remove_destination_or_retry_old_event():
    output = Output()
    engine = MidiEngine(config(), output, clock=lambda: 0)
    output.fail = True
    engine.process(MidiEvent("control_change", 0, 7, 1, 0))
    output.fail = False
    engine.process(MidiEvent("control_change", 0, 7, 2, 0))
    assert [data for data, _ in output.sent] == [encode_osc("/fader", 2)]
    assert engine.metrics["send_dropped"] == 1


def test_fader_burst_and_simultaneous_load_never_replay_expired_events():
    now = [100_000_000]
    output = Output()
    engine = MidiEngine(config(), output, clock=lambda: now[0])
    # Represents data accumulated by a driver while the process was descheduled.
    for index in range(20_000):
        engine.process(MidiEvent("control_change", 0, 7, index % 128, index))
    assert output.sent == []
    assert engine.metrics["expired"] == 20_000
    engine.process(MidiEvent("control_change", 0, 7, 127, now[0]))
    assert output.sent == [(encode_osc("/fader", 127), ("127.0.0.1", 7001))]
    # Subsequent status/control reads do not drain or emit any pending work.
    for _ in range(100):
        dict(engine.metrics)
    assert len(output.sent) == 1


def test_deadline_rechecked_between_every_destination_and_route():
    now = [0]
    def load():
        now[0] += 11_000_000
    output = Output(load)
    cfg = config([MidiRoute(kind="cc", number=7, address="/a"), MidiRoute(kind="cc", number=7, address="/b")])
    cfg.destinations.append(OscDestination(host="127.0.0.2"))
    engine = MidiEngine(cfg, output, clock=lambda: now[0])
    engine.process(MidiEvent("control_change", 0, 7, 10, 0))
    assert len(output.sent) == 1
    assert engine.metrics["expired"] == 1


def test_controller_selection_survives_alsa_numeric_id_change_and_refuses_fallback():
    old = Port(24, 0, "Controller", "MIDI 1")
    reconnected = Port(31, 0, "Controller", "MIDI 1")
    internal = Port(14, 0, "Midi Through", "Midi Through Port-0")
    assert select_port(old.name, [internal]) is None
    assert select_port(old.name, [internal, reconnected]) == reconnected
    with pytest.raises(AlsaError, match="Ambiguous"):
        select_port(old.name, [old, reconnected])


def test_identical_controllers_are_distinguished_by_physical_identity():
    left = Port(20, 0, "Controller", "MIDI 1", "usb:1234:abcd:left")
    right = Port(21, 0, "Controller", "MIDI 1", "usb:1234:abcd:right")
    assert select_port(left.name, [left, right]) == left
    assert select_port(left.name, [right]) is None
    assert select_port(left.name, [Port(34, 0, "Controller", "MIDI 1", left.hardware_id)]) is not None


@pytest.mark.parametrize("message", [
    {"type": "clock"}, {"type": "note_on", "note": 128},
    {"type": "control_change", "cc": 7, "value": 128},
    {"type": "note_on", "note": 60, "channel": 16},
    {"type": "note_on", "note": True},
])
def test_invalid_injection_is_rejected(message):
    with pytest.raises(ValueError):
        MidiEvent.from_dict(message)


def test_osc_types_and_alignment():
    assert encode_osc("/x", True) == b"/x\0\0,T\0\0"
    assert encode_osc("/x", False) == b"/x\0\0,F\0\0"
    assert encode_osc("/x", 3) == b"/x\0\0,i\0\0" + struct.pack(">i", 3)
    assert encode_osc("/x", 0.5) == b"/x\0\0,f\0\0" + struct.pack(">f", 0.5)
    assert encode_osc("/x", "go") == b"/x\0\0,s\0\0go\0\0"


def test_slow_dns_never_blocks_control_or_retries_old_osc(monkeypatch):
    release, started = threading.Event(), threading.Event()
    def slow_dns(*args, **kwargs):
        started.set()
        release.wait(2)
        return [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("127.0.0.1", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", slow_dns)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(0.1)
        cfg = config()
        cfg.destinations = [OscDestination(host="slow.example", port=receiver.getsockname()[1])]
        output = UdpOutput()
        engine = MidiEngine(cfg, output)
        try:
            assert started.wait(1)
            # Both return while the DNS function is deliberately still blocked.
            engine.process(MidiEvent("control_change", 0, 7, 1, time.monotonic_ns()))
            engine.refresh_destinations(cfg)
            assert engine.metrics["send_dropped"] == 1
            release.set()
            deadline = time.monotonic() + 1
            while not output._dns_cache:
                assert time.monotonic() < deadline
                time.sleep(0.005)
            engine.refresh_destinations(cfg)
            with pytest.raises(socket.timeout):
                receiver.recv(4096)
            engine.process(MidiEvent("control_change", 0, 7, 2, time.monotonic_ns()))
            assert receiver.recv(4096) == encode_osc("/fader", 2)
        finally:
            release.set()
            engine.close()
