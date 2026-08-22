from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest

from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import (
    parse_arxiv_identifier,
    stable_embedding_id,
    stable_graph_edge_id,
    stable_graph_entity_mention_id,
    stable_lineage_snapshot_id,
    stable_paper_id,
    stable_paper_version_id,
    stable_trend_snapshot_id,
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


def test_product_occurrence_identities_are_isolated_by_pipeline_execution() -> None:
    first_execution = UUID("05baa0ee-9bb2-5e06-ab74-ee77bca475f6")
    second_execution = UUID("61bf7a75-3d4e-56c0-a9c0-5ff9be2c1de4")
    entity_id = UUID("e0324287-3a3a-4f4c-b0eb-b1e7a9732799")
    target_entity_id = UUID("967b6604-48b3-4864-817b-334fbc3e344e")
    version_id = UUID("1c27b53f-e172-469e-808f-33d0495968c0")
    analysis_id = UUID("ed261504-34c2-4328-9b58-76b3cb559e9f")
    paper_id = UUID("d8fdbf73-cf9a-487f-9b6a-237e13272d55")
    topic_id = UUID("4b7db6d4-349c-5c06-bc41-f84091580fcb")

    assert stable_graph_entity_mention_id(
        entity_id,
        version_id,
        analysis_id=analysis_id,
        pipeline_execution_id=first_execution,
    ) != stable_graph_entity_mention_id(
        entity_id,
        version_id,
        analysis_id=analysis_id,
        pipeline_execution_id=second_execution,
    )
    assert stable_graph_edge_id(
        entity_id,
        target_entity_id,
        "extends",
        version_id,
        analysis_id=analysis_id,
        pipeline_execution_id=first_execution,
    ) != stable_graph_edge_id(
        entity_id,
        target_entity_id,
        "extends",
        version_id,
        analysis_id=analysis_id,
        pipeline_execution_id=second_execution,
    )
    assert stable_trend_snapshot_id(
        topic_id,
        date(2026, 8, 10),
        "7",
        "m4-trend-v1",
        pipeline_execution_id=first_execution,
    ) != stable_trend_snapshot_id(
        topic_id,
        date(2026, 8, 10),
        "7",
        "m4-trend-v1",
        pipeline_execution_id=second_execution,
    )
    assert stable_lineage_snapshot_id(
        topic_id,
        paper_id,
        date(2026, 8, 10),
        permitted_relation_types=("extends",),
        max_depth=3,
        max_nodes=20,
        max_edges=40,
        lineage_version="m4-lineage-v1",
        pipeline_execution_id=first_execution,
    ) != stable_lineage_snapshot_id(
        topic_id,
        paper_id,
        date(2026, 8, 10),
        permitted_relation_types=("extends",),
        max_depth=3,
        max_nodes=20,
        max_edges=40,
        lineage_version="m4-lineage-v1",
        pipeline_execution_id=second_execution,
    )
