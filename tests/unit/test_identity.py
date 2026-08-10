from __future__ import annotations

from uuid import UUID

import pytest

from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import (
    parse_arxiv_identifier,
    stable_embedding_id,
    stable_paper_id,
    stable_paper_version_id,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2601.01234v2", ("2601.01234", 2)),
        ("https://arxiv.org/abs/2601.01234v12", ("2601.01234", 12)),
        ("hep-th/9901001v3", ("hep-th/9901001", 3)),
    ],
)
def test_parse_arxiv_identifier_requires_and_preserves_version(
    raw: str, expected: tuple[str, int]
) -> None:
    assert parse_arxiv_identifier(raw) == expected


def test_unversioned_identifier_is_rejected() -> None:
    with pytest.raises(DomainInvariantError, match="explicit version"):
        parse_arxiv_identifier("2601.01234")


def test_stable_ids_distinguish_versions_but_not_repeated_ingestion() -> None:
    assert stable_paper_id("2601.01234") == stable_paper_id("2601.01234")
    assert stable_paper_version_id("2601.01234", 1) != stable_paper_version_id("2601.01234", 2)


def _embedding_id(
    *,
    model_identifier: str = "allenai/specter2_base",
    model_revision: str = "model-revision",
    tokenizer_identifier: str = "allenai/specter2_base",
    tokenizer_revision: str = "tokenizer-revision",
    dimension: int = 768,
    preprocessing_contract: str = "title + separator + abstract; cls; max_length=512",
    model_provenance: str = "huggingface:allenai/specter2_base@model-revision",
    source: str = "specter2_base_title_abstract_cls",
) -> UUID:
    return stable_embedding_id(
        UUID("74f482e4-34fe-4a48-9fd5-d0b38684371d"),
        model_identifier=model_identifier,
        model_revision=model_revision,
        tokenizer_identifier=tokenizer_identifier,
        tokenizer_revision=tokenizer_revision,
        dimension=dimension,
        preprocessing_contract=preprocessing_contract,
        model_provenance=model_provenance,
        source=source,
    )


def test_embedding_identity_includes_the_complete_static_contract() -> None:
    baseline = _embedding_id()

    assert baseline == _embedding_id()
    assert baseline != _embedding_id(model_identifier="allenai/another-model")
    assert baseline != _embedding_id(model_revision="another-model-revision")
    assert baseline != _embedding_id(tokenizer_identifier="allenai/another-tokenizer")
    assert baseline != _embedding_id(tokenizer_revision="another-tokenizer-revision")
    assert baseline != _embedding_id(dimension=1024)
    assert baseline != _embedding_id(preprocessing_contract="another preprocessing contract")
    assert baseline != _embedding_id(model_provenance="another:model@revision")
    assert baseline != _embedding_id(source="another_embedding_source")
    assert _embedding_id(model_identifier="a:b", model_revision="c") != _embedding_id(
        model_identifier="a", model_revision="b:c"
    )
