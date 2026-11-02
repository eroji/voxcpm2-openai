"""Clone + transcribe API (SPEC §7). Implemented by M2-T2."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["clone"])
