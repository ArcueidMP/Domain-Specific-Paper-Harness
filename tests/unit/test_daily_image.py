"""Structural checks for weight-free CI and the model-bearing Daily image."""

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DAILY_DOCKERFILE = REPOSITORY_ROOT / "infra" / "docker" / "Dockerfile.daily"
BUILD_IMAGES_SCRIPT = REPOSITORY_ROOT / "scripts" / "build-images.ps1"
VERIFY_SCRIPT = REPOSITORY_ROOT / "scripts" / "verify.ps1"
CI_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_daily_image_keeps_model_weights_out_of_the_default_target() -> None:
    dockerfile = _read(DAILY_DOCKERFILE)

    assert "uv sync --frozen --no-dev --extra specter2" in dockerfile
    assert "FROM dependencies AS specter2-model" in dockerfile
    assert "ARG PREPARE_SPECTER2_BASE=0" in dockerfile
    assert 'test "$PREPARE_SPECTER2_BASE" = "1"' in dockerfile
    assert "type=cache,id=paper-harness-huggingface" in dockerfile
    assert "python -m paper_harness.adapters.specter2.prepare" in dockerfile
    assert "--output /opt/models/specter2_base" in dockerfile
    assert "FROM runtime-base AS production" in dockerfile
    assert dockerfile.rstrip().endswith("FROM runtime-base AS runtime")


def test_model_preparation_cache_boundary_has_only_pinned_inputs() -> None:
    dockerfile = _read(DAILY_DOCKERFILE)
    model_stage = dockerfile.split("FROM dependencies AS specter2-model", maxsplit=1)[1].split(
        "FROM dependencies AS build", maxsplit=1
    )[0]

    expected_inputs = {
        "src/paper_harness/adapters/specter2/prepare.py",
        "src/paper_harness/adapters/specter2/contract.py",
        "src/paper_harness/adapters/specter2/loader.py",
        "src/paper_harness/ports/scientific_embedding.py",
        "src/paper_harness/domain/errors.py",
    }
    copy_sources = {
        line.strip().split()[1]
        for line in model_stage.splitlines()
        if line.strip().startswith("COPY ")
    }

    assert copy_sources == expected_inputs
    assert "COPY src src" not in model_stage
    assert "COPY apps" not in model_stage


def test_production_target_copies_the_prepared_model_once() -> None:
    dockerfile = _read(DAILY_DOCKERFILE)
    production_stage = dockerfile.split("FROM runtime-base AS production", maxsplit=1)[1].split(
        "FROM runtime-base AS runtime", maxsplit=1
    )[0]

    expected_copy = (
        "COPY --from=specter2-model --chown=paper-harness:paper-harness "
        "/opt/models/specter2_base /opt/models/specter2_base"
    )
    assert expected_copy in production_stage
    assert production_stage.count("COPY --from=specter2-model") == 1


def test_daily_runtime_is_offline_and_carries_model_license_notices() -> None:
    dockerfile = _read(DAILY_DOCKERFILE)

    assert "HF_HUB_OFFLINE=1" in dockerfile
    assert "TRANSFORMERS_OFFLINE=1" in dockerfile
    assert "/opt/licenses/specter2_base/Apache-2.0.txt" in dockerfile
    assert "THIRD_PARTY_NOTICES.md /opt/licenses/paper-harness/THIRD_PARTY_NOTICES.md" in dockerfile


def test_only_deployment_selects_the_model_bearing_target() -> None:
    build_images = _read(BUILD_IMAGES_SCRIPT)
    verify = _read(VERIFY_SCRIPT)
    workflow = _read(CI_WORKFLOW)

    assert '"--target", "production"' in build_images
    assert '"--build-arg", "PREPARE_SPECTER2_BASE=1"' in build_images
    assert "--target production" not in verify
    assert "PREPARE_SPECTER2_BASE" not in verify
    assert "--target production" not in workflow
    assert "PREPARE_SPECTER2_BASE" not in workflow
