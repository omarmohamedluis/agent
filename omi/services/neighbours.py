"""Off-path Linux route/neighbor admission for OSC destinations.

Only read-only iproute2 queries run here. ICMP echo primes ARP/ND without putting
an OSC trigger into an unresolved-neighbor queue. Observations are deliberately
short lived; they cannot eliminate a neighbor/route change between check/send,
or impose deadlines on packets a qdisc/NIC/network already accepted.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NeighbourState:
    ready: bool
    checked_ns: int
    detail: str


def read_ip(arguments: list[str]):
    result = subprocess.run(["ip", "-j", *arguments], capture_output=True, check=True,
                            timeout=0.25, text=True)
    return json.loads(result.stdout)


def warm_neighbour(family: int, host: str):
    """ICMP datagram echo only; failure leaves OSC gated, never falls back to UDP."""
    protocol = socket.IPPROTO_ICMP if family == socket.AF_INET else socket.IPPROTO_ICMPV6
    request_type = 8 if family == socket.AF_INET else 128
    try:
        with socket.socket(family, socket.SOCK_DGRAM, protocol) as ping:
            ping.setblocking(False)
            # Ping sockets supply identifier/checksum in the Linux kernel.
            ping.sendto(struct.pack("!BBHHH", request_type, 0, 0, 0, 1), (host, 0))
    except OSError:
        pass


class NeighbourGate:
    freshness_ns = 250_000_000

    def __init__(self, *, query=read_ip, warmup=warm_neighbour, clock=time.monotonic_ns):
        self.query, self.warmup, self.clock = query, warmup, clock
        self.targets = ()
        self.states: dict[tuple[int, str], NeighbourState] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._last_warm = {}
        self._routes = {}

    @staticmethod
    def bypass(family: int, host: str) -> bool:
        if not sys.platform.startswith("linux"):
            return True
        try:
            address = ipaddress.ip_address(host)
            return address.is_loopback or address.is_multicast or host == "255.255.255.255"
        except ValueError:
            return False

    def configure(self, endpoints):
        targets = tuple(dict.fromkeys((family, address[0]) for family, address in endpoints
                                     if family is not None and not self.bypass(family, address[0])))
        if targets != self.targets:
            self.targets = targets
            self.states = {key: value for key, value in self.states.items() if key in targets}
            self._last_warm = {key: value for key, value in self._last_warm.items() if key in targets}
            self._routes = {key: value for key, value in self._routes.items() if key in targets}
            self._wake.set()
        if targets and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="omi-osc-neighbours", daemon=True)
            self._thread.start()

    def admits(self, endpoint) -> bool:
        family, address = endpoint
        if family is None:
            return False
        if self.bypass(family, address[0]):
            return True
        state = self.states.get((family, address[0]))
        return bool(state and state.ready and self.clock() - state.checked_ns <= self.freshness_ns)

    def _route(self, target):
        cached = self._routes.get(target)
        now = self.clock()
        if cached and now - cached[0] <= 2_000_000_000:
            return cached[1]
        family, host = target
        prefix = ["-6"] if family == socket.AF_INET6 else ["-4"]
        try:
            routes = self.query([*prefix, "route", "get", host])
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            self._routes[target] = (now, {})
            raise
        route = routes[0] if routes and isinstance(routes[0], dict) else {}
        self._routes[target] = (now, route)
        return route

    def inspect(self, target: tuple[int, str], *, neighbour_snapshot=None) -> NeighbourState:
        family, host = target
        now = self.clock()
        try:
            prefix = ["-6"] if family == socket.AF_INET6 else ["-4"]
            route = self._route(target)
            if not route:
                return NeighbourState(False, now, "No route")
            device = route.get("dev")
            if route.get("type") in ("local", "broadcast", "multicast") or device == "lo":
                return NeighbourState(True, now, "Local or link broadcast route")
            if not device or route.get("type") in ("unreachable", "blackhole", "prohibit"):
                return NeighbourState(False, now, "Route is not usable")
            next_hop = route.get("gateway", host)
            if neighbour_snapshot is None:
                neighbours = self.query([*prefix, "neigh", "show", "to", next_hop, "dev", device])
            else:
                neighbours, now = neighbour_snapshot
            for neighbour in neighbours:
                states = neighbour.get("state", [])
                if isinstance(states, str):
                    states = states.split(",")
                known = {value.upper() for value in states}
                if neighbour.get("dst") == next_hop and neighbour.get("dev") == device and neighbour.get("lladdr"):
                    if known & {"REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT", "NOARP"} and not known & {"INCOMPLETE", "FAILED"}:
                        return NeighbourState(True, now, "Next-hop link address is known")
            warm_key = (family, device, next_hop)
            last = self._last_warm.get(warm_key, -1_000_000_000)
            if now - last >= 1_000_000_000:
                self._last_warm[warm_key] = now
                self.warmup(family, host)
            return NeighbourState(False, now, "Waiting for ARP/ND next-hop resolution")
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            return NeighbourState(False, now, "Route/neighbor inspection unavailable")

    def _run(self):
        while not self._stop.is_set():
            self._wake.clear()
            targets = self.targets
            # Route decisions change slowly; resolve/cache them before taking a
            # single fresh neighbor snapshot per address family. This avoids
            # two subprocesses per destination on every 100 ms refresh.
            for target in targets:
                try:
                    self._route(target)
                except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                    pass
            snapshots = {}
            for family in {target[0] for target in targets}:
                try:
                    prefix = ["-6"] if family == socket.AF_INET6 else ["-4"]
                    sampled_before = self.clock()
                    snapshots[family] = (self.query([*prefix, "neigh", "show"]), sampled_before)
                except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                    snapshots[family] = ([], self.clock())
            for target in targets:
                if self._stop.is_set():
                    return
                state = self.inspect(target, neighbour_snapshot=snapshots[target[0]])
                # A configuration change while a subprocess was running must
                # not re-add removed targets or grow the cache without bound.
                if target in self.targets:
                    self.states[target] = state
            self._last_warm = {key: value for key, value in self._last_warm.items()
                               if self.clock() - value <= 5_000_000_000}
            self._wake.wait(0.1)

    def close(self):
        self._stop.set()
        self._wake.set()
