"""Static checks for the s6 service definitions and Containerfile (M1-T3, M1-T6, M1-T8)."""

import hashlib
import json
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
    assert run.startswith("#!/command/with-contenv sh")
    # the command lives in app/engine_runner.py so its output can be tee'd to the engine log
    assert "exec" in run and "app.engine_runner" in run, run
    assert "vllm" not in run, run
    assert os.access(S6 / "engine" / "run", os.X_OK)


def test_engine_runner_builds_the_documented_command():
    src = (ROOT / "app" / "engine_runner.py").read_text()
    for needle in (
        "vllm serve",
        "--omni",
        "--deploy-config",
        "--served-model-name",
        "127.0.0.1",
        "$ENGINE_PORT",
    ):
        assert needle in src, needle
    assert "0.0.0.0" not in src  # engine stays loopback-only (R-001)


def test_engine_finish_backs_off_then_stays_down():
    finish = S6 / "engine" / "finish"
    assert finish.exists(), "engine service has no finish step: s6 restarts immediately"
    assert os.access(finish, os.X_OK)
    text = finish.read_text()
    assert text.startswith("#!/command/with-contenv sh")
    # ui/STARTING.md:38 — back off 30 s, 60 s, 120 s, then stay down until a manual restart
    for needle in ("sleep 30", "sleep 60", "sleep 120", "/run/service/engine/down"):
        assert needle in text, needle
    assert "/run/engine-failures" in text and "/run/engine-last-exit" in text
    assert re.search(r"0 \| 143", text), "planned stops must not count as failures"


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


# SPEC §5a: the supported set ships with the image; the app reads it at start.
SUPPORTED_FILE = ROOT / "deploy" / "supported-languages.json"


def test_containerfile_copies_supported_languages_with_a_build_check():
    folded = _folded()
    assert "COPY deploy/supported-languages.json /opt/app/deploy/supported-languages.json" in folded
    # the file must parse and hold the whole VoxCPM2 set, or the build fails
    check = [
        ln for ln in folded.splitlines() if "supported-languages.json" in ln and "json.load" in ln
    ]
    assert check, "no build-time parse check for the supported-languages file"
    assert "assert len(d['languages']) == 30" in check[0], check[0]
    assert folded.index("COPY deploy/supported-languages.json") < folded.index(check[0])


def test_supported_languages_file_has_30_unique_entries():
    data = json.loads(SUPPORTED_FILE.read_text("utf-8"))
    entries = data["languages"]
    assert len(entries) == 30
    codes = [item["code"] for item in entries]
    assert len(set(codes)) == 30, "duplicate language codes"
    for item in entries:
        assert set(item) == {"code", "name", "native"}
        assert all(item.values()), item
    assert {"en", "ja", "ko", "zh"} <= set(codes), "SPEC §5a seed set must be supported"


def test_voxcpm_stays_on_uv():
    cf = CONTAINERFILE.read_text()
    assert "uv pip install" in cf and "voxcpm==2.0.3" in cf
    assert "pip install voxcpm" not in cf  # plain pip would downgrade NCCL


def test_compose_keeps_hf_offline_by_default_but_allows_token():
    compose = (ROOT / "deploy" / "compose.yaml").read_text()
    assert 'HF_HUB_OFFLINE: "${HF_HUB_OFFLINE:-1}"' in compose
    assert 'HF_TOKEN: "${HF_TOKEN:-}"' in compose


def test_inherited_qwen3_supervisor_is_neutralized():
    text = (ROOT / "deploy" / "Containerfile").read_text()
    assert "S6_STAGE2_HOOK=/bin/true" in text, (
        "base stage-2 hook generates Qwen3 engines at runtime"
    )
    assert 'ENTRYPOINT ["/init"]' in text, "only s6-overlay should be the main program"
    assert "/opt/app/supervisor/start.sh" not in text, "must not invoke the Qwen3 supervisor"
    assert "RUN rm -rf /opt/app" in text, "old Qwen3 app/supervisor must be removed"


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
# not just a deletion. voxcpm2-3090.yaml is the committed original; compose.yaml is pinned to
# the v0.2.0 state (M2-T18 removed the /import/qwen3-speakers mount).
REFERENCE_SHA256 = {
    "deploy/voxcpm2-3090.yaml": (
        "a97e7dc7984bf2ef697d96a5fd37d46afa3f394a655db0e560822ff99a2640a9"
    ),
    # re-pinned 2026-11-09: multi-user engine profile (4 seqs) + app concurrency env
    "deploy/compose.yaml": "6639b6782cf2e29b017518ead9a1c0d95f9f57469d3af79bbdb0ef6752a23ff0",
}


def test_reference_deploy_files_byte_identical():
    for rel, want in REFERENCE_SHA256.items():
        got = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        assert got == want, f"{rel} was edited (sha256 {got} != pinned {want})"


def test_deploy_config_override_is_optional():
    override = (ROOT / "deploy" / "compose.config.yaml").read_text()
    assert "${VOXCPM2_DEPLOY_CONFIG" in override
    assert "/opt/app/deploy/voxcpm2-3090.yaml:ro" in override
    ci = CI_FILE.read_text()
    assert "compose.config.yaml" in ci
    assert "VOXCPM2_DEPLOY_CONFIG" in ci
    assert 'COMPOSE_FILES="-f compose.yaml"' in ci


def test_compose_has_no_qwen3_import_mount():
    """SPEC v0.2.0 §5/§7: no Qwen3 import, so the read-only speakers mount must be gone."""
    compose = (ROOT / "deploy" / "compose.yaml").read_text()
    assert "qwen3-speakers" not in compose and "/opt/qwen3-tts" not in compose


# ------------------------------------------------- M5-T4: CI/CD house pattern (notely parity)
CI_FILE = ROOT / ".gitlab-ci.yml"
RUNBOOK = ROOT / "docs" / "RUNBOOK.md"
STT_URL = "http://vllm.dotslash.io:8890/v1"  # actual value, human 2026-11-02


def test_compose_image_comes_from_env():
    compose = (ROOT / "deploy" / "compose.yaml").read_text()
    assert "${VOXCPM2_IMAGE" in compose, "image tag lives in .env / environment"
    assert not (ROOT / "deploy" / "compose.deploy.yaml").exists()


def test_ci_fails_loudly_without_harbor_credentials():
    ci = CI_FILE.read_text()
    assert "HARBOR_USER and HARBOR_TOKEN must be set" in ci
    assert "unprotected" in ci, "explain protected-variable pitfall for tag pipelines"


def test_ci_mirrors_notely_conventions():
    ci = CI_FILE.read_text()
    assert "HARBOR_TOKEN" in ci
    assert "HARBOR_PASSWORD" not in ci, "house pattern is HARBOR_TOKEN (notely)"
    assert "GIT_DEPTH: 0" in ci
    assert 'DOCKER_TLS_CERTDIR: ""' in ci, "dind needs plain tcp://docker:2375"
    assert "docker:27-dind" in ci
    assert "--password-stdin" in ci


def test_ci_builds_only_on_release_tags():
    ci = CI_FILE.read_text()
    assert "workflow:" in ci
    assert r"/^v\d+\.\d+\.\d+$/" in ci, "only vX.Y.Z tags create pipelines"
    assert "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH" not in ci, "branch pushes must not build"
    assert "CI_COMMIT_SHORT_SHA" not in ci, "no commit-tagged images"
    assert 'TAG="$CI_COMMIT_TAG"' in ci, "image tag keeps the v prefix"
    assert '"$HARBOR_IMAGE:latest"' in ci and 'docker push "$HARBOR_IMAGE:latest"' in ci, (
        "latest must move with each release"
    )
    assert "${CI_COMMIT_TAG#v}" not in ci, "do not strip the v prefix"


def test_deploy_uses_jumpbox_and_podman():
    deploy = CI_FILE.read_text().split("\ndeploy:")[-1]
    assert r"$CI_COMMIT_TAG =~ /^v\d+\.\d+\.\d+$/" in deploy
    assert "when: never" in deploy
    assert "DEPLOY_HOST: vllm.dotslash.io" in deploy
    assert "DEPLOY_USER: deploy" in deploy
    assert "sudo -n podman" in deploy, "deploy runs rootful podman through scoped sudo"
    assert "jumpbox" not in deploy
    assert "sudo -n podman login" in deploy and "--password-stdin" in deploy
    assert "sudo -n podman-compose $COMPOSE_FILES up -d --force-recreate" in deploy
    assert "VOXCPM2_IMAGE=%s" in deploy and ".env" in deploy
    assert "HEALTH_URL: http://127.0.0.1:8881/ready" in deploy
    assert "docker compose" not in deploy


def test_deploy_uses_base64_ssh_key_pattern():
    ci = CI_FILE.read_text()
    assert "DEPLOY_SSH_KEY" in ci
    assert "base64 -d" in ci, "deploy key is base64 -w0"
    assert "BatchMode=yes" in ci
    assert "StrictHostKeyChecking=accept-new" in ci
    assert "DEPLOY_HOST: vllm.dotslash.io" in ci
    assert "DEPLOY_PATH: /opt/voxcpm2-openai" in ci


def test_runbook_documents_cd():
    rb = RUNBOOK.read_text()
    assert STT_URL in rb, "runbook must carry the exact STT URL"
    assert "HARBOR_TOKEN" in rb and "HARBOR_PASSWORD" not in rb
    assert "DEPLOY_SSH_KEY" in rb and "base64" in rb
    assert "git tag vX.Y.Z" in rb, "tag-release procedure documented"


# ----------------------------------- VoxCPM2 CFG/steps: vllm-omni PR #7010 backport (S4)


def test_containerfile_applies_the_runtime_knobs_patch():
    text = _folded()
    patch = "deploy/patches/voxcpm2_runtime_knobs.py"
    assert f"COPY {patch} /tmp/voxcpm2_runtime_knobs.py" in text
    assert "RUN python3 /tmp/voxcpm2_runtime_knobs.py" in text
    # the patch reads both values from voxcpm2_runtime_config and fails on drift
    source = (ROOT / patch).read_text()
    assert "self._runtime_config.cfg_value" in source
    assert "self._runtime_config.inference_timesteps" in source
    assert "sys.exit(" in source


def test_compose_passes_the_runtime_knobs_through():
    compose = (ROOT / "deploy" / "compose.yaml").read_text()
    assert 'VOXCPM2_CFG_VALUE: "${VOXCPM2_CFG_VALUE:-}"' in compose
    assert 'VOXCPM2_INFERENCE_TIMESTEPS: "${VOXCPM2_INFERENCE_TIMESTEPS:-}"' in compose
