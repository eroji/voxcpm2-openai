"""HTTP contracts shared by routers, services, tests and the UI."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ResponseFormat = Literal["wav", "pcm", "mp3", "opus", "flac"]
Language = str  # en | ja | ko | zh; unknown codes pass through

VOICE_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"


class APIModel(BaseModel):
    """Ignore unknown fields: existing Qwen3 callers send extras (task_type, ...)."""

    model_config = ConfigDict(extra="ignore")


class SpeechRequest(APIModel):
    model: str = "voxcpm2"
    input: str = Field(min_length=1, max_length=4000)
    voice: str = Field(min_length=1)
    response_format: ResponseFormat = "wav"
    stream: bool = False
    instructions: str | None = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    sample_rate: int | None = Field(default=None, ge=8000, le=96000)
    ref_audio: str | None = None
    ref_text: str | None = None
    cfg: float | None = None
    steps: int | None = Field(default=None, ge=1, le=100)
    seed: int | None = None
    task_type: str | None = None


class ModelCard(APIModel):
    id: str
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str = "openbmb"


class ModelList(APIModel):
    object: Literal["list"] = "list"
    data: list[ModelCard]


class VoiceList(APIModel):
    voices: list[dict]


class VoiceSummary(APIModel):
    key: str
    name: str
    language: str
    source: str
    ref_duration_s: float
    ref_text: str
    created_at: int


class VoicePatch(APIModel):
    name: str | None = None
    ref_text: str | None = None
    language: str | None = None


class SaveAsVoice(APIModel):
    name: str
    language: str


class TakeInfo(APIModel):
    id: str
    voice: str
    text: str
    language: str
    kind: str = "studio"
    duration_s: float
    rtf: float | None = None
    first_audio_ms: float | None = None
    created_at: int
    audio_url: str
    wave_url: str


class TakeList(APIModel):
    takes: list[TakeInfo]


class DesignRequest(APIModel):
    description: str = Field(min_length=1, max_length=2000)
    sample_text: str = Field(min_length=1, max_length=2000)
    language: str = "en"
    n: int = Field(default=3, ge=1, le=4)


class CandidateInfo(APIModel):
    id: str
    index: int
    seed: int
    duration_s: float
    audio_url: str
    wave_url: str


class CandidateList(APIModel):
    candidates: list[CandidateInfo]


class DesignSave(APIModel):
    candidate_id: str
    name: str
    language: str


class EngineStatus(APIModel):
    ready: bool
    model: str
    reason: str | None = None
    runtime: str
    gpu: str | None = None
    vram_used_mb: int | None = None
    vram_total_mb: int | None = None
    kv_cache: str | None = None
    max_concurrency: int
    rtf: float | None = None
    first_audio_ms: float | None = None
    voices: int = 0
    queue_depth: int = 0
    stt: bool = False
    api_keys_required: bool = False


class TranscribeResult(APIModel):
    text: str
