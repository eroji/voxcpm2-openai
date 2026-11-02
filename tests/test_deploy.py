"""Static checks for the s6 service definitions and Containerfile (M1-T3, M1-T6)."""

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S6 = ROOT / "deploy" / "s6"
CONTAINERFILE = ROOT / "deploy" / "Containerfile"

# M1-T6: only app deps the base may lack. fastapi/uvicorn/httpx/numpy/pydantic must NOT be
# reinstalled — the base pins them for the vLLM engine stack (unbounded upgrade, wave-1 review).
APP_ONLY_DEPS = ["soundfile", "safetensors", "python-multipart", "pydantic-settings"]
ENGINE_STACK = ["fastapi", "uvicorn", "httpx", "numpy", "pydantic"]
IMPORT_CHECK = (
    "import fastapi, uvicorn, httpx, numpy, soundfile, safetensors, pydantic, "
    "pydantic_settings, multipart"
)


def _folded() -> str:
    return CONTAINERFILE.read_text().replace("\\\n", " ")


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


def test_runtime_installs_limited_to_allowed_packages():
    installs = [
        ln.strip()
        for ln in _folded().splitlines()
        if ln.strip().startswith("RUN uv pip install") and "voxcpm" not in ln
    ]
    assert installs, "no app-dep install layer"
    for ln in installs:
        assert "--system" in ln and "--break-system-packages" in ln and "--no-cache" in ln, ln
        pkgs = ln.split("--no-cache", 1)[1].split()
        names = [re.split(r"[<>=\[]", p)[0] for p in pkgs]
        assert names == APP_ONLY_DEPS, ln
        for pkg in ENGINE_STACK:  # no unbounded upgrade of the engine stack
            assert not re.search(rf"(?<![\w-]){re.escape(pkg)}(?![\w-])", ln), (pkg, ln)


def test_runtime_import_check_present():
    assert IMPORT_CHECK in _folded()


def test_ffmpeg_and_curl_available_at_build():
    folded = _folded()
    assert "ffmpeg -version" in folded and "curl --version" in folded
    # distro install only if the base lacks ffmpeg, in the same layer
    assert re.search(r"command -v ffmpeg.*apt-get install -y.*ffmpeg", folded), folded


def test_stale_services_removed_before_copy():
    folded = _folded()
    rms = [ln for ln in folded.splitlines() if ln.strip().startswith("RUN rm -rf /etc/s6-overlay")]
    assert len(rms) == 1, rms
    for needle in (
        "/etc/s6-overlay/s6-rc.d/engine",
        "/etc/s6-overlay/s6-rc.d/app",
        "/etc/s6-overlay/s6-rc.d/qwen3*",
        "/etc/s6-overlay/s6-rc.d/user/contents.d/engine",
        "/etc/s6-overlay/s6-rc.d/user/contents.d/app",
        "/etc/s6-overlay/s6-rc.d/user/contents.d/qwen3*",
    ):
        assert needle in rms[0], needle
    assert folded.index("rm -rf /etc/s6-overlay") < folded.index("COPY deploy/s6/")


def test_s6_guard_rejects_foreign_engine_services():
    folded = _folded()
    guards = [
        ln for ln in folded.splitlines() if "grep" in ln and "qwen3" in ln and "vllm serve" in ln
    ]
    assert guards, "no s6-rc.d grep guard"
    assert "--include=run" in guards[0], guards[0]
    assert folded.index("COPY deploy/s6/") < folded.index(guards[0])


def test_inherited_cmd_cleared():
    assert re.search(r"^CMD \[\]$", CONTAINERFILE.read_text(), re.M)


def test_reference_deploy_config_untouched():
    assert (ROOT / "deploy" / "voxcpm2-3090.yaml").exists()
    assert (ROOT / "deploy" / "compose.yaml").exists()
