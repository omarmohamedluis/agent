from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import HTTPException


def api_error(
    status_code: int,
    message: str,
    *,
    logger: Optional[logging.Logger] = None,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Raise an HTTPException with a consistent JSON payload and optional logging."""
    detail: Dict[str, Any] = {"error": message}
    if context:
        detail["context"] = context
    if logger:
        logger.error("%s (status=%s) context=%s", message, status_code, context)
    raise HTTPException(status_code=status_code, detail=detail)
