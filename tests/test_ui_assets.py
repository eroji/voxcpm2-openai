"""Static checks for the Voice Studio shell assets (M3-T1).

Text-based on purpose: the browser behaviour is covered by the Playwright suite
(tests/e2e). Here we only prove the shell matches docs/CONTRACTS.md §7.
"""

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "studio.css").read_text(encoding="utf-8")
JS_DIR = ROOT / "app" / "static" / "js"
CORE = (JS_DIR / "core.js").read_text(encoding="utf-8")


def js(name):
    return (JS_DIR / name).read_text(encoding="utf-8")


TABS = ["speak", "voices", "clone", "design", "engine"]

# Element IDs from docs/CONTRACTS.md §7 (header row handled separately).
CONTRACT_IDS = [
    "engine-status",
    # speak
    "voice-chips",
    "speak-text",
    "speak-style",
    "cfg",
    "cfg-out",
    "steps",
    "steps-out",
    "fmt",
    "stream-toggle",
    "generate",
    "takes-list",
    "speak-error",
    # voices
    "voice-search",
    "lang-filters",
    "voice-rows",
    "voices-count",
    "voices-error",
    # clone
    "drop-zone",
    "ref-file",
    "ref-text",
    "auto-transcribe",
    "clone-name",
    "clone-lang",
    "consent",
    "test-line",
    "save-voice",
    "clone-error",
    # design
    "design-desc",
    "design-tags",
    "design-lang",
    "design-n",
    "design-sample",
    "generate-candidates",
    "candidates",
    "design-name",
    "save-designed",
    "design-error",
    # engine
    "engine-stats",
    "run-warmup",
    "restart-engine",
    "engine-endpoint",
    "engine-error",
]


def test_shell_basics():
    assert "<title>Voice Studio</title>" in HTML
    assert 'class="vs-mark"' in HTML  # the red square
    assert 'id="engine-status"' in HTML
    for tab in TABS:
        assert f'class="vs-tab" data-tab="{tab}"' in HTML
    for tab in TABS:
        assert f'id="screen-{tab}"' in HTML
    assert HTML.count('class="vs-tab"') == 5
    assert HTML.count('class="vs-screen"') == 5


def test_contract_ids_exist_exactly_once():
    counts = Counter(re.findall(r'id="([^"]+)"', HTML))
    for el_id in CONTRACT_IDS:
        assert counts[el_id] == 1, f"{el_id} appears {counts[el_id]}x"
    dupes = [i for i, c in counts.items() if c > 1]
    assert not dupes, f"duplicate ids: {dupes}"


def test_assets_and_modules_linked():
    assert 'href="/static/tokens.css"' in HTML
    assert 'href="/static/studio.css"' in HTML
    for mod in ["core"] + TABS:
        assert f'<script type="module" src="/static/js/{mod}.js"></script>' in HTML
    # core.js must load before the screen modules
    assert HTML.index("/js/core.js") < HTML.index("/js/speak.js")


def test_error_rows_per_screen():
    for tab in TABS:
        assert f'<p class="vs-error" id="{tab}-error" role="alert" hidden></p>' in HTML, (
            f"missing error row for {tab}"
        )


def test_no_sample_data_left():
    assert "data-seed" not in HTML
    assert '<article class="vs-take"' not in HTML
    assert '<article class="vs-cand"' not in HTML
    for empty in ["voice-chips", "takes-list", "voice-rows", "candidates", "engine-stats"]:
        inner = re.search(rf'id="{empty}"[^>]*>(.*?)</', HTML, re.S)
        assert inner is not None and not inner.group(1).strip(), f"#{empty} not empty"


def test_core_exports():
    for name in [
        "api",
        "state",
        "loadVoices",
        "refreshEngine",
        "registerScreen",
        "selectVoice",
        "fmtDuration",
        "fmtClock",
        "fmtBytes",
        "wavePath",
        "renderWave",
        "play",
        "stopAudio",
        "showError",
        "clearError",
        "formatLang",
    ]:
        assert re.search(rf"export (async )?(function {name}\b|const {name}\b)", CORE), name
    assert "innerHTML" not in CORE


def test_wave_path_matches_reference_algorithm():
    assert "export function wavePath(amps)" in CORE
    # the exact reference algorithm (ui/studio.html): 300-wide mirrored curve
    for fragment in ["300 / (n - 1)", "mid = 12", "scale = 11.5", "Math.max(0.05, v)"]:
        assert fragment in CORE, fragment


def test_css_rules():
    assert ".vs-error" in CSS
    assert "[hidden]" in CSS
    assert "@media (prefers-reduced-motion: reduce)" in CSS
    assert "--vs-rule-section" in CSS  # reference rules kept
    # zero radius rule: only the token, never a literal
    assert set(re.findall(r"border-radius:\s*([^;}]+)", CSS)) <= {"0", "var(--vs-radius)"}


# --- M3-T0 review fixes -------------------------------------------------------


def test_clone_drop_copy_is_3_to_30_seconds():
    # SPEC §7/§8/§10: 3–30 s, not the reference mock's 5–20 s.
    assert "3–30 seconds of clean, single-speaker speech" in HTML
    assert "5–20" not in HTML


def test_design_language_default_follows_reference_order():
    # SPEC §8 does not pin the default; ui/studio.html lists Japanese first.
    select = re.search(r'id="design-lang">(.*?)</select>', HTML, re.S).group(1)
    opts = re.findall(r'<option value="(\w+)"', select)
    assert opts == ["ja", "en", "ko", "zh"]


def test_engine_status_renders_reason():
    # EngineStatus.reason must surface as text, never colour-only (R-005).
    assert "status.reason" in CORE
    assert "Not ready ·" in CORE
    assert "Ready ·" in CORE
    assert "Warming up…" in CORE


def test_start_waits_for_domcontentloaded_without_settimeout():
    assert 'document.addEventListener("DOMContentLoaded", start)' in CORE
    assert "setTimeout" not in CORE
    # late-registering screens must still get their initial show()
    assert "activeName" in CORE


def test_every_js_file_parses():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    for path in sorted(JS_DIR.glob("*.js")):
        proc = subprocess.run(
            [node, "--check", str(path)], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, f"{path.name}: {proc.stderr.strip()}"


def test_screen_imports_exist_in_core():
    exports = set(re.findall(r"export (?:async )?(?:function|const) (\w+)", CORE))
    for path in sorted(JS_DIR.glob("*.js")):
        if path.name == "core.js":
            continue
        source = path.read_text(encoding="utf-8")
        for names in re.findall(r"import\s*\{([^}]*)\}\s*from\s*[\"']\./core\.js[\"']", source):
            for name in (n.strip() for n in names.split(",")):
                if name:
                    assert name in exports, f"{path.name} imports {name}, not exported by core.js"


# --- M3-T7 wave-3 review fixes -------------------------------------------------


def test_voice_keys_not_display_names_in_api_calls():
    speak, voices = js("speak.js"), js("voices.js")
    assert "chip.dataset.key = v.key" in speak
    assert "v.key === state.selectedVoice" in speak
    assert "selectVoice(chip.dataset.key)" in speak
    assert "state.voices.some(v => v.key === saved)" in speak
    assert "state.voices.some(v => v.key === state.selectedVoice)" in speak
    for fragment in [
        "tr.dataset.key = v.key",
        "findVoice(tr.dataset.key)",
        "encodeURIComponent(v.key)",
        "encodeURIComponent(tr.dataset.key)",
    ]:
        assert fragment in voices, fragment
    assert "v.name === state.selectedVoice" not in speak
    assert "v.name === saved" not in speak
    assert "encodeURIComponent(v.name)" not in voices
    assert "encodeURIComponent(tr.dataset.name)" not in voices
    # display text still uses the name
    assert 'el("td", "name", v.name)' in voices


def test_rename_body_uses_name_and_validates():
    voices = js("voices.js")
    assert "new_name" not in voices
    assert "body.name = newName" in voices
    assert "^[a-z0-9][a-z0-9_-]{0,63}$" in voices
    assert "confirm.focus()" in voices  # delete confirm gets focus (R-039)


def test_clone_gates_on_engine_flags():
    clone = js("clone.js")
    assert "state.engine?.stt" in clone
    # M3-T11 (Q-023 resolved): Test a line goes through /api/speak — no bearer key,
    # so no api_keys_required gating string may remain anywhere.
    assert "api_keys_required" not in clone
    assert "api_keys_required" not in HTML
    assert 'api.get("/api/engine")' not in clone
    assert "setTimeout" not in clone
    assert 'id="clone-status"' in HTML
    assert 'class="vs-status" id="clone-status" role="status"' in HTML


def test_speak_lifecycle_releases_resources():
    speak = js("speak.js")
    assert "reader.cancel()" in speak
    assert "ctx.close()" in speak
    assert "await new Promise(resolve => setTimeout" not in speak  # no playback-tail await
    # M3-T11 (Q-024 resolved): Format is live in the markup; the stream toggle disables it in JS
    assert not re.search(r'<select[^>]*id="fmt"[^>]*\bdisabled\b', HTML)
    assert '$("fmt").disabled = streaming;' in speak
    assert "URL.revokeObjectURL" in CORE  # object URLs revoked in stopAudio()


def test_drop_zone_has_visible_focus():
    # the file input is visually hidden; the drop zone must show focus (R-035)
    assert ".vs-drop:focus-within" in CSS
    assert "[hidden] { display: none !important; }" in CSS  # hidden still wins


# --- M3-T8 wave-4 review fixes -------------------------------------------------


def test_clone_buttons_default_disabled_in_markup():
    # cold deep link must not offer auto-transcribe/test-line before gating runs
    assert re.search(r'<button[^>]*id="auto-transcribe"[^>]*\bdisabled\b', HTML)
    assert re.search(r'<button[^>]*id="test-line"[^>]*\bdisabled\b', HTML)
    # M3-T11: the /v1-key hint is gone — Test a line works under Studio auth alone
    assert 'aria-describedby="clone-notes"' not in HTML
    assert 'id="clone-notes"' not in HTML


def test_clone_show_gates_after_engine_status():
    clone = js("clone.js")
    assert re.search(
        r"import\s*\{[^}]*\brefreshEngine\b[^}]*\}\s*from\s*[\"']\./core\.js", clone
    ), "clone.js must import refreshEngine from core"
    assert "Promise.allSettled([refreshEngine(), loadVoices()])" in clone
    show = re.search(r"async show\(\) \{(?P<body>.*?)\n  \},", clone, re.S)
    assert show, "show() not found"
    body = show.group("body")
    assert "finally" in body, "gating must run in a finally"
    # the status must be awaited *before* gating, not merely both present
    assert "Promise.allSettled" in body
    assert body.index("Promise.allSettled") < body.index("gate()"), "gate() runs before the status"
    # an older duration probe must not overwrite a newer file
    assert re.search(r"\+\+probeSeq", clone)


def test_status_row_has_css_rule():
    rule = re.search(r"\.vs-status \{([^}]*)\}", CSS)
    assert rule, ".vs-status rule missing from studio.css"
    for token in ["var(--vs-s4)", "var(--vs-fs-small)", "var(--vs-muted)"]:
        assert token in rule.group(1), token


# --- M3-T9 wave-5 review fixes -------------------------------------------------


def test_clone_test_line_needs_only_a_file():
    clone = js("clone.js")
    # M3-T11: the /v1 hint element and its gating are gone; a file is the only condition
    assert "clone-notes" not in clone
    assert '$("test-line").disabled = !file;' in clone
    assert 'fetch("/api/speak?save=false"' in clone


def test_format_applies_with_stream_hint_and_no_dead_branch():
    # Q-024 resolved: Format drives response_format; streams are PCM at 48 kHz
    assert "Streaming uses PCM at 48 kHz" in HTML
    assert "Studio takes are WAV; use /v1 for other formats." not in HTML
    speak = js("speak.js")
    assert "res.headers.get(" not in speak  # dead non-WAV response branch removed
    assert "res.blob()" not in speak


# --- M3-T10 release review fixes ------------------------------------------------


def test_disabled_controls_have_an_affordance():
    # R-039: disabled must look disabled everywhere, without new colours
    rule = re.search(
        r"button:disabled,\s*select:disabled,\s*input:disabled,\s*textarea:disabled"
        r"\s*\{([^}]*)\}",
        CSS,
    )
    assert rule, "disabled control rule missing from studio.css"
    assert re.search(r"opacity:\s*\.45", rule.group(1))
    assert "cursor: not-allowed" in rule.group(1)
    assert ":focus-visible { outline: 2px solid var(--vs-accent)" in CSS  # focus ring kept


def test_design_sample_declares_japanese_and_follows_the_language_select():
    assert re.search(r'id="design-sample"[^>]*\blang="ja"', HTML)
    design = js("design.js")
    assert '$("design-sample").lang = $("design-lang").value' in design


def test_stream_requests_pin_sample_rate_to_the_decoder():
    speak = js("speak.js")
    assert "if (stream) body.sample_rate = 48000;" in speak
    assert "new AudioContext({ sampleRate: 48000 })" in speak  # 48 kHz handling unchanged


def test_voices_count_is_the_library_total():
    voices = js("voices.js")
    assert 'getElementById("voices-count").textContent = String(state.voices.length)' in voices
    assert "No voices match the current search or filter." in voices  # filtered state is named


# --- M3-T11: /api/speak preview + real Format ------------------------------------


def test_clone_preview_posts_to_api_speak_save_false():
    clone = js("clone.js")
    assert 'fetch("/api/speak?save=false"' in clone
    assert 'fetch("/v1/audio/speech"' not in clone


def test_take_playback_appends_format():
    speak = js("speak.js")
    # play/download use the take's requested container; the waveform keeps wave_url
    assert "${take.audio_url}?format=${take.format" in speak
    assert "play(takeUrl(take))" in speak
    assert "dl.href = takeUrl(take)" in speak
    assert ".get(take.wave_url)" in speak


def test_stream_toggle_gates_format():
    speak = js("speak.js")
    assert '$("fmt").disabled = streaming;' in speak
    assert '$("fmt-hint").hidden = !streaming;' in speak
    # streams stay pinned to the 48 kHz PCM decoder
    assert "if (stream) body.sample_rate = 48000;" in speak


def test_e2e_suite_never_skips_silently():
    for path in sorted((ROOT / "tests" / "e2e").glob("*.py")):
        assert "importorskip" not in path.read_text(encoding="utf-8"), path
