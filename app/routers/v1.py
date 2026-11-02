"""OpenAI-compatible ``/v1`` routes (SPEC §6). Implemented by M1-T2."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/v1", tags=["openai"])
