"""Voice design API (SPEC §7). Implemented by M4-T1."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.design import DesignService
from app.models import CandidateList, DesignRequest, DesignSave, VoiceSummary

router = APIRouter(prefix="/api", tags=["design"])


def _service(request: Request) -> DesignService:
    state = request.app.state
    return DesignService(state.store, state.takes, state.engine, state.settings)


@router.post("/design/candidates", response_model=CandidateList)
async def design_candidates(request: Request, req: DesignRequest) -> CandidateList:
    return CandidateList(candidates=await _service(request).candidates(req))


@router.post("/design/save", response_model=VoiceSummary)
async def design_save(request: Request, req: DesignSave) -> VoiceSummary:
    voice = await _service(request).save(req.candidate_id, req.name, req.language)
    return VoiceSummary(**voice.summary())
