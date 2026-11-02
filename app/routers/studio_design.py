"""Voice design API (SPEC §7). Implemented by M4-T1."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["design"])
