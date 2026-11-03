"""Application settings (environment-driven)."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Env vars map to fields by name: DATA_DIR, ENGINE_URL, API_KEYS, ..."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, env_file=None)

    app_name: str = "voxcpm2-openai"
    version: str = "0.3.10"

    data_dir: Path = Path("/data")
    static_dir: Path = APP_DIR / "static"
    asset_dir: Path = APP_DIR / "assets"

    # SPEC §5a: the engine's supported-language set. Swapping models means swapping this file.
    supported_languages_file: Path = APP_DIR.parent / "deploy" / "supported-languages.json"

    model_path: str = "/models/openbmb/VoxCPM2"
    engine_url: str = "http://127.0.0.1:8102"
    engine_model: str = "openbmb/VoxCPM2"
    engine_port: int = 8102
    deploy_config: str = "/opt/app/deploy/voxcpm2-3090.yaml"
    engine_concurrency: int = 2
    kv_cache_label: str = "2 GiB"
    max_queue: int = 16
    metrics_window: int = 20
    warmup_timeout_s: float = 300.0
    s6_service: str = "engine"

    sample_rate: int = 48000
    max_input_chars: int = 4000
    max_upload_bytes: int = 20_000_000
    min_ref_seconds: float = 3.0
    max_ref_seconds: float = 30.0

    take_retention: int = 50
    backup_retention: int = 20

    stt_url: str = ""
    studio_auth: str = ""
    api_keys: str = ""
    allow_remote_ref_audio: bool = False

    log_level: str = "INFO"
    startup_page: bool = True
    startup_fail_after_s: float = 600.0
    request_timeout_s: float = Field(default=300.0)

    @cached_property
    def voices_dir(self) -> Path:
        return self.data_dir / "voices"

    @cached_property
    def takes_dir(self) -> Path:
        return self.data_dir / "takes"

    @cached_property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @cached_property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.json"

    @cached_property
    def engine_base(self) -> str:
        base = self.engine_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        return base

    @cached_property
    def api_key_set(self) -> frozenset[str]:
        return frozenset(k.strip() for k in self.api_keys.split(",") if k.strip())

    @cached_property
    def studio_credentials(self) -> tuple[str, str] | None:
        if ":" not in self.studio_auth:
            return None
        user, password = self.studio_auth.split(":", 1)
        if not user:
            return None
        return user, password

    def validate(self) -> None:  # type: ignore[override]  # shadows deprecated pydantic classmethod
        """Fail closed on auth misconfiguration (SPEC §10): never boot with auth silently off."""
        if self.studio_auth:
            creds = self.studio_credentials
            if not creds or not all(creds):
                raise RuntimeError("STUDIO_AUTH must be 'user:password', both parts non-empty")
        if self.api_keys and not self.api_key_set:
            raise RuntimeError("API_KEYS is set but contains no non-blank tokens")

    def ensure_dirs(self) -> None:
        for path in (self.voices_dir, self.takes_dir, self.backups_dir):
            path.mkdir(parents=True, exist_ok=True)
