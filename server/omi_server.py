#!/usr/bin/env python3
"""Entry point for the OMI control server (FastAPI)."""
from __future__ import annotations

import uvicorn

from server.app import create_app
from server.app.settings import Settings
from server.logger import get_server_logger

get_server_logger()
app = create_app()


if __name__ == "__main__":
    settings = Settings()
    uvicorn.run("server.omi_server:app", host="0.0.0.0", port=settings.http_port, reload=False)
