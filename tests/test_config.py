"""M1-T13: auth configuration fails closed at startup (R-041, SPEC §10)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.main import create_app


def _settings(tmp_path: Path, **fields: object) -> Settings:
    # Pin auth fields empty so ambient env cannot leak into the test.
    values: dict[str, object] = {"api_keys": "", "studio_auth": "", **fields}
    return Settings(data_dir=tmp_path / "data", **values)


@pytest.mark.parametrize("bad", ["admin", ":pass", "user:", ":", "  "])
def test_malformed_studio_auth_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(RuntimeError):
        _settings(tmp_path, studio_auth=bad).validate()


@pytest.mark.parametrize("bad", ["   ", ",", " , , "])
def test_blank_api_keys_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(RuntimeError):
        _settings(tmp_path, api_keys=bad).validate()


@pytest.mark.parametrize(
    "ok",
    [
        {},
        {"studio_auth": "u:p"},
        {"api_keys": "k1, ,k2"},
        {"studio_auth": "u:p w", "api_keys": "k1"},
    ],
)
def test_valid_values_pass_validation(tmp_path: Path, ok: dict[str, str]) -> None:
    _settings(tmp_path, **ok).validate()  # must not raise


def test_create_app_fails_fast_on_bad_auth(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        create_app(_settings(tmp_path, studio_auth="admin"))
    with pytest.raises(RuntimeError):
        create_app(_settings(tmp_path, api_keys="   "))


@pytest.mark.parametrize(
    ("raw", "expected"), [("off", None), ("None", None), ("", -20.0), ("-23", -23.0)]
)
def test_loudness_target_parses_off_and_empty(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: float | None
) -> None:
    monkeypatch.setenv("LOUDNESS_TARGET_DB", raw)
    assert Settings().loudness_target_db == expected


def test_kv_cache_label_prefers_env_then_deploy_yaml(tmp_path):
    cfg = tmp_path / "deploy.yaml"
    cfg.write_text("stages:\n  - stage_id: 0\n    kv_cache_memory_bytes: 4294967296\n", "utf-8")
    derived = Settings(data_dir=tmp_path / "data", deploy_config=cfg, kv_cache_label="")
    assert derived.effective_kv_cache_label() == "4 GiB"
    explicit = Settings(data_dir=tmp_path / "data", deploy_config=cfg, kv_cache_label="6 GiB")
    assert explicit.effective_kv_cache_label() == "6 GiB"
    missing = Settings(
        data_dir=tmp_path / "data", deploy_config=tmp_path / "missing.yaml", kv_cache_label=""
    )
    assert missing.effective_kv_cache_label() is None
