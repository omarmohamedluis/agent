import socket
import sys

import pytest

from omi.models import MidiConfig, OscDestination
from omi.services.midi import UdpOutput
from omi.services.neighbours import NeighbourGate, NeighbourState


def gate(monkeypatch, family=socket.AF_INET, *, neighbours=None, route=None):
    monkeypatch.setattr("omi.services.neighbours.sys.platform", "linux")
    calls, warmed, now = [], [], [1_000_000_000]
    route = route if route is not None else {"dev": "eth0", "gateway": "192.0.2.1"}
    def query(arguments):
        calls.append(arguments)
        return [route] if "route" in arguments and route else neighbours or []
    instance = NeighbourGate(query=query, warmup=lambda *args: warmed.append(args), clock=lambda: now[0])
    return instance, calls, warmed, now


def test_ipv4_next_hop_not_remote_host_controls_admission_and_cache_expires(monkeypatch):
    instance, calls, warmed, now = gate(monkeypatch, neighbours=[
        {"dst": "192.0.2.1", "dev": "eth0", "lladdr": "00:11:22:33:44:55", "state": ["STALE"]}])
    target = (socket.AF_INET, "198.51.100.5")
    state = instance.inspect(target)
    assert state.ready and not warmed
    assert calls[-1] == ["-4", "neigh", "show", "to", "192.0.2.1", "dev", "eth0"]
    instance.states[target] = state
    assert instance.admits((target[0], (target[1], 7001)))
    now[0] += 251_000_000
    assert not instance.admits((target[0], (target[1], 7001)))


def test_unresolved_neighbour_is_gated_and_icmp_rate_limited_by_shared_gateway(monkeypatch):
    instance, _, warmed, now = gate(monkeypatch, neighbours=[
        {"dst": "192.0.2.1", "dev": "eth0", "state": ["INCOMPLETE"]}])
    assert not instance.inspect((socket.AF_INET, "198.51.100.5")).ready
    assert not instance.inspect((socket.AF_INET, "198.51.100.6")).ready
    assert warmed == [(socket.AF_INET, "198.51.100.5")]
    now[0] += 1_000_000_001
    assert not instance.inspect((socket.AF_INET, "198.51.100.6")).ready
    assert len(warmed) == 2


def test_ipv6_nd_uses_ipv6_route_and_gateway(monkeypatch):
    instance, calls, warmed, _ = gate(monkeypatch, route={"dev": "eth0", "gateway": "fe80::1"}, neighbours=[
        {"dst": "fe80::1", "dev": "eth0", "lladdr": "00:11:22:33:44:55", "state": ["REACHABLE"]}])
    assert instance.inspect((socket.AF_INET6, "2001:db8::5")).ready
    assert calls[0] == ["-6", "route", "get", "2001:db8::5"]
    assert calls[1] == ["-6", "neigh", "show", "to", "fe80::1", "dev", "eth0"]
    assert not warmed


def test_unavailable_route_inspection_fails_closed_and_local_route_needs_no_neighbour(monkeypatch):
    instance, _, warmed, _ = gate(monkeypatch)
    instance.query = lambda args: (_ for _ in ()).throw(FileNotFoundError("ip unavailable"))
    assert not instance.inspect((socket.AF_INET, "198.51.100.5")).ready
    assert not warmed
    instance, calls, _, _ = gate(monkeypatch, route={"dev": "lo", "type": "local"})
    assert instance.inspect((socket.AF_INET, "192.0.2.3")).ready
    assert len(calls) == 1
    instance, _, warmed, _ = gate(monkeypatch, route={})
    assert not instance.inspect((socket.AF_INET, "198.51.100.5")).ready
    assert not warmed


def test_neighbor_snapshot_is_shared_and_route_cached_between_refreshes(monkeypatch):
    instance, calls, _, now = gate(monkeypatch)
    snapshot = ([{"dst": "192.0.2.1", "dev": "eth0", "lladdr": "00:11:22:33:44:55", "state": ["REACHABLE"]}], now[0])
    target = (socket.AF_INET, "198.51.100.5")
    assert instance.inspect(target, neighbour_snapshot=snapshot).ready
    assert instance.inspect(target, neighbour_snapshot=snapshot).ready
    assert calls == [["-4", "route", "get", "198.51.100.5"]]


def test_busy_destination_socket_does_not_block_other_same_family_destinations(monkeypatch):
    class Gate:
        def configure(self, endpoints): pass
        def admits(self, endpoint): return True
        def close(self): pass
    class Socket:
        count = 0
        sent = []
        def __init__(self, *args): self.id = type(self).count; type(self).count += 1
        def setblocking(self, value): pass
        def setsockopt(self, *args): pass
        def fileno(self): return self.id
        def sendto(self, payload, address): self.sent.append((payload, address)); return len(payload)
        def close(self): pass
    monkeypatch.setattr("omi.services.midi.sys.platform", "linux")
    monkeypatch.setattr("omi.services.midi.socket.socket", Socket)
    class Fcntl:
        @staticmethod
        def ioctl(fd, command, pending, mutate): pending[0] = 100 if fd == 0 else 0
    monkeypatch.setitem(sys.modules, "fcntl", Fcntl)
    output = UdpOutput(Gate())
    try:
        endpoints = output.resolve(MidiConfig(destinations=[OscDestination(host="192.0.2.10"), OscDestination(host="192.0.2.11")]))
        assert not output.send(b"old", endpoints[0])
        assert output.send(b"fresh", endpoints[1])
        assert Socket.sent == [(b"fresh", ("192.0.2.11", 7001))]
    finally:
        output.close()


def test_unresolved_neighbor_never_admits_first_osc_datagram_or_replays_it(monkeypatch):
    instance, _, _, now = gate(monkeypatch)
    instance.configure = lambda endpoints: None  # use controlled snapshots, no OS probes
    endpoint = (socket.AF_INET, ("192.0.2.10", 7001))
    target = (socket.AF_INET, "192.0.2.10")
    instance.states[target] = NeighbourState(False, now[0], "INCOMPLETE")
    class Socket:
        sent = []
        def __init__(self, *args): pass
        def setblocking(self, value): pass
        def setsockopt(self, *args): pass
        def fileno(self): return 0
        def sendto(self, payload, address): self.sent.append(payload); return len(payload)
        def close(self): pass
    class Fcntl:
        @staticmethod
        def ioctl(fd, command, pending, mutate): pending[0] = 0
    monkeypatch.setattr("omi.services.midi.socket.socket", Socket)
    monkeypatch.setitem(sys.modules, "fcntl", Fcntl)
    output = UdpOutput(instance)
    try:
        output.resolve(MidiConfig(destinations=[OscDestination(host="192.0.2.10")]))
        assert not output.send(b"old OSC trigger", endpoint)
        assert Socket.sent == []
        instance.states[target] = NeighbourState(True, now[0], "REACHABLE")
        assert Socket.sent == []  # resolution itself never replays the discarded trigger
        assert output.send(b"new OSC event", endpoint)
        assert Socket.sent == [b"new OSC event"]
    finally:
        output.close()
