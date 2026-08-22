"""Allow additive same-date pipeline reprocessing.

Revision ID: 0006_topic_reprocessing
Revises: 0005_m5_pipeline_provenance
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_topic_reprocessing"
down_revision: str | None = "0005_m5_pipeline_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "paper_analyses",
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.drop_constraint("uq_paper_analyses_provenance", "paper_analyses", type_="unique")
    op.create_unique_constraint(
        "uq_paper_analyses_provenance",
        "paper_analyses",
        [
            "paper_version_id",
            "analysis_scope",
            "parsed_paper_id",
            "provider",
            "configured_model",
            "model_version",
            "prompt_version",
            "revision_id",
        ],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_constraint("ck_daily_runs_pipeline_selection_limit", "daily_runs", type_="check")
    op.drop_constraint(
        "ck_daily_runs_pipeline_execution_mode_allowed",
        "daily_runs",
        type_="check",
    )
    op.drop_constraint("ck_pipeline_executions_key", "pipeline_executions", type_="check")
    op.drop_constraint("ck_pipeline_executions_mode", "pipeline_executions", type_="check")

    op.create_check_constraint(
        "ck_pipeline_executions_mode",
        "pipeline_executions",
        "execution_mode IN ('NORMAL', 'REPROCESS', 'SMOKE')",
    )
    op.create_check_constraint(
        "ck_pipeline_executions_key",
        "pipeline_executions",
        "(execution_mode = 'NORMAL' AND execution_key = 'canonical') OR "
        "(execution_mode IN ('REPROCESS', 'SMOKE') AND execution_key <> 'canonical' "
        "AND execution_key = btrim(execution_key) "
        "AND length(execution_key) BETWEEN 1 AND 200)",
    )
    op.create_check_constraint(
        "ck_daily_runs_pipeline_execution_mode_allowed",
        "daily_runs",
        "pipeline_execution_mode IN ('STANDALONE', 'NORMAL', 'REPROCESS', 'SMOKE')",
    )
    op.create_check_constraint(
        "ck_daily_runs_pipeline_selection_limit",
        "daily_runs",
        "(pipeline_execution_mode = 'STANDALONE' AND pipeline_execution_id IS NULL "
        "AND pipeline_selection_limit IS NULL) OR "
        "(pipeline_execution_mode IN ('NORMAL', 'REPROCESS') "
        "AND pipeline_execution_id IS NOT NULL "
        "AND pipeline_selection_limit BETWEEN 1 AND 200) OR "
        "(pipeline_execution_mode = 'SMOKE' AND pipeline_execution_id IS NOT NULL "
        "AND pipeline_selection_limit BETWEEN 1 AND 5)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_daily_runs_pipeline_selection_limit", "daily_runs", type_="check")
    op.drop_constraint(
        "ck_daily_runs_pipeline_execution_mode_allowed",
        "daily_runs",
        type_="check",
    )
    op.drop_constraint("ck_pipeline_executions_key", "pipeline_executions", type_="check")
    op.drop_constraint("ck_pipeline_executions_mode", "pipeline_executions", type_="check")

    op.create_check_constraint(
        "ck_pipeline_executions_mode",
        "pipeline_executions",
        "execution_mode IN ('NORMAL', 'SMOKE')",
    )
    op.create_check_constraint(
        "ck_pipeline_executions_key",
        "pipeline_executions",
        "(execution_mode = 'NORMAL' AND execution_key = 'canonical') OR "
        "(execution_mode = 'SMOKE' AND execution_key <> 'canonical' "
        "AND execution_key = btrim(execution_key) "
        "AND length(execution_key) BETWEEN 1 AND 200)",
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
    op.drop_constraint("uq_paper_analyses_provenance", "paper_analyses", type_="unique")
    op.create_unique_constraint(
        "uq_paper_analyses_provenance",
        "paper_analyses",
        [
            "paper_version_id",
            "analysis_scope",
            "parsed_paper_id",
            "provider",
            "configured_model",
            "model_version",
            "prompt_version",
        ],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_column("paper_analyses", "revision_id")
