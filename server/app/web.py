from __future__ import annotations

from pathlib import Path
from typing import Dict

from fastapi.responses import HTMLResponse

from .settings import Settings


def render_index(settings: Settings, context: Dict[str, str]) -> HTMLResponse:
    template = (settings.web_root / "index.html").read_text(encoding="utf-8")
    for key, value in context.items():
        template = template.replace(f"{{{{{key}}}}}", value)
    return HTMLResponse(template)
