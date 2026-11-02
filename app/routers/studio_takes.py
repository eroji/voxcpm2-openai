"""Speak + takes API (SPEC §7). Implemented by M2-T3."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["takes"])
