# pyright: reportPrivateUsage=false

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY

import pytest

from paper_harness.adapters.specter2 import loader
from paper_harness.adapters.specter2.contract import (
    SPECTER2_DIMENSION,
    SPECTER2_MAX_TOKENS,
    SPECTER2_MODEL_IDENTIFIER,
    SPECTER2_MODEL_PROVENANCE,
    SPECTER2_MODEL_REVISION,
    SPECTER2_PREPROCESSING_CONTRACT,
    SPECTER2_TOKENIZER_IDENTIFIER,
    SPECTER2_TOKENIZER_REVISION,
)
from paper_harness.ports.scientific_embedding import (
    ScientificEmbeddingConfigurationError,
    ScientificPaperText,
)


class _Tensor:
    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors

    def __getitem__(self, key: object) -> _Tensor:
        assert key == (slice(None), 0, slice(None))
        return self

    def detach(self) -> _Tensor:
        return self

    def cpu(self) -> _Tensor:
        return self

    def tolist(self) -> list[list[float]]:
        return self._vectors


class _Tokenizer:
    sep_token = "[SEP]"

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def __call__(self, texts: tuple[str, ...], **kwargs: object) -> dict[str, object]:
        self.calls.append((texts, kwargs))
        return {"input_ids": object(), "attention_mask": object()}


class _Model:
    config = SimpleNamespace(hidden_size=SPECTER2_DIMENSION, model_type="bert")

    def __init__(self) -> None:
        self.eval_called = False
        self.inputs: dict[str, object] | None = None

    def eval(self) -> None:
        self.eval_called = True

    def __call__(self, **inputs: object) -> SimpleNamespace:
        self.inputs = inputs
        return SimpleNamespace(
            last_hidden_state=_Tensor(
                [[float(index + 1)] * SPECTER2_DIMENSION for index in range(2)]
            )
        )


class _Factory:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls: list[tuple[str, dict[str, object]]] = []

    def from_pretrained(self, path: str, **kwargs: object) -> object:
        self.calls.append((path, kwargs))
        return self.value


def _manifest(weights: bytes, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": loader.SPECTER2_ARTIFACT_SCHEMA_VERSION,
        "model_identifier": SPECTER2_MODEL_IDENTIFIER,
        "model_revision": SPECTER2_MODEL_REVISION,
        "tokenizer_identifier": SPECTER2_TOKENIZER_IDENTIFIER,
        "tokenizer_revision": SPECTER2_TOKENIZER_REVISION,
        "dimension": SPECTER2_DIMENSION,
        "max_input_length": SPECTER2_MAX_TOKENS,
        "preprocessing_contract": SPECTER2_PREPROCESSING_CONTRACT,
        "model_provenance": SPECTER2_MODEL_PROVENANCE,
        "source_pytorch_model_sha256": loader.SPECTER2_SOURCE_WEIGHTS_SHA256,
        "model_safetensors_sha256": hashlib.sha256(weights).hexdigest(),
        "transformers_version": loader.SPECTER2_TRANSFORMERS_VERSION,
        "torch_version": loader.SPECTER2_TORCH_VERSION,
        "trust_remote_code": False,
        "local_files_only": True,
        "use_safetensors": True,
        "weights_only": True,
        "generated_at": "2026-08-10T01:02:03+00:00",
    }
    value.update(overrides)
    return value


def _write_artifact(path: Path, **manifest_overrides: object) -> bytes:
    path.mkdir()
    weights = b"safe tensor bytes for unit contract"
    (path / "model.safetensors").write_bytes(weights)
    (path / loader.SPECTER2_ARTIFACT_MANIFEST).write_text(
        json.dumps(_manifest(weights, **manifest_overrides)),
        encoding="utf-8",
    )
    return weights


def _runtime(
    tokenizer_factory: _Factory,
    model_factory: _Factory,
    deterministic_calls: list[bool],
    *,
    transformers_version: str = loader.SPECTER2_TRANSFORMERS_VERSION,
) -> loader._Runtime:
    return loader._Runtime(
        auto_tokenizer=tokenizer_factory,
        auto_model=model_factory,
        inference_mode=nullcontext,
        use_deterministic_algorithms=deterministic_calls.append,
        transformers_version=transformers_version,
        torch_version=f"{loader.SPECTER2_TORCH_VERSION}+cpu",
    )


def test_loader_is_offline_safetensors_only_and_encodes_cls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "specter2"
    _write_artifact(artifact)
    tokenizer = _Tokenizer()
    model = _Model()
    tokenizer_factory = _Factory(tokenizer)
    model_factory = _Factory(model)
    deterministic_calls: list[bool] = []
    monkeypatch.setattr(
        loader,
        "_load_runtime",
        lambda: _runtime(tokenizer_factory, model_factory, deterministic_calls),
    )

    encoder = loader.load_specter2_encoder(artifact)
    embeddings = encoder.encode(
        (
            ScientificPaperText(key="one", title=" Paper One ", abstract=" First abstract. "),
            ScientificPaperText(key="two", title="Paper Two", abstract="Second abstract."),
        )
    )

    assert encoder.model_identifier == SPECTER2_MODEL_IDENTIFIER
    assert encoder.model_revision == SPECTER2_MODEL_REVISION
    assert encoder.tokenizer_revision == SPECTER2_TOKENIZER_REVISION
    assert encoder.dimension == 768
    assert encoder.artifact_generated_at == datetime(2026, 8, 10, 1, 2, 3, tzinfo=UTC)
    assert encoder.trust_remote_code is False
    assert encoder.local_files_only is True
    assert encoder.use_safetensors is True
    assert encoder.weights_only is True
    assert encoder.transformers_version == "5.3.0"
    assert encoder.torch_version == "2.13.0"
    assert encoder.max_input_length == 512
    assert deterministic_calls == [True]
    assert tokenizer_factory.calls == [
        (
            str(artifact.resolve()),
            {"trust_remote_code": False, "local_files_only": True, "token": False},
        )
    ]
    assert model_factory.calls == [
        (
            str(artifact.resolve()),
            {
                "trust_remote_code": False,
                "local_files_only": True,
                "use_safetensors": True,
                "weights_only": True,
                "token": False,
                "attn_implementation": "eager",
            },
        )
    ]
    assert tokenizer.calls == [
        (
            ("Paper One[SEP]First abstract.", "Paper Two[SEP]Second abstract."),
            {
                "padding": True,
                "truncation": True,
                "return_tensors": "pt",
                "return_token_type_ids": False,
                "max_length": 512,
            },
        )
    ]
    assert model.eval_called is True
    assert model.inputs == {"input_ids": ANY, "attention_mask": ANY}
    assert tuple(len(item.vector) for item in embeddings) == (768, 768)
    assert embeddings[0].vector[0] == 1.0
    assert embeddings[1].vector[0] == 2.0


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"model_revision": "main"}, "provenance"),
        ({"tokenizer_revision": "main"}, "provenance"),
        ({"trust_remote_code": True}, "provenance"),
        ({"use_safetensors": False}, "provenance"),
        ({"generated_at": "2026-08-10T01:02:03"}, "time zone"),
    ],
)
def test_loader_rejects_invalid_manifest(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    artifact = tmp_path / "specter2"
    _write_artifact(artifact, **overrides)

    with pytest.raises(ScientificEmbeddingConfigurationError, match=message):
        loader.load_specter2_encoder(artifact)


def test_loader_rejects_tampered_safetensors(tmp_path: Path) -> None:
    artifact = tmp_path / "specter2"
    _write_artifact(artifact)
    (artifact / "model.safetensors").write_bytes(b"tampered")

    with pytest.raises(ScientificEmbeddingConfigurationError, match="hash does not match"):
        loader.load_specter2_encoder(artifact)


def test_loader_rejects_pickle_weights_even_with_valid_safetensors(tmp_path: Path) -> None:
    artifact = tmp_path / "specter2"
    _write_artifact(artifact)
    (artifact / "pytorch_model.bin").write_bytes(b"not allowed at runtime")

    with pytest.raises(ScientificEmbeddingConfigurationError, match="must not contain pickle"):
        loader.load_specter2_encoder(artifact)


def test_loader_rejects_an_unpinned_transformers_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "specter2"
    _write_artifact(artifact)
    tokenizer_factory = _Factory(_Tokenizer())
    model_factory = _Factory(_Model())
    monkeypatch.setattr(
        loader,
        "_load_runtime",
        lambda: _runtime(
            tokenizer_factory,
            model_factory,
            [],
            transformers_version="5.2.0",
        ),
    )

    with pytest.raises(ScientificEmbeddingConfigurationError, match="transformers==5.3.0"):
        loader.load_specter2_encoder(artifact)

    assert tokenizer_factory.calls == []
    assert model_factory.calls == []
