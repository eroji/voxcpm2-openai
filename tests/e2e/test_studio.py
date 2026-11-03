"""Browser e2e for Voice Studio (M3-T6): shell, routing, all five screens, screenshots.

Every test uses a fresh page (fresh JS state); anything persisted server-side is either
read-only or cleaned up by the test that wrote it, so the suite is order-independent.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile as sf
from playwright.sync_api import expect  # missing Playwright = collection error, never a skip

from conftest import _seed, _serve, _settings

RESULTS_DIR = Path(__file__).resolve().parents[2] / "test-results"
SCREENS = ["speak", "voices", "clone", "design", "languages", "engine"]
_serve_app = contextlib.contextmanager(_serve)  # conftest's _serve is a bare generator


@pytest.fixture(scope="session")
def live_app_empty(fake_engine, tmp_path_factory: pytest.TempPathFactory):
    """Same app without seeded voices: the SPEC §8 empty-library states."""
    data_dir = tmp_path_factory.mktemp("e2e-data-empty")
    yield from _serve(_settings(data_dir, fake_engine))


@pytest.fixture
def live_langs(fake_engine, tmp_path):
    """Isolated app for the Languages screen: its mutations never touch the session data."""
    settings = _settings(tmp_path / "langs", fake_engine)
    _seed(settings)
    yield from _serve(settings)


def test_shell_and_engine_status(page, live_app, open_studio):
    open_studio(page)
    assert page.title() == "Voice Studio"
    expect(page.locator(".vs-tab")).to_have_count(6)
    expect(page.locator('.vs-tab[data-tab="speak"]')).to_have_attribute("aria-current", "page")
    expect(page.locator("#engine-status")).to_have_text(re.compile("^Ready"), timeout=10000)


@pytest.mark.parametrize("screen", ["voices", "clone", "design", "languages", "engine", "speak"])
def test_hash_routing(page, live_app, open_studio, screen):
    open_studio(page)
    page.click(f'.vs-tab[data-tab="{screen}"]')
    expect(page).to_have_url(f"{live_app}/#{screen}")
    expect(page.locator(f"#screen-{screen}")).to_be_visible()
    for other in SCREENS:
        if other != screen:
            expect(page.locator(f"#screen-{other}")).to_be_hidden()


def test_speak_generate(page, live_app, open_studio):
    open_studio(page)
    expect(page.locator("#voice-chips .vs-chip")).to_have_count(3)
    # "slow" makes the fake engine hold the request 250 ms so the busy state is observable
    page.fill("#speak-text", "This line is deliberately slow to synthesise.")
    page.click("#stream-toggle")
    expect(page.locator("#stream-toggle")).to_have_attribute("aria-pressed", "false")
    page.click("#generate")
    expect(page.locator("#generate")).to_be_disabled()
    expect(page.locator("#generate")).to_have_text("Generating…")
    expect(page.locator("#generate")).to_be_enabled()
    expect(page.locator("#generate")).to_have_text("Generate")
    newest = page.locator("#takes-list .vs-take").first
    expect(newest.locator(".who b")).to_have_text("ashley-en")
    expect(newest.locator("svg.vs-wave")).to_have_class(re.compile("is-current"))
    expect(newest.locator("svg.vs-wave path.base")).to_have_attribute("d", re.compile("M0.+Z"))

    # M3-T11: Format is real — a non-stream generate records it and the row links use it
    expect(page.locator("#fmt")).to_be_enabled()  # streaming was turned off above
    page.select_option("#fmt", "mp3")
    page.fill("#speak-text", "Format check line.")
    page.click("#generate")
    expect(page.locator("#generate")).to_have_text("Generate", timeout=30000)
    newest = page.locator("#takes-list .vs-take").first
    expect(newest.locator("a.btn-link")).to_have_attribute("href", re.compile(r"\?format=mp3$"))


def test_speak_format_follows_stream_toggle(page, live_app, open_studio):
    """M3-T11: Format applies when streaming is off; streams are PCM at 48 kHz."""
    open_studio(page)
    expect(page.locator("#fmt")).to_be_disabled()  # stream on by default
    expect(page.locator("#fmt-hint")).to_be_visible()
    page.click("#stream-toggle")
    expect(page.locator("#fmt")).to_be_enabled()
    expect(page.locator("#fmt-hint")).to_be_hidden()


def test_speak_stream(page, live_app, open_studio):
    """Stream on (default): the PCM path plays and still lands a take row (R-033)."""
    open_studio(page)
    expect(page.locator("#stream-toggle")).to_have_attribute("aria-pressed", "true")
    # "slow" holds the first chunk 250 ms so the busy state is observable
    page.fill("#speak-text", "This streamed line is deliberately slow to synthesise.")
    page.click("#generate")
    expect(page.locator("#generate")).to_be_disabled()
    expect(page.locator("#generate")).to_be_enabled(timeout=30000)
    expect(page.locator("#generate")).to_have_text("Generate")
    expect(page.locator("#speak-error")).to_be_hidden()
    newest = page.locator("#takes-list .vs-take").first
    expect(newest.locator(".who b")).to_have_text("ashley-en")


def test_voices_screen(page, live_app, open_studio):
    open_studio(page, "voices")
    rows = page.locator("#voice-rows tr[data-key]")
    expect(rows).to_have_count(3)
    expect(page.locator("#voices-count")).to_have_text("3")

    page.click('#lang-filters [data-lang="ja"]')
    expect(rows).to_have_count(1)
    expect(page.locator("#voice-rows tr[data-key]")).to_have_attribute("data-key", "ashley-ja")
    page.click('#lang-filters [data-lang="all"]')

    page.fill("#voice-search", "ashley-ko")
    expect(rows).to_have_count(1)
    expect(page.locator("#voice-rows tr[data-key]")).to_have_attribute("data-key", "ashley-ko")
    page.fill("#voice-search", "")
    expect(rows).to_have_count(3)

    # the header count is the library total; a filter that matches nothing says so in the rows
    page.fill("#voice-search", "no-such-voice")
    expect(rows).to_have_count(0)
    expect(page.locator("#voices-count")).to_have_text("3")
    expect(page.locator("#voice-rows .vs-empty")).to_contain_text("No voices match")
    page.fill("#voice-search", "")
    expect(rows).to_have_count(3)

    en_row = page.locator('#voice-rows tr[data-key="ashley-en"]')
    en_row.get_by_role("button", name="Edit").click()
    expect(en_row.locator('input[data-field="name"]')).to_be_visible()
    expect(en_row.locator('select[data-field="language"]')).to_be_visible()
    expect(en_row.locator('textarea[data-field="ref_text"]')).to_be_visible()
    en_row.get_by_role("button", name="Cancel").click()
    expect(en_row.locator("td.name")).to_have_text("ashley-en")

    ja_row = page.locator('#voice-rows tr[data-key="ashley-ja"]')
    ja_row.get_by_role("button", name="Delete").click()
    expect(ja_row.locator("td.act")).to_contain_text(
        re.compile(r"Delete ashley-ja\? This can.t be undone from the UI\.")
    )
    ja_row.get_by_role("button", name="Cancel").click()
    expect(ja_row.get_by_role("button", name="Delete")).to_be_visible()


def test_speak_presets_follow_voice_language(page, live_app, open_studio):
    """M3-T14: six chips, default Question, in the selected voice's language."""
    open_studio(page)
    expect(page.locator("#speak-presets .btn-tag")).to_have_count(6)
    question = page.locator('#speak-presets [data-id="question"]')
    expect(question).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#speak-lang-name")).to_have_text("English")
    expect(page.locator("#speak-text")).to_have_value(re.compile("certificate"))

    page.click('#voice-chips .vs-chip[data-key="ashley-ja"]')
    expect(page.locator("#speak-lang-name")).to_have_text("Japanese")
    expect(page.locator("#speak-text")).to_have_value(re.compile("[぀-ゟ゠-ヿ]"))
    expect(question).to_have_attribute("aria-pressed", "true")

    # switch the preset, then the voice: same preset id, new language's text
    page.click('#speak-presets [data-id="greeting"]')
    greeting_ja = "おはようございます。今日は会議が三つあり、最初の会議は九時半からです。"
    expect(page.locator("#speak-text")).to_have_value(greeting_ja)
    page.click('#voice-chips .vs-chip[data-key="ashley-en"]')
    greeting = page.locator('#speak-presets [data-id="greeting"]')
    expect(greeting).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#speak-text")).to_have_value(re.compile("^Good morning"))
    expect(page.locator("#speak-text")).to_have_attribute("lang", "en")


def test_design_presets_follow_language(page, live_app, open_studio):
    """M3-T14: Design chips follow the language select; a selected preset swaps text."""
    open_studio(page, "design")
    expect(page.locator("#design-presets .btn-tag")).to_have_count(6)
    page.click('#design-presets [data-id="question"]')
    expect(page.locator("#design-sample")).to_have_value(re.compile("[぀-ゟ゠-ヿ]"))
    page.select_option("#design-lang", "en")
    expect(page.locator("#design-sample")).to_have_value(re.compile("certificate"))
    question = page.locator('#design-presets [data-id="question"]')
    expect(question).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#design-sample")).to_have_attribute("lang", "en")


def test_speak_empty_library(page, live_app_empty):
    """SPEC §8 empty library: notice instead of chips, Generate aria-disabled, no API call."""
    page.goto(f"{live_app_empty}/")
    expect(page.locator("#speak-novoice")).to_be_visible()
    expect(page.locator("#voice-chips")).to_be_hidden()
    expect(page.locator("#gen-hint")).to_be_visible()
    expect(page.locator("#generate")).to_have_attribute("aria-disabled", "true")
    expect(page.locator("#takes-empty")).to_be_visible()

    spoken: list[str] = []
    page.on("request", lambda r: spoken.append(r.url))
    # aria-disabled buttons are not actionable for Playwright; the browser click must still fire
    page.locator("#generate").dispatch_event("click")
    expect(page.locator("#speak-novoice button").first).to_be_focused()
    assert not any("/api/speak" in url for url in spoken)


def test_voices_empty_library(page, live_app_empty):
    """SPEC §8: one empty state, never both sets of buttons."""
    page.goto(f"{live_app_empty}/#voices")
    expect(page.locator("#voices-empty")).to_be_visible()
    expect(page.locator("#voices-actions")).to_be_hidden()
    expect(page.locator("#voices-list")).to_be_hidden()
    expect(page.locator("#voices-count")).to_be_hidden()
    assert page.locator("#screen-voices button:visible").count() == 2
    page.click("#voices-empty [data-goto='clone']")
    expect(page.locator("#screen-clone")).to_be_visible()


def test_clone_gating(page, live_app, open_studio, tmp_path):
    open_studio(page, "clone")
    save = page.locator("#save-voice")
    expect(save).to_be_disabled()

    wav = tmp_path / "reference.wav"
    t = np.linspace(0, 5.0, 48000 * 5, endpoint=False)
    sf.write(wav, (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), 48000, format="WAV")
    page.set_input_files("#ref-file", str(wav))
    expect(page.locator("#drop-zone .meta")).to_contain_text("reference.wav")

    # M3-T11: Test a line needs only a file — preview goes via /api/speak?save=false, no bearer key
    expect(page.locator("#test-line")).to_be_enabled()
    with page.expect_response(lambda r: r.url.endswith("/api/speak?save=false")) as resp:
        page.click("#test-line")
    assert resp.value.status == 200
    assert resp.value.request.headers.get("authorization") is None
    expect(page.locator("#clone-error")).to_be_hidden()

    expect(save).to_be_disabled()  # file alone is not enough: no name yet

    page.fill("#clone-name", "Bad Name")
    expect(page.locator("#clone-error")).to_contain_text("lowercase")
    expect(save).to_be_disabled()

    page.fill("#clone-name", "e2e-clone")
    expect(page.locator("#clone-error")).to_be_hidden()
    expect(save).to_be_disabled()  # consent still missing
    page.check("#consent")
    expect(save).to_be_enabled()


def test_clone_test_line_with_api_keys(page, live_app_keys, open_studio, tmp_path):
    """Q-023 resolved: Test a line still works without a bearer key when API_KEYS is set."""
    assert httpx.get(f"{live_app_keys}/api/engine").json()["api_keys_required"] is True
    open_studio(page, "clone", live_app_keys)
    wav = tmp_path / "reference.wav"
    t = np.linspace(0, 5.0, 48000 * 5, endpoint=False)
    sf.write(wav, (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), 48000, format="WAV")
    page.set_input_files("#ref-file", str(wav))
    expect(page.locator("#test-line")).to_be_enabled()  # api_keys_required gates nothing here
    with page.expect_response(lambda r: r.url.endswith("/api/speak?save=false")) as resp:
        page.click("#test-line")
    assert resp.value.status == 200
    assert resp.value.request.headers.get("authorization") is None
    expect(page.locator("#clone-error")).to_be_hidden()


def test_clone_save(page, live_app, open_studio, tmp_path):
    """Full clone round trip in the browser: file + name + consent → saved voice (R-035)."""
    httpx.delete(f"{live_app}/api/voices/e2e-saved")  # idempotent across reruns
    open_studio(page, "clone")
    wav = tmp_path / "reference.wav"
    t = np.linspace(0, 5.0, 48000 * 5, endpoint=False)
    sf.write(wav, (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), 48000, format="WAV")
    page.set_input_files("#ref-file", str(wav))
    page.fill("#clone-name", "e2e-saved")
    page.check("#consent")
    expect(page.locator("#save-voice")).to_be_enabled()
    page.click("#save-voice")
    expect(page.locator("#clone-status")).to_contain_text("Voice saved.", timeout=15000)
    expect(page.locator("#clone-error")).to_be_hidden()

    keys = [v["key"] for v in httpx.get(f"{live_app}/api/voices").json()["voices"]]
    assert "e2e-saved" in keys
    assert httpx.delete(f"{live_app}/api/voices/e2e-saved").status_code == 200


def test_design_flow(page, live_app, open_studio):
    httpx.delete(f"{live_app}/api/voices/e2e-designed")  # idempotent across reruns
    open_studio(page, "design")
    page.fill("#design-desc", "calm, professional, early 30s")
    page.fill("#design-sample", "Good morning, here is the sample line.")
    page.select_option("#design-n", "2")
    page.click("#generate-candidates")
    expect(page.locator("#candidates .vs-cand")).to_have_count(2)

    first = page.locator("#candidates .vs-cand").first
    first.locator(".pick").click()
    expect(first).to_have_class(re.compile("is-selected"))
    expect(first.locator(".pick")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#save-designed")).to_be_disabled()

    page.fill("#design-name", "e2e-designed")
    expect(page.locator("#save-designed")).to_be_enabled()
    page.click("#save-designed")
    expect(page.locator("#save-designed")).to_have_text("Saved ✓")

    keys = [v["key"] for v in httpx.get(f"{live_app}/api/voices").json()["voices"]]
    assert "e2e-designed" in keys
    assert httpx.delete(f"{live_app}/api/voices/e2e-designed").status_code == 200


def test_engine_screen(page, live_app, open_studio, fake_engine):
    open_studio(page, "engine")
    stats = page.locator("#engine-stats .vs-stat")
    expect(stats).to_have_count(8)

    # M3-T12 cosmetics: short value + full name in title, updated labels/notes
    expect(stats.nth(0).locator(".meta").first).to_have_text("Model")
    expect(stats.nth(0).locator(".v")).to_have_text("VoxCPM2")
    expect(stats.nth(0).locator(".v")).to_have_attribute("title", "openbmb/VoxCPM2")
    expect(stats.nth(0).locator(".meta").nth(1)).to_have_text("openbmb/VoxCPM2 · 2B · 48 kHz")
    expect(stats.nth(1).locator(".meta").first).to_have_text("GPU")
    expect(stats.nth(1).locator(".meta").nth(1)).to_have_text("Shared with whisper-stt, TEI")
    # the value never keeps the "NVIDIA GeForce " prefix (hosts without nvidia-smi show Unknown)
    expect(stats.nth(1).locator(".v")).not_to_have_text(re.compile(r"^NVIDIA GeForce"))
    expect(stats.nth(2).locator(".meta").first).to_have_text("GPU memory used")
    expect(stats.nth(4).locator(".meta").nth(1)).to_have_text("Last 20 requests, warm-up excluded")
    expect(stats.nth(7).locator(".meta").first).to_have_text("Runtime")
    expect(stats.nth(7).locator(".v")).to_have_text("vLLM-Omni")

    # endpoint example body is pretty-printed JSON (ui/studio.html reference)
    expect(page.locator("#engine-endpoint pre")).to_have_text(
        re.compile(r"\{\n\s+\"model\": \"voxcpm2\",\n")
    )

    calls = fake_engine.state["calls"]
    page.click("#run-warmup")
    expect(page.locator("#run-warmup")).to_be_disabled()
    expect(page.locator("#run-warmup")).to_be_enabled()
    expect(page.locator("#run-warmup")).to_have_text("Run warm-up")
    expect(page.locator("#engine-error")).to_be_hidden()
    assert fake_engine.state["calls"] > calls  # the warm-up really hit the engine

    restart = page.locator("#restart-engine")
    restart.click()
    expect(restart).to_have_text("Confirm restart")
    # one click must not touch the server: still warm, no error
    assert httpx.get(f"{live_app}/api/engine").json()["ready"] is True
    expect(page.locator("#engine-error")).to_be_hidden()

    restart.click()
    # Dev hosts have no s6: the documented outcome is a 502 mentioning s6-svc. On a deploy
    # host the restart succeeds and no error appears. Accept either; assert recovery either
    # way — button state back and /api/engine still answering.
    try:
        expect(page.locator("#engine-error")).to_be_visible(timeout=15000)
    except AssertionError:
        pass  # restart succeeded: never assert s6-svc text here
    expect(restart).to_have_text("Restart engine")
    expect(restart).to_be_enabled()
    assert httpx.get(f"{live_app}/api/engine").status_code == 200
    # the app re-warms on its own; wait it out so the session state is back to Ready
    deadline = time.time() + 30
    while time.time() < deadline and not httpx.get(f"{live_app}/api/engine").json().get("ready"):
        time.sleep(0.25)
    assert httpx.get(f"{live_app}/api/engine").json()["ready"] is True


def test_keyboard_nav(page, live_app, open_studio):
    open_studio(page)
    reached_tabs = False
    for _ in range(6):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.classList.contains('vs-tab')"):
            reached_tabs = True
            break
    assert reached_tabs, "Tab from the top never reached the tab buttons"
    assert page.evaluate("getComputedStyle(document.activeElement).outlineWidth") == "2px"

    page.keyboard.press("Tab")  # → Voices tab
    expect(page.locator('.vs-tab[data-tab="voices"]')).to_be_focused()
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{live_app}/#voices")
    expect(page.locator("#screen-voices")).to_be_visible()


# --- M3-T16: Languages screen and language-aware Studio --------------------------


def test_languages_table(page, live_langs, open_studio):
    """SPEC §8: enabled languages default-first, with counts, presets and the Default tag."""
    open_studio(page, "languages", live_langs)
    rows = page.locator("#lang-rows .vs-lang-row")
    expect(rows).to_have_count(4)
    expect(page.locator("#lang-count")).to_have_text("4 of 30 enabled")
    expect(rows.first).to_have_attribute("data-code", "en")  # the default language is first
    english = page.locator('.vs-lang-row[data-code="en"]')
    expect(english.locator(".tag")).to_have_text("Default")
    expect(english).to_contain_text("6 of 6")
    expect(english).to_contain_text("1 voice")
    expect(english.locator('button[data-act="remove"]')).to_have_count(0)
    expect(english.locator(".lock")).to_contain_text(
        "Default · make another language default to remove"
    )
    japanese = page.locator('.vs-lang-row[data-code="ja"]')
    expect(japanese).to_contain_text("1 voice")
    expect(japanese.locator(".code")).to_have_text("JA")
    expect(japanese.get_by_role("button", name="Make default")).to_be_visible()


def test_languages_add_and_make_default(page, live_langs, open_studio):
    open_studio(page, "languages", live_langs)
    expect(page.locator("#lang-rows .vs-lang-row")).to_have_count(4)  # show() has rendered
    page.select_option("#add-lang", "de")
    page.click("#add-lang-btn")
    rows = page.locator("#lang-rows .vs-lang-row")
    expect(rows).to_have_count(5)
    expect(page.locator("#lang-count")).to_have_text("5 of 30 enabled")
    german = page.locator('.vs-lang-row[data-code="de"]')
    expect(german).to_contain_text("None yet")
    # a new language appears everywhere a language is chosen
    page.click('.vs-tab[data-tab="clone"]')
    expect(page.locator("#clone-lang option")).to_have_count(5)
    page.click('.vs-tab[data-tab="languages"]')
    german.get_by_role("button", name="Make default").click()
    expect(german.locator(".tag")).to_have_text("Default")
    expect(page.locator('.vs-lang-row[data-code="en"] .tag')).to_have_count(0)
    expect(rows.first).to_have_attribute("data-code", "de")
    expect(page.locator('.vs-lang-row[data-code="de"] button[data-act="remove"]')).to_have_count(0)
    expect(page.locator('.vs-lang-row[data-code="en"] button[data-act="remove"]')).to_be_visible()


def test_languages_remove_with_voices_relabels(page, live_langs, open_studio):
    """SPEC §5a rule 3: the row expands with the voice decision before anything is removed."""
    open_studio(page, "languages", live_langs)
    expect(page.locator("#lang-rows .vs-lang-row")).to_have_count(4)  # show() has rendered
    japanese = page.locator('.vs-lang-row[data-code="ja"]')
    japanese.get_by_role("button", name="Remove").click()
    panel = japanese.locator(".vs-lang-remove")
    expect(japanese).to_have_class(re.compile("removing"))
    expect(panel).to_contain_text("1 voice use Japanese: ashley-ja")
    expect(panel).to_contain_text("Choose what happens to them before Japanese is removed.")
    expect(panel.locator("select option")).to_have_text(
        ["English", "Korean", "Chinese"]
    )  # enabled others, default first
    panel.get_by_role("button", name="Cancel").click()
    expect(page.locator("#lang-rows .vs-lang-row")).to_have_count(4)  # nothing happened

    japanese.get_by_role("button", name="Remove").click()
    panel = japanese.locator(".vs-lang-remove")
    panel.locator("select").select_option("en")
    panel.get_by_role("button", name="Relabel and remove").click()
    expect(page.locator('.vs-lang-row[data-code="ja"]')).to_have_count(0)
    expect(page.locator("#lang-rows .vs-lang-row")).to_have_count(3)
    saved = httpx.get(f"{live_langs}/api/voices").json()["voices"]
    voices = {v["key"]: v["language"] for v in saved}
    assert voices == {"ashley-en": "en", "ashley-ja": "en", "ashley-ko": "ko"}
    # the removed language is gone from the filters, and the relabelled voice reads as English
    page.click('.vs-tab[data-tab="voices"]')
    expect(page.locator('#lang-filters [data-lang="ja"]')).to_have_count(0)
    expect(page.locator('#voice-rows tr[data-key="ashley-ja"] td').nth(1)).to_have_text("English")


def test_languages_remove_without_voices(page, live_langs, open_studio):
    open_studio(page, "languages", live_langs)
    expect(page.locator("#lang-rows .vs-lang-row")).to_have_count(4)  # show() has rendered
    chinese = page.locator('.vs-lang-row[data-code="zh"]')
    chinese.get_by_role("button", name="Remove").click()
    panel = chinese.locator(".vs-lang-remove")
    expect(panel).to_contain_text(
        "Remove Chinese? No voices use it. Its test phrases are deleted too."
    )
    panel.get_by_role("button", name="Remove").click()
    expect(page.locator("#lang-rows .vs-lang-row")).to_have_count(3)
    codes = [item["code"] for item in httpx.get(f"{live_langs}/api/languages").json()["languages"]]
    assert codes == ["en", "ja", "ko"]


def test_languages_presets_editor(page, live_langs, open_studio):
    open_studio(page, "languages", live_langs)
    expect(page.locator("#lang-rows .vs-lang-row")).to_have_count(4)  # show() has rendered
    english = page.locator('.vs-lang-row[data-code="en"]')
    english.get_by_role("button", name="Edit phrases").click()
    editor = english.locator(".vs-lang-remove")
    expect(editor.locator(".phrase")).to_have_count(6)
    editor.locator('input[data-field="label"]').first.fill("")
    editor.get_by_role("button", name="Save").click()
    expect(page.locator("#languages-error")).to_contain_text("Every test phrase needs a label")
    editor.locator('input[data-field="label"]').first.fill("Hello")
    editor.locator('input[data-field="text"]').first.fill("E2E edited phrase.")
    editor.get_by_role("button", name="Save").click()
    expect(page.locator("#languages-error")).to_be_hidden()
    saved = httpx.get(f"{live_langs}/api/languages/en/presets").json()["presets"]
    assert saved[0]["label"] == "Hello" and saved[0]["text"] == "E2E edited phrase."
    expect(english).to_contain_text("6 of 6")
    # Speak offers the edited phrase in the first chip
    page.click('.vs-tab[data-tab="speak"]')
    expect(page.locator("#speak-presets .btn-tag").first).to_have_text("Hello")


def test_clone_and_design_language_selects(page, live_langs, open_studio):
    """Enabled languages only, default first; Design prefers Japanese (SPEC §5a)."""

    def option_values(selector: str, expected: list[str]) -> None:
        expect(page.locator(selector)).to_have_count(len(expected))  # show() is async
        got = page.eval_on_selector_all(selector, "els => els.map(e => e.value)")
        assert got == expected

    open_studio(page, "clone", live_langs)
    option_values("#clone-lang option", ["en", "ja", "ko", "zh"])
    assert page.locator("#clone-lang").input_value() == "en"
    page.click('.vs-tab[data-tab="design"]')
    option_values("#design-lang option", ["en", "ja", "ko", "zh"])
    assert page.locator("#design-lang").input_value() == "ja"

    httpx.post(f"{live_langs}/api/languages", json={"code": "de"})
    page.click('.vs-tab[data-tab="languages"]')  # re-reads the enabled set for every screen
    page.click('.vs-tab[data-tab="design"]')
    option_values("#design-lang option", ["en", "ja", "ko", "zh", "de"])
    page.click('.vs-tab[data-tab="clone"]')
    option_values("#clone-lang option", ["en", "ja", "ko", "zh", "de"])


def test_design_empty_presets_hint(page, live_langs, open_studio):
    """A language with no phrases shows the hint instead of chips and never rewrites the text."""
    httpx.post(f"{live_langs}/api/languages", json={"code": "de"})
    open_studio(page, "design", live_langs)
    expect(page.locator("#design-lang option")).to_have_count(5)  # show() has filled the select
    page.select_option("#design-lang", "de")
    expect(page.locator("#design-presets .btn-tag")).to_have_count(0)
    expect(page.locator("#design-presets")).to_contain_text("No test phrases for German yet")
    expect(page.locator("#design-presets a")).to_have_attribute("href", "#languages")
    page.fill("#design-sample", "Typed line stays put.")
    page.select_option("#design-lang", "en")
    expect(page.locator("#design-presets .btn-tag")).to_have_count(6)
    expect(page.locator("#design-sample")).to_have_value("Typed line stays put.")


def test_speak_presets_and_no_phrases_link(page, live_langs, open_studio):
    """Speak follows the voice language; a language with none links to Languages (SPEC §5a)."""
    open_studio(page, "speak", live_langs)
    expect(page.locator("#voice-chips .vs-chip")).to_have_count(3)  # show() has rendered
    page.click('#voice-chips .vs-chip[data-key="ashley-ja"]')
    expect(page.locator("#speak-lang-name")).to_have_text("Japanese")
    expect(page.locator('#speak-presets [data-id="question"]')).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(page.locator("#speak-text")).to_have_value(re.compile("[぀-ゟ゠-ヿ]"))

    httpx.post(f"{live_langs}/api/languages", json={"code": "de"})
    relabel = httpx.patch(f"{live_langs}/api/voices/ashley-ko", json={"language": "de"})
    assert relabel.status_code == 200
    page.click('.vs-tab[data-tab="languages"]')  # refresh the cached language lists
    page.click('.vs-tab[data-tab="speak"]')
    expect(page.locator('#voice-chips .vs-chip[data-key="ashley-ko"] .lang')).to_have_text("DE")
    page.click('#voice-chips .vs-chip[data-key="ashley-ko"]')
    expect(page.locator("#speak-presets .btn-tag")).to_have_count(0)
    expect(page.locator("#speak-presets")).to_contain_text("No test phrases for German yet")
    page.click("#speak-presets a")
    expect(page.locator("#screen-languages")).to_be_visible()


def test_unsupported_language_blocks_speak(fake_engine, tmp_path, page, open_studio):
    """SPEC §5a rule 5: drift marks the row, the Voices cell and blocks Generate without sending."""
    supported = tmp_path / "supported.json"
    supported.write_text(
        json.dumps(
            {
                "languages": [
                    {"code": "en", "name": "English", "native": "English"},
                    {"code": "ja", "name": "Japanese", "native": "日本語"},
                    {"code": "zh", "name": "Chinese", "native": "中文"},
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = _settings(tmp_path / "drift", fake_engine, supported_languages_file=supported)
    _seed(settings)  # ashley-ko keeps a language the engine no longer offers
    with _serve_app(settings) as base:
        open_studio(page, "languages", base)
        korean = page.locator('.vs-lang-row[data-code="ko"]')
        expect(korean).to_contain_text("Not supported by the engine")
        expect(korean).to_have_text(re.compile("KO"))

        page.click('.vs-tab[data-tab="voices"]')
        expect(page.locator('#voice-rows tr[data-key="ashley-ko"] td').nth(1)).to_have_text(
            "Not supported by the engine"
        )

        page.click('.vs-tab[data-tab="speak"]')
        expect(page.locator("#voice-chips .vs-chip")).to_have_count(3)  # show() has rendered
        page.click('#voice-chips .vs-chip[data-key="ashley-ko"]')
        sent: list[str] = []
        page.on("request", lambda r: sent.append(r.url))
        page.click("#generate")
        expect(page.locator("#speak-error")).to_contain_text(
            "This voice's language isn't supported by the current engine"
        )
        assert not any("/api/speak" in url for url in sent)


# --- M3-T17: take player, waveform progress, voice editor, clone copy, session --------------


def _new_take(page, open_studio) -> None:
    """Non-stream generate: the take row appears and auto-plays once (the reported bug)."""
    open_studio(page)
    page.click("#stream-toggle")  # off → Format applies and the new take auto-plays
    page.fill("#speak-text", "Take player check line.")
    page.click("#generate")
    expect(page.locator("#generate")).to_have_text("Generate", timeout=30000)
    expect(page.locator("#takes-list .vs-take")).to_have_count(1)
    row = page.locator("#takes-list .vs-take")
    # the auto-play must drive the shared player state, then wait the 0.6 s clip out so the
    # next click means "play" and not "pause" (the reported bug)
    expect(row).to_have_class(re.compile("is-playing"), timeout=5000)
    gone = "() => !document.querySelector('#takes-list .is-playing')"
    page.wait_for_function(gone, timeout=15000)


def test_take_play_pause_and_waveform_progress(page, live_app, open_studio):
    _new_take(page, open_studio)
    row = page.locator("#takes-list .vs-take")
    button = row.locator("button.btn-play")
    # the auto-play ended: the row is back to the idle state, progress reset
    expect(button).to_have_attribute("aria-label", "Play take")
    expect(row).not_to_have_class(re.compile("is-playing"))
    assert float(row.locator("svg.vs-wave clipPath rect").get_attribute("width")) == 0
    button.click()
    expect(row).to_have_class(re.compile("is-playing"))
    expect(button).to_have_attribute("aria-label", "Pause take")
    expect(button).to_have_attribute("title", "Pause")
    accent = row.locator("svg.vs-wave path.accent")
    expect(accent).to_have_attribute("clip-path", re.compile(r"url\(#wc-\d+"))
    page.wait_for_timeout(150)  # well inside the 0.6 s clip
    button.click()
    expect(row).not_to_have_class(re.compile("is-playing"))
    expect(button).to_have_attribute("aria-label", "Play take")
    # the accent overlay is clipped to the playhead the clip reached, and stays there
    rect = row.locator("svg.vs-wave clipPath rect")
    width = float(rect.get_attribute("width"))
    assert width > 0
    page.wait_for_timeout(200)
    assert float(rect.get_attribute("width")) == width
    # the newest take is all-accent when idle; paused it must show grey past the playhead
    expect(row).to_have_class(re.compile("is-paused"))
    expect(row.locator("svg.vs-wave path.base")).to_have_css("fill", "rgb(138, 138, 138)")


def test_streamed_take_autoplay_drives_the_row_player(page, live_app, open_studio):
    """Streaming (the default) hands the still-playing stream to the new take's player."""
    open_studio(page)
    page.fill("#speak-text", "Streamed take check line.")
    page.click("#generate")
    row = page.locator("#takes-list .vs-take").first
    button = row.locator("button.btn-play")
    expect(row).to_have_class(re.compile("is-playing"), timeout=15000)
    expect(button).to_have_attribute("aria-label", "Pause take")
    # the row drives the live stream itself (no source switch): its playhead moves past the
    # 0.3 s start buffer, and pause freezes it there
    page.wait_for_function(
        "() => +document.querySelector('#takes-list .vs-take clipPath rect')"
        ".getAttribute('width') > 0"
    )
    button.click()
    expect(row).to_have_class(re.compile("is-paused"))
    rect = row.locator("svg.vs-wave clipPath rect")
    width = float(rect.get_attribute("width"))
    assert width > 0
    page.wait_for_timeout(200)
    assert float(rect.get_attribute("width")) == width


def test_take_hover_highlights_the_row(page, live_app, open_studio):
    _new_take(page, open_studio)
    row = page.locator("#takes-list .vs-take")
    plain = row.evaluate("el => getComputedStyle(el).backgroundColor")
    row.hover()
    expect(row).to_have_css("background-color", "rgb(26, 26, 26)")  # --vs-code-bg
    assert plain != "rgb(26, 26, 26)"


def test_download_link_is_styled_as_a_text_button(page, live_app, open_studio):
    _new_take(page, open_studio)
    link = page.locator("#takes-list .vs-take a.btn-link")
    expect(link).to_have_text("Download")
    assert link.evaluate("el => getComputedStyle(el).textDecorationLine") == "none"
    assert link.evaluate("el => getComputedStyle(el).color") == "rgb(237, 237, 237)"
    assert link.bounding_box()["height"] == 24
    assert page.locator("#takes-list .vs-take button", has_text="Save as voice").count() == 0


def test_candidate_play_pause_and_waveform_progress(page, live_app, open_studio):
    """M3-T18: Design candidates use the same player as Speak takes."""
    open_studio(page, "design")
    page.fill("#design-desc", "calm, professional, early 30s")
    page.fill("#design-sample", "Good morning, here is the sample line.")
    page.select_option("#design-n", "2")
    page.click("#generate-candidates")
    expect(page.locator("#candidates .vs-cand")).to_have_count(2)

    first = page.locator("#candidates .vs-cand").first
    button = first.locator("button.btn-play")
    expect(button).to_have_attribute("aria-label", "Play candidate 1")
    button.click()
    expect(first).to_have_class(re.compile("is-playing"))
    expect(button).to_have_attribute("aria-label", "Pause candidate 1")
    expect(button).to_have_attribute("title", "Pause")
    rect = first.locator("svg.vs-wave clipPath rect")
    expect(first.locator("svg.vs-wave path.accent")).to_have_attribute(
        "clip-path", re.compile(r"url\(#cd-\d+")
    )
    page.wait_for_timeout(150)
    assert float(rect.get_attribute("width")) > 0
    button.click()
    expect(first).not_to_have_class(re.compile("is-playing"))
    expect(button).to_have_attribute("aria-label", "Play candidate 1")
    width = float(rect.get_attribute("width"))
    page.wait_for_timeout(200)
    assert float(rect.get_attribute("width")) == width
    # the other candidate stops the one that was playing: one active player
    page.locator("#candidates .vs-cand").nth(1).locator("button.btn-play").click()
    expect(first).not_to_have_class(re.compile("is-playing"))
    expect(page.locator("#candidates .vs-cand").nth(1)).to_have_class(re.compile("is-playing"))
    # selecting is independent of the player, and only highlights the row: nothing moves
    # and the top rule keeps its colour
    cands = page.locator("#candidates .vs-cand")
    ys = cands.evaluate_all("els => els.map(e => e.getBoundingClientRect().y)")
    border = first.evaluate("el => getComputedStyle(el).borderTop")
    first.locator(".pick").click()
    expect(first).to_have_class(re.compile("is-selected"))
    page.mouse.move(0, 0)
    expect(first).to_have_css("background-color", "rgb(26, 26, 26)")  # --vs-code-bg
    assert cands.evaluate_all("els => els.map(e => e.getBoundingClientRect().y)") == ys
    assert first.evaluate("el => getComputedStyle(el).borderTop") == border


def test_voice_editor_is_an_aligned_table_row(page, live_app, open_studio):
    transcript = "Thanks for waiting. Everything checks out."
    open_studio(page, "voices")
    row = page.locator('#voice-rows tr[data-key="ashley-en"]')
    other = page.locator('#voice-rows tr[data-key="ashley-ja"]')
    xs = "tr => [...tr.children].map(td => Math.round(td.getBoundingClientRect().x))"

    def aligned() -> None:
        """Every row, edited or not, sits on the header's six-column grid."""
        grid = page.locator(".vs-table thead tr").evaluate(xs)
        assert len(grid) == 6
        assert row.evaluate(xs) == grid
        assert other.evaluate(xs) == grid

    aligned()
    columns = page.locator(".vs-table thead tr").evaluate(xs)
    # vertical centre of each cell's first line: a control's box, else its first text run
    centres = """tr => [...tr.children].map(td => {
      const c = td.querySelector('input, select, textarea, button');
      let box;
      if (c) box = c.getBoundingClientRect();
      else {
        const r = document.createRange();
        r.selectNodeContents(document.createTreeWalker(td, NodeFilter.SHOW_TEXT).nextNode());
        box = r.getBoundingClientRect();
      }
      return box.top + (c && c.tagName === 'TEXTAREA' ? 14 : box.height / 2);
    })"""
    before = row.evaluate(centres)
    row.get_by_role("button", name="Edit").click()
    expect(row.locator('textarea[data-field="ref_text"]')).to_be_visible()
    # fixed layout: opening the editor never moves the columns
    assert page.locator(".vs-table thead tr").evaluate(xs) == columns
    # "slide down": every first line stays put, only the transcript cell grows downward
    page.wait_for_timeout(300)  # let the slide-open animation finish
    for was, now in zip(before, row.evaluate(centres), strict=True):
        assert abs(was - now) <= 1, (before, row.evaluate(centres))
    assert row.evaluate("tr => tr.children.length") == 6  # a normal row, never a colspan
    aligned()
    expect(row.locator("td.name input")).to_be_visible()
    expect(row.locator('select[data-field="language"]')).to_be_visible()

    tx = row.locator('textarea[data-field="ref_text"]')
    one_line = tx.evaluate("el => el.clientHeight")
    expect(row.get_by_role("button", name="Auto-transcribe")).to_be_visible()
    tx.fill(" ".join(["Edited transcript"] * 20))
    assert tx.evaluate("el => el.clientHeight") > one_line  # auto-grew, never scrolled
    assert tx.evaluate("el => el.scrollHeight <= el.clientHeight + 1")
    assert tx.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
    aligned()
    row.get_by_role("button", name="Save").click()
    expect(row.locator("textarea")).to_have_count(0)  # collapsed back to a normal row
    expect(row.locator("td.tx")).to_have_text(re.compile("Edited transcript"))
    assert row.evaluate("tr => tr.getBoundingClientRect().height") < 60
    aligned()
    # restore the seeded transcript: live_app is shared by the rest of the suite
    row.get_by_role("button", name="Edit").click()
    row.locator('textarea[data-field="ref_text"]').fill(transcript)
    row.get_by_role("button", name="Save").click()
    expect(row.locator("td.tx")).to_have_text(transcript)
    aligned()


def test_clone_transcribe_copy_is_transcribe_with_stt(page, live_app, open_studio):
    open_studio(page, "clone")
    expect(page.locator("#auto-transcribe")).to_have_text("Transcribe with STT")


def test_takes_are_scoped_to_the_page_session(page, live_app, open_studio):
    """M2-T21 parity: a new page load is a new session, so the Takes list starts empty."""
    _new_take(page, open_studio)
    page.reload()
    page.wait_for_function(
        "document.getElementById('engine-status')?.textContent?.startsWith('Ready')", timeout=10000
    )
    expect(page.locator("#takes-list .vs-take")).to_have_count(0)
    expect(page.locator("#takes-empty")).to_be_visible()
    assert httpx.get(f"{live_app}/api/takes").json()["takes"]  # still on disk, just not listed


def _settle(page, screen: str) -> None:
    if screen == "speak":
        expect(page.locator("#voice-chips .vs-chip")).to_have_count(3)
    elif screen == "voices":
        expect(page.locator("#voice-rows tr[data-key]")).to_have_count(3)
    elif screen == "languages":
        expect(page.locator("#lang-rows .vs-lang-row")).to_have_count(4)
    elif screen == "engine":
        expect(page.locator("#engine-stats .vs-stat")).to_have_count(8)
    else:
        expect(page.locator(f"#screen-{screen}")).to_be_visible()


@pytest.mark.parametrize("screen", SCREENS)
def test_screenshot_1280(page, live_app, open_studio, screen):
    open_studio(page, screen)
    _settle(page, screen)
    RESULTS_DIR.mkdir(exist_ok=True)
    page.screenshot(path=str(RESULTS_DIR / f"ui-{screen}-1280.png"), full_page=True)


@pytest.mark.parametrize("screen", SCREENS)
def test_screenshot_390(page, live_app, open_studio, screen):
    """Mobile parity: every screen at 390×844 (R-047)."""
    RESULTS_DIR.mkdir(exist_ok=True)
    context = page.context.browser.new_context(viewport={"width": 390, "height": 844})
    mobile = context.new_page()
    open_studio(mobile, screen)
    _settle(mobile, screen)
    mobile.screenshot(path=str(RESULTS_DIR / f"ui-{screen}-390.png"), full_page=True)
    context.close()


def test_style_and_design_description_survive_a_reload(page, live_app, open_studio):
    """Speak's Style control and Design's Description persist in this browser."""
    open_studio(page)
    page.fill("#speak-style", "warm, slow, smiling")
    open_studio(page, "design")
    page.fill("#design-desc", "calm, professional")
    page.locator("#design-tags button").first.click()  # a tag chip persists like typing
    tagged = page.locator("#design-desc").input_value()
    page.reload()
    open_studio(page)
    expect(page.locator("#speak-style")).to_have_value("warm, slow, smiling")
    open_studio(page, "design")
    expect(page.locator("#design-desc")).to_have_value(tagged)


def test_speed_slider_zones_and_take_tempo(page, live_app, open_studio):
    """Speed 0.85–1.15×: 0.90–1.10 natural (green), the ends yellow; off while streaming."""
    open_studio(page)
    speed, zone = page.locator("#speed"), page.locator("#speed-zone")
    expect(speed).to_be_disabled()  # streaming is the default: speed is not applied
    expect(zone).to_have_text("Not applied while streaming")
    page.click("#stream-toggle")
    expect(speed).to_be_enabled()
    expect(page.locator("#speed-out")).to_have_text("1.00×")
    expect(zone).to_have_text("Natural")
    expect(zone).to_have_css("color", "rgb(52, 199, 89)")  # --vs-zone-ok
    speed.fill("1.1")
    expect(zone).to_have_text("Natural")
    speed.fill("0.85")
    expect(page.locator("#speed-out")).to_have_text("0.85×")
    expect(zone).to_have_text("May sound processed")
    expect(zone).to_have_css("color", "rgb(255, 204, 0)")  # --vs-zone-warn
    sent = []
    page.on("request", lambda r: sent.append(r.post_data_json) if "/api/speak" in r.url else None)
    page.fill("#speak-text", "Speed check line.")
    page.click("#generate")
    expect(page.locator("#takes-list .vs-take").first).to_contain_text("0.85×", timeout=30000)
    assert sent and sent[-1]["speed"] == 0.85


def test_voices_grouped_by_language_and_sortable(page, live_app, open_studio):
    open_studio(page, "voices")
    groups = page.locator("#voice-rows tr.vs-group")
    expect(groups).to_have_count(3)  # default: grouped by language, one header each
    expect(groups.first).to_contain_text("English")
    expect(page.locator("th:has(.vs-sort[data-sort='language'])")).to_have_attribute(
        "aria-sort", "ascending"
    )
    keys = "els => els.map(e => e.dataset.key)"
    rows = page.locator("#voice-rows tr[data-key]")
    assert rows.evaluate_all(keys) == ["ashley-en", "ashley-ja", "ashley-ko"]
    name = page.locator(".vs-sort[data-sort='name']")
    name.click()
    expect(groups).to_have_count(0)  # another column: a flat sorted list
    name.click()  # same column again flips the direction
    expect(page.locator("th:has(.vs-sort[data-sort='name'])")).to_have_attribute(
        "aria-sort", "descending"
    )
    assert rows.evaluate_all(keys) == ["ashley-ko", "ashley-ja", "ashley-en"]
    page.locator(".vs-sort[data-sort='language']").click()
    expect(groups).to_have_count(3)


def test_delete_confirm_slides_down_without_moving_the_row(page, live_app, open_studio):
    open_studio(page, "voices")
    row = page.locator('#voice-rows tr[data-key="ashley-ja"]')
    centres = """tr => [...tr.children].slice(0, 5).map(td => {
      const r = document.createRange();
      r.selectNodeContents(document.createTreeWalker(td, NodeFilter.SHOW_TEXT).nextNode());
      const b = r.getBoundingClientRect();
      return b.top + b.height / 2;
    })"""
    before = row.evaluate(centres)
    row.get_by_role("button", name="Delete").click()
    expect(row.locator(".vs-confirm-box")).to_contain_text("This can't be undone from the UI.")
    page.wait_for_timeout(300)  # slide-open animation
    for was, now in zip(before, row.evaluate(centres), strict=True):
        assert abs(was - now) <= 1
    row.get_by_role("button", name="Cancel").click()
    expect(row.get_by_role("button", name="Audition")).to_be_visible()
