"""Persist preselected full-pipeline execution provenance.

Revision ID: 0005_m5_pipeline_provenance
Revises: 0004_m4_graph_trends_reports
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0005_m5_pipeline_provenance"
down_revision: str | None = "0004_m4_graph_trends_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipeline_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("logical_date", sa.Date(), nullable=False),
        sa.Column("execution_mode", sa.String(length=16), nullable=False),
        sa.Column("execution_key", sa.String(length=200), nullable=False),
        sa.Column("analysis_scope", sa.String(length=32), nullable=False),
        sa.Column("selection_limit", sa.Integer(), nullable=False),
        sa.Column("execution_contract", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_detail", sa.String(length=1000), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "execution_mode IN ('NORMAL', 'SMOKE')",
            name="ck_pipeline_executions_mode",
        ),
        sa.CheckConstraint(
            "(execution_mode = 'NORMAL' AND execution_key = 'canonical') OR "
            "(execution_mode = 'SMOKE' AND execution_key <> 'canonical' "
            "AND execution_key = btrim(execution_key) "
            "AND length(execution_key) BETWEEN 1 AND 200)",
            name="ck_pipeline_executions_key",
        ),
        sa.CheckConstraint(
            "analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT')",
            name="ck_pipeline_executions_analysis_scope",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(execution_contract) = 'object'",
            name="ck_pipeline_executions_contract",
        ),
        sa.CheckConstraint(
            "selection_limit BETWEEN 1 AND 200 "
            "AND (execution_mode <> 'SMOKE' OR selection_limit <= 5)",
            name="ck_pipeline_executions_selection_limit",
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE', 'PARTIAL', 'FAILED')",
            name="ck_pipeline_executions_status",
        ),
        sa.CheckConstraint(
            "deadline_at > started_at",
            name="ck_pipeline_executions_deadline",
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL)",
            name="ck_pipeline_executions_completion",
        ),
        sa.CheckConstraint(
            "(status = 'FAILED' AND error_code IS NOT NULL) OR "
            "(status <> 'FAILED' AND error_code IS NULL AND error_detail IS NULL)",
            name="ck_pipeline_executions_failure",
        ),
        sa.CheckConstraint(
            "schema_version > 0",
            name="ck_pipeline_executions_schema_version",
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "topic_id",
            "logical_date",
            "execution_mode",
            "execution_key",
            name="uq_pipeline_executions_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "topic_id",
            "logical_date",
            "execution_mode",
            name="uq_pipeline_executions_child_ownership",
        ),
        sa.UniqueConstraint(
            "id",
            "topic_id",
            name="uq_pipeline_executions_topic_ownership",
        ),
    )
    op.create_index(
        "ix_pipeline_executions_topic_date",
        "pipeline_executions",
        ["topic_id", "logical_date"],
    )
    op.create_index(
        "ix_pipeline_executions_status_deadline",
        "pipeline_executions",
        ["status", "deadline_at"],
    )
    op.add_column(
        "daily_runs",
        sa.Column(
            "pipeline_execution_mode",
            sa.String(length=16),
            server_default="STANDALONE",
            nullable=False,
        ),
    )
    op.drop_constraint(
        "ck_daily_runs_operation_fields",
        "daily_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_daily_runs_operation_allowed",
        "daily_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_daily_runs_operation_allowed",
        "daily_runs",
        "operation IN ('ARXIV_INGESTION', 'STRUCTURED_ANALYSIS', "
        "'HISTORICAL_ANALYSIS', 'PRODUCT_PUBLICATION')",
    )
    op.create_check_constraint(
        "ck_daily_runs_operation_fields",
        "daily_runs",
        "(operation = 'ARXIV_INGESTION' AND cursor_from IS NOT NULL "
        "AND cursor_to IS NOT NULL AND cursor_from <= cursor_to "
        "AND analysis_scope IS NULL AND selected_count = 0 AND completed_count = 0) OR "
        "(operation IN ('STRUCTURED_ANALYSIS', 'HISTORICAL_ANALYSIS') "
        "AND cursor_from IS NULL AND cursor_to IS NULL "
        "AND analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT')) OR "
        "(operation = 'PRODUCT_PUBLICATION' AND cursor_from IS NULL "
        "AND cursor_to IS NULL AND analysis_scope IS NULL "
        "AND discovered_count = 0 AND normalized_count = 0)",
    )
    op.add_column(
        "daily_runs",
        sa.Column("pipeline_selection_limit", sa.Integer(), nullable=True),
    )
    op.add_column(
        "daily_runs",
        sa.Column("pipeline_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.drop_constraint(
        "uq_daily_runs_topic_date_operation",
        "daily_runs",
        type_="unique",
    )
    op.create_check_constraint(
        "ck_daily_runs_pipeline_execution_mode_allowed",
        "daily_runs",
        "pipeline_execution_mode IN ('STANDALONE', 'NORMAL', 'SMOKE')",
    )
    op.create_check_constraint(
        "ck_daily_runs_pipeline_selection_limit",
        "daily_runs",
        "(pipeline_execution_mode = 'STANDALONE' AND pipeline_execution_id IS NULL "
        "AND pipeline_selection_limit IS NULL) OR "
        "(pipeline_execution_mode = 'NORMAL' AND pipeline_execution_id IS NOT NULL "
        "AND pipeline_selection_limit BETWEEN 1 AND 200) OR "
        "(pipeline_execution_mode = 'SMOKE' AND pipeline_execution_id IS NOT NULL "
        "AND pipeline_selection_limit BETWEEN 1 AND 5)",
    )
    op.create_foreign_key(
        "fk_daily_runs_pipeline_execution",
        "daily_runs",
        "pipeline_executions",
        [
            "pipeline_execution_id",
            "topic_id",
            "logical_date",
            "pipeline_execution_mode",
        ],
        ["id", "topic_id", "logical_date", "execution_mode"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_daily_runs_standalone_topic_date_operation",
        "daily_runs",
        ["topic_id", "logical_date", "operation"],
        unique=True,
        postgresql_where=sa.text("pipeline_execution_id IS NULL"),
    )
    op.create_index(
        "uq_daily_runs_pipeline_execution_operation",
        "daily_runs",
        ["pipeline_execution_id", "operation"],
        unique=True,
        postgresql_where=sa.text("pipeline_execution_id IS NOT NULL"),
    )
    op.add_column(
        "search_sessions",
        sa.Column("pipeline_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_search_sessions_pipeline_execution",
        "search_sessions",
        "pipeline_executions",
        ["pipeline_execution_id", "topic_id"],
        ["id", "topic_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_search_sessions_pipeline_execution",
        "search_sessions",
        ["pipeline_execution_id"],
    )
    op.create_index(
        "uq_search_sessions_pipeline_execution_source_analysis",
        "search_sessions",
        ["pipeline_execution_id", "source_analysis_id"],
        unique=True,
        postgresql_where=sa.text("pipeline_execution_id IS NOT NULL"),
    )
    op.add_column(
        "search_candidates",
        sa.Column("comparison_target_decision", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "search_candidates",
        sa.Column("comparison_target_reason", sa.String(length=1000), nullable=True),
    )
    op.create_check_constraint(
        "ck_search_candidates_comparison_target",
        "search_candidates",
        "(comparison_target_decision IS NULL AND comparison_target_reason IS NULL) OR "
        "(comparison_target_decision IN ('TARGET', 'NOT_TARGET', 'INELIGIBLE') "
        "AND comparison_target_reason IS NOT NULL)",
    )
    for table_name in (
        "graph_entity_mentions",
        "graph_edges",
        "trend_snapshots",
        "lineage_snapshots",
    ):
        op.add_column(
            table_name,
            sa.Column(
                "pipeline_execution_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
        )
        op.create_foreign_key(
            f"fk_{table_name}_pipeline_execution",
            table_name,
            "pipeline_executions",
            ["pipeline_execution_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.drop_constraint("uq_graph_entity_mentions_source", "graph_entity_mentions", type_="unique")
    op.create_unique_constraint(
        "uq_graph_entity_mentions_source",
        "graph_entity_mentions",
        [
            "entity_id",
            "paper_version_id",
            "analysis_id",
            "comparison_id",
            "observed_label",
            "provenance",
            "pipeline_execution_id",
        ],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_constraint("uq_graph_edges_source", "graph_edges", type_="unique")
    op.create_unique_constraint(
        "uq_graph_edges_source",
        "graph_edges",
        [
            "topic_id",
            "source_entity_id",
            "target_entity_id",
            "relation_type",
            "source_paper_version_id",
            "target_paper_version_id",
            "analysis_id",
            "comparison_id",
            "provenance",
            "pipeline_execution_id",
        ],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_constraint("uq_trend_snapshots_identity", "trend_snapshots", type_="unique")
    op.create_unique_constraint(
        "uq_trend_snapshots_identity",
        "trend_snapshots",
        [
            "topic_id",
            "as_of_date",
            "window",
            "aggregation_version",
            "pipeline_execution_id",
        ],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_constraint("uq_lineage_snapshots_identity", "lineage_snapshots", type_="unique")
    op.create_unique_constraint(
        "uq_lineage_snapshots_identity",
        "lineage_snapshots",
        [
            "topic_id",
            "root_paper_id",
            "as_of_date",
            "max_depth",
            "max_nodes",
            "max_edges",
            "permitted_relation_types",
            "lineage_version",
            "pipeline_execution_id",
        ],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_pipeline_provenance = bool(
        connection.scalar(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM pipeline_executions) OR EXISTS ("
                "SELECT 1 FROM daily_runs "
                "WHERE operation = 'HISTORICAL_ANALYSIS' "
                "OR pipeline_execution_mode <> 'STANDALONE' "
                "OR pipeline_selection_limit IS NOT NULL) OR EXISTS ("
                "SELECT 1 FROM search_candidates "
                "WHERE comparison_target_decision IS NOT NULL "
                "OR comparison_target_reason IS NOT NULL)"
            )
        )
    )
    allow_data_loss = (
        context.get_x_argument(as_dictionary=True).get("allow_m5_data_loss", "false").lower()
        == "true"
    )
    if has_pipeline_provenance and not allow_data_loss:
        raise RuntimeError(
            "M5 downgrade refused because persisted pipeline provenance or historical "
            "analysis exists. "
            "Create and verify a PostgreSQL backup, then explicitly rerun with "
            "'-x allow_m5_data_loss=true' only when destructive rollback is intended."
        )
    if allow_data_loss:
        connection.execute(
            sa.text(
                "DELETE FROM daily_runs WHERE operation = 'PRODUCT_PUBLICATION' "
                "AND pipeline_execution_mode <> 'STANDALONE'"
            )
        )
        connection.execute(
            sa.text("DELETE FROM daily_runs WHERE pipeline_execution_mode <> 'STANDALONE'")
        )

    op.drop_index(
        "uq_search_sessions_pipeline_execution_source_analysis",
        table_name="search_sessions",
    )
    op.drop_index("ix_search_sessions_pipeline_execution", table_name="search_sessions")
    op.drop_constraint(
        "fk_search_sessions_pipeline_execution",
        "search_sessions",
        type_="foreignkey",
    )
    op.drop_column("search_sessions", "pipeline_execution_id")
    op.drop_constraint(
        "ck_search_candidates_comparison_target",
        "search_candidates",
        type_="check",
    )
    op.drop_column("search_candidates", "comparison_target_reason")
    op.drop_column("search_candidates", "comparison_target_decision")
    op.drop_constraint("uq_lineage_snapshots_identity", "lineage_snapshots", type_="unique")
    op.create_unique_constraint(
        "uq_lineage_snapshots_identity",
        "lineage_snapshots",
        [
            "topic_id",
            "root_paper_id",
            "as_of_date",
            "max_depth",
            "max_nodes",
            "max_edges",
            "permitted_relation_types",
            "lineage_version",
        ],
    )
    op.drop_constraint("uq_trend_snapshots_identity", "trend_snapshots", type_="unique")
    op.create_unique_constraint(
        "uq_trend_snapshots_identity",
        "trend_snapshots",
        ["topic_id", "as_of_date", "window", "aggregation_version"],
    )
    op.drop_constraint("uq_graph_edges_source", "graph_edges", type_="unique")
    op.create_unique_constraint(
        "uq_graph_edges_source",
        "graph_edges",
        [
            "topic_id",
            "source_entity_id",
            "target_entity_id",
            "relation_type",
            "source_paper_version_id",
            "target_paper_version_id",
            "analysis_id",
            "comparison_id",
            "provenance",
        ],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_constraint("uq_graph_entity_mentions_source", "graph_entity_mentions", type_="unique")
    op.create_unique_constraint(
        "uq_graph_entity_mentions_source",
        "graph_entity_mentions",
        [
            "entity_id",
            "paper_version_id",
            "analysis_id",
            "comparison_id",
            "observed_label",
            "provenance",
        ],
        postgresql_nulls_not_distinct=True,
    )
    for table_name in (
        "lineage_snapshots",
        "trend_snapshots",
        "graph_edges",
        "graph_entity_mentions",
    ):
        op.drop_constraint(
            f"fk_{table_name}_pipeline_execution",
            table_name,
            type_="foreignkey",
        )
        op.drop_column(table_name, "pipeline_execution_id")
    op.drop_index("uq_daily_runs_pipeline_execution_operation", table_name="daily_runs")
    op.drop_index("uq_daily_runs_standalone_topic_date_operation", table_name="daily_runs")
    op.drop_constraint(
        "fk_daily_runs_pipeline_execution",
        "daily_runs",
        type_="foreignkey",
    )

    op.drop_constraint(
        "ck_daily_runs_pipeline_selection_limit",
        "daily_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_daily_runs_operation_fields",
        "daily_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_daily_runs_operation_allowed",
        "daily_runs",
        type_="check",
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
    op.drop_constraint(
        "ck_daily_runs_pipeline_execution_mode_allowed",
        "daily_runs",
        type_="check",
    )
    op.create_unique_constraint(
        "uq_daily_runs_topic_date_operation",
        "daily_runs",
        ["topic_id", "logical_date", "operation"],
    )
    op.drop_column("daily_runs", "pipeline_execution_id")
    op.drop_column("daily_runs", "pipeline_selection_limit")
    op.drop_column("daily_runs", "pipeline_execution_mode")
    op.drop_index("ix_pipeline_executions_status_deadline", table_name="pipeline_executions")
    op.drop_index("ix_pipeline_executions_topic_date", table_name="pipeline_executions")
    op.drop_table("pipeline_executions")
