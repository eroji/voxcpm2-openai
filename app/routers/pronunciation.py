"""Pronunciation management API: respellings applied before synthesis (app/pronunciation.py)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field

from app.models import APIModel
from app.pronunciation import MAX_RULES, PronRule, PronunciationStore, export_rules

router = APIRouter(prefix="/api", tags=["pronunciation"])


class RuleList(APIModel):
    rules: list[PronRule] = Field(max_length=MAX_RULES)


class ImportRequest(APIModel):
    mode: Literal["merge", "replace"] = "merge"
    rules: Any = None  # the uploaded file's JSON, any shape parse_rule_file reads
    dry_run: bool = False


class TestRequest(APIModel):
    text: str = Field(max_length=20000)


def _store(request: Request) -> PronunciationStore:
    return request.app.state.pronunciation


def _dump(rules: list[PronRule]) -> dict[str, list[dict[str, Any]]]:
    return {"rules": [r.model_dump() for r in rules]}


@router.get("/pronunciation")
async def get_rules(request: Request) -> dict[str, list[dict[str, Any]]]:
    return _dump(_store(request).rules())


@router.put("/pronunciation")
async def put_rules(request: Request, body: RuleList) -> dict[str, list[dict[str, Any]]]:
    return _dump(_store(request).set_rules(body.rules))


@router.post("/pronunciation/import")
async def import_rules(request: Request, body: ImportRequest) -> dict[str, Any]:
    store = _store(request)
    summary = store.import_rules(body.rules, mode=body.mode, dry_run=body.dry_run)
    return {"dry_run": body.dry_run, "mode": body.mode, "summary": summary, **_dump(store.rules())}


@router.get("/pronunciation/export")
async def export(request: Request) -> dict[str, Any]:
    return export_rules(_store(request).rules())


@router.post("/pronunciation/test")
async def test_rules(request: Request, body: TestRequest) -> dict[str, str]:
    return {"text": _store(request).apply(body.text)}
