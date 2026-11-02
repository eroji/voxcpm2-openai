"""Static checks for the s6 service definitions and Containerfile (M1-T3)."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S6 = ROOT / "deploy" / "s6"
CONTAINERFILE = ROOT / "deploy" / "Containerfile"

RUNTIME_DEPS = [
    "fastapi",
    "uvicorn[standard]",
    "httpx",
    "numpy",
    "soundfile",
    "safetensors",
    "pydantic",
    "pydantic-settings",
    "python-multipart",
]


def test_engine_is_longrun_with_documented_command():
    run = (S6 / "engine" / "run").read_text()
    assert (S6 / "engine" / "type").read_text().strip() == "longrun"
    for needle in (
        "vllm serve",
        "--omni",
        "--deploy-config",
        "--served-model-name openbmb/VoxCPM2",
        "--host 127.0.0.1",
        '"$ENGINE_PORT"',
        '"$MODEL_PATH"',
        '"$DEPLOY_CONFIG"',
    ):
        assert needle in run, needle
    assert "0.0.0.0" not in run  # engine stays loopback-only (R-001)
    assert os.access(S6 / "engine" / "run", os.X_OK)


def test_app_is_longrun_on_public_bind():
    run = (S6 / "app" / "run").read_text()
    assert (S6 / "app" / "type").read_text().strip() == "longrun"
    assert "uvicorn app.main:app" in run
    assert "--host 0.0.0.0" in run
    assert "/opt/app" in run  # cwd so `app.main` is importable
    assert os.access(S6 / "app" / "run", os.X_OK)


def test_app_depends_on_engine():
    assert (S6 / "app" / "dependencies.d" / "engine").exists()


def test_user_bundle_lists_both_services():
    for svc in ("engine", "app"):
        marker = S6 / "user" / "contents.d" / svc
        assert marker.exists(), svc
        assert marker.read_text() == ""


def test_containerfile_wires_app_config_and_s6():
    cf = CONTAINERFILE.read_text()
    assert "COPY app/ /opt/app/app" in cf
    assert "COPY deploy/voxcpm2-3090.yaml" in cf
    assert "COPY deploy/s6/" in cf
    assert "/etc/s6-overlay/s6-rc.d" in cf
    assert "chmod +x" in cf
    assert "HF_HOME=" in cf


def test_voxcpm_stays_on_uv():
    cf = CONTAINERFILE.read_text()
    assert "uv pip install" in cf and "voxcpm==2.0.3" in cf
    assert "pip install voxcpm" not in cf  # plain pip would downgrade NCCL


def test_runtime_python_deps_installed():
    cf = CONTAINERFILE.read_text().replace("\\\n", " ")
    lines = [ln.strip() for ln in cf.splitlines() if ln.strip().startswith("RUN uv pip install")]
    assert any("fastapi" in ln for ln in lines), lines
    for pkg in RUNTIME_DEPS:
        assert any(pkg in ln for ln in lines), pkg


def test_reference_deploy_config_untouched():
    assert (ROOT / "deploy" / "voxcpm2-3090.yaml").exists()
    assert (ROOT / "deploy" / "compose.yaml").exists()
