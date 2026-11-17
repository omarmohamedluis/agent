"""Private line-delimited control protocol; MIDI never traverses these pipes.

The worker exits on parent EOF. Only --simulate accepts injected events. An
isolated data thread owns ALSA; HTTP/preset/disk work runs in other processes.
"""
from __future__ import annotations

import json
import sys
import threading
import time

from omi.models import MidiConfig
from .alsa import Alsa, AlsaError, ClockUncertain
from .midi import MidiEngine, MidiEvent


class MidiWorker:
    def __init__(self, config: MidiConfig, *, simulated=False, capture=False, muted=False):
        self.config = config
        self.simulated = simulated
        self.capture = capture
        self.engine = MidiEngine(config)
        self.engine.set_editor(muted or capture)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.startup_done = threading.Event()
        self.health = "waiting"
        self.detail = "Opening MIDI input"
        self.backend = None
        self.thread = None
        self.last_refresh = time.monotonic()

    def start(self):
        if self.simulated:
            self.health, self.detail = "ready", "Simulated MIDI input"
            self.startup_done.set()
            return
        if not self.config.input:
            raise ValueError("Select a MIDI input before starting")
        self.thread = threading.Thread(target=self._capture, name="omi-midi-ingress", daemon=True)
        self.thread.start()
        if not self.startup_done.wait(5):
            raise TimeoutError("MIDI input startup exceeded 5 seconds")
        if self.health == "error":
            raise RuntimeError(self.detail)

    def _capture(self):
        established = False
        attempts = 0
        backend = None
        next_check = 0.0
        try:
            while not self.stop_event.is_set():
                if backend is None:
                    try:
                        backend = Alsa()
                        backend.connect(self.config.input)
                        self.backend = backend
                        established = True
                        self.health, self.detail = "ready", f"MIDI: {self.config.input}"
                        self.startup_done.set()
                        next_check = time.monotonic() + 0.5
                    except (AlsaError, OSError) as exc:
                        if backend:
                            backend.close()
                        backend = self.backend = None
                        attempts += 1
                        self.health, self.detail = "waiting", str(exc)
                        if not established and attempts >= 6:
                            self.health = "error"
                            self.startup_done.set()
                            return
                        self.stop_event.wait(0.5)
                        continue
                if time.monotonic() >= next_check:
                    next_check = time.monotonic() + 0.5
                    if not backend.connected():
                        self.health, self.detail = "waiting", f"Waiting for MIDI input: {self.config.input}"
                        backend.close()
                        backend = self.backend = None
                        continue
                try:
                    origin = backend.clock_origin()
                except ClockUncertain:
                    backend.drop()
                    self.engine.metrics["clock_dropped"] += 1
                    self.stop_event.wait(0.001)
                    continue
                batch_started = time.monotonic_ns()
                # Bounded drain prevents control/stop starvation during a flood.
                for _ in range(256):
                    if self.stop_event.is_set():
                        break
                    try:
                        event = backend.read(origin)
                    except OverflowError:
                        self.engine.metrics["overruns"] += 1
                        break
                    if event is None:
                        break
                    if event is False:
                        continue
                    if self.lock.acquire(blocking=False):
                        try:
                            self.engine.process(event)
                        finally:
                            self.lock.release()
                    else:
                        self.engine.metrics["send_dropped"] += 1
                    if time.monotonic_ns() - batch_started > 2_000_000:
                        break
                self.stop_event.wait(0.001)
        except Exception as exc:
            self.health, self.detail = "error", f"MIDI backend failed: {exc}"
            self.startup_done.set()
        finally:
            if backend:
                backend.close()

    def status(self):
        return {"health": self.health, "detail": self.detail, "ready": self.startup_done.is_set() and self.health != "error",
                "input": self.config.input, "capture_only": self.capture, "muted": self.engine.muted,
                "learn": self.engine.learn, "metrics": dict(self.engine.metrics)}

    def command(self, request):
        action = request["action"]
        if action == "apply":
            config = MidiConfig.model_validate(request["config"])
            if config.input != self.config.input:
                raise ValueError("Changing MIDI input requires restart")
            self.engine.apply(config)  # compilation/DNS outside ingress lock
            self.config = config
        elif action == "editor":
            with self.lock:
                self.engine.set_editor(bool(request["opened"]))
        elif action == "learn":
            with self.lock:
                self.engine.arm()
        elif action == "inject":
            if not self.simulated:
                raise ValueError("MIDI injection is only available in simulation")
            event = MidiEvent.from_dict(request["message"], request.get("received_ns"))
            with self.lock:
                self.engine.process(event)
        elif action != "status":
            raise ValueError(f"Unknown MIDI command: {action}")
        # Periodic resolution belongs to control, never the data thread.
        if time.monotonic() - self.last_refresh > 0.5:
            self.last_refresh = time.monotonic()
            self.engine.refresh_destinations(self.config)
        return self.status()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(1)
        self.engine.close()


def main():
    worker = None
    try:
        if "--inputs" in sys.argv:
            backend = Alsa()
            try:
                print(json.dumps({"ok": True, "inputs": [port.name for port in backend.inputs()]}), flush=True)
            finally:
                backend.close()
            return 0
        initialization = json.loads(sys.stdin.readline())
        worker = MidiWorker(MidiConfig.model_validate(initialization["config"]),
                            simulated=initialization["simulated"], capture=initialization["capture"],
                            muted=initialization["muted"])
        worker.start()
        print(json.dumps({"ok": True, "status": worker.status()}), flush=True)
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if request.get("action") == "stop":
                    break
                result = {"ok": True, "status": worker.command(request)}
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            print(json.dumps(result, separators=(",", ":")), flush=True)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), flush=True)
        return 1
    finally:
        if worker:
            worker.close()
    return 0


if __name__ == "__main__":
    # SIGTERM is handled by the supervisor's bounded TERM/KILL process group.
    raise SystemExit(main())
