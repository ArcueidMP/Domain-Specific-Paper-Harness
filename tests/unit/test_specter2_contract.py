from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import pytest

from paper_harness.adapters.specter2.contract import (
    SPECTER2_DIMENSION,
    SPECTER2_MODEL_IDENTIFIER,
    SPECTER2_MODEL_PROVENANCE,
    SPECTER2_MODEL_REVISION,
    SPECTER2_PREPROCESSING_CONTRACT,
    SPECTER2_TOKENIZER_REVISION,
    Specter2ContractEncoder,
    specter2_document_text,
)
from paper_harness.ports.scientific_embedding import (
    ScientificEmbeddingOutputError,
    ScientificPaperText,
)


def _paper(key: str = "paper-1") -> ScientificPaperText:
    return ScientificPaperText(key=key, title="Agent Planning", abstract="A bounded planner.")


def _wrong_dimension(_texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
    return ((0.0,) * 767,)


def _non_finite(_texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
    return ((math.nan,) + (0.0,) * 767,)


def _wrong_batch(_texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
    return ()


def _zero_vector(_texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
    return ((0.0,) * 768,)


INVALID_BACKENDS: tuple[tuple[Callable[[tuple[str, ...]], Sequence[Sequence[float]]], str], ...] = (
    (_wrong_dimension, "wrong embedding dimension"),
    (_non_finite, "non-finite value"),
    (_wrong_batch, "wrong batch dimension"),
    (_zero_vector, "zero vector"),
)


def test_specter2_contract_records_exact_identity_revision_and_shape() -> None:
    encoder = Specter2ContractEncoder(
        lambda texts: tuple(
            (float(index + 1),) * SPECTER2_DIMENSION for index, _ in enumerate(texts)
        )
    )

    result = encoder.encode((_paper(), _paper("paper-2")))

    assert encoder.model_identifier == SPECTER2_MODEL_IDENTIFIER
    assert encoder.model_revision == SPECTER2_MODEL_REVISION
    assert encoder.tokenizer_revision == SPECTER2_TOKENIZER_REVISION
    assert encoder.preprocessing_contract == SPECTER2_PREPROCESSING_CONTRACT
    assert encoder.model_provenance == SPECTER2_MODEL_PROVENANCE
    assert encoder.dimension == 768
    assert [item.key for item in result] == ["paper-1", "paper-2"]
    assert all(len(item.vector) == 768 for item in result)


def test_specter2_preprocessing_uses_title_separator_abstract() -> None:
    assert specter2_document_text("  Agent   Planning ", " Bounded   search. ") == (
        "Agent Planning[SEP]Bounded search."
    )


@pytest.mark.parametrize(
    "backend, message",
    INVALID_BACKENDS,
)
def test_specter2_contract_rejects_invalid_backend_output(
    backend: Callable[[tuple[str, ...]], Sequence[Sequence[float]]], message: str
) -> None:
    encoder = Specter2ContractEncoder(backend)

    with pytest.raises(ScientificEmbeddingOutputError, match=message):
        encoder.encode((_paper(),))


def test_specter2_contract_does_not_invent_empty_abstract_input() -> None:
    with pytest.raises(ScientificEmbeddingOutputError, match="non-empty title and abstract"):
        specter2_document_text("Title", " ")
