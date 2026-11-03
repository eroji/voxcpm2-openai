"""M2-T19: /api/languages serves the 30 VoxCPM2 model-card languages."""

from __future__ import annotations

from app.config import Settings
from app.languages import LANGUAGE_NAMES, LANGUAGES, language_name
from app.main import create_app
from fastapi.testclient import TestClient

EXPECTED_CODES = [
    "ar",
    "my",
    "zh",
    "da",
    "nl",
    "en",
    "fi",
    "fr",
    "de",
    "el",
    "he",
    "hi",
    "id",
    "it",
    "ja",
    "km",
    "ko",
    "lo",
    "ms",
    "no",
    "pl",
    "pt",
    "ru",
    "es",
    "sw",
    "sv",
    "tl",
    "th",
    "tr",
    "vi",
]


def test_languages_table_matches_model_card() -> None:
    assert len(LANGUAGES) == 30
    codes = [lang["code"] for lang in LANGUAGES]
    assert codes == EXPECTED_CODES
    assert len(set(codes)) == 30
    assert all(lang["name"].strip() for lang in LANGUAGES)
    assert LANGUAGE_NAMES["zh"] == "Chinese"
    assert LANGUAGE_NAMES["tl"] == "Tagalog"


def test_language_name_falls_back_to_code() -> None:
    assert language_name("ja") == "Japanese"
    assert language_name("xx") == "xx"
    assert language_name("") == ""


def test_route_returns_30_languages(client: TestClient) -> None:
    r = client.get("/api/languages")
    assert r.status_code == 200
    body = r.json()
    assert list(body) == ["languages"]
    assert [lang["code"] for lang in body["languages"]] == EXPECTED_CODES
    assert all(list(lang) == ["code", "name"] for lang in body["languages"])


def test_route_requires_studio_auth(settings: Settings) -> None:
    s = Settings(
        data_dir=settings.data_dir,
        engine_url=settings.engine_url,
        static_dir=settings.static_dir,
        asset_dir=settings.asset_dir,
        startup_page=False,
        studio_auth="u:p",
    )
    with TestClient(create_app(s)) as c:
        assert c.get("/api/languages").status_code == 401
        assert c.get("/api/languages").json()["error"]["type"] == "authentication_error"
        r = c.get("/api/languages", auth=("u", "p"))
        assert r.status_code == 200
        assert len(r.json()["languages"]) == 30
