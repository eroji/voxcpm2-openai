"""Voice library API (SPEC §7). Implemented by M2-T4."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["voices"])
