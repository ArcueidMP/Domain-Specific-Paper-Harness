"""Pinned SPECTER2 Base preprocessing and embedding-output contract."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from paper_harness.ports.scientific_embedding import (
    GeneratedScientificEmbedding,
    ScientificEmbeddingOutputError,
    ScientificPaperText,
)

SPECTER2_MODEL_IDENTIFIER = "allenai/specter2_base"
SPECTER2_MODEL_REVISION = "3447645e1def9117997203454fa4495937bfbd83"
SPECTER2_TOKENIZER_IDENTIFIER = SPECTER2_MODEL_IDENTIFIER
SPECTER2_TOKENIZER_REVISION = SPECTER2_MODEL_REVISION
SPECTER2_DIMENSION = 768
SPECTER2_MAX_TOKENS = 512
SPECTER2_SEPARATOR_TOKEN = "[SEP]"
SPECTER2_PREPROCESSING_CONTRACT = (
    "whitespace-normalized title + tokenizer.sep_token + whitespace-normalized abstract; "
    "padding=true; truncation=true; max_length=512; return_token_type_ids=false; "
    "pooling=last_hidden_state[:,0,:]; normalization=none"
)
SPECTER2_MODEL_PROVENANCE = f"huggingface:{SPECTER2_MODEL_IDENTIFIER}@{SPECTER2_MODEL_REVISION}"
SPECTER2_EMBEDDING_SOURCE = "specter2_base_title_abstract_cls"
MAX_EMBEDDING_BATCH_SIZE = 64

EmbeddingBackend = Callable[[tuple[str, ...]], Sequence[Sequence[float]]]


def specter2_document_text(
    title: str,
    abstract: str,
    *,
    separator_token: str = SPECTER2_SEPARATOR_TOKEN,
) -> str:
    """Return normalized title + tokenizer separator token + abstract."""

    normalized_title = " ".join(title.split())
    normalized_abstract = " ".join(abstract.split())
    if not normalized_title or not normalized_abstract:
        raise ScientificEmbeddingOutputError("SPECTER2 requires non-empty title and abstract text")
    if not separator_token:
        raise ScientificEmbeddingOutputError("SPECTER2 tokenizer requires a separator token")
    return f"{normalized_title}{separator_token}{normalized_abstract}"


class Specter2ContractEncoder:
    """Validate a backend implementing the pinned SPECTER2 Base contract.

    The backend must tokenize the document text with the pinned tokenizer,
    truncate to 512 tokens, and return the final hidden-state CLS vector without
    implicit normalization. Production construction lives in ``loader.py``;
    injected backends keep deterministic CI independent of model downloads.
    """

    def __init__(
        self,
        backend: EmbeddingBackend,
        *,
        separator_token: str = SPECTER2_SEPARATOR_TOKEN,
    ) -> None:
        if not separator_token:
            raise ScientificEmbeddingOutputError("SPECTER2 tokenizer requires a separator token")
        self._backend = backend
        self._separator_token = separator_token

    @property
    def model_identifier(self) -> str:
        return SPECTER2_MODEL_IDENTIFIER

    @property
    def model_revision(self) -> str:
        return SPECTER2_MODEL_REVISION

    @property
    def tokenizer_identifier(self) -> str:
        return SPECTER2_TOKENIZER_IDENTIFIER

    @property
    def tokenizer_revision(self) -> str:
        return SPECTER2_TOKENIZER_REVISION

    @property
    def preprocessing_contract(self) -> str:
        return SPECTER2_PREPROCESSING_CONTRACT

    @property
    def model_provenance(self) -> str:
        return SPECTER2_MODEL_PROVENANCE

    @property
    def dimension(self) -> int:
        return SPECTER2_DIMENSION

    @property
    def source(self) -> str:
        return SPECTER2_EMBEDDING_SOURCE

    def encode(
        self, papers: tuple[ScientificPaperText, ...]
    ) -> tuple[GeneratedScientificEmbedding, ...]:
        if not papers:
            return ()
        if len(papers) > MAX_EMBEDDING_BATCH_SIZE:
            raise ScientificEmbeddingOutputError(
                "SPECTER2 batch exceeds the configured paper count"
            )
        if len({paper.key for paper in papers}) != len(papers):
            raise ScientificEmbeddingOutputError("SPECTER2 input keys must be unique")
        texts = tuple(
            specter2_document_text(
                paper.title,
                paper.abstract,
                separator_token=self._separator_token,
            )
            for paper in papers
        )
        try:
            raw_vectors = tuple(
                tuple(float(value) for value in row) for row in self._backend(texts)
            )
        except (TypeError, ValueError, OverflowError) as error:
            raise ScientificEmbeddingOutputError(
                "SPECTER2 backend returned non-numeric output"
            ) from error
        if len(raw_vectors) != len(papers):
            raise ScientificEmbeddingOutputError(
                "SPECTER2 backend returned the wrong batch dimension"
            )
        if any(len(vector) != SPECTER2_DIMENSION for vector in raw_vectors):
            raise ScientificEmbeddingOutputError(
                "SPECTER2 backend returned the wrong embedding dimension"
            )
        if any(not math.isfinite(value) for vector in raw_vectors for value in vector):
            raise ScientificEmbeddingOutputError("SPECTER2 backend returned a non-finite value")
        if any(not any(value != 0 for value in vector) for vector in raw_vectors):
            raise ScientificEmbeddingOutputError("SPECTER2 backend returned a zero vector")
        return tuple(
            GeneratedScientificEmbedding(key=paper.key, vector=vector)
            for paper, vector in zip(papers, raw_vectors, strict=True)
        )
