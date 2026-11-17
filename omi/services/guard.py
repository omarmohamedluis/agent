"""Keep a lifetime pipe to the agent and reap Satellite on unexpected parent EOF.

The guard and all Satellite descendants share one newly-created process group.
Normal shutdown and parent death both give the child four seconds, then kill
the complete group. systemd KillMode=control-group remains a second boundary.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time


def main():
    if len(sys.argv) < 2:
        return 2
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    process = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL)
    def parent_pipe():
        try:
            # A daemon blocked in BufferedReader holds its lock at interpreter
            # shutdown; os.read avoids that fatal CPython finalization race.
            while os.read(sys.stdin.fileno(), 1):
                pass
        finally:
            stopped.set()
    threading.Thread(target=parent_pipe, daemon=True).start()
    while process.poll() is None and not stopped.wait(0.1):
        pass
    code = process.poll()
    if os.name != "nt":
        os.killpg(os.getpgrp(), signal.SIGTERM)
        deadline = time.monotonic() + 4
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        # Also clean descendants after the leader exited; this kills the guard
        # itself last as part of the same group. The supervisor knows this is
        # intentional during shutdown, and otherwise counts a child crash.
        os.killpg(os.getpgrp(), signal.SIGKILL)
    else:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=3, check=False)
    return code or 0


if __name__ == "__main__":
    raise SystemExit(main())
