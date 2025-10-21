# server/src/omiDB.py

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DB_PATH = DATA_DIR / "omi.db"


def _get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def initialize_db() -> None:
    with _get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                serial TEXT PRIMARY KEY,
                host TEXT,
                device_index INTEGER,
                version TEXT,
                cpu REAL,
                temp REAL,
                service_actual TEXT,
                service_configuration TEXT,
                service_web_port TEXT,
                raw_payload TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def _find_next_index(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT device_index FROM devices ORDER BY device_index").fetchall()
    expected = 1
    for row in rows:
        idx = row["device_index"]
        if idx is None:
            continue
        if idx > expected:
            break
        if idx == expected:
            expected += 1
    return expected



def upsert_device(cliente_payload: Dict[str, Any]) -> Dict[str, Any]:

    serial = cliente_payload.get("serial")
    if not serial:
        raise ValueError("El payload debe incluir un número de serie 'serial'")

    heartbeat = cliente_payload.get("heartbeat", {}) or {}
    cpu = heartbeat.get("cpu")
    temp = heartbeat.get("temp")

    payload_for_storage = dict(cliente_payload)

    with _get_connection() as conn:
        existing = conn.execute(
            "SELECT * FROM devices WHERE serial = ?", (serial,)
        ).fetchone()

        if existing is None:
            next_index = _find_next_index(conn)
            payload_for_storage["index"] = next_index
            raw_payload_json = json.dumps(payload_for_storage)

            conn.execute(
                """
                INSERT INTO devices (
                    serial,
                    host,
                    device_index,
                    version,
                    cpu,
                    temp,
                    service_actual,
                    service_configuration,
                    service_web_port,
                    raw_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    serial,
                    payload_for_storage.get("host"),
                    next_index,
                    payload_for_storage.get("version"),
                    cpu,
                    temp,
                    "standby",
                    None,
                    None,
                    raw_payload_json,
                ),
            )
            conn.commit()

            response = {
                "version": payload_for_storage.get("version"),
                "serial": serial,
                "host": payload_for_storage.get("host"),
                "index": next_index,
                "service_state": {
                    "actual": "standby",
                    "configuration": None,
                    "web_port": None,
                },
            }
            return response



        existing_index = existing["device_index"]
        payload_for_storage.setdefault("index", existing_index)
        raw_payload_json = json.dumps(payload_for_storage)

        conn.execute(
            """
            UPDATE devices
               SET host = ?,
                   version = ?,
                   cpu = ?,
                   temp = ?,
                   raw_payload = ?,
                   updated_at = CURRENT_TIMESTAMP
             WHERE serial = ?
            """,
            (
                payload_for_storage.get("host"),
                payload_for_storage.get("version"),
                cpu,
                temp,
                raw_payload_json,
                serial,
            ),
        )
        conn.commit()

        service_actual = existing["service_actual"] or "standby"
        service_configuration = existing["service_configuration"]


        response = {
            "version": payload_for_storage.get("version") or existing["version"],
            "serial": serial,
            "host": payload_for_storage.get("host") or existing["host"],
            "index": existing_index,
            "service_state": {
                "actual": service_actual,
                "configuration": service_configuration
            },
        }
        return response

