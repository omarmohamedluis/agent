"""Small transactional store. No file writes or globals at import time."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        # Invalid/corrupt databases raise, preserving the original for diagnosis.
        result = self.connection.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            self.connection.close()
            raise RuntimeError(f"Database integrity failure: {result}; original preserved at {path}")
        self.connection.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS revisions (id TEXT PRIMARY KEY, preset_id TEXT NOT NULL, value TEXT NOT NULL)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS revisions_preset ON revisions(preset_id)")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise
            else:
                self.connection.execute("COMMIT")

    def get(self, key: str, default: Any = None) -> Any:
        with self.lock:
            row = self.connection.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return default if row is None else json.loads(row[0])

    def put(self, key: str, value: Any) -> None:
        value_json = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        with self.transaction() as connection:
            connection.execute("INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value_json))

    def delete(self, key: str) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM kv WHERE key=?", (key,))

    def items(self, prefix: str = "") -> dict[str, Any]:
        with self.lock:
            rows = self.connection.execute("SELECT key,value FROM kv").fetchall()
            return {row[0]: json.loads(row[1]) for row in rows if row[0].startswith(prefix)}

    def delete_prefix(self, prefix: str) -> None:
        with self.transaction() as connection:
            keys = [row[0] for row in connection.execute("SELECT key FROM kv") if row[0].startswith(prefix)]
            connection.executemany("DELETE FROM kv WHERE key=?", [(key,) for key in keys])

    def backup(self, destination: Path | str) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.resolve() == self.path.resolve():
            raise ValueError("Backup destination must differ from the active database")
        with self.lock, sqlite3.connect(str(destination)) as backup:
            self.connection.backup(backup)
        return destination

    def close(self) -> None:
        with self.lock:
            self.connection.close()
