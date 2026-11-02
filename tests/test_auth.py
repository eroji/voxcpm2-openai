"""M2-T11: auth proven at the HTTP level — STUDIO_AUTH basic auth, API_KEYS on /v1."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def _settings(settings: Settings, **fields: Any) -> Settings:
    return Settings(
        data_dir=settings.data_dir,
        import_dir=settings.import_dir,
        engine_url=settings.engine_url,
        static_dir=settings.static_dir,
        asset_dir=settings.asset_dir,
        **fields,
    )


def _client(settings: Settings, **fields: Any) -> TestClient:
    return TestClient(create_app(_settings(settings, **fields)))


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


def test_malformed_auth_config_fails_startup(settings: Settings) -> None:
    # Fail closed: a typo must never boot the app with auth silently disabled.
    for bad in ({"studio_auth": "admin"}, {"studio_auth": ":pw"}, {"api_keys": "   "}):
        with pytest.raises(RuntimeError):
            create_app(_settings(settings, **bad))


def test_non_ascii_basic_credentials_never_500(settings: Settings, library: Any) -> None:
    with _client(settings, studio_auth="üser:p") as c:
        assert c.get("/api/voices", auth=("üser", "p")).status_code == 200
        assert c.get("/api/voices", auth=("user", "p")).status_code == 401

    header = "Basic " + base64.b64encode("üser:p".encode()).decode()
    with _client(settings, studio_auth="u:p") as c:
        assert c.get("/api/voices", headers={"Authorization": header}).status_code == 401


def test_unhandled_exception_uses_openai_500_shape(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/boom")
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["message"] == "Internal server error"
    assert err["type"] == "internal_error"
    assert err["code"] == "internal_error"


def test_405_keeps_allow_header(client: TestClient) -> None:
    r = client.get("/v1/audio/speech")
    assert r.status_code == 405
    assert "POST" in r.headers.get("allow", "")
    assert r.json()["error"]["code"] == "method_not_allowed"


def test_docs_and_openapi_are_closed(client: TestClient) -> None:
    # R-003: no interactive docs endpoint, no CDN-backed Redoc.
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
