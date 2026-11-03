"""Languages management API (SPEC §5a, §7). M2-T20."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field

from app.languages import MAX_PRESETS, LanguageStore
from app.models import APIModel

router = APIRouter(prefix="/api", tags=["languages"])


class LanguageCreate(APIModel):
    code: str = Field(min_length=1, max_length=16)


class LanguagePatch(APIModel):
    default: bool = False


class LanguageRemoval(APIModel):
    voices: Literal["relabel", "delete"] | None = None
    to: str | None = None


class Preset(APIModel):
    id: str
    label: str
    text: str


class PresetList(APIModel):
    presets: list[Preset] = Field(max_length=MAX_PRESETS)


def _store(request: Request) -> LanguageStore:
    return request.app.state.languages


@router.get("/languages")
async def list_languages(request: Request) -> dict[str, list[dict[str, Any]]]:
    return {"languages": _store(request).entries()}


@router.get("/languages/supported")
async def list_supported(request: Request) -> dict[str, list[dict[str, str]]]:
    return {"languages": _store(request).supported()}


@router.post("/languages", status_code=201)
async def add_language(request: Request, body: LanguageCreate) -> dict[str, Any]:
    return _store(request).add(body.code)


@router.patch("/languages/{code}")
async def patch_language(request: Request, code: str, body: LanguagePatch) -> dict[str, Any]:
    store = _store(request)
    if body.default:
        return store.set_default(code)
    return store.get(code)


@router.delete("/languages/{code}")
async def remove_language(
    request: Request, code: str, body: LanguageRemoval | None = None
) -> dict[str, Any]:
    body = body or LanguageRemoval()
    return _store(request).remove(code, voices=body.voices, to=body.to)


@router.get("/languages/{code}/presets")
async def get_presets(request: Request, code: str) -> dict[str, list[dict[str, str]]]:
    return {"presets": _store(request).presets(code)}


@router.put("/languages/{code}/presets")
async def put_presets(
    request: Request, code: str, body: PresetList
) -> dict[str, list[dict[str, str]]]:
    return {"presets": _store(request).set_presets(code, [p.model_dump() for p in body.presets])}
