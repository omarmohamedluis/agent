"""Bounded diagnostics; deliberately absent from the realtime event path."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(data_dir: Path, *, level: str = "INFO", budget_mb: int = 40) -> None:
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler = RotatingFileHandler(logs / "omi.log", maxBytes=max(1, budget_mb) * 1024 * 1024 // 5, backupCount=4, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.addHandler(console)
    root.addHandler(file_handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def recent_logs(data_dir: Path, *, limit: int = 200, level: str = "") -> list[str]:
    result: list[str] = []
    paths = sorted((data_dir / "logs").glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths[:4]:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 1024 * 1024))
            content = stream.read().decode("utf-8", errors="replace")
        lines = content.splitlines()
        if size > 1024 * 1024:
            lines = lines[1:]
        result.extend(line for line in lines if not level or f" {level.upper()} " in line)
    return result[-min(max(limit, 1), 2000):]
