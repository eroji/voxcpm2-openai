"""M1-T18: metric exclusion for non-warm requests (SPEC §4).

``record=False`` keeps a request out of the rolling RTF / first-audio windows the
Engine screen shows: warm-up uses it, and the routers pass the live warm flag so
anything admitted before ``/ready`` went green is excluded too.
"""

from __future__ import annotations

from typing import Any

from app.engine import EngineClient, SynthParams
from app.models import SpeechRequest
from app.speech import SpeechService


def _counts(engine: EngineClient) -> tuple[int, int]:
    return len(engine._rtf), len(engine._first)


async def test_synth_record_false_keeps_metrics_untouched(fake_engine: Any) -> None:
    engine = EngineClient(fake_engine.url, concurrency=1, max_queue=0)
    await engine.start()
    before = _counts(engine)

    result = await engine.synth(SynthParams(text="warm-up"), record=False)
    assert result.audio  # the audio still comes back, only the metrics are skipped
    assert _counts(engine) == before

    await engine.synth(SynthParams(text="a real request"))  # default record=True
    assert _counts(engine) == (before[0] + 1, before[1])  # non-stream records RTF only
    await engine.aclose()


async def test_stream_record_false_keeps_metrics_untouched(fake_engine: Any) -> None:
    engine = EngineClient(fake_engine.url, concurrency=1, max_queue=0)
    await engine.start()
    before = _counts(engine)

    for record, expected in ((False, before), (True, (before[0] + 1, before[1] + 1))):
        handle = await engine.open_stream(
            SynthParams(text="warm-up", fmt="pcm", stream=True), record=record
        )
        async for _ in handle.chunks:
            pass
        assert _counts(engine) == expected
    await engine.aclose()


async def test_service_forwards_record(fake_engine: Any, settings: Any, library: Any) -> None:
    """The plumbing this task added: ``SpeechService`` must pass ``record`` through."""
    engine = EngineClient(fake_engine.url, concurrency=1, max_queue=0)
    await engine.start()
    service = SpeechService(library, engine, settings)
    request = SpeechRequest(model="voxcpm2", input="hello", voice="ashley-en")
    before = _counts(engine)

    await service.synthesize(request, record=False)
    assert _counts(engine) == before

    speech = await service.stream(
        SpeechRequest(model="voxcpm2", input="hello", voice="ashley-en", stream=True), record=False
    )
    async for _ in speech.chunks:
        pass
    assert _counts(engine) == before

    await service.synthesize(request)  # default keeps real traffic in the window
    assert _counts(engine) == (before[0] + 1, before[1])
    await engine.aclose()
