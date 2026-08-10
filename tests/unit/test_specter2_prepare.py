"""Deterministic coverage for the offline SPECTER2 Base preparation boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from paper_harness.adapters.specter2 import prepare
from paper_harness.adapters.specter2.contract import (
    SPECTER2_MODEL_IDENTIFIER,
    SPECTER2_MODEL_REVISION,
)
from paper_harness.adapters.specter2.loader import (
    SPECTER2_ARTIFACT_MANIFEST,
    SPECTER2_TRANSFORMERS_VERSION,
    SPECTER2_TRUST_REMOTE_CODE,
    SPECTER2_WEIGHTS_ONLY,
)
from paper_harness.ports.scientific_embedding import ScientificEmbeddingConfigurationError

SOURCE_FILES = (
    "config.json",
    "pytorch_model.bin",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
)


class _FakeTokenizer:
    sep_token = "[SEP]"

    def save_pretrained(self, path: str) -> None:
        target = Path(path)
        for filename in ("tokenizer.json", "tokenizer_config.json"):
            (target / filename).write_text("{}", encoding="utf-8")


class _FakeConfig:
    hidden_size = 768
    model_type = "bert"


class _FakeModel:
    config = _FakeConfig()

    def eval(self) -> None:
        return None

    def save_pretrained(self, path: str, *, safe_serialization: bool) -> None:
        assert safe_serialization is True
        target = Path(path)
        (target / "config.json").write_text("{}", encoding="utf-8")
        (target / "model.safetensors").write_bytes(b"converted-safe-weights")


class _FakeAutoLoader:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls: list[tuple[str, dict[str, object]]] = []

    def from_pretrained(self, path: str, **kwargs: object) -> object:
        self.calls.append((path, kwargs))
        return self.value


def test_prepare_downloads_only_pinned_files_and_writes_safe_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    source_content: dict[str, bytes] = {
        filename: (b"verified-upstream-weights" if filename == "pytorch_model.bin" else b"{}")
        for filename in SOURCE_FILES
    }
    for filename, content in source_content.items():
        (downloads / filename).write_bytes(content)
    expected_source_hash = hashlib.sha256(source_content["pytorch_model.bin"]).hexdigest()
    download_calls: list[dict[str, object]] = []

    def fake_download(**kwargs: object) -> str:
        download_calls.append(kwargs)
        return str(downloads / str(kwargs["filename"]))

    tokenizer_loader = _FakeAutoLoader(_FakeTokenizer())
    model_loader = _FakeAutoLoader(_FakeModel())
    runtime = SimpleNamespace(
        hf_hub_download=fake_download,
        auto_tokenizer=tokenizer_loader,
        auto_model=model_loader,
        transformers_version=SPECTER2_TRANSFORMERS_VERSION,
        torch_version="2.13.0+cpu",
    )
    monkeypatch.setattr(prepare, "_load_preparation_runtime", lambda: cast(Any, runtime))
    monkeypatch.setattr(prepare, "SPECTER2_SOURCE_WEIGHTS_SHA256", expected_source_hash)

    output = tmp_path / "artifact"
    result = prepare.prepare_specter2_base(output, cache_dir=tmp_path / "cache")

    assert result.path == output
    assert {str(call["filename"]) for call in download_calls} == set(SOURCE_FILES)
    assert all(call["repo_id"] == SPECTER2_MODEL_IDENTIFIER for call in download_calls)
    assert all(call["revision"] == SPECTER2_MODEL_REVISION for call in download_calls)
    assert all(call["token"] is False for call in download_calls)
    assert tokenizer_loader.calls[0][1] == {
        "trust_remote_code": SPECTER2_TRUST_REMOTE_CODE,
        "local_files_only": True,
        "token": False,
    }
    assert model_loader.calls[0][1] == {
        "trust_remote_code": SPECTER2_TRUST_REMOTE_CODE,
        "local_files_only": True,
        "use_safetensors": False,
        "weights_only": SPECTER2_WEIGHTS_ONLY,
        "token": False,
        "attn_implementation": "eager",
    }
    assert not tuple(output.glob("*.bin"))
    manifest = json.loads((output / SPECTER2_ARTIFACT_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["model_identifier"] == SPECTER2_MODEL_IDENTIFIER
    assert manifest["model_revision"] == SPECTER2_MODEL_REVISION
    assert manifest["source_pytorch_model_sha256"] == expected_source_hash
    assert manifest["model_safetensors_sha256"] == result.model_safetensors_sha256
    assert manifest["trust_remote_code"] is False
    assert manifest["local_files_only"] is True
    assert manifest["use_safetensors"] is True
    assert manifest["weights_only"] is True
    assert manifest["generated_at"] == result.generated_at.isoformat()


def test_prepare_refuses_to_replace_an_existing_artifact(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()

    with pytest.raises(ScientificEmbeddingConfigurationError, match="already exists"):
        prepare.prepare_specter2_base(output)
