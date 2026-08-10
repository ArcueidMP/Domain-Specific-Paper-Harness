"""Add M4 graph, trend, lineage, and product-report persistence.

Revision ID: 0004_m4_graph_trends_reports
Revises: 0003_m3_pasa_semantic_scholar
"""

# Alembic renders database check expressions as indivisible string literals.
# ruff: noqa: E501

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "0004_m4_graph_trends_reports"
down_revision = "0003_m3_pasa_semantic_scholar"
branch_labels = None
depends_on = None


def _upgrade_run_contract() -> None:
    op.drop_constraint("ck_daily_runs_operation_fields", "daily_runs", type_="check")
    op.drop_constraint("ck_daily_runs_operation_allowed", "daily_runs", type_="check")
    op.drop_constraint("ck_run_items_stage_allowed", "run_items", type_="check")
    op.add_column("daily_runs", sa.Column("source_run_id", sa.UUID(), nullable=True))
    op.create_unique_constraint("uq_daily_runs_topic_ownership", "daily_runs", ["id", "topic_id"])
    op.create_unique_constraint(
        "uq_daily_runs_topic_date_ownership",
        "daily_runs",
        ["id", "topic_id", "logical_date"],
    )
    op.create_unique_constraint(
        "uq_daily_runs_product_input_ownership",
        "daily_runs",
        ["id", "topic_id", "source_run_id"],
    )
    op.create_foreign_key(
        "fk_daily_runs_source_run",
        "daily_runs",
        "daily_runs",
        ["source_run_id", "topic_id", "logical_date"],
        ["id", "topic_id", "logical_date"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_run_items_run_paper_version",
        "run_items",
        ["run_id", "paper_id", "paper_version_id"],
    )
    op.create_index(
        "uq_daily_runs_product_source_date",
        "daily_runs",
        ["source_run_id", "logical_date"],
        unique=True,
        postgresql_where=sa.text("operation = 'PRODUCT_PUBLICATION'"),
    )
    op.create_check_constraint(
        "ck_daily_runs_operation_allowed",
        "daily_runs",
        "operation IN ('ARXIV_INGESTION', 'STRUCTURED_ANALYSIS', 'PRODUCT_PUBLICATION')",
    )
    op.create_check_constraint(
        "ck_daily_runs_operation_fields",
        "daily_runs",
        "(operation = 'ARXIV_INGESTION' AND cursor_from IS NOT NULL "
        "AND cursor_to IS NOT NULL AND cursor_from <= cursor_to "
        "AND analysis_scope IS NULL AND selected_count = 0 AND completed_count = 0) OR "
        "(operation = 'STRUCTURED_ANALYSIS' AND cursor_from IS NULL "
        "AND cursor_to IS NULL AND analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT')) OR "
        "(operation = 'PRODUCT_PUBLICATION' AND cursor_from IS NULL "
        "AND cursor_to IS NULL AND analysis_scope IS NULL "
        "AND discovered_count = 0 AND normalized_count = 0)",
    )
    op.create_check_constraint(
        "ck_daily_runs_failed_lte_selected",
        "daily_runs",
        "operation = 'ARXIV_INGESTION' OR failed_count <= selected_count",
    )
    op.create_check_constraint(
        "ck_daily_runs_source_run",
        "daily_runs",
        "(operation = 'PRODUCT_PUBLICATION' AND source_run_id IS NOT NULL) OR "
        "(operation <> 'PRODUCT_PUBLICATION' AND source_run_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_run_items_stage_allowed",
        "run_items",
        "stage IN ('DISCOVERED', 'NORMALIZED', 'ENRICHED', 'RELEVANCE_SCORED', "
        "'SELECTED', 'PDF_DOWNLOADED', 'PARSED', 'ANALYZED', 'EVIDENCE_EXTRACTED', "
        "'PRIOR_WORK_RETRIEVED', 'COMPARED', 'GRAPH_UPDATED', "
        "'TREND_SNAPSHOTS_GENERATED', 'REPORT_GENERATED', 'PUBLISHED')",
    )


def _upgrade_reports() -> None:
    op.add_column(
        "reports",
        sa.Column(
            "report_type",
            sa.String(length=16),
            nullable=True,
            server_default=sa.text("'ANALYSIS'"),
        ),
    )
    op.add_column("reports", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("reports", sa.Column("period_end", sa.Date(), nullable=True))
    for name in (
        "retrieved_count",
        "selected_count",
        "processed_count",
        "completed_count",
        "failed_count",
        "graph_entity_count",
        "graph_edge_count",
        "new_graph_entity_count",
        "inferred_graph_edge_count",
    ):
        op.add_column(
            "reports",
            sa.Column(name, sa.Integer(), nullable=True, server_default=sa.text("0")),
        )
    op.add_column(
        "reports",
        sa.Column(
            "limitations",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.add_column(
        "reports",
        sa.Column(
            "missing_sections",
            postgresql.ARRAY(sa.String(length=200)),
            nullable=True,
            server_default=sa.text("'{}'::varchar[]"),
        ),
    )
    op.add_column(
        "reports",
        sa.Column(
            "narrative_mode",
            sa.String(length=32),
            nullable=True,
            server_default=sa.text("'STRUCTURED_ONLY'"),
        ),
    )
    for name, column_type in (
        ("provider", sa.String(length=100)),
        ("configured_model", sa.String(length=200)),
        ("model_version", sa.String(length=200)),
        ("prompt_version", sa.String(length=100)),
        ("prompt_tokens", sa.Integer()),
        ("completion_tokens", sa.Integer()),
        ("total_tokens", sa.Integer()),
        ("call_count", sa.Integer()),
        ("duration_ms", sa.Integer()),
        ("estimated_cost_usd", sa.Numeric(precision=18, scale=8)),
    ):
        op.add_column("reports", sa.Column(name, column_type, nullable=True))
    op.add_column(
        "reports",
        sa.Column(
            "verification_status",
            sa.String(length=32),
            nullable=True,
            server_default=sa.text("'UNVERIFIED'"),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE reports AS report SET "
            "report_type = 'ANALYSIS', "
            "period_start = report.logical_date, period_end = report.logical_date, "
            "retrieved_count = GREATEST(run.selected_count, "
            "run.completed_count + run.failed_count), "
            "selected_count = GREATEST(run.selected_count, "
            "run.completed_count + run.failed_count), "
            "processed_count = run.completed_count + run.failed_count, "
            "completed_count = run.completed_count, failed_count = run.failed_count, "
            "graph_entity_count = 0, graph_edge_count = 0, "
            "new_graph_entity_count = 0, inferred_graph_edge_count = 0, "
            "limitations = '{}'::text[], missing_sections = '{}'::varchar[], "
            "narrative_mode = 'STRUCTURED_ONLY', verification_status = 'UNVERIFIED' "
            "FROM daily_runs AS run WHERE report.run_id = run.id"
        )
    )
    required_columns: tuple[tuple[str, sa.types.TypeEngine], ...] = (
        ("report_type", sa.String(length=16)),
        ("period_start", sa.Date()),
        ("period_end", sa.Date()),
        ("retrieved_count", sa.Integer()),
        ("selected_count", sa.Integer()),
        ("processed_count", sa.Integer()),
        ("completed_count", sa.Integer()),
        ("failed_count", sa.Integer()),
        ("graph_entity_count", sa.Integer()),
        ("graph_edge_count", sa.Integer()),
        ("new_graph_entity_count", sa.Integer()),
        ("inferred_graph_edge_count", sa.Integer()),
        ("limitations", postgresql.ARRAY(sa.Text())),
        ("missing_sections", postgresql.ARRAY(sa.String(length=200))),
        ("narrative_mode", sa.String(length=32)),
        ("verification_status", sa.String(length=32)),
    )
    for name, column_type in required_columns:
        op.alter_column(
            "reports",
            name,
            existing_type=column_type,
            nullable=False,
            server_default=None,
        )
    op.alter_column("reports", "run_id", existing_type=sa.UUID(), nullable=True)
    op.drop_constraint("uq_reports_ownership", "reports", type_="unique")
    op.create_unique_constraint("uq_reports_topic_ownership", "reports", ["id", "topic_id"])
    op.drop_constraint("reports_run_id_fkey", "reports", type_="foreignkey")
    op.create_foreign_key(
        "fk_reports_run_topic",
        "reports",
        "daily_runs",
        ["run_id", "topic_id"],
        ["id", "topic_id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "uq_reports_aggregate_period",
        "reports",
        ["topic_id", "report_type", "period_start", "period_end"],
        unique=True,
        postgresql_where=sa.text("report_type IN ('WEEKLY', 'MONTHLY')"),
    )
    op.create_check_constraint(
        "ck_reports_type_allowed",
        "reports",
        "report_type IN ('ANALYSIS', 'DAILY', 'WEEKLY', 'MONTHLY')",
    )
    op.create_check_constraint(
        "ck_reports_run_ownership",
        "reports",
        "(report_type IN ('ANALYSIS', 'DAILY') AND run_id IS NOT NULL) OR "
        "(report_type IN ('WEEKLY', 'MONTHLY') AND run_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_reports_calendar_period",
        "reports",
        "(report_type IN ('ANALYSIS', 'DAILY') "
        "AND period_start = logical_date AND period_end = logical_date) OR "
        "(report_type = 'WEEKLY' AND logical_date = period_end "
        "AND EXTRACT(ISODOW FROM period_start) = 1 "
        "AND period_end = period_start + 6) OR "
        "(report_type = 'MONTHLY' AND logical_date = period_end "
        "AND period_start = date_trunc('month', period_start)::date "
        "AND period_end = (period_start + INTERVAL '1 month - 1 day')::date)",
    )
    op.create_check_constraint(
        "ck_reports_counts",
        "reports",
        "retrieved_count >= 0 AND selected_count >= 0 AND processed_count >= 0 "
        "AND completed_count >= 0 AND failed_count >= 0 "
        "AND selected_count <= retrieved_count AND processed_count <= selected_count "
        "AND completed_count <= processed_count AND failed_count <= selected_count "
        "AND processed_count = completed_count + failed_count",
    )
    op.create_check_constraint(
        "ck_reports_graph_counts",
        "reports",
        "graph_entity_count >= 0 AND graph_edge_count >= 0 "
        "AND new_graph_entity_count BETWEEN 0 AND graph_entity_count "
        "AND inferred_graph_edge_count BETWEEN 0 AND graph_edge_count",
    )
    op.create_check_constraint(
        "ck_reports_narrative_mode",
        "reports",
        "narrative_mode IN ('STRUCTURED_ONLY', 'DEEPSEEK')",
    )
    op.create_check_constraint(
        "ck_reports_model_provenance",
        "reports",
        "(narrative_mode = 'DEEPSEEK' AND provider IS NOT NULL "
        "AND configured_model IS NOT NULL AND model_version IS NOT NULL "
        "AND prompt_version IS NOT NULL AND prompt_tokens IS NOT NULL "
        "AND completion_tokens IS NOT NULL AND total_tokens IS NOT NULL "
        "AND call_count IS NOT NULL AND duration_ms IS NOT NULL) OR "
        "(narrative_mode = 'STRUCTURED_ONLY' AND provider IS NULL "
        "AND configured_model IS NULL AND model_version IS NULL "
        "AND prompt_version IS NULL AND prompt_tokens IS NULL "
        "AND completion_tokens IS NULL AND total_tokens IS NULL "
        "AND call_count IS NULL AND duration_ms IS NULL "
        "AND estimated_cost_usd IS NULL)",
    )
    op.create_check_constraint(
        "ck_reports_usage",
        "reports",
        "prompt_tokens IS NULL OR (prompt_tokens BETWEEN 0 AND 1000000 "
        "AND completion_tokens BETWEEN 0 AND 16000 "
        "AND total_tokens = prompt_tokens + completion_tokens "
        "AND total_tokens BETWEEN 0 AND 1000000 "
        "AND call_count BETWEEN 1 AND 4 AND duration_ms BETWEEN 0 AND 1800000 "
        "AND (estimated_cost_usd IS NULL OR estimated_cost_usd >= 0))",
    )
    op.create_check_constraint(
        "ck_reports_verification",
        "reports",
        "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
    )


def upgrade() -> None:
    _upgrade_run_contract()
    _upgrade_reports()
    op.create_unique_constraint(
        "uq_comparisons_product_source_ownership",
        "comparisons",
        ["id", "source_paper_id", "source_paper_version_id", "source_analysis_id"],
    )

    op.create_table(
        "product_run_paper_inputs",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("source_run_id", sa.UUID(), nullable=False),
        sa.Column("paper_id", sa.UUID(), nullable=False),
        sa.Column("paper_version_id", sa.UUID(), nullable=False),
        sa.Column("analysis_id", sa.UUID(), nullable=False),
        sa.Column("analysis_scope", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schema_version > 0", name="ck_product_run_paper_inputs_schema"),
        sa.ForeignKeyConstraint(
            ["analysis_id", "paper_id", "paper_version_id", "analysis_scope"],
            [
                "paper_analyses.id",
                "paper_analyses.paper_id",
                "paper_analyses.paper_version_id",
                "paper_analyses.analysis_scope",
            ],
            name="fk_product_run_paper_inputs_analysis",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "paper_id", "paper_version_id"],
            ["run_items.run_id", "run_items.paper_id", "run_items.paper_version_id"],
            name="fk_product_run_paper_inputs_item",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "topic_id", "source_run_id"],
            ["daily_runs.id", "daily_runs.topic_id", "daily_runs.source_run_id"],
            name="fk_product_run_paper_inputs_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "paper_version_id"),
        sa.UniqueConstraint(
            "run_id",
            "topic_id",
            "source_run_id",
            "paper_id",
            "paper_version_id",
            "analysis_id",
            name="uq_product_run_paper_inputs_ownership",
        ),
    )
    op.create_table(
        "product_run_comparison_inputs",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("source_run_id", sa.UUID(), nullable=False),
        sa.Column("paper_id", sa.UUID(), nullable=False),
        sa.Column("paper_version_id", sa.UUID(), nullable=False),
        sa.Column("analysis_id", sa.UUID(), nullable=False),
        sa.Column("comparison_id", sa.UUID(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schema_version > 0", name="ck_product_run_comparison_inputs_schema"),
        sa.ForeignKeyConstraint(
            ["comparison_id", "paper_id", "paper_version_id", "analysis_id"],
            [
                "comparisons.id",
                "comparisons.source_paper_id",
                "comparisons.source_paper_version_id",
                "comparisons.source_analysis_id",
            ],
            name="fk_product_run_comparison_inputs_comparison",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "run_id",
                "topic_id",
                "source_run_id",
                "paper_id",
                "paper_version_id",
                "analysis_id",
            ],
            [
                "product_run_paper_inputs.run_id",
                "product_run_paper_inputs.topic_id",
                "product_run_paper_inputs.source_run_id",
                "product_run_paper_inputs.paper_id",
                "product_run_paper_inputs.paper_version_id",
                "product_run_paper_inputs.analysis_id",
            ],
            name="fk_product_run_comparison_inputs_paper",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "paper_version_id", "comparison_id"),
    )

    # Create normalized artifacts only after report ownership keys are upgraded.
    op.create_table(
        "graph_entities",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("paper_id", sa.UUID(), nullable=True),
        sa.Column("canonical_label", sa.Text(), nullable=False),
        sa.Column("normalized_key", sa.String(length=500), nullable=False),
        sa.Column("display_label", sa.Text(), nullable=False),
        sa.Column("aliases", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("provenance", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(entity_type = 'PAPER' AND paper_id IS NOT NULL) OR (entity_type <> 'PAPER' AND paper_id IS NULL)",
            name="ck_graph_entities_paper_owner",
        ),
        sa.CheckConstraint(
            "entity_type IN ('PAPER', 'RESEARCH_PROBLEM', 'METHOD', 'TASK', 'DATASET', 'BENCHMARK')",
            name="ck_graph_entities_type",
        ),
        sa.CheckConstraint(
            "provenance IN ('METADATA_EXPLICIT', 'TEXT_EXPLICIT', 'DETERMINISTICALLY_DERIVED', 'LLM_INFERRED', 'HUMAN_VERIFIED')",
            name="ck_graph_entities_provenance",
        ),
        sa.CheckConstraint(
            "char_length(normalized_key) BETWEEN 1 AND 500",
            name="ck_graph_entities_key_length",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_graph_entities_schema_version"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "topic_id", name="uq_graph_entities_topic_ownership"),
        sa.UniqueConstraint(
            "topic_id", "entity_type", "normalized_key", name="uq_graph_entities_canonical_key"
        ),
    )
    op.create_index(
        "ix_graph_entities_topic_type", "graph_entities", ["topic_id", "entity_type"], unique=False
    )
    op.create_index(
        "uq_graph_entities_topic_paper",
        "graph_entities",
        ["topic_id", "paper_id"],
        unique=True,
        postgresql_where=sa.text("entity_type = 'PAPER'"),
    )

    op.create_table(
        "trend_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("publication_run_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("window", sa.String(length=8), nullable=False),
        sa.Column("window_size_days", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("preceding_start", sa.Date(), nullable=False),
        sa.Column("preceding_end", sa.Date(), nullable=False),
        sa.Column("included_paper_count", sa.Integer(), nullable=False),
        sa.Column("preceding_paper_count", sa.Integer(), nullable=False),
        sa.Column("paper_count_change", sa.Integer(), nullable=False),
        sa.Column("paper_count_denominator", sa.Integer(), nullable=False),
        sa.Column("paper_growth_rate", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("growth_status", sa.String(length=32), nullable=False),
        sa.Column("entity_count", sa.Integer(), nullable=False),
        sa.Column("relation_count", sa.Integer(), nullable=False),
        sa.Column("new_entity_count", sa.Integer(), nullable=False),
        sa.Column("recurring_entity_count", sa.Integer(), nullable=False),
        sa.Column("limited_paper_count", sa.Integer(), nullable=False),
        sa.Column("sufficient_paper_count", sa.Integer(), nullable=False),
        sa.Column("minimum_growth_denominator", sa.Integer(), nullable=False),
        sa.Column("data_sufficiency", sa.String(length=16), nullable=False),
        sa.Column("preceding_data_sufficiency", sa.String(length=16), nullable=False),
        sa.Column("aggregation_version", sa.String(length=100), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(growth_status = 'AVAILABLE' AND paper_count_denominator > 0 AND paper_growth_rate IS NOT NULL) OR (growth_status = 'ZERO_DENOMINATOR' AND paper_count_denominator = 0 AND paper_growth_rate IS NULL) OR (growth_status = 'LIMITED_SAMPLE' AND paper_growth_rate IS NULL)",
            name="ck_trend_snapshots_growth",
        ),
        sa.CheckConstraint(
            "(\"window\" = '7D' AND window_size_days = 7) OR (\"window\" = '30D' AND window_size_days = 30) OR (\"window\" = '90D' AND window_size_days = 90)",
            name="ck_trend_snapshots_window_size",
        ),
        sa.CheckConstraint(
            "data_sufficiency IN ('SUFFICIENT', 'LIMITED', 'INSUFFICIENT') AND preceding_data_sufficiency IN ('SUFFICIENT', 'LIMITED', 'INSUFFICIENT')",
            name="ck_trend_snapshots_sufficiency",
        ),
        sa.CheckConstraint(
            "growth_status IN ('AVAILABLE', 'ZERO_DENOMINATOR', 'LIMITED_SAMPLE')",
            name="ck_trend_snapshots_growth_status",
        ),
        sa.CheckConstraint("\"window\" IN ('7D', '30D', '90D')", name="ck_trend_snapshots_window"),
        sa.CheckConstraint(
            "entity_count >= 0 AND relation_count >= 0 AND new_entity_count BETWEEN 0 AND entity_count AND recurring_entity_count BETWEEN 0 AND entity_count",
            name="ck_trend_snapshots_graph_counts",
        ),
        sa.CheckConstraint(
            "included_paper_count >= 0 AND preceding_paper_count >= 0 AND paper_count_change = included_paper_count - preceding_paper_count AND paper_count_denominator = preceding_paper_count",
            name="ck_trend_snapshots_paper_counts",
        ),
        sa.CheckConstraint(
            "limited_paper_count BETWEEN 1 AND sufficient_paper_count AND minimum_growth_denominator > 0",
            name="ck_trend_snapshots_thresholds",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_trend_snapshots_schema_version"),
        sa.CheckConstraint(
            "window_end = as_of_date AND window_end - window_start = window_size_days - 1 AND preceding_end = window_start - 1 AND preceding_end - preceding_start = window_size_days - 1",
            name="ck_trend_snapshots_periods",
        ),
        sa.ForeignKeyConstraint(
            ["publication_run_id", "topic_id"],
            ["daily_runs.id", "daily_runs.topic_id"],
            name="fk_trend_snapshots_publication_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "topic_id", name="uq_trend_snapshots_topic_ownership"),
        sa.UniqueConstraint(
            "topic_id",
            "as_of_date",
            "window",
            "aggregation_version",
            name="uq_trend_snapshots_identity",
        ),
    )
    op.create_index(
        "ix_trend_snapshots_topic_as_of",
        "trend_snapshots",
        ["topic_id", "as_of_date"],
        unique=False,
    )
    op.create_index(
        "ix_trend_snapshots_publication_run",
        "trend_snapshots",
        ["publication_run_id"],
        unique=False,
    )

    op.create_table(
        "lineage_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("publication_run_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("root_paper_id", sa.UUID(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("permitted_relation_types", sa.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("max_depth", sa.Integer(), nullable=False),
        sa.Column("max_nodes", sa.Integer(), nullable=False),
        sa.Column("max_edges", sa.Integer(), nullable=False),
        sa.Column("node_count", sa.Integer(), nullable=False),
        sa.Column("edge_count", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("corpus_scope", sa.String(length=40), nullable=False),
        sa.Column("explicit_predecessor_available", sa.Boolean(), nullable=False),
        sa.Column("verified_predecessor_available", sa.Boolean(), nullable=False),
        sa.Column("limitations", sa.ARRAY(sa.String(length=500)), nullable=False),
        sa.Column("lineage_version", sa.String(length=100), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "cardinality(permitted_relation_types) BETWEEN 1 AND 3 "
            "AND permitted_relation_types <@ "
            "ARRAY['CITES', 'EXTENDS', 'IMPROVES_ON']::varchar[]",
            name="ck_lineage_snapshots_relations",
        ),
        sa.CheckConstraint(
            "corpus_scope = 'CURRENTLY_RETRIEVED_CORPUS'", name="ck_lineage_snapshots_corpus_scope"
        ),
        sa.CheckConstraint(
            "max_depth BETWEEN 1 AND 10 AND max_nodes BETWEEN 1 AND 200 "
            "AND max_edges BETWEEN 1 AND 1000",
            name="ck_lineage_snapshots_limits",
        ),
        sa.CheckConstraint(
            "node_count BETWEEN 1 AND max_nodes AND edge_count BETWEEN 0 AND max_edges",
            name="ck_lineage_snapshots_counts",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_lineage_snapshots_schema_version"),
        sa.ForeignKeyConstraint(
            ["publication_run_id", "topic_id"],
            ["daily_runs.id", "daily_runs.topic_id"],
            name="fk_lineage_snapshots_publication_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["root_paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "topic_id", "root_paper_id", name="uq_lineage_snapshots_root_ownership"
        ),
        sa.UniqueConstraint("id", "topic_id", name="uq_lineage_snapshots_topic_ownership"),
        sa.UniqueConstraint(
            "topic_id",
            "root_paper_id",
            "as_of_date",
            "max_depth",
            "max_nodes",
            "max_edges",
            "permitted_relation_types",
            "lineage_version",
            name="uq_lineage_snapshots_identity",
        ),
    )
    op.create_index(
        "ix_lineage_snapshots_topic_as_of",
        "lineage_snapshots",
        ["topic_id", "as_of_date"],
        unique=False,
    )
    op.create_index(
        "ix_lineage_snapshots_publication_run",
        "lineage_snapshots",
        ["publication_run_id"],
        unique=False,
    )

    op.create_table(
        "graph_entity_mentions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("publication_run_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("paper_id", sa.UUID(), nullable=False),
        sa.Column("paper_version_id", sa.UUID(), nullable=False),
        sa.Column("analysis_id", sa.UUID(), nullable=True),
        sa.Column("comparison_id", sa.UUID(), nullable=True),
        sa.Column("observed_label", sa.Text(), nullable=False),
        sa.Column("provenance", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("configured_model", sa.String(length=200), nullable=True),
        sa.Column("model_version", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(provenance = 'LLM_INFERRED' AND provider IS NOT NULL AND configured_model IS NOT NULL AND model_version IS NOT NULL AND prompt_version IS NOT NULL AND confidence BETWEEN 0 AND 1) OR (provenance = 'DETERMINISTICALLY_DERIVED' AND confidence IS NULL AND ((provider IS NULL AND configured_model IS NULL AND model_version IS NULL AND prompt_version IS NULL) OR (provider IS NOT NULL AND configured_model IS NOT NULL AND model_version IS NOT NULL AND prompt_version IS NOT NULL))) OR (provenance NOT IN ('LLM_INFERRED', 'DETERMINISTICALLY_DERIVED') AND provider IS NULL AND configured_model IS NULL AND model_version IS NULL AND prompt_version IS NULL AND confidence IS NULL)",
            name="ck_graph_entity_mentions_model_provenance",
        ),
        sa.CheckConstraint(
            "provenance <> 'HUMAN_VERIFIED' OR verification_status = 'HUMAN_VERIFIED'",
            name="ck_graph_entity_mentions_human_verified",
        ),
        sa.CheckConstraint(
            "(analysis_id IS NOT NULL AND comparison_id IS NULL) OR "
            "(analysis_id IS NULL AND comparison_id IS NOT NULL)",
            name="ck_graph_entity_mentions_owner_shape",
        ),
        sa.CheckConstraint(
            "provenance IN ('METADATA_EXPLICIT', 'TEXT_EXPLICIT', 'DETERMINISTICALLY_DERIVED', 'LLM_INFERRED', 'HUMAN_VERIFIED')",
            name="ck_graph_entity_mentions_provenance",
        ),
        sa.CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_graph_entity_mentions_verification",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_graph_entity_mentions_schema_version"),
        sa.ForeignKeyConstraint(
            ["analysis_id", "paper_id", "paper_version_id"],
            ["paper_analyses.id", "paper_analyses.paper_id", "paper_analyses.paper_version_id"],
            name="fk_graph_entity_mentions_analysis",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["comparison_id"], ["comparisons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["entity_id", "topic_id"],
            ["graph_entities.id", "graph_entities.topic_id"],
            name="fk_graph_entity_mentions_entity_topic",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_graph_entity_mentions_paper_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["publication_run_id", "topic_id"],
            ["daily_runs.id", "daily_runs.topic_id"],
            name="fk_graph_entity_mentions_publication_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_id",
            "paper_version_id",
            "analysis_id",
            "comparison_id",
            "observed_label",
            "provenance",
            name="uq_graph_entity_mentions_source",
            postgresql_nulls_not_distinct=True,
        ),
        sa.UniqueConstraint(
            "id", "paper_id", "paper_version_id", name="uq_graph_entity_mentions_paper_ownership"
        ),
    )
    op.create_index(
        "ix_graph_entity_mentions_entity",
        "graph_entity_mentions",
        ["entity_id", "generated_at"],
        unique=False,
    )
    op.create_index(
        "ix_graph_entity_mentions_paper",
        "graph_entity_mentions",
        ["paper_id", "paper_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_graph_entity_mentions_publication_run",
        "graph_entity_mentions",
        ["publication_run_id"],
        unique=False,
    )

    op.create_table(
        "graph_edges",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("publication_run_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("source_entity_id", sa.UUID(), nullable=False),
        sa.Column("target_entity_id", sa.UUID(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("source_paper_id", sa.UUID(), nullable=False),
        sa.Column("source_paper_version_id", sa.UUID(), nullable=False),
        sa.Column("target_paper_id", sa.UUID(), nullable=True),
        sa.Column("target_paper_version_id", sa.UUID(), nullable=True),
        sa.Column("analysis_id", sa.UUID(), nullable=True),
        sa.Column("comparison_id", sa.UUID(), nullable=True),
        sa.Column("paper_relation_id", sa.UUID(), nullable=True),
        sa.Column("provenance", sa.String(length=32), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("configured_model", sa.String(length=200), nullable=True),
        sa.Column("model_version", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(provenance = 'LLM_INFERRED' AND provider IS NOT NULL AND configured_model IS NOT NULL AND model_version IS NOT NULL AND prompt_version IS NOT NULL AND confidence BETWEEN 0 AND 1 AND char_length(justification) > 0) OR (provenance = 'DETERMINISTICALLY_DERIVED' AND confidence IS NULL AND ((provider IS NULL AND configured_model IS NULL AND model_version IS NULL AND prompt_version IS NULL) OR (provider IS NOT NULL AND configured_model IS NOT NULL AND model_version IS NOT NULL AND prompt_version IS NOT NULL))) OR (provenance NOT IN ('LLM_INFERRED', 'DETERMINISTICALLY_DERIVED') AND provider IS NULL AND configured_model IS NULL AND model_version IS NULL AND prompt_version IS NULL AND confidence IS NULL)",
            name="ck_graph_edges_model_provenance",
        ),
        sa.CheckConstraint(
            "(relation_type IN ('CITES', 'SIMILAR_TO', 'EXTENDS', 'COMPARES_WITH', 'CONTRADICTS', 'IMPROVES_ON') AND target_paper_version_id IS NOT NULL AND target_paper_id IS NOT NULL AND paper_relation_id IS NOT NULL AND comparison_id IS NOT NULL AND analysis_id IS NULL AND source_paper_version_id <> target_paper_version_id) OR (relation_type IN ('ADDRESSES', 'USES_METHOD', 'TARGETS_TASK', 'USES_DATASET', 'EVALUATES_ON') AND target_paper_version_id IS NULL AND target_paper_id IS NULL AND paper_relation_id IS NULL AND ((analysis_id IS NOT NULL AND comparison_id IS NULL) OR (analysis_id IS NULL AND comparison_id IS NOT NULL)))",
            name="ck_graph_edges_owner_shape",
        ),
        sa.CheckConstraint(
            "provenance <> 'HUMAN_VERIFIED' OR verification_status = 'HUMAN_VERIFIED'",
            name="ck_graph_edges_human_verified",
        ),
        sa.CheckConstraint(
            "provenance IN ('METADATA_EXPLICIT', 'TEXT_EXPLICIT', 'DETERMINISTICALLY_DERIVED', 'LLM_INFERRED', 'HUMAN_VERIFIED')",
            name="ck_graph_edges_provenance",
        ),
        sa.CheckConstraint(
            "relation_type IN ('CITES', 'SIMILAR_TO', 'EXTENDS', 'COMPARES_WITH', 'CONTRADICTS', 'IMPROVES_ON', 'ADDRESSES', 'USES_METHOD', 'TARGETS_TASK', 'USES_DATASET', 'EVALUATES_ON')",
            name="ck_graph_edges_relation_type",
        ),
        sa.CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_graph_edges_verification",
        ),
        sa.CheckConstraint(
            "(target_paper_id IS NULL AND target_paper_version_id IS NULL) OR (target_paper_id IS NOT NULL AND target_paper_version_id IS NOT NULL)",
            name="ck_graph_edges_target_paper_pair",
        ),
        sa.CheckConstraint("char_length(justification) > 0", name="ck_graph_edges_justification"),
        sa.CheckConstraint(
            "paper_relation_id IS NULL OR comparison_id IS NOT NULL",
            name="ck_graph_edges_paper_relation_comparison",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_graph_edges_schema_version"),
        sa.CheckConstraint("source_entity_id <> target_entity_id", name="ck_graph_edges_no_self"),
        sa.ForeignKeyConstraint(
            ["analysis_id", "source_paper_id", "source_paper_version_id"],
            ["paper_analyses.id", "paper_analyses.paper_id", "paper_analyses.paper_version_id"],
            name="fk_graph_edges_analysis",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["comparison_id"], ["comparisons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["paper_relation_id", "comparison_id"],
            ["paper_relations.id", "paper_relations.comparison_id"],
            name="fk_graph_edges_paper_relation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["publication_run_id", "topic_id"],
            ["daily_runs.id", "daily_runs.topic_id"],
            name="fk_graph_edges_publication_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_entity_id", "topic_id"],
            ["graph_entities.id", "graph_entities.topic_id"],
            name="fk_graph_edges_source_entity",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_paper_version_id", "source_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_graph_edges_source_paper_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_entity_id", "topic_id"],
            ["graph_entities.id", "graph_entities.topic_id"],
            name="fk_graph_edges_target_entity",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_paper_version_id", "target_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_graph_edges_target_paper_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "topic_id", name="uq_graph_edges_topic_ownership"),
        sa.UniqueConstraint(
            "topic_id",
            "source_entity_id",
            "target_entity_id",
            "relation_type",
            "source_paper_version_id",
            "target_paper_version_id",
            "analysis_id",
            "comparison_id",
            "provenance",
            name="uq_graph_edges_source",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_graph_edges_source", "graph_edges", ["source_entity_id"], unique=False)
    op.create_index("ix_graph_edges_target", "graph_edges", ["target_entity_id"], unique=False)
    op.create_index(
        "ix_graph_edges_publication_run", "graph_edges", ["publication_run_id"], unique=False
    )
    op.create_index(
        "ix_graph_edges_topic_relation", "graph_edges", ["topic_id", "relation_type"], unique=False
    )

    op.create_table(
        "lineage_nodes",
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("graph_entity_id", sa.UUID(), nullable=False),
        sa.Column("paper_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("depth BETWEEN 0 AND 10", name="ck_lineage_nodes_depth"),
        sa.CheckConstraint("position BETWEEN 0 AND 199", name="ck_lineage_nodes_position"),
        sa.CheckConstraint("schema_version > 0", name="ck_lineage_nodes_schema_version"),
        sa.ForeignKeyConstraint(
            ["graph_entity_id", "topic_id"],
            ["graph_entities.id", "graph_entities.topic_id"],
            name="fk_lineage_nodes_entity_topic",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["papers.id"], name="fk_lineage_nodes_paper", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "topic_id"],
            ["lineage_snapshots.id", "lineage_snapshots.topic_id"],
            name="fk_lineage_nodes_snapshot_topic",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "graph_entity_id"),
        sa.UniqueConstraint(
            "snapshot_id", "graph_entity_id", "topic_id", name="uq_lineage_nodes_snapshot_ownership"
        ),
        sa.UniqueConstraint("snapshot_id", "paper_id", name="uq_lineage_nodes_paper"),
        sa.UniqueConstraint("snapshot_id", "position", name="uq_lineage_nodes_position"),
    )

    op.create_table(
        "trend_metrics",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("metric_kind", sa.String(length=24), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("relation_type", sa.String(length=32), nullable=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("current_count", sa.Integer(), nullable=False),
        sa.Column("preceding_count", sa.Integer(), nullable=False),
        sa.Column("absolute_change", sa.Integer(), nullable=False),
        sa.Column("denominator_count", sa.Integer(), nullable=False),
        sa.Column("relative_change", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("growth_status", sa.String(length=32), nullable=False),
        sa.Column("newly_appearing", sa.Boolean(), nullable=False),
        sa.Column("recurring", sa.Boolean(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(growth_status = 'AVAILABLE' AND preceding_count > 0 AND relative_change IS NOT NULL) OR (growth_status = 'ZERO_DENOMINATOR' AND preceding_count = 0 AND relative_change IS NULL) OR (growth_status = 'LIMITED_SAMPLE' AND relative_change IS NULL)",
            name="ck_trend_metrics_growth",
        ),
        sa.CheckConstraint(
            "(metric_kind = 'PAPER_VOLUME' AND entity_id IS NULL AND entity_type IS NULL AND relation_type IS NULL) OR (metric_kind = 'ENTITY' AND entity_id IS NOT NULL AND entity_type IN ('RESEARCH_PROBLEM', 'METHOD', 'TASK', 'DATASET', 'BENCHMARK') AND relation_type IS NULL) OR (metric_kind = 'RELATION' AND entity_id IS NULL AND entity_type IS NULL AND relation_type IN ('CITES', 'SIMILAR_TO', 'EXTENDS', 'COMPARES_WITH', 'CONTRADICTS', 'IMPROVES_ON', 'ADDRESSES', 'USES_METHOD', 'TARGETS_TASK', 'USES_DATASET', 'EVALUATES_ON'))",
            name="ck_trend_metrics_dimension",
        ),
        sa.CheckConstraint(
            "growth_status IN ('AVAILABLE', 'ZERO_DENOMINATOR', 'LIMITED_SAMPLE')",
            name="ck_trend_metrics_growth_status",
        ),
        sa.CheckConstraint(
            "metric_kind IN ('PAPER_VOLUME', 'ENTITY', 'RELATION')", name="ck_trend_metrics_kind"
        ),
        sa.CheckConstraint(
            "NOT (newly_appearing AND recurring) AND (NOT newly_appearing OR (current_count > 0 AND preceding_count = 0)) AND (NOT recurring OR (current_count > 0 AND preceding_count > 0))",
            name="ck_trend_metrics_lifecycle",
        ),
        sa.CheckConstraint(
            "current_count >= 0 AND preceding_count >= 0 AND absolute_change = current_count - preceding_count AND denominator_count = preceding_count",
            name="ck_trend_metrics_counts",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_trend_metrics_schema_version"),
        sa.ForeignKeyConstraint(
            ["entity_id", "topic_id"],
            ["graph_entities.id", "graph_entities.topic_id"],
            name="fk_trend_metrics_entity_topic",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "topic_id"],
            ["trend_snapshots.id", "trend_snapshots.topic_id"],
            name="fk_trend_metrics_snapshot_topic",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "snapshot_id", name="uq_trend_metrics_snapshot_ownership"),
        sa.UniqueConstraint(
            "snapshot_id",
            "metric_kind",
            "entity_id",
            "relation_type",
            name="uq_trend_metrics_dimension",
            postgresql_nulls_not_distinct=True,
        ),
    )

    op.create_table(
        "trend_representative_papers",
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("paper_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "position BETWEEN 0 AND 19", name="ck_trend_representative_papers_position"
        ),
        sa.CheckConstraint(
            "schema_version > 0", name="ck_trend_representative_papers_schema_version"
        ),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "topic_id"],
            ["trend_snapshots.id", "trend_snapshots.topic_id"],
            name="fk_trend_representative_papers_snapshot",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "paper_id"),
        sa.UniqueConstraint(
            "snapshot_id", "position", name="uq_trend_representative_papers_position"
        ),
    )

    op.create_table(
        "report_sections",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(kind = 'OVERVIEW' AND position = 0) OR (kind = 'TRENDS' AND position = 1) OR (kind = 'COMPARISONS' AND position = 2) OR (kind = 'LINEAGE' AND position = 3) OR (kind = 'LIMITATIONS' AND position = 4)",
            name="ck_report_sections_canonical_order",
        ),
        sa.CheckConstraint(
            "kind IN ('OVERVIEW', 'TRENDS', 'COMPARISONS', 'LINEAGE', 'LIMITATIONS')",
            name="ck_report_sections_kind",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_report_sections_schema_version"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "report_id", name="uq_report_sections_ownership"),
        sa.UniqueConstraint("report_id", "kind", name="uq_report_sections_kind"),
        sa.UniqueConstraint("report_id", "position", name="uq_report_sections_position"),
    )

    op.create_table(
        "report_paper_highlights",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("paper_id", sa.UUID(), nullable=False),
        sa.Column("paper_version_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "position BETWEEN 0 AND 199", name="ck_report_paper_highlights_position"
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_report_paper_highlights_schema_version"),
        sa.ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_report_paper_highlights_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "report_id", name="uq_report_paper_highlights_ownership"),
        sa.UniqueConstraint(
            "report_id", "paper_version_id", name="uq_report_paper_highlights_version"
        ),
        sa.UniqueConstraint("report_id", "position", name="uq_report_paper_highlights_position"),
    )

    op.create_table(
        "report_entity_highlights",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("graph_entity_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("distinct_paper_count", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entity_type IN ('PAPER', 'RESEARCH_PROBLEM', 'METHOD', 'TASK', 'DATASET', 'BENCHMARK')",
            name="ck_report_entity_highlights_type",
        ),
        sa.CheckConstraint(
            "distinct_paper_count > 0",
            name="ck_report_entity_highlights_distinct_papers",
        ),
        sa.CheckConstraint(
            "position BETWEEN 0 AND 199", name="ck_report_entity_highlights_position"
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_report_entity_highlights_schema_version"),
        sa.ForeignKeyConstraint(
            ["graph_entity_id", "topic_id"],
            ["graph_entities.id", "graph_entities.topic_id"],
            name="fk_report_entity_highlights_entity_topic",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["report_id", "topic_id"],
            ["reports.id", "reports.topic_id"],
            name="fk_report_entity_highlights_report_topic",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "report_id", name="uq_report_entity_highlights_ownership"),
        sa.UniqueConstraint(
            "report_id", "graph_entity_id", name="uq_report_entity_highlights_entity"
        ),
        sa.UniqueConstraint("report_id", "position", name="uq_report_entity_highlights_position"),
    )

    op.create_table(
        "report_comparison_highlights",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("comparison_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("comparability_status", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "comparability_status IN ('DIRECTLY_COMPARABLE', 'PARTIALLY_COMPARABLE', 'NOT_DIRECTLY_COMPARABLE', 'INSUFFICIENT_EVIDENCE')",
            name="ck_report_comparison_highlights_comparability",
        ),
        sa.CheckConstraint(
            "position BETWEEN 0 AND 199", name="ck_report_comparison_highlights_position"
        ),
        sa.CheckConstraint(
            "schema_version > 0", name="ck_report_comparison_highlights_schema_version"
        ),
        sa.ForeignKeyConstraint(["comparison_id"], ["comparisons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "report_id", name="uq_report_comparison_highlights_ownership"),
        sa.UniqueConstraint(
            "report_id", "comparison_id", name="uq_report_comparison_highlights_comparison"
        ),
        sa.UniqueConstraint(
            "report_id", "position", name="uq_report_comparison_highlights_position"
        ),
    )

    op.create_table(
        "report_trend_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position BETWEEN 0 AND 2", name="ck_report_trend_links_position"),
        sa.CheckConstraint("schema_version > 0", name="ck_report_trend_links_schema_version"),
        sa.ForeignKeyConstraint(
            ["report_id", "topic_id"],
            ["reports.id", "reports.topic_id"],
            name="fk_report_trend_links_report_topic",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "topic_id"],
            ["trend_snapshots.id", "trend_snapshots.topic_id"],
            name="fk_report_trend_links_snapshot_topic",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "position", name="uq_report_trend_links_position"),
        sa.UniqueConstraint("report_id", "snapshot_id", name="uq_report_trend_links_snapshot"),
    )

    op.create_table(
        "report_lineage_highlights",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("lineage_snapshot_id", sa.UUID(), nullable=False),
        sa.Column("root_paper_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("uncertain", sa.Boolean(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "position BETWEEN 0 AND 199", name="ck_report_lineage_highlights_position"
        ),
        sa.CheckConstraint(
            "schema_version > 0", name="ck_report_lineage_highlights_schema_version"
        ),
        sa.ForeignKeyConstraint(
            ["lineage_snapshot_id", "topic_id", "root_paper_id"],
            [
                "lineage_snapshots.id",
                "lineage_snapshots.topic_id",
                "lineage_snapshots.root_paper_id",
            ],
            name="fk_report_lineage_highlights_snapshot_topic",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["report_id", "topic_id"],
            ["reports.id", "reports.topic_id"],
            name="fk_report_lineage_highlights_report_topic",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "report_id", name="uq_report_lineage_highlights_ownership"),
        sa.UniqueConstraint(
            "report_id", "lineage_snapshot_id", name="uq_report_lineage_highlights_snapshot"
        ),
        sa.UniqueConstraint("report_id", "position", name="uq_report_lineage_highlights_position"),
    )

    op.create_table(
        "graph_mention_evidence_links",
        sa.Column("mention_id", sa.UUID(), nullable=False),
        sa.Column("evidence_id", sa.UUID(), nullable=False),
        sa.Column("mention_paper_id", sa.UUID(), nullable=False),
        sa.Column("mention_paper_version_id", sa.UUID(), nullable=False),
        sa.Column("evidence_paper_id", sa.UUID(), nullable=False),
        sa.Column("evidence_paper_version_id", sa.UUID(), nullable=False),
        sa.Column("evidence_analysis_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "mention_paper_id = evidence_paper_id AND mention_paper_version_id = evidence_paper_version_id",
            name="ck_graph_mention_evidence_links_paper",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "evidence_analysis_id"],
            ["evidence.id", "evidence.analysis_id"],
            name="fk_graph_mention_evidence_links_analysis",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "evidence_paper_id", "evidence_paper_version_id"],
            ["evidence.id", "evidence.paper_id", "evidence.paper_version_id"],
            name="fk_graph_mention_evidence_links_evidence",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["mention_id", "mention_paper_id", "mention_paper_version_id"],
            [
                "graph_entity_mentions.id",
                "graph_entity_mentions.paper_id",
                "graph_entity_mentions.paper_version_id",
            ],
            name="fk_graph_mention_evidence_links_mention",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("mention_id", "evidence_id"),
    )

    op.create_table(
        "graph_edge_evidence_links",
        sa.Column("graph_edge_id", sa.UUID(), nullable=False),
        sa.Column("evidence_id", sa.UUID(), nullable=False),
        sa.Column("evidence_role", sa.String(length=16), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("evidence_paper_id", sa.UUID(), nullable=False),
        sa.Column("evidence_paper_version_id", sa.UUID(), nullable=False),
        sa.Column("evidence_analysis_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "evidence_role IN ('SOURCE', 'TARGET', 'RELATION')",
            name="ck_graph_edge_evidence_links_role",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "evidence_analysis_id"],
            ["evidence.id", "evidence.analysis_id"],
            name="fk_graph_edge_evidence_links_analysis",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "evidence_paper_id", "evidence_paper_version_id"],
            ["evidence.id", "evidence.paper_id", "evidence.paper_version_id"],
            name="fk_graph_edge_evidence_links_evidence",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["graph_edge_id", "topic_id"],
            ["graph_edges.id", "graph_edges.topic_id"],
            name="fk_graph_edge_evidence_links_edge",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("graph_edge_id", "evidence_id", "evidence_role"),
    )

    op.create_table(
        "lineage_edges",
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("graph_edge_id", sa.UUID(), nullable=False),
        sa.Column("source_entity_id", sa.UUID(), nullable=False),
        sa.Column("target_entity_id", sa.UUID(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("provenance", sa.String(length=32), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("uncertain", sa.Boolean(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "provenance IN ('METADATA_EXPLICIT', 'TEXT_EXPLICIT', 'DETERMINISTICALLY_DERIVED', 'LLM_INFERRED', 'HUMAN_VERIFIED')",
            name="ck_lineage_edges_provenance",
        ),
        sa.CheckConstraint(
            "relation_type IN ('CITES', 'EXTENDS', 'IMPROVES_ON', 'COMPARES_WITH', 'CONTRADICTS')",
            name="ck_lineage_edges_relation_type",
        ),
        sa.CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_lineage_edges_verification",
        ),
        sa.CheckConstraint("position BETWEEN 0 AND 999", name="ck_lineage_edges_position"),
        sa.CheckConstraint("schema_version > 0", name="ck_lineage_edges_schema_version"),
        sa.CheckConstraint(
            "source_entity_id <> target_entity_id", name="ck_lineage_edges_distinct_nodes"
        ),
        sa.ForeignKeyConstraint(
            ["graph_edge_id", "topic_id"],
            ["graph_edges.id", "graph_edges.topic_id"],
            name="fk_lineage_edges_graph_edge_topic",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "source_entity_id", "topic_id"],
            [
                "lineage_nodes.snapshot_id",
                "lineage_nodes.graph_entity_id",
                "lineage_nodes.topic_id",
            ],
            name="fk_lineage_edges_source_node",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "target_entity_id", "topic_id"],
            [
                "lineage_nodes.snapshot_id",
                "lineage_nodes.graph_entity_id",
                "lineage_nodes.topic_id",
            ],
            name="fk_lineage_edges_target_node",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "topic_id"],
            ["lineage_snapshots.id", "lineage_snapshots.topic_id"],
            name="fk_lineage_edges_snapshot_topic",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "graph_edge_id"),
        sa.UniqueConstraint("snapshot_id", "position", name="uq_lineage_edges_position"),
    )

    op.create_table(
        "report_evidence_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("evidence_id", sa.UUID(), nullable=False),
        sa.Column("evidence_paper_id", sa.UUID(), nullable=False),
        sa.Column("evidence_paper_version_id", sa.UUID(), nullable=False),
        sa.Column("evidence_analysis_id", sa.UUID(), nullable=False),
        sa.Column("context_type", sa.String(length=32), nullable=False),
        sa.Column("report_section_id", sa.UUID(), nullable=True),
        sa.Column("paper_highlight_id", sa.UUID(), nullable=True),
        sa.Column("comparison_highlight_id", sa.UUID(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(context_type = 'REPORT' AND report_section_id IS NULL AND paper_highlight_id IS NULL AND comparison_highlight_id IS NULL) OR (context_type = 'SECTION' AND report_section_id IS NOT NULL AND paper_highlight_id IS NULL AND comparison_highlight_id IS NULL) OR (context_type = 'PAPER_HIGHLIGHT' AND report_section_id IS NULL AND paper_highlight_id IS NOT NULL AND comparison_highlight_id IS NULL) OR (context_type = 'COMPARISON_HIGHLIGHT' AND report_section_id IS NULL AND paper_highlight_id IS NULL AND comparison_highlight_id IS NOT NULL)",
            name="ck_report_evidence_links_context",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_report_evidence_links_schema_version"),
        sa.ForeignKeyConstraint(
            ["comparison_highlight_id", "report_id"],
            ["report_comparison_highlights.id", "report_comparison_highlights.report_id"],
            name="fk_report_evidence_links_comparison_highlight",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "evidence_analysis_id"],
            ["evidence.id", "evidence.analysis_id"],
            name="fk_report_evidence_links_analysis",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "evidence_paper_id", "evidence_paper_version_id"],
            ["evidence.id", "evidence.paper_id", "evidence.paper_version_id"],
            name="fk_report_evidence_links_evidence",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["paper_highlight_id", "report_id"],
            ["report_paper_highlights.id", "report_paper_highlights.report_id"],
            name="fk_report_evidence_links_paper_highlight",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["report_section_id", "report_id"],
            ["report_sections.id", "report_sections.report_id"],
            name="fk_report_evidence_links_section",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "report_id",
            "evidence_id",
            "context_type",
            "report_section_id",
            "paper_highlight_id",
            "comparison_highlight_id",
            name="uq_report_evidence_links_context",
            postgresql_nulls_not_distinct=True,
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_m4_data = bool(
        connection.scalar(
            sa.text(
                "SELECT EXISTS ("
                "SELECT 1 FROM graph_entities "
                "UNION ALL SELECT 1 FROM product_run_paper_inputs "
                "UNION ALL SELECT 1 FROM product_run_comparison_inputs "
                "UNION ALL SELECT 1 FROM graph_entity_mentions "
                "UNION ALL SELECT 1 FROM graph_mention_evidence_links "
                "UNION ALL SELECT 1 FROM graph_edges "
                "UNION ALL SELECT 1 FROM graph_edge_evidence_links "
                "UNION ALL SELECT 1 FROM trend_snapshots "
                "UNION ALL SELECT 1 FROM trend_metrics "
                "UNION ALL SELECT 1 FROM trend_representative_papers "
                "UNION ALL SELECT 1 FROM lineage_snapshots "
                "UNION ALL SELECT 1 FROM lineage_nodes "
                "UNION ALL SELECT 1 FROM lineage_edges "
                "UNION ALL SELECT 1 FROM report_sections "
                "UNION ALL SELECT 1 FROM report_paper_highlights "
                "UNION ALL SELECT 1 FROM report_entity_highlights "
                "UNION ALL SELECT 1 FROM report_comparison_highlights "
                "UNION ALL SELECT 1 FROM report_trend_links "
                "UNION ALL SELECT 1 FROM report_lineage_highlights "
                "UNION ALL SELECT 1 FROM report_evidence_links "
                "UNION ALL SELECT 1 FROM reports "
                "WHERE report_type <> 'ANALYSIS' OR cardinality(limitations) > 0 "
                "OR cardinality(missing_sections) > 0 OR provider IS NOT NULL "
                "OR graph_entity_count <> 0 OR graph_edge_count <> 0 "
                "OR new_graph_entity_count <> 0 OR inferred_graph_edge_count <> 0 "
                "UNION ALL SELECT 1 FROM daily_runs "
                "WHERE operation = 'PRODUCT_PUBLICATION' "
                "UNION ALL SELECT 1 FROM run_items "
                "WHERE stage IN ('TREND_SNAPSHOTS_GENERATED', 'REPORT_GENERATED') "
                "OR failed_stage IN ('TREND_SNAPSHOTS_GENERATED', 'REPORT_GENERATED')"
                ")"
            )
        )
    )
    allow_data_loss = (
        context.get_x_argument(as_dictionary=True).get("allow_m4_data_loss", "false").lower()
        == "true"
    )
    if has_m4_data and not allow_data_loss:
        raise RuntimeError(
            "M4 downgrade refused because graph, trend, lineage, or product-report data "
            "exists. Create and verify a PostgreSQL backup, then explicitly rerun with "
            "'-x allow_m4_data_loss=true' only when destructive rollback is intended."
        )

    op.drop_table("report_evidence_links")
    op.drop_table("product_run_comparison_inputs")
    op.drop_table("product_run_paper_inputs")
    op.drop_table("lineage_edges")
    op.drop_table("graph_edge_evidence_links")
    op.drop_table("graph_mention_evidence_links")
    op.drop_table("report_lineage_highlights")
    op.drop_table("report_trend_links")
    op.drop_table("report_comparison_highlights")
    op.drop_table("report_entity_highlights")
    op.drop_table("report_paper_highlights")
    op.drop_table("report_sections")
    op.drop_table("trend_representative_papers")
    op.drop_table("trend_metrics")
    op.drop_table("lineage_nodes")
    op.drop_table("graph_edges")
    op.drop_table("graph_entity_mentions")
    op.drop_table("trend_snapshots")
    op.drop_table("lineage_snapshots")
    op.drop_table("graph_entities")
    op.drop_constraint("uq_comparisons_product_source_ownership", "comparisons", type_="unique")

    op.execute("DELETE FROM reports WHERE report_type <> 'ANALYSIS'")
    for name in (
        "ck_reports_verification",
        "ck_reports_usage",
        "ck_reports_model_provenance",
        "ck_reports_narrative_mode",
        "ck_reports_graph_counts",
        "ck_reports_counts",
        "ck_reports_calendar_period",
        "ck_reports_run_ownership",
        "ck_reports_type_allowed",
    ):
        op.drop_constraint(name, "reports", type_="check")
    op.drop_constraint("fk_reports_run_topic", "reports", type_="foreignkey")
    op.create_foreign_key(
        "reports_run_id_fkey",
        "reports",
        "daily_runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_reports_topic_ownership", "reports", type_="unique")
    op.drop_index("uq_reports_aggregate_period", table_name="reports")
    op.alter_column("reports", "run_id", existing_type=sa.UUID(), nullable=False)
    for name in (
        "verification_status",
        "estimated_cost_usd",
        "duration_ms",
        "call_count",
        "total_tokens",
        "completion_tokens",
        "prompt_tokens",
        "prompt_version",
        "model_version",
        "configured_model",
        "provider",
        "narrative_mode",
        "missing_sections",
        "limitations",
        "inferred_graph_edge_count",
        "new_graph_entity_count",
        "graph_edge_count",
        "graph_entity_count",
        "failed_count",
        "completed_count",
        "processed_count",
        "selected_count",
        "retrieved_count",
        "period_end",
        "period_start",
        "report_type",
    ):
        op.drop_column("reports", name)
    op.create_unique_constraint("uq_reports_ownership", "reports", ["id", "run_id"])

    op.execute("DELETE FROM daily_runs WHERE operation = 'PRODUCT_PUBLICATION'")
    op.drop_constraint("ck_daily_runs_source_run", "daily_runs", type_="check")
    op.drop_constraint("ck_daily_runs_failed_lte_selected", "daily_runs", type_="check")
    op.drop_constraint("ck_daily_runs_operation_fields", "daily_runs", type_="check")
    op.drop_constraint("ck_daily_runs_operation_allowed", "daily_runs", type_="check")
    op.drop_constraint("ck_run_items_stage_allowed", "run_items", type_="check")
    op.drop_constraint("fk_daily_runs_source_run", "daily_runs", type_="foreignkey")
    op.drop_constraint("uq_run_items_run_paper_version", "run_items", type_="unique")
    op.drop_constraint("uq_daily_runs_product_input_ownership", "daily_runs", type_="unique")
    op.drop_constraint("uq_daily_runs_topic_date_ownership", "daily_runs", type_="unique")
    op.drop_constraint("uq_daily_runs_topic_ownership", "daily_runs", type_="unique")
    op.drop_index("uq_daily_runs_product_source_date", table_name="daily_runs")
    op.drop_column("daily_runs", "source_run_id")
    op.create_check_constraint(
        "ck_daily_runs_operation_allowed",
        "daily_runs",
        "operation IN ('ARXIV_INGESTION', 'STRUCTURED_ANALYSIS')",
    )
    op.create_check_constraint(
        "ck_daily_runs_operation_fields",
        "daily_runs",
        "(operation = 'ARXIV_INGESTION' AND cursor_from IS NOT NULL "
        "AND cursor_to IS NOT NULL AND cursor_from <= cursor_to "
        "AND analysis_scope IS NULL AND selected_count = 0 AND completed_count = 0) OR "
        "(operation = 'STRUCTURED_ANALYSIS' AND cursor_from IS NULL "
        "AND cursor_to IS NULL AND analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT'))",
    )
    op.create_check_constraint(
        "ck_run_items_stage_allowed",
        "run_items",
        "stage IN ('DISCOVERED', 'NORMALIZED', 'ENRICHED', 'RELEVANCE_SCORED', "
        "'SELECTED', 'PDF_DOWNLOADED', 'PARSED', 'ANALYZED', 'EVIDENCE_EXTRACTED', "
        "'PRIOR_WORK_RETRIEVED', 'COMPARED', 'GRAPH_UPDATED', 'PUBLISHED')",
    )
