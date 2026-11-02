"""Static checks for the Voice Studio shell assets (M3-T1).

Text-based on purpose: the browser behaviour is covered by the Playwright suite
(tests/e2e). Here we only prove the shell matches docs/CONTRACTS.md §7.
"""

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "studio.css").read_text(encoding="utf-8")
CORE = (ROOT / "app" / "static" / "js" / "core.js").read_text(encoding="utf-8")

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
