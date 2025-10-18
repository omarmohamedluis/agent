"""SQLite persistence for OMI control server."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DB_PATH = Path(__file__).resolve().parent / "omi.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS service_configs (
                service_id TEXT NOT NULL,
                name TEXT NOT NULL,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT,
                PRIMARY KEY (service_id, name)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                serial TEXT PRIMARY KEY,
                host TEXT,
                desired_service TEXT,
                desired_config TEXT,
                device_index INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        try:
            conn.execute("ALTER TABLE devices ADD COLUMN device_index INTEGER")
        except sqlite3.OperationalError:
            pass
        conn.execute("UPDATE service_configs SET service_id = 'OSCnum' WHERE service_id = 'OSC'")
        conn.execute("UPDATE devices SET desired_service = 'OSCnum' WHERE desired_service = 'OSC'")
        conn.commit()


def _next_device_index(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT device_index FROM devices WHERE device_index IS NOT NULL ORDER BY device_index").fetchall()
    used = {row["device_index"] for row in rows if row["device_index"] is not None}
    candidate = 1
    while candidate in used:
        candidate += 1
    return candidate


def _resequence_device_indices(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT serial FROM devices ORDER BY device_index, serial").fetchall()
    for idx, row in enumerate(rows, start=1):
        conn.execute("UPDATE devices SET device_index = ? WHERE serial = ?", (idx, row["serial"]))


def save_config(service_id: str, name: str, data: Dict[str, Any], updated_by: Optional[str]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO service_configs (service_id, name, data, updated_at, updated_by)
            VALUES (?, ?, ?, datetime('now'), ?)
            ON CONFLICT(service_id, name) DO UPDATE SET
                data=excluded.data,
                updated_at=excluded.updated_at,
                updated_by=excluded.updated_by
            """,
            (service_id, name, json.dumps(data), updated_by),
        )
        conn.commit()


def list_configs(service_id: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT service_id, name, data, updated_at, updated_by FROM service_configs WHERE service_id = ? ORDER BY name",
            (service_id,),
        ).fetchall()
    result = []
    for row in rows:
        payload = json.loads(row["data"])
        result.append(
            {
                "service_id": row["service_id"],
                "name": row["name"],
                "data": payload,
                "updated_at": row["updated_at"],
                "updated_by": row["updated_by"],
            }
        )
    return result


def get_config(service_id: str, name: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT data, updated_at, updated_by FROM service_configs WHERE service_id = ? AND name = ?",
            (service_id, name),
        ).fetchone()
    if not row:
        return None
    payload = json.loads(row["data"])
    return {
        "data": payload,
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


def delete_config(service_id: str, name: str) -> None:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM service_configs WHERE service_id = ? AND name = ?",
            (service_id, name),
        )
        conn.commit()


def upsert_device(
    serial: str,
    *,
    host: Optional[str] = None,
    desired_service: Optional[str] = None,
    desired_config: Optional[str] = None,
    device_index: Optional[int] = None,
) -> None:
    if desired_service == "OSC":
        desired_service = "OSCnum"
    with _connect() as conn:
        existing = conn.execute(
            "SELECT serial, device_index FROM devices WHERE serial = ?",
            (serial,),
        ).fetchone()
        current_index = existing["device_index"] if existing else None
        if existing and current_index is None and device_index is None:
            device_index = _next_device_index(conn)
        if existing:
            conn.execute(
                """
                UPDATE devices
                SET host = COALESCE(?, host),
                    desired_service = COALESCE(?, desired_service),
                    desired_config = COALESCE(?, desired_config),
                    device_index = COALESCE(?, device_index),
                    updated_at = datetime('now')
                WHERE serial = ?
                """,
                (host, desired_service, desired_config, device_index, serial),
            )
        else:
            if device_index is None:
                device_index = _next_device_index(conn)
            conn.execute(
                """
                INSERT INTO devices(serial, host, desired_service, desired_config, device_index, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (serial, host, desired_service, desired_config, device_index),
            )
        conn.commit()


def get_device(serial: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT serial, host, desired_service, desired_config, device_index, created_at, updated_at FROM devices WHERE serial = ?",
            (serial,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def delete_device(serial: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM devices WHERE serial = ?", (serial,))
        _resequence_device_indices(conn)
        conn.commit()


def list_devices() -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT serial, host, desired_service, desired_config, device_index, created_at, updated_at FROM devices ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def ensure_device_index(serial: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT device_index FROM devices WHERE serial = ?",
            (serial,),
        ).fetchone()
        if not row:
            new_index = _next_device_index(conn)
            conn.execute(
                """
                INSERT INTO devices(serial, host, desired_service, desired_config, device_index, created_at, updated_at)
                VALUES (?, NULL, NULL, NULL, ?, datetime('now'), datetime('now'))
                """,
                (serial, new_index),
            )
            conn.commit()
            return new_index

        current = row["device_index"]
        if current is not None:
            return current

        new_index = _next_device_index(conn)
        conn.execute(
            "UPDATE devices SET device_index = ?, updated_at = datetime('now') WHERE serial = ?",
            (new_index, serial),
        )
        conn.commit()
        return new_index
