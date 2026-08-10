"""Prepare the pinned SPECTER2 Base artifact for offline production loading.

This command is an explicit build/deployment operation. Runtime code never
downloads model files and never deserializes the upstream pickle artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from paper_harness.adapters.specter2.contract import (
    SPECTER2_DIMENSION,
    SPECTER2_MAX_TOKENS,
    SPECTER2_MODEL_IDENTIFIER,
    SPECTER2_MODEL_PROVENANCE,
    SPECTER2_MODEL_REVISION,
    SPECTER2_PREPROCESSING_CONTRACT,
    SPECTER2_SEPARATOR_TOKEN,
    SPECTER2_TOKENIZER_IDENTIFIER,
    SPECTER2_TOKENIZER_REVISION,
)
from paper_harness.adapters.specter2.loader import (
    SPECTER2_ARTIFACT_MANIFEST,
    SPECTER2_ARTIFACT_SCHEMA_VERSION,
    SPECTER2_LOCAL_FILES_ONLY,
    SPECTER2_SOURCE_WEIGHTS_SHA256,
    SPECTER2_TORCH_VERSION,
    SPECTER2_TRANSFORMERS_VERSION,
    SPECTER2_TRUST_REMOTE_CODE,
    SPECTER2_USE_SAFETENSORS,
    SPECTER2_WEIGHTS_ONLY,
)
from paper_harness.ports.scientific_embedding import ScientificEmbeddingConfigurationError

_SOURCE_FILES = (
    "config.json",
    "pytorch_model.bin",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
)
_MODEL_SAFETENSORS_FILENAME = "model.safetensors"


@dataclass(frozen=True, slots=True)
class PreparedSpecter2Artifact:
    """Identity and integrity result of one completed preparation."""

    path: Path
    model_safetensors_sha256: str
    generated_at: datetime


@dataclass(frozen=True, slots=True)
class _PreparationRuntime:
    hf_hub_download: Callable[..., str]
    auto_tokenizer: Any
    auto_model: Any
    transformers_version: str
    torch_version: str


def prepare_specter2_base(
    output_path: str | Path,
    *,
    cache_dir: str | Path | None = None,
) -> PreparedSpecter2Artifact:
    """Download, verify, and convert the exact official revision to safetensors."""

    output = Path(output_path).resolve()
    if output.exists():
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base output path already exists"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    runtime = _load_preparation_runtime()
    _validate_runtime_versions(runtime)

    cache = None if cache_dir is None else Path(cache_dir).resolve()
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)

    work_path = Path(tempfile.mkdtemp(prefix="specter2-base-prepare-", dir=output.parent))
    try:
        source_path = work_path / "source"
        artifact_path = work_path / "artifact"
        source_path.mkdir()
        artifact_path.mkdir()
        _download_source_files(runtime, source_path, cache)
        _verify_sha256(
            source_path / "pytorch_model.bin",
            SPECTER2_SOURCE_WEIGHTS_SHA256,
            label="official SPECTER2 Base PyTorch weights",
        )
        tokenizer, model = _load_verified_source(runtime, source_path)
        model.save_pretrained(str(artifact_path), safe_serialization=True)
        tokenizer.save_pretrained(str(artifact_path))
        _validate_converted_artifact(artifact_path)

        safetensors_sha256 = _sha256(artifact_path / _MODEL_SAFETENSORS_FILENAME)
        generated_at = datetime.now(UTC)
        _write_manifest(
            artifact_path,
            safetensors_sha256=safetensors_sha256,
            generated_at=generated_at,
        )
        os.replace(artifact_path, output)
        return PreparedSpecter2Artifact(
            path=output,
            model_safetensors_sha256=safetensors_sha256,
            generated_at=generated_at,
        )
    finally:
        shutil.rmtree(work_path, ignore_errors=True)


def _load_preparation_runtime() -> _PreparationRuntime:
    try:
        huggingface_hub = import_module("huggingface_hub")
        transformers = import_module("transformers")
        torch = import_module("torch")
        hf_hub_download = cast(Callable[..., str], huggingface_hub.hf_hub_download)
        auto_tokenizer = transformers.AutoTokenizer
        auto_model = transformers.AutoModel
        transformers_version = str(transformers.__version__)
        torch_version = str(torch.__version__)
    except (AttributeError, ImportError) as error:
        raise ScientificEmbeddingConfigurationError(
            "SPECTER2 Base preparation requires the pinned Transformers and PyTorch runtime"
        ) from error
    return _PreparationRuntime(
        hf_hub_download=hf_hub_download,
        auto_tokenizer=auto_tokenizer,
        auto_model=auto_model,
        transformers_version=transformers_version,
        torch_version=torch_version,
    )


def _validate_runtime_versions(runtime: _PreparationRuntime) -> None:
    if runtime.transformers_version != SPECTER2_TRANSFORMERS_VERSION:
        raise ScientificEmbeddingConfigurationError(
            f"SPECTER2 Base preparation requires transformers=={SPECTER2_TRANSFORMERS_VERSION}"
        )
    if runtime.torch_version.split("+", maxsplit=1)[0] != SPECTER2_TORCH_VERSION:
        raise ScientificEmbeddingConfigurationError(
            f"SPECTER2 Base preparation requires torch=={SPECTER2_TORCH_VERSION}"
        )


def _download_source_files(
    runtime: _PreparationRuntime,
    source_path: Path,
    cache_dir: Path | None,
) -> None:
    for filename in _SOURCE_FILES:
        downloaded = Path(
            runtime.hf_hub_download(
                repo_id=SPECTER2_MODEL_IDENTIFIER,
                filename=filename,
                revision=SPECTER2_MODEL_REVISION,
                cache_dir=None if cache_dir is None else str(cache_dir),
                token=False,
            )
        )
        if not downloaded.is_file():
            raise ScientificEmbeddingConfigurationError(
                f"the pinned SPECTER2 Base source file is unavailable: {filename}"
            )
        shutil.copyfile(downloaded, source_path / filename)
    actual_files = {path.name for path in source_path.iterdir() if path.is_file()}
    if actual_files != set(_SOURCE_FILES):
        raise ScientificEmbeddingConfigurationError(
            "the downloaded SPECTER2 Base source file allowlist was violated"
        )


def _load_verified_source(runtime: _PreparationRuntime, source_path: Path) -> tuple[Any, Any]:
    try:
        tokenizer = runtime.auto_tokenizer.from_pretrained(
            str(source_path),
            trust_remote_code=SPECTER2_TRUST_REMOTE_CODE,
            local_files_only=True,
            token=False,
        )
        model = runtime.auto_model.from_pretrained(
            str(source_path),
            trust_remote_code=SPECTER2_TRUST_REMOTE_CODE,
            local_files_only=True,
            use_safetensors=False,
            weights_only=SPECTER2_WEIGHTS_ONLY,
            token=False,
            attn_implementation="eager",
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise ScientificEmbeddingConfigurationError(
            "the verified SPECTER2 Base source artifact could not be loaded safely"
        ) from error
    if getattr(tokenizer, "sep_token", None) != SPECTER2_SEPARATOR_TOKEN:
        raise ScientificEmbeddingConfigurationError(
            "the pinned SPECTER2 Base tokenizer has an unexpected separator token"
        )
    config = getattr(model, "config", None)
    if getattr(config, "hidden_size", None) != SPECTER2_DIMENSION:
        raise ScientificEmbeddingConfigurationError(
            "the pinned SPECTER2 Base model has an unexpected hidden dimension"
        )
    if getattr(config, "model_type", None) != "bert":
        raise ScientificEmbeddingConfigurationError(
            "the pinned SPECTER2 Base model has an unexpected architecture"
        )
    model.eval()
    return tokenizer, model


def _validate_converted_artifact(artifact_path: Path) -> None:
    weights_path = artifact_path / _MODEL_SAFETENSORS_FILENAME
    if not weights_path.is_file() or weights_path.stat().st_size <= 0:
        raise ScientificEmbeddingConfigurationError(
            "SPECTER2 Base conversion did not produce model.safetensors"
        )
    if any(artifact_path.glob("*.bin")):
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base artifact must not contain pickle weights"
        )
    # Transformers 5.3 serializes the complete fast-tokenizer state into these
    # two files. The legacy vocabulary and special-token files are verified in
    # the pinned source snapshot but are intentionally not required after the
    # safe, local-only serialization step.
    required_tokenizer_files = {"tokenizer.json", "tokenizer_config.json"}
    actual_files = {path.name for path in artifact_path.iterdir() if path.is_file()}
    if not {"config.json", _MODEL_SAFETENSORS_FILENAME}.issubset(actual_files):
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base model artifact is incomplete"
        )
    if not required_tokenizer_files.issubset(actual_files):
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base tokenizer artifact is incomplete"
        )


def _write_manifest(
    artifact_path: Path,
    *,
    safetensors_sha256: str,
    generated_at: datetime,
) -> None:
    manifest: dict[str, object] = {
        "schema_version": SPECTER2_ARTIFACT_SCHEMA_VERSION,
        "model_identifier": SPECTER2_MODEL_IDENTIFIER,
        "model_revision": SPECTER2_MODEL_REVISION,
        "tokenizer_identifier": SPECTER2_TOKENIZER_IDENTIFIER,
        "tokenizer_revision": SPECTER2_TOKENIZER_REVISION,
        "dimension": SPECTER2_DIMENSION,
        "max_input_length": SPECTER2_MAX_TOKENS,
        "preprocessing_contract": SPECTER2_PREPROCESSING_CONTRACT,
        "model_provenance": SPECTER2_MODEL_PROVENANCE,
        "source_pytorch_model_sha256": SPECTER2_SOURCE_WEIGHTS_SHA256,
        "model_safetensors_sha256": safetensors_sha256,
        "transformers_version": SPECTER2_TRANSFORMERS_VERSION,
        "torch_version": SPECTER2_TORCH_VERSION,
        "trust_remote_code": SPECTER2_TRUST_REMOTE_CODE,
        "local_files_only": SPECTER2_LOCAL_FILES_ONLY,
        "use_safetensors": SPECTER2_USE_SAFETENSORS,
        "weights_only": SPECTER2_WEIGHTS_ONLY,
        "generated_at": generated_at.isoformat(),
    }
    manifest_path = artifact_path / SPECTER2_ARTIFACT_MANIFEST
    temporary_path = artifact_path / f".{SPECTER2_ARTIFACT_MANIFEST}.tmp"
    temporary_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, manifest_path)


def _sha256(path: Path) -> str:
    try:
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()
    except OSError as error:
        raise ScientificEmbeddingConfigurationError(
            f"cannot hash the SPECTER2 Base artifact: {path.name}"
        ) from error


def _verify_sha256(path: Path, expected: str, *, label: str) -> None:
    actual = _sha256(path)
    if actual != expected:
        raise ScientificEmbeddingConfigurationError(
            f"{label} SHA-256 does not match the pinned provenance"
        )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare pinned SPECTER2 Base safetensors for offline production loading."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    result = prepare_specter2_base(args.output, cache_dir=args.cache_dir)
    print(result.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
