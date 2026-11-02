"""M2-T11: auth proven at the HTTP level — STUDIO_AUTH basic auth, API_KEYS on /v1."""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def _client(settings: Settings, **fields: Any) -> TestClient:
    secured = Settings(
        data_dir=settings.data_dir,
        import_dir=settings.import_dir,
        engine_url=settings.engine_url,
        static_dir=settings.static_dir,
        asset_dir=settings.asset_dir,
        **fields,
    )
    return TestClient(create_app(secured))


def test_studio_auth_gates_ui_api_static(settings: Settings, library: Any) -> None:
    with _client(settings, studio_auth="u:p") as c:
        for path in ("/", "/api/voices", "/static/js/core.js"):
            assert c.get(path).status_code == 401, path
            assert c.get(path).json()["error"]["type"] == "authentication_error", path
            assert c.get(path, auth=("u", "p")).status_code == 200, path
        assert c.get("/api/voices", auth=("u", "wrong")).status_code == 401
        # System endpoints stay open (SPEC §10); /ready may still be warming up.
        assert c.get("/health").status_code == 200
        assert c.get("/ready").status_code != 401


def test_api_keys_gate_covers_v1_voices(settings: Settings, library: Any) -> None:
    with _client(settings, api_keys="k1") as c:
        assert c.get("/v1/audio/voices").status_code == 401
        assert c.get("/v1/audio/voices", headers={"Authorization": "Bearer k1"}).status_code == 200
