"""Boundary for the required pinned scientific-paper embedding model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from paper_harness.domain.errors import DomainInvariantError


class ScientificEmbeddingPortError(RuntimeError):
    error_code = "SCIENTIFIC_EMBEDDING_FAILURE"
    retryable = False


class ScientificEmbeddingConfigurationError(ScientificEmbeddingPortError):
    error_code = "SCIENTIFIC_EMBEDDING_CONFIGURATION_INVALID"


class ScientificEmbeddingUnavailableError(ScientificEmbeddingPortError):
    error_code = "SCIENTIFIC_EMBEDDING_UNAVAILABLE"
    retryable = True


class ScientificEmbeddingOutputError(ScientificEmbeddingPortError):
    error_code = "SCIENTIFIC_EMBEDDING_OUTPUT_INVALID"


@dataclass(frozen=True, slots=True)
class ScientificPaperText:
    key: str
    title: str
    abstract: str

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.title.strip() or not self.abstract.strip():
            raise DomainInvariantError(
                "scientific embedding input requires key, title, and abstract"
            )
        if len(self.key) > 200 or len(self.title) > 4000 or len(self.abstract) > 100_000:
            raise DomainInvariantError("scientific embedding input exceeds a configured bound")


@dataclass(frozen=True, slots=True)
class GeneratedScientificEmbedding:
    key: str
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.vector:
            raise DomainInvariantError("generated scientific embedding is incomplete")


class ScientificEmbeddingPort(Protocol):
    @property
    def model_identifier(self) -> str: ...

    @property
    def model_revision(self) -> str: ...

    @property
    def tokenizer_identifier(self) -> str: ...

    @property
    def tokenizer_revision(self) -> str: ...

    @property
    def preprocessing_contract(self) -> str: ...

    @property
    def model_provenance(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    @property
    def source(self) -> str: ...

    def encode(
        self, papers: tuple[ScientificPaperText, ...]
    ) -> tuple[GeneratedScientificEmbedding, ...]: ...
