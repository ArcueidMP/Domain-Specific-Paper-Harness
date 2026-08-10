"""Offline, safetensors-only production loader for pinned SPECTER2 Base."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
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
    Specter2ContractEncoder,
)
from paper_harness.ports.scientific_embedding import (
    ScientificEmbeddingConfigurationError,
    ScientificEmbeddingOutputError,
)

SPECTER2_TRANSFORMERS_VERSION = "5.3.0"
SPECTER2_TORCH_VERSION = "2.13.0"
SPECTER2_SOURCE_WEIGHTS_SHA256 = "801eda968fad1752fe846a8e572bfdc25202be85544680dda4c99f4589646ebc"
SPECTER2_ARTIFACT_MANIFEST = "paper-harness-specter2-manifest.json"
SPECTER2_ARTIFACT_SCHEMA_VERSION = 1
SPECTER2_DEFAULT_MODEL_PATH = Path("/opt/models/specter2_base")
SPECTER2_TRUST_REMOTE_CODE = False
SPECTER2_LOCAL_FILES_ONLY = True
SPECTER2_USE_SAFETENSORS = True
SPECTER2_WEIGHTS_ONLY = True
_MODEL_WEIGHTS_FILENAME = "model.safetensors"
_MAX_MANIFEST_BYTES = 32_768
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "model_identifier",
        "model_revision",
        "tokenizer_identifier",
        "tokenizer_revision",
        "dimension",
        "max_input_length",
        "preprocessing_contract",
        "model_provenance",
        "source_pytorch_model_sha256",
        "model_safetensors_sha256",
        "transformers_version",
        "torch_version",
        "trust_remote_code",
        "local_files_only",
        "use_safetensors",
        "weights_only",
        "generated_at",
    }
)


@dataclass(frozen=True, slots=True)
class Specter2ArtifactManifest:
    """Validated provenance for the build-time safetensors conversion."""

    model_safetensors_sha256: str
    generated_at: datetime


@dataclass(frozen=True, slots=True)
class _Runtime:
    auto_tokenizer: Any
    auto_model: Any
    inference_mode: Callable[[], AbstractContextManager[None]]
    use_deterministic_algorithms: Callable[[bool], None]
    transformers_version: str
    torch_version: str


class _HuggingFaceBackend:
    def __init__(
        self,
        *,
        tokenizer: Any,
        model: Any,
        inference_mode: Callable[[], AbstractContextManager[None]],
    ) -> None:
        self._tokenizer = tokenizer
        self._model = model
        self._inference_mode = inference_mode

    def __call__(self, texts: tuple[str, ...]) -> Sequence[Sequence[float]]:
        try:
            inputs = self._tokenizer(
                texts,
                padding=True,
                truncation=True,
                return_tensors="pt",
                return_token_type_ids=False,
                max_length=SPECTER2_MAX_TOKENS,
            )
            if not isinstance(inputs, Mapping):
                raise ScientificEmbeddingOutputError(
                    "SPECTER2 tokenizer returned an invalid input mapping"
                )
            with self._inference_mode():
                output = self._model(**inputs)
                vectors = output.last_hidden_state[:, 0, :].detach().cpu().tolist()
        except ScientificEmbeddingOutputError:
            raise
        except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError) as error:
            raise ScientificEmbeddingOutputError("SPECTER2 Base inference failed") from error
        return cast(Sequence[Sequence[float]], vectors)


class Specter2BaseEncoder(Specter2ContractEncoder):
    """Pinned Base encoder plus inspectable offline-loading provenance."""

    def __init__(
        self,
        backend: _HuggingFaceBackend,
        *,
        separator_token: str,
        artifact_path: Path,
        artifact_manifest: Specter2ArtifactManifest,
    ) -> None:
        super().__init__(backend, separator_token=separator_token)
        self._artifact_path = artifact_path
        self._artifact_manifest = artifact_manifest

    @property
    def artifact_path(self) -> Path:
        return self._artifact_path

    @property
    def artifact_sha256(self) -> str:
        return self._artifact_manifest.model_safetensors_sha256

    @property
    def artifact_generated_at(self) -> datetime:
        return self._artifact_manifest.generated_at

    @property
    def trust_remote_code(self) -> bool:
        return SPECTER2_TRUST_REMOTE_CODE

    @property
    def local_files_only(self) -> bool:
        return SPECTER2_LOCAL_FILES_ONLY

    @property
    def use_safetensors(self) -> bool:
        return SPECTER2_USE_SAFETENSORS

    @property
    def weights_only(self) -> bool:
        return SPECTER2_WEIGHTS_ONLY

    @property
    def transformers_version(self) -> str:
        return SPECTER2_TRANSFORMERS_VERSION

    @property
    def torch_version(self) -> str:
        return SPECTER2_TORCH_VERSION

    @property
    def max_input_length(self) -> int:
        return SPECTER2_MAX_TOKENS


def load_specter2_encoder(
    model_path: str | Path = SPECTER2_DEFAULT_MODEL_PATH,
) -> Specter2BaseEncoder:
    """Load the prepared model without network access or pickle deserialization."""

    artifact_path = Path(model_path).resolve()
    manifest = _load_artifact_manifest(artifact_path)
    runtime = _load_runtime()
    _validate_runtime_versions(runtime)
    runtime.use_deterministic_algorithms(True)
    try:
        tokenizer = runtime.auto_tokenizer.from_pretrained(
            str(artifact_path),
            trust_remote_code=SPECTER2_TRUST_REMOTE_CODE,
            local_files_only=SPECTER2_LOCAL_FILES_ONLY,
            token=False,
        )
        model = runtime.auto_model.from_pretrained(
            str(artifact_path),
            trust_remote_code=SPECTER2_TRUST_REMOTE_CODE,
            local_files_only=SPECTER2_LOCAL_FILES_ONLY,
            use_safetensors=SPECTER2_USE_SAFETENSORS,
            weights_only=SPECTER2_WEIGHTS_ONLY,
            token=False,
            attn_implementation="eager",
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base artifact could not be loaded"
        ) from error

    separator_token = getattr(tokenizer, "sep_token", None)
    if separator_token != SPECTER2_SEPARATOR_TOKEN:
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 tokenizer has an unexpected separator token"
        )
    config = getattr(model, "config", None)
    if getattr(config, "hidden_size", None) != SPECTER2_DIMENSION:
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base model has an unexpected hidden dimension"
        )
    if getattr(config, "model_type", None) != "bert":
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base model has an unexpected architecture"
        )
    model.eval()
    return Specter2BaseEncoder(
        _HuggingFaceBackend(
            tokenizer=tokenizer,
            model=model,
            inference_mode=runtime.inference_mode,
        ),
        separator_token=separator_token,
        artifact_path=artifact_path,
        artifact_manifest=manifest,
    )


def _load_runtime() -> _Runtime:
    try:
        transformers = import_module("transformers")
        torch = import_module("torch")
    except ImportError as error:
        raise ScientificEmbeddingConfigurationError(
            "SPECTER2 Base requires the pinned Transformers and PyTorch runtime"
        ) from error
    try:
        return _Runtime(
            auto_tokenizer=transformers.AutoTokenizer,
            auto_model=transformers.AutoModel,
            inference_mode=torch.inference_mode,
            use_deterministic_algorithms=torch.use_deterministic_algorithms,
            transformers_version=str(transformers.__version__),
            torch_version=str(torch.__version__),
        )
    except AttributeError as error:
        raise ScientificEmbeddingConfigurationError(
            "the SPECTER2 Base runtime is missing a required API"
        ) from error


def _validate_runtime_versions(runtime: _Runtime) -> None:
    if runtime.transformers_version != SPECTER2_TRANSFORMERS_VERSION:
        raise ScientificEmbeddingConfigurationError(
            f"SPECTER2 Base requires transformers=={SPECTER2_TRANSFORMERS_VERSION}"
        )
    if runtime.torch_version.split("+", maxsplit=1)[0] != SPECTER2_TORCH_VERSION:
        raise ScientificEmbeddingConfigurationError(
            f"SPECTER2 Base requires torch=={SPECTER2_TORCH_VERSION}"
        )


def _load_artifact_manifest(artifact_path: Path) -> Specter2ArtifactManifest:
    if not artifact_path.is_dir():
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base artifact directory does not exist"
        )
    if any(artifact_path.glob("*.bin")):
        raise ScientificEmbeddingConfigurationError(
            "the runtime SPECTER2 Base artifact must not contain pickle weights"
        )
    manifest_path = artifact_path / SPECTER2_ARTIFACT_MANIFEST
    weights_path = artifact_path / _MODEL_WEIGHTS_FILENAME
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as error:
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base manifest is unavailable"
        ) from error
    if not manifest_bytes or len(manifest_bytes) > _MAX_MANIFEST_BYTES:
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base manifest has an invalid size"
        )
    try:
        raw_value = json.loads(manifest_bytes, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base manifest is invalid JSON"
        ) from error
    if not isinstance(raw_value, dict):
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base manifest has an invalid schema"
        )
    value = cast(dict[str, object], raw_value)
    if frozenset(value) != _MANIFEST_KEYS:
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base manifest has an invalid schema"
        )
    _validate_fixed_manifest_fields(value)
    expected_hash = value["model_safetensors_sha256"]
    if not isinstance(expected_hash, str) or _SHA256_PATTERN.fullmatch(expected_hash) is None:
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base safetensors hash is invalid"
        )
    generated_at = _parse_generated_at(value["generated_at"])
    try:
        with weights_path.open("rb") as weights_file:
            actual_hash = hashlib.file_digest(weights_file, "sha256").hexdigest()
    except OSError as error:
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base safetensors weights are unavailable"
        ) from error
    if actual_hash != expected_hash:
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base safetensors hash does not match its manifest"
        )
    return Specter2ArtifactManifest(
        model_safetensors_sha256=expected_hash,
        generated_at=generated_at,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_fixed_manifest_fields(value: dict[str, object]) -> None:
    expected: dict[str, object] = {
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
        "transformers_version": SPECTER2_TRANSFORMERS_VERSION,
        "torch_version": SPECTER2_TORCH_VERSION,
        "trust_remote_code": SPECTER2_TRUST_REMOTE_CODE,
        "local_files_only": SPECTER2_LOCAL_FILES_ONLY,
        "use_safetensors": SPECTER2_USE_SAFETENSORS,
        "weights_only": SPECTER2_WEIGHTS_ONLY,
    }
    if any(
        type(value[field]) is not type(expected_value) for field, expected_value in expected.items()
    ):
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base manifest has an invalid field type"
        )
    if any(value[field] != expected_value for field, expected_value in expected.items()):
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base manifest provenance does not match the pinned model"
        )


def _parse_generated_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base manifest generated_at is invalid"
        )
    try:
        generated_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base manifest generated_at is invalid"
        ) from error
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ScientificEmbeddingConfigurationError(
            "the prepared SPECTER2 Base manifest generated_at must include a time zone"
        )
    return generated_at


__all__ = [
    "SPECTER2_ARTIFACT_MANIFEST",
    "SPECTER2_ARTIFACT_SCHEMA_VERSION",
    "SPECTER2_DEFAULT_MODEL_PATH",
    "SPECTER2_LOCAL_FILES_ONLY",
    "SPECTER2_SOURCE_WEIGHTS_SHA256",
    "SPECTER2_TORCH_VERSION",
    "SPECTER2_TRANSFORMERS_VERSION",
    "SPECTER2_TRUST_REMOTE_CODE",
    "SPECTER2_USE_SAFETENSORS",
    "SPECTER2_WEIGHTS_ONLY",
    "Specter2ArtifactManifest",
    "Specter2BaseEncoder",
    "load_specter2_encoder",
]
