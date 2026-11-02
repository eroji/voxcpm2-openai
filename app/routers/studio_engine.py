"""Engine status/warm-up/restart API (SPEC §7). Implemented by M2-T5."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["engine"])
