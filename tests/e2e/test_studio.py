"""Browser e2e for Voice Studio (M3-T6): shell, routing, all five screens, screenshots.

Every test uses a fresh page (fresh JS state); anything persisted server-side is either
read-only or cleaned up by the test that wrote it, so the suite is order-independent.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile as sf
from playwright.sync_api import expect  # missing Playwright = collection error, never a skip

RESULTS_DIR = Path(__file__).resolve().parents[2] / "test-results"
SCREENS = ["speak", "voices", "clone", "design", "engine"]


def test_shell_and_engine_status(page, live_app, open_studio):
    open_studio(page)
    assert page.title() == "Voice Studio"
    expect(page.locator(".vs-tab")).to_have_count(5)
    expect(page.locator('.vs-tab[data-tab="speak"]')).to_have_attribute("aria-current", "page")
    expect(page.locator("#engine-status")).to_have_text(re.compile("^Ready"), timeout=10000)


@pytest.mark.parametrize("screen", ["voices", "clone", "design", "engine", "speak"])
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
    expect(newest.locator("svg.vs-wave path")).to_have_attribute("d", re.compile("M0.+Z"))


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
    expect(page.locator("#voice-rows tr")).to_have_attribute("data-key", "ashley-ja")
    page.click('#lang-filters [data-lang="all"]')

    page.fill("#voice-search", "ashley-ko")
    expect(rows).to_have_count(1)
    expect(page.locator("#voice-rows tr")).to_have_attribute("data-key", "ashley-ko")
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
    expect(en_row.locator('input[data-field="ref_text"]')).to_be_visible()
    en_row.get_by_role("button", name="Cancel").click()
    expect(en_row.locator("td.name")).to_have_text("ashley-en")

    ja_row = page.locator('#voice-rows tr[data-key="ashley-ja"]')
    ja_row.get_by_role("button", name="Delete").click()
    expect(ja_row.locator("td.act")).to_contain_text(
        re.compile(r"Delete ashley-ja\? This can.t be undone from the UI\.")
    )
    ja_row.get_by_role("button", name="Cancel").click()
    expect(ja_row.get_by_role("button", name="Delete")).to_be_visible()


def test_clone_gating(page, live_app, open_studio, tmp_path):
    open_studio(page, "clone")
    save = page.locator("#save-voice")
    expect(save).to_be_disabled()

    wav = tmp_path / "reference.wav"
    t = np.linspace(0, 5.0, 48000 * 5, endpoint=False)
    sf.write(wav, (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), 48000, format="WAV")
    page.set_input_files("#ref-file", str(wav))
    expect(page.locator("#drop-zone .meta")).to_contain_text("reference.wav")
    expect(save).to_be_disabled()  # file alone is not enough: no name yet

    page.fill("#clone-name", "Bad Name")
    expect(page.locator("#clone-error")).to_contain_text("lowercase")
    expect(save).to_be_disabled()

    page.fill("#clone-name", "e2e-clone")
    expect(page.locator("#clone-error")).to_be_hidden()
    expect(save).to_be_disabled()  # consent still missing
    page.check("#consent")
    expect(save).to_be_enabled()


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
    expect(page.locator("#engine-stats .vs-stat")).to_have_count(8)

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


def _settle(page, screen: str) -> None:
    if screen == "speak":
        expect(page.locator("#voice-chips .vs-chip")).to_have_count(3)
    elif screen == "voices":
        expect(page.locator("#voice-rows tr[data-key]")).to_have_count(3)
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
