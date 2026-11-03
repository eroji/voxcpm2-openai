"""Voice design service (SPEC §7, Q-002/Q-007).

VoxCPM2 design = synthesis with **no reference audio**: the description travels as the
``(control instruction)text`` prefix VoxCPM2 expects, and every candidate gets a fresh
``seed``. Candidates are takes with
``kind="design"`` and ``extra={"design_prompt", "design_seed"}``; saving one writes a
voice with ``source="designed"`` and ``consent="own-generated-voice"``. The transport is
isolated here so spike S2 can swap it without touching routes.
"""

from __future__ import annotations

import secrets

from app import audio as audio_utils
from app.config import Settings
from app.engine import EngineClient, SynthParams
from app.errors import NotFound
from app.models import CandidateInfo, DesignRequest
from app.store import TakeStore, Voice, VoiceStore

_SEED_MAX = 2**31 - 1


class DesignService:
    def __init__(
        self,
        store: VoiceStore,
        takes: TakeStore,
        engine: EngineClient,
        settings: Settings,
        languages,
    ) -> None:
        self.store = store
        self.takes = takes
        self.engine = engine
        self.settings = settings
        self.languages = languages

    async def candidates(self, req: DesignRequest) -> list[CandidateInfo]:
        out: list[CandidateInfo] = []
        for index in range(req.n):
            seed = secrets.randbelow(_SEED_MAX) + 1
            params = SynthParams(
                text=f"({req.description}){req.sample_text}",
                ref_audio=None,
                ref_text=None,
                seed=seed,
                fmt="wav",
            )
            result = await self.engine.synth(params)
            samples, rate = audio_utils.read_audio(result.audio)
            take = self.takes.create(
                samples,
                rate,
                voice="design",
                text=req.sample_text,
                language=req.language,
                kind="design",
                extra={"design_prompt": req.description, "design_seed": seed},
            )
            out.append(
                CandidateInfo(
                    id=take["id"],
                    index=index + 1,
                    seed=seed,
                    duration_s=take["duration_s"],
                    **self.takes.take_urls(take["id"]),
                )
            )
        return out

    async def save(self, candidate_id: str, name: str, language: str) -> Voice:
        take = self.takes.get(candidate_id)  # TakeNotFound -> 404
        if take.get("kind") != "design":
            raise NotFound(
                f"Take '{candidate_id}' is not a design candidate.", code="take_not_found"
            )
        # SPEC §5a rule 4: a voice can only carry an enabled language. The router builds this
        # service without app.state, so read the enabled set straight from the data dir.
        language = self.languages.ensure_enabled(language)
        samples, rate = audio_utils.read_audio_file(self.takes.audio_path(candidate_id))
        return self.store.create(  # duplicate name -> Conflicting (409), propagated
            name,
            samples,
            rate,
            ref_text=take["text"],
            language=language,
            consent="own-generated-voice",
            source="designed",
            metadata={
                "design_prompt": take.get("design_prompt", ""),
                "design_seed": str(take.get("design_seed", "")),
            },
        )
