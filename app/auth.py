"""Auth dependencies (SPEC §10)."""

from __future__ import annotations

import secrets

from fastapi import Request

from app.config import Settings
from app.errors import Unauthorized


async def require_v1(request: Request) -> None:
    """Bearer auth for ``/v1/*`` when ``API_KEYS`` is set."""
    settings: Settings = request.app.state.settings
    keys = settings.api_key_set
    if not keys:
        return
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise Unauthorized()
    token = header[7:].strip()
    for key in keys:
        if secrets.compare_digest(token, key):
            return
    raise Unauthorized()
