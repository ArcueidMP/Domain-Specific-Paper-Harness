"""Structural checks for weight-free CI and the model-bearing Daily image."""

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DAILY_DOCKERFILE = REPOSITORY_ROOT / "infra" / "docker" / "Dockerfile.daily"
DEPLOY_SCRIPT = REPOSITORY_ROOT / "scripts" / "deploy.ps1"
VERIFY_SCRIPT = REPOSITORY_ROOT / "scripts" / "verify.ps1"
CI_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_daily_image_keeps_model_weights_out_of_the_default_target() -> None:
    dockerfile = _read(DAILY_DOCKERFILE)

    assert "uv sync --frozen --no-dev --extra specter2" in dockerfile
    assert "FROM build AS specter2-model" in dockerfile
    assert "ARG PREPARE_SPECTER2_BASE=0" in dockerfile
    assert 'test "$PREPARE_SPECTER2_BASE" = "1"' in dockerfile
    assert "type=cache,id=paper-harness-huggingface" in dockerfile
    assert "python -m paper_harness.adapters.specter2.prepare" in dockerfile
    assert "--output /opt/models/specter2_base" in dockerfile
    assert "FROM runtime-base AS production" in dockerfile
    assert dockerfile.rstrip().endswith("FROM runtime-base AS runtime")


def test_daily_runtime_is_offline_and_carries_model_license_notices() -> None:
    dockerfile = _read(DAILY_DOCKERFILE)

    assert "HF_HUB_OFFLINE=1" in dockerfile
    assert "TRANSFORMERS_OFFLINE=1" in dockerfile
    assert "/opt/licenses/specter2_base/Apache-2.0.txt" in dockerfile
    assert "THIRD_PARTY_NOTICES.md /opt/licenses/paper-harness/THIRD_PARTY_NOTICES.md" in dockerfile


def test_only_deployment_selects_the_model_bearing_target() -> None:
    deploy = _read(DEPLOY_SCRIPT)
    verify = _read(VERIFY_SCRIPT)
    workflow = _read(CI_WORKFLOW)

    assert "--target production" in deploy
    assert '--build-arg "PREPARE_SPECTER2_BASE=1"' in deploy
    assert "--target production" not in verify
    assert "PREPARE_SPECTER2_BASE" not in verify
    assert "--target production" not in workflow
    assert "PREPARE_SPECTER2_BASE" not in workflow
