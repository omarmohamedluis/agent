"""Explicit scopes for maintenance; data is never coupled to installed code."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

from .network import run_command
from .store import Store


def backup(store: Store, directory: Path) -> Path:
    destination = directory / f"omi-backup-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.sqlite3"
    return store.backup(destination)


def clear_logs(data_dir: Path) -> None:
    # Truncate the current log so an open handler can keep writing safely.
    directory = (data_dir / "logs").resolve()
    if not directory.exists():
        return
    for path in directory.iterdir():
        if path.is_file() and not path.is_symlink() and path.resolve().parent == directory:
            if path.suffix == ".log":
                path.write_bytes(b"")
            elif ".log." in path.name:
                path.unlink()


def reset_state(store: Store) -> None:
    # Execution, identity, network and presets are deliberately preserved.
    store.delete_prefix("command.")
    store.delete_prefix("temporary.")


def factory_reset(store: Store, *, confirm: bool, backup_dir: Path | None = None) -> Path | None:
    if not confirm:
        raise ValueError("Explicit confirmation is required")
    saved = backup(store, backup_dir) if backup_dir else None
    with store.transaction() as connection:
        connection.execute("DELETE FROM kv")
        connection.execute("DELETE FROM revisions")
    return saved


async def run_update(role: str, body: dict) -> dict:
    if role not in ("client", "server"):
        raise ValueError("Unknown installation role")
    reference = body.get("ref", "v3")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", reference) or ".." in reference:
        raise ValueError("Invalid Git revision")
    operation_id = body.get("operation_id", str(uuid.uuid4()))
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", operation_id):
        raise ValueError("Invalid update operation identifier")
    if sys.platform != "linux":
        raise RuntimeError("Installed system updates require Linux/systemd; use Git in development")
    script = Path("/usr/local/sbin/omi-update")
    if not script.is_file():
        raise RuntimeError("Update launcher is not installed; update your development checkout with Git")
    arguments = [str(script), role, "--ref", reference, "--operation-id", operation_id]
    if os.geteuid() != 0:
        arguments = ["sudo", "-n", *arguments]
    await run_command(*arguments, timeout=10)
    return {"id": operation_id, "status": "accepted", "detail": "Staging update; monitor update-status.json and system journal"}


async def wait_update(data_dir: Path, operation_id: str, *, timeout: float = 1800) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        path = data_dir / "update-status.json"
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            status = {}
        if status.get("id") == operation_id or status.get("operation_id") == operation_id:
            if status.get("status") in ("failed", "error"):
                raise RuntimeError(status.get("error") or status.get("detail") or "Update failed")
            if status.get("status") in ("succeeded", "complete"):
                return
        await asyncio.sleep(1)
    raise TimeoutError("Update result was not received; inspect systemd update unit")
