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
