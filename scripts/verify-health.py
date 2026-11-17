#!/usr/bin/env python3
"""Gate activation on the prepared code and completed local recovery."""
from __future__ import annotations

import argparse
import json
import time
import urllib.request


def readiness(status: dict, role: str, commit: str, target=None) -> tuple[bool, str]:
    if status.get("role") != role:
        return False, "Unexpected HTTP role"
    if status.get("code_revision") != commit:
        return False, "HTTP is not running the prepared code revision"
    if role == "server":
        return True, "Central HTTP ready"
    if status.get("booting") is not False:
        return False, "Local state recovery is still running"
    state = status.get("state") or {}
    if (status.get("busy") or state.get("busy")) and not status.get("update_verifying"):
        return False, "Agent operation is still running"
    historical_standby_error = (state.get("health") == "error" and state.get("target") is None
                                and target is None and status.get("recovery_error") is False)
    if state.get("health") not in ("ready", "waiting", "standby") and not historical_standby_error:
        return False, str(state.get("detail") or "Agent recovery is not healthy")
    if state.get("target") != target:
        return False, "Recovered execution differs from the saved local target"
    return True, "Local state recovered"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("client", "server"), required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--target", default="null")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    target = json.loads(args.target)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + args.timeout
    reason = "HTTP has not responded"
    while time.monotonic() < deadline:
        try:
            with opener.open(f"http://127.0.0.1:{args.port}/api/status", timeout=2) as reply:
                status = json.loads(reply.read(2 * 1024 * 1024))
            ready, reason = readiness(status, args.role, args.commit, target)
            if ready:
                print(reason)
                return
            if args.role == "client" and status.get("booting") is False and status.get("recovery_error"):
                raise SystemExit("Local recovery failed: " + reason)
        except (OSError, ValueError) as error:
            reason = str(error)
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise SystemExit(f"Prepared OMI release did not become ready within {args.timeout:g}s: {reason}")


if __name__ == "__main__":
    main()
