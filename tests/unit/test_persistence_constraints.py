from __future__ import annotations

from typing import cast

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Table, UniqueConstraint

from paper_harness.adapters.postgres.models import (
    ComparisonEvidenceLinkRow,
    ComparisonRow,
    EvidenceRow,
    ExternalPaperStubRow,
    PaperAnalysisRow,
    PaperRelationRow,
    PaperSourceIdentityRow,
    PaperVersionRow,
    RelationEvidenceLinkRow,
    RunItemRow,
    ScientificEmbeddingRow,
    SearchActionRow,
    SearchCandidateRow,
    SearchSessionRow,
)


def test_version_identity_and_run_item_use_composite_paper_foreign_keys() -> None:
    version_table = cast(Table, PaperVersionRow.__table__)
    version_unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in version_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("id", "paper_id") in version_unique_columns

    for table in (
        cast(Table, PaperSourceIdentityRow.__table__),
        cast(Table, RunItemRow.__table__),
    ):
        composite_foreign_keys = {
            tuple(constraint.column_keys)
            for constraint in table.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }
        assert ("paper_version_id", "paper_id") in composite_foreign_keys


def test_m3_identity_and_evidence_constraints_are_database_enforced() -> None:
    external_unique = _unique_columns(cast(Table, ExternalPaperStubRow.__table__))
    session_unique = _unique_columns(cast(Table, SearchSessionRow.__table__))
    evidence_unique = _unique_columns(cast(Table, EvidenceRow.__table__))
    assert ("id", "semantic_scholar_id") in external_unique
    assert (
        "id",
        "source_paper_id",
        "source_paper_version_id",
        "source_analysis_id",
        "source_analysis_scope",
    ) in session_unique
    assert ("id", "paper_id", "paper_version_id") in evidence_unique
    assert (
        "id",
        "paper_id",
        "paper_version_id",
        "analysis_scope",
    ) in _unique_columns(cast(Table, PaperAnalysisRow.__table__))

    session_foreign_keys = _foreign_key_columns(cast(Table, SearchSessionRow.__table__))
    assert (
        "source_analysis_id",
        "source_paper_id",
        "source_paper_version_id",
        "source_analysis_scope",
    ) in session_foreign_keys

    candidate_foreign_keys = _foreign_key_columns(cast(Table, SearchCandidateRow.__table__))
    assert ("external_paper_id", "semantic_scholar_id") in candidate_foreign_keys
    assert ("local_paper_version_id", "local_paper_id") in candidate_foreign_keys
    assert ("discovered_by_action_id", "session_id") in candidate_foreign_keys

    comparison_foreign_keys = _foreign_key_columns(cast(Table, ComparisonRow.__table__))
    assert (
        "search_session_id",
        "source_paper_id",
        "source_paper_version_id",
        "source_analysis_id",
        "source_analysis_scope",
    ) in comparison_foreign_keys
    assert (
        "source_analysis_id",
        "source_paper_id",
        "source_paper_version_id",
        "source_analysis_scope",
    ) in comparison_foreign_keys
    assert (
        "target_analysis_id",
        "target_paper_id",
        "target_paper_version_id",
        "target_analysis_scope",
    ) in comparison_foreign_keys
    relation_foreign_keys = _foreign_key_columns(cast(Table, PaperRelationRow.__table__))
    assert (
        "comparison_id",
        "source_paper_id",
        "source_paper_version_id",
        "target_paper_id",
        "target_paper_version_id",
    ) in relation_foreign_keys

    for row in (ComparisonEvidenceLinkRow, RelationEvidenceLinkRow):
        evidence_foreign_keys = _foreign_key_columns(cast(Table, row.__table__))
        assert (
            "evidence_id",
            "evidence_paper_id",
            "evidence_paper_version_id",
        ) in evidence_foreign_keys
    assert (
        "evidence_id",
        "evidence_analysis_id",
    ) in _foreign_key_columns(cast(Table, ComparisonEvidenceLinkRow.__table__))

    embedding_checks = _check_sql(cast(Table, ScientificEmbeddingRow.__table__))
    embedding_unique = _unique_columns(cast(Table, ScientificEmbeddingRow.__table__))
    assert any(
        "paper_version_id IS NOT NULL AND external_paper_id IS NULL" in sql
        for sql in embedding_checks
    )
    assert "dimension = 768" in embedding_checks
    assert (
        "paper_version_id",
        "external_paper_id",
        "model_identifier",
        "model_revision",
        "tokenizer_identifier",
        "tokenizer_revision",
        "dimension",
        "preprocessing_contract",
        "model_provenance",
        "source",
    ) in embedding_unique

    action_checks = _check_sql(cast(Table, SearchActionRow.__table__))
    assert any("result_count BETWEEN 0 AND requested_limit" in sql for sql in action_checks)


def _unique_columns(table: Table) -> set[tuple[str, ...]]:
    return {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_key_columns(table: Table) -> set[tuple[str, ...]]:
    return {
        tuple(constraint.column_keys)
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def _check_sql(table: Table) -> set[str]:
    return {
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
