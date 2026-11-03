"""Static checks for the s6 service definitions and Containerfile (M1-T3, M1-T6, M1-T8)."""

import hashlib
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S6 = ROOT / "deploy" / "s6"
CONTAINERFILE = ROOT / "deploy" / "Containerfile"

# M1-T6/T8: only app deps the base may lack, installed with --no-deps so pip/uv cannot pull a
# newer transitive pydantic/numpy. fastapi/uvicorn/httpx/numpy/pydantic must NOT appear at all —
# the base pins them for the vLLM engine stack (wave-1 + wave-2 review). python-dotenv is the
# one runtime dep of pydantic-settings we must list explicitly; soundfile bundles libsndfile and
# numpy/pydantic already come from the proven base. The import-check layer is the backstop.
APP_ONLY_DEPS = [
    "soundfile",
    "safetensors",
    "python-multipart",
    "pydantic-settings",
    "python-dotenv",
]
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
        # --no-deps: a transitive pydantic/numpy upgrade would defeat the engine-stack pin
        assert "--no-deps" in ln, ln
        pkgs = ln.split("--no-deps", 1)[1].split()
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
    # a base service running `uvicorn ...:8000` would double-bind the app port (wave-2 review)
    assert "uvicorn" in guards[0], guards[0]
    assert "--include=run" in guards[0], guards[0]
    # our own two services are the only allowed matches (app/run legitimately runs uvicorn)
    assert "engine|app" in guards[0], guards[0]
    assert "exit 1" in guards[0], guards[0]
    assert folded.index("COPY deploy/s6/") < folded.index(guards[0])


def test_inherited_cmd_cleared():
    assert re.search(r"^CMD \[\]$", CONTAINERFILE.read_text(), re.M)


# Read-only reference files (AGENTS.md §4): pinned by SHA-256 so any edit fails the test,
# not just a deletion. Hashes are of the committed originals (git status: clean).
REFERENCE_SHA256 = {
    "deploy/voxcpm2-3090.yaml": (
        "3ddc3cabf84d86154b66365e18938d3f1f4d8d732d613396b2b97a387734f194"
    ),
    "deploy/compose.yaml": "6cf09350d832dfb80168bdb8d2d7acd4c69a54c068685004cebb2b60489a5750",
}


def test_reference_deploy_files_byte_identical():
    for rel, want in REFERENCE_SHA256.items():
        got = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        assert got == want, f"{rel} was edited (sha256 {got} != pinned {want})"


# ------------------------------------------------- M5-T4: CI/CD house pattern (notely parity)
CI_FILE = ROOT / ".gitlab-ci.yml"
OVERRIDE = ROOT / "deploy" / "compose.deploy.yaml"
RUNBOOK = ROOT / "docs" / "RUNBOOK.md"
STT_URL = "http://vllm.dotslash.io:8890/v1"  # actual value, human 2026-11-02


def test_image_override_file_pins_deploy_image():
    text = OVERRIDE.read_text()
    assert "${DEPLOY_IMAGE" in text, "override must take the image from CI"
    assert "voxcpm2-openai" in text


def test_ci_mirrors_notely_conventions():
    ci = CI_FILE.read_text()
    assert "HARBOR_TOKEN" in ci
    assert "HARBOR_PASSWORD" not in ci, "house pattern is HARBOR_TOKEN (notely)"
    assert "GIT_DEPTH: 0" in ci
    assert 'DOCKER_TLS_CERTDIR: ""' in ci, "dind needs plain tcp://docker:2375"
    assert "docker:27-dind" in ci
    assert "compose.deploy.yaml" in ci, "CD must pull/up through the image override"
    assert "--password-stdin" in ci


def test_ci_builds_only_on_release_tags():
    ci = CI_FILE.read_text()
    assert "workflow:" in ci
    assert r"/^v\d+\.\d+\.\d+$/" in ci, "only vX.Y.Z tags create pipelines"
    assert "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH" not in ci, "branch pushes must not build"
    assert "CI_COMMIT_SHORT_SHA" not in ci, "no commit-tagged images"
    assert "${CI_COMMIT_TAG#v}" in ci, "tagged builds are published as X.Y.Z, not vX.Y.Z"


def test_production_deploy_is_tag_only_and_manual():
    prod = CI_FILE.read_text().split("deploy:production:")[-1]
    assert '$DEPLOY_TARGET == "production"' in prod
    assert r"$CI_COMMIT_TAG =~ /^v\d+\.\d+\.\d+$/" in prod
    assert "when: manual" in prod
    assert "HEALTH_URL: http://127.0.0.1:8880/ready" in prod, "production readiness target"
    assert "curl -fsS $HEALTH_URL" in prod, "health gate after up -d"


def test_deploy_uses_base64_ssh_key_pattern():
    ci = CI_FILE.read_text()
    assert "base64 -d" in ci, "DEPLOY_SSH_KEY is base64 -w0 of the key file (notely)"
    assert "BatchMode=yes" in ci
    assert "StrictHostKeyChecking=accept-new" in ci
    assert "DEPLOY_HOST: vllm.dotslash.io" in ci
    assert "DEPLOY_USER: deploy" in ci
    assert "DEPLOY_PATH: /opt/voxcpm2-openai" in ci


def test_runbook_documents_cd():
    rb = RUNBOOK.read_text()
    assert STT_URL in rb, "runbook must carry the exact STT URL"
    assert "HARBOR_TOKEN" in rb and "HARBOR_PASSWORD" not in rb
    assert "DEPLOY_SSH_KEY" in rb and "base64" in rb
    assert "--follow-tags" in rb, "tag-release procedure: git tag vX.Y.Z && git push --follow-tags"
