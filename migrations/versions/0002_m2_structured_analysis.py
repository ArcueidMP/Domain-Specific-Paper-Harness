"""Add M2 structured analysis, parsed text, evidence, and reports.

Revision ID: 0002_m2_structured_analysis
Revises: 0001_m1_ingestion
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0002_m2_structured_analysis"
down_revision: str | None = "0001_m1_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_topics_representative_count_positive", "topics", type_="check")
    op.create_check_constraint(
        "ck_topics_representative_count_bounded",
        "topics",
        "representative_full_text_count BETWEEN 1 AND 200",
    )
    op.drop_constraint("ck_daily_runs_operation_allowed", "daily_runs", type_="check")
    op.drop_constraint("ck_daily_runs_cursor_order", "daily_runs", type_="check")
    op.add_column(
        "daily_runs",
        sa.Column("selected_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "daily_runs",
        sa.Column("completed_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "daily_runs",
        sa.Column("analysis_scope", sa.String(length=32), nullable=True),
    )
    op.alter_column(
        "daily_runs", "cursor_from", existing_type=sa.DateTime(timezone=True), nullable=True
    )
    op.alter_column(
        "daily_runs", "cursor_to", existing_type=sa.DateTime(timezone=True), nullable=True
    )
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
        "(operation = 'STRUCTURED_ANALYSIS' AND cursor_from IS NULL AND cursor_to IS NULL "
        "AND analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT'))",
    )
    op.create_check_constraint(
        "ck_daily_runs_selected_nonnegative", "daily_runs", "selected_count >= 0"
    )
    op.create_check_constraint(
        "ck_daily_runs_completed_nonnegative", "daily_runs", "completed_count >= 0"
    )
    op.create_check_constraint(
        "ck_daily_runs_completed_lte_selected",
        "daily_runs",
        "completed_count <= selected_count",
    )

    op.create_table(
        "parsed_papers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parser_name", sa.String(length=100), nullable=False),
        sa.Column("parser_version", sa.String(length=100), nullable=False),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("call_count", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schema_version > 0", name="ck_parsed_papers_schema_version_positive"),
        sa.CheckConstraint(
            "call_count BETWEEN 1 AND 4 AND duration_ms BETWEEN 0 AND 1000000",
            name="ck_parsed_papers_usage",
        ),
        sa.ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_parsed_papers_version_paper",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "paper_version_id",
            "parser_name",
            "parser_version",
            name="uq_parsed_papers_version_parser",
        ),
        sa.UniqueConstraint(
            "id", "paper_id", "paper_version_id", name="uq_parsed_papers_ownership"
        ),
    )
    op.create_table(
        "parsed_sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parsed_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_parsed_sections_position_nonnegative"),
        sa.ForeignKeyConstraint(["parsed_paper_id"], ["parsed_papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parsed_paper_id", "position", name="uq_parsed_sections_position"),
        sa.UniqueConstraint("id", "parsed_paper_id", name="uq_parsed_sections_ownership"),
    )
    op.create_table(
        "parsed_passages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parsed_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parsed_section_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.String(length=200), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("coordinates", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_parsed_passages_position_nonnegative"),
        sa.ForeignKeyConstraint(
            ["parsed_section_id", "parsed_paper_id"],
            ["parsed_sections.id", "parsed_sections.parsed_paper_id"],
            name="fk_parsed_passages_section_paper",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parsed_paper_id", "source_id", name="uq_parsed_passages_source"),
        sa.UniqueConstraint("id", "parsed_paper_id", name="uq_parsed_passages_ownership"),
        sa.UniqueConstraint(
            "parsed_section_id", "position", name="uq_parsed_passages_section_position"
        ),
    )
    op.create_table(
        "parsed_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parsed_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.String(length=200), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("authors", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "publication_year IS NULL OR publication_year BETWEEN 1000 AND 9999",
            name="ck_parsed_references_year",
        ),
        sa.ForeignKeyConstraint(["parsed_paper_id"], ["parsed_papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parsed_paper_id", "source_id", name="uq_parsed_references_source"),
        sa.UniqueConstraint("id", "parsed_paper_id", name="uq_parsed_references_ownership"),
    )
    op.create_table(
        "citation_contexts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parsed_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parsed_passage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parsed_reference_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reference_source_id", sa.String(length=200), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("coordinates", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["parsed_passage_id", "parsed_paper_id"],
            ["parsed_passages.id", "parsed_passages.parsed_paper_id"],
            name="fk_citation_contexts_passage_paper",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parsed_reference_id", "parsed_paper_id"],
            ["parsed_references.id", "parsed_references.parsed_paper_id"],
            name="fk_citation_contexts_reference_paper",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "parsed_paper_id", name="uq_citation_contexts_ownership"),
    )
    op.create_table(
        "paper_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parsed_paper_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("analysis_scope", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("research_problem", sa.Text(), nullable=False),
        sa.Column("method_summary", sa.Text(), nullable=False),
        sa.Column("key_contributions", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("limitations", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("configured_model", sa.String(length=200), nullable=False),
        sa.Column("model_version", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("call_count", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT')",
            name="ck_paper_analyses_scope_allowed",
        ),
        sa.CheckConstraint(
            "(analysis_scope = 'ABSTRACT_ONLY' AND parsed_paper_id IS NULL) OR "
            "(analysis_scope = 'FULL_TEXT' AND parsed_paper_id IS NOT NULL)",
            name="ck_paper_analyses_parsed_scope",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_paper_analyses_schema_version_positive"),
        sa.CheckConstraint(
            "prompt_tokens BETWEEN 0 AND 1000000 AND "
            "completion_tokens BETWEEN 0 AND 16000 AND "
            "total_tokens BETWEEN 0 AND 1000000 AND "
            "total_tokens = prompt_tokens + completion_tokens AND "
            "call_count BETWEEN 1 AND 4 AND duration_ms BETWEEN 0 AND 1800000 AND "
            "(estimated_cost_usd IS NULL OR estimated_cost_usd >= 0)",
            name="ck_paper_analyses_usage",
        ),
        sa.CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_paper_analyses_verification_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_paper_analyses_version_paper",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parsed_paper_id", "paper_id", "paper_version_id"],
            ["parsed_papers.id", "parsed_papers.paper_id", "parsed_papers.paper_version_id"],
            name="fk_paper_analyses_parsed_ownership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "paper_version_id",
            "analysis_scope",
            "parsed_paper_id",
            "provider",
            "configured_model",
            "model_version",
            "prompt_version",
            name="uq_paper_analyses_provenance",
            postgresql_nulls_not_distinct=True,
        ),
        sa.UniqueConstraint(
            "id", "paper_id", "paper_version_id", name="uq_paper_analyses_ownership"
        ),
    )
    op.create_index("ix_paper_analyses_generated_at", "paper_analyses", ["generated_at"])
    op.create_table(
        "analysis_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_key", sa.String(length=80), nullable=False),
        sa.Column("claim_type", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model_version", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "claim_type IN ('RESEARCH_PROBLEM', 'METHOD', 'CONTRIBUTION', 'RESULT', 'LIMITATION')",
            name="ck_analysis_claims_type_allowed",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_analysis_claims_schema_version_positive"),
        sa.CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_analysis_claims_verification_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id", "paper_id", "paper_version_id"],
            ["paper_analyses.id", "paper_analyses.paper_id", "paper_analyses.paper_version_id"],
            name="fk_analysis_claims_analysis_ownership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_id", "claim_key", name="uq_analysis_claims_key"),
        sa.UniqueConstraint("id", "analysis_id", name="uq_analysis_claims_ownership"),
    )
    op.create_table(
        "evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_key", sa.String(length=80), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("passage_id", sa.String(length=200), nullable=False),
        sa.Column("coordinates", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("excerpt", sa.String(length=600), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("extraction_source", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model_version", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("char_length(excerpt) <= 600", name="ck_evidence_excerpt_concise"),
        sa.CheckConstraint(
            "evidence_type IN ('SUPPORTS', 'QUALIFIES', 'CONTRADICTS')",
            name="ck_evidence_type_allowed",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_evidence_schema_version_positive"),
        sa.CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_evidence_verification_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id", "paper_id", "paper_version_id"],
            ["paper_analyses.id", "paper_analyses.paper_id", "paper_analyses.paper_version_id"],
            name="fk_evidence_analysis_ownership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_id", "evidence_key", name="uq_evidence_key"),
        sa.UniqueConstraint("id", "analysis_id", name="uq_evidence_ownership"),
    )
    op.create_table(
        "evidence_claims",
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id", "analysis_id"],
            ["evidence.id", "evidence.analysis_id"],
            name="fk_evidence_claims_evidence_analysis",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id", "analysis_id"],
            ["analysis_claims.id", "analysis_claims.analysis_id"],
            name="fk_evidence_claims_claim_analysis",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("evidence_id", "claim_id"),
    )
    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("logical_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schema_version > 0", name="ck_reports_schema_version_positive"),
        sa.CheckConstraint("status IN ('COMPLETE', 'PARTIAL')", name="ck_reports_status_allowed"),
        sa.ForeignKeyConstraint(["run_id"], ["daily_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_reports_run"),
        sa.UniqueConstraint("id", "run_id", name="uq_reports_ownership"),
    )
    op.create_table(
        "report_failures",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("failed_stage", sa.String(length=40), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("error_detail", sa.String(length=1000), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schema_version > 0", name="ck_report_failures_schema_version_positive"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_report_failures_version_paper",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "paper_version_id", name="uq_report_failures_version"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_m2_data = bool(
        connection.scalar(
            sa.text(
                "SELECT EXISTS ("
                "SELECT 1 FROM daily_runs WHERE operation = 'STRUCTURED_ANALYSIS' "
                "UNION ALL SELECT 1 FROM parsed_papers "
                "UNION ALL SELECT 1 FROM paper_analyses "
                "UNION ALL SELECT 1 FROM reports"
                ")"
            )
        )
    )
    allow_data_loss = (
        context.get_x_argument(as_dictionary=True).get("allow_m2_data_loss", "false").lower()
        == "true"
    )
    if has_m2_data and not allow_data_loss:
        raise RuntimeError(
            "M2 downgrade refused because structured-analysis data exists. Create and verify a "
            "PostgreSQL backup, then explicitly rerun with "
            "'-x allow_m2_data_loss=true' only when destructive rollback is intended."
        )

    op.drop_table("report_failures")
    op.drop_table("reports")
    op.drop_table("evidence_claims")
    op.drop_table("evidence")
    op.drop_table("analysis_claims")
    op.drop_index("ix_paper_analyses_generated_at", table_name="paper_analyses")
    op.drop_table("paper_analyses")
    op.drop_table("citation_contexts")
    op.drop_table("parsed_references")
    op.drop_table("parsed_passages")
    op.drop_table("parsed_sections")
    op.drop_table("parsed_papers")

    # M1 cannot represent analysis runs because its cursor columns were mandatory.
    op.execute("DELETE FROM daily_runs WHERE operation = 'STRUCTURED_ANALYSIS'")
    op.drop_constraint("ck_daily_runs_completed_lte_selected", "daily_runs", type_="check")
    op.drop_constraint("ck_daily_runs_completed_nonnegative", "daily_runs", type_="check")
    op.drop_constraint("ck_daily_runs_selected_nonnegative", "daily_runs", type_="check")
    op.drop_constraint("ck_daily_runs_operation_fields", "daily_runs", type_="check")
    op.drop_constraint("ck_daily_runs_operation_allowed", "daily_runs", type_="check")
    op.alter_column(
        "daily_runs", "cursor_to", existing_type=sa.DateTime(timezone=True), nullable=False
    )
    op.alter_column(
        "daily_runs", "cursor_from", existing_type=sa.DateTime(timezone=True), nullable=False
    )
    op.drop_column("daily_runs", "completed_count")
    op.drop_column("daily_runs", "selected_count")
    op.drop_column("daily_runs", "analysis_scope")
    op.create_check_constraint(
        "ck_daily_runs_cursor_order", "daily_runs", "cursor_from <= cursor_to"
    )
    op.create_check_constraint(
        "ck_daily_runs_operation_allowed", "daily_runs", "operation IN ('ARXIV_INGESTION')"
    )
    op.drop_constraint("ck_topics_representative_count_bounded", "topics", type_="check")
    op.create_check_constraint(
        "ck_topics_representative_count_positive",
        "topics",
        "representative_full_text_count > 0",
    )
