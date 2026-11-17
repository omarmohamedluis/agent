"""Self-contained web interface used by both roles."""
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


def mount_web(app: FastAPI, available: Callable[[], bool] = lambda: True) -> None:
    directory = Path(__file__).parent
    app.mount("/assets", StaticFiles(directory=directory), name="web-assets")

    @app.get("/", include_in_schema=False)
    async def index():
        if not available():
            return HTMLResponse(
                '<!doctype html><html lang="es"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>OMI · Control central</title><link rel="stylesheet" href="/assets/style.css">'
                '<main><h1>Control central disponible</h1><p>Esta Pi sigue funcionando. '
                'La interfaz local se habilita al perder el servidor o si hay una discrepancia.</p>'
                '<button onclick="location.reload()">Comprobar de nuevo</button></main></html>',
                status_code=423,
            )
        return FileResponse(directory / "index.html")
