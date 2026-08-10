from __future__ import annotations

from typing import cast

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Table, UniqueConstraint

from paper_harness.adapters.postgres.models import (
    ComparisonEvidenceLinkRow,
    ComparisonRow,
    DailyRunRow,
    EvidenceRow,
    ExternalPaperStubRow,
    GraphEdgeEvidenceLinkRow,
    GraphEdgeRow,
    GraphEntityMentionRow,
    GraphEntityRow,
    LineageEdgeRow,
    LineageNodeRow,
    LineageSnapshotRow,
    PaperAnalysisRow,
    PaperRelationRow,
    PaperSourceIdentityRow,
    PaperVersionRow,
    ProductRunComparisonInputRow,
    ProductRunPaperInputRow,
    RelationEvidenceLinkRow,
    ReportRow,
    RunItemRow,
    ScientificEmbeddingRow,
    SearchActionRow,
    SearchCandidateRow,
    SearchSessionRow,
    TrendMetricRow,
    TrendRepresentativePaperRow,
    TrendSnapshotRow,
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


def test_m4_graph_trend_lineage_and_report_constraints_are_database_enforced() -> None:
    daily_checks = _check_sql(cast(Table, DailyRunRow.__table__))
    item_checks = _check_sql(cast(Table, RunItemRow.__table__))
    assert any("PRODUCT_PUBLICATION" in sql and "source_run_id" in sql for sql in daily_checks)
    assert any("TREND_SNAPSHOTS_GENERATED" in sql for sql in item_checks)
    assert any("REPORT_GENERATED" in sql for sql in item_checks)

    entity_checks = _check_sql(cast(Table, GraphEntityRow.__table__))
    edge_checks = _check_sql(cast(Table, GraphEdgeRow.__table__))
    assert any("RESEARCH_PROBLEM" in sql and "BENCHMARK" in sql for sql in entity_checks)
    assert any("ADDRESSES" in sql and "EVALUATES_ON" in sql for sql in edge_checks)
    assert any("source_entity_id <> target_entity_id" in sql for sql in edge_checks)
    assert any("LLM_INFERRED" in sql and "confidence" in sql for sql in edge_checks)
    assert ("source_entity_id", "topic_id") in _foreign_key_columns(
        cast(Table, GraphEdgeRow.__table__)
    )
    assert ("target_entity_id", "topic_id") in _foreign_key_columns(
        cast(Table, GraphEdgeRow.__table__)
    )
    assert ("entity_id", "topic_id") in _foreign_key_columns(
        cast(Table, GraphEntityMentionRow.__table__)
    )
    assert (
        "evidence_id",
        "evidence_paper_id",
        "evidence_paper_version_id",
    ) in _foreign_key_columns(cast(Table, GraphEdgeEvidenceLinkRow.__table__))

    trend_checks = _check_sql(cast(Table, TrendSnapshotRow.__table__))
    metric_checks = _check_sql(cast(Table, TrendMetricRow.__table__))
    assert any("7D" in sql and "30D" in sql and "90D" in sql for sql in trend_checks)
    assert any(
        "ZERO_DENOMINATOR" in sql and "paper_growth_rate IS NULL" in sql for sql in trend_checks
    )
    assert any("denominator_count = preceding_count" in sql for sql in metric_checks)
    assert ("snapshot_id", "topic_id") in _foreign_key_columns(
        cast(Table, TrendRepresentativePaperRow.__table__)
    )

    lineage_checks = _check_sql(cast(Table, LineageSnapshotRow.__table__))
    assert any("max_depth BETWEEN 1 AND 10" in sql and "max_nodes" in sql for sql in lineage_checks)
    assert ("graph_entity_id", "topic_id") in _foreign_key_columns(
        cast(Table, LineageNodeRow.__table__)
    )
    assert ("graph_edge_id", "topic_id") in _foreign_key_columns(
        cast(Table, LineageEdgeRow.__table__)
    )

    report_table = cast(Table, ReportRow.__table__)
    report_checks = _check_sql(report_table)
    report_indexes = {index.name for index in report_table.indexes}
    assert "uq_reports_aggregate_period" in report_indexes
    assert ("run_id",) in _unique_columns(report_table)
    assert ("run_id", "topic_id") in _foreign_key_columns(report_table)
    assert any("WEEKLY" in sql and "run_id IS NULL" in sql for sql in report_checks)
    assert any("ISODOW" in sql and "date_trunc" in sql for sql in report_checks)
    assert any("STRUCTURED_ONLY" in sql and "DEEPSEEK" in sql for sql in report_checks)

    daily_unique = _unique_columns(cast(Table, DailyRunRow.__table__))
    assert ("id", "topic_id") in daily_unique
    assert ("id", "topic_id", "logical_date") in daily_unique
    assert ("source_run_id", "topic_id", "logical_date") in _foreign_key_columns(
        cast(Table, DailyRunRow.__table__)
    )
    for table in (
        cast(Table, GraphEntityMentionRow.__table__),
        cast(Table, GraphEdgeRow.__table__),
        cast(Table, TrendSnapshotRow.__table__),
        cast(Table, LineageSnapshotRow.__table__),
    ):
        assert ("publication_run_id", "topic_id") in _foreign_key_columns(table)
    mention_checks = _check_sql(cast(Table, GraphEntityMentionRow.__table__))
    assert any(
        "analysis_id IS NOT NULL" in sql and "comparison_id IS NULL" in sql
        for sql in mention_checks
    )

    paper_input_fks = _foreign_key_columns(cast(Table, ProductRunPaperInputRow.__table__))
    comparison_input_fks = _foreign_key_columns(cast(Table, ProductRunComparisonInputRow.__table__))
    assert ("run_id", "topic_id", "source_run_id") in paper_input_fks
    assert ("analysis_id", "paper_id", "paper_version_id", "analysis_scope") in paper_input_fks
    assert (
        "comparison_id",
        "paper_id",
        "paper_version_id",
        "analysis_id",
    ) in comparison_input_fks


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
