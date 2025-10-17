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
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


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


def upsert_device(serial: str, *, host: Optional[str] = None, desired_service: Optional[str] = None, desired_config: Optional[str] = None) -> None:
    with _connect() as conn:
        existing = conn.execute("SELECT serial FROM devices WHERE serial = ?", (serial,)).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE devices
                SET host = COALESCE(?, host),
                    desired_service = COALESCE(?, desired_service),
                    desired_config = COALESCE(?, desired_config),
                    updated_at = datetime('now')
                WHERE serial = ?
                """,
                (host, desired_service, desired_config, serial),
            )
        else:
            conn.execute(
                """
                INSERT INTO devices(serial, host, desired_service, desired_config, created_at, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (serial, host, desired_service, desired_config),
            )
        conn.commit()


def get_device(serial: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT serial, host, desired_service, desired_config, created_at, updated_at FROM devices WHERE serial = ?",
            (serial,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def delete_device(serial: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM devices WHERE serial = ?", (serial,))
        conn.commit()


def list_devices() -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT serial, host, desired_service, desired_config, created_at, updated_at FROM devices ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]
