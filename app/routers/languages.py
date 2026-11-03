"""GET /api/languages — the 30 VoxCPM2 model-card languages (SPEC v0.2.0 §8)."""

from __future__ import annotations

from fastapi import APIRouter

from app.languages import LANGUAGES

router = APIRouter(prefix="/api", tags=["languages"])


@router.get("/languages")
async def languages() -> dict[str, list[dict[str, str]]]:
    return {"languages": [{"code": lang["code"], "name": lang["name"]} for lang in LANGUAGES]}
