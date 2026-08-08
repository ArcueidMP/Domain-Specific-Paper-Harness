"""Create the M1 ingestion, identity, cursor, and run schema.

Revision ID: 0001_m1_ingestion
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_m1_ingestion"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "topics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("categories", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("include_terms", postgresql.ARRAY(sa.String(length=120)), nullable=False),
        sa.Column("exclude_terms", postgresql.ARRAY(sa.String(length=120)), nullable=False),
        sa.Column("overlap_hours", sa.Integer(), nullable=False),
        sa.Column("initial_lookback_days", sa.Integer(), nullable=False),
        sa.Column("max_results", sa.Integer(), nullable=False),
        sa.Column("representative_full_text_count", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_topics_schema_version_positive"),
        sa.CheckConstraint("overlap_hours > 0", name="ck_topics_overlap_positive"),
        sa.CheckConstraint("initial_lookback_days > 0", name="ck_topics_lookback_positive"),
        sa.CheckConstraint("max_results > 0", name="ck_topics_max_results_positive"),
        sa.CheckConstraint(
            "representative_full_text_count > 0", name="ck_topics_representative_count_positive"
        ),
        sa.CheckConstraint("cardinality(categories) > 0", name="ck_topics_categories_nonempty"),
        sa.CheckConstraint(
            "cardinality(include_terms) > 0", name="ck_topics_include_terms_nonempty"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    op.create_table(
        "papers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_arxiv_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("first_submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("primary_category", sa.String(length=32), nullable=False),
        sa.Column("categories", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("authors", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("pdf_url", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("current_version > 0", name="ck_papers_current_version_positive"),
        sa.CheckConstraint("schema_version > 0", name="ck_papers_schema_version_positive"),
        sa.CheckConstraint(
            "latest_updated_at >= first_submitted_at", name="ck_papers_update_order"
        ),
        sa.CheckConstraint("cardinality(categories) > 0", name="ck_papers_categories_nonempty"),
        sa.CheckConstraint("cardinality(authors) > 0", name="ck_papers_authors_nonempty"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_arxiv_id"),
    )
    op.create_index("ix_papers_latest_updated_at", "papers", ["latest_updated_at"], unique=False)

    op.create_table(
        "authors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_authors_schema_version_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_name"),
    )

    op.create_table(
        "paper_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("primary_category", sa.String(length=32), nullable=False),
        sa.Column("categories", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("authors", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("pdf_url", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0", name="ck_paper_versions_version_positive"),
        sa.CheckConstraint("schema_version > 0", name="ck_paper_versions_schema_version_positive"),
        sa.CheckConstraint("updated_at >= submitted_at", name="ck_paper_versions_update_order"),
        sa.CheckConstraint(
            "cardinality(categories) > 0", name="ck_paper_versions_categories_nonempty"
        ),
        sa.CheckConstraint("cardinality(authors) > 0", name="ck_paper_versions_authors_nonempty"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "paper_id", name="uq_paper_versions_id_paper"),
        sa.UniqueConstraint("paper_id", "version", name="uq_paper_versions_paper_version"),
    )

    op.create_table(
        "paper_source_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("source_version", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_version > 0", name="ck_source_identities_schema_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_source_identities_version_paper",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source", "external_id", "source_version", name="uq_source_identity_external_version"
        ),
    )

    op.create_table(
        "paper_version_authors",
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_version_authors_position_nonnegative"),
        sa.ForeignKeyConstraint(["author_id"], ["authors.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["paper_version_id"], ["paper_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("paper_version_id", "author_id"),
        sa.UniqueConstraint("paper_version_id", "position", name="uq_version_author_position"),
    )

    op.create_table(
        "topic_papers",
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("first_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("topic_id", "paper_id"),
    )

    op.create_table(
        "ingestion_cursors",
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("watermark", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_version > 0", name="ck_ingestion_cursors_schema_version_positive"
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("topic_id"),
    )

    op.create_table(
        "daily_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("logical_date", sa.Date(), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cursor_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discovered_count", sa.Integer(), nullable=False),
        sa.Column("normalized_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_detail", sa.String(length=1000), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("discovered_count >= 0", name="ck_daily_runs_discovered_nonnegative"),
        sa.CheckConstraint("normalized_count >= 0", name="ck_daily_runs_normalized_nonnegative"),
        sa.CheckConstraint("failed_count >= 0", name="ck_daily_runs_failed_nonnegative"),
        sa.CheckConstraint("schema_version > 0", name="ck_daily_runs_schema_version_positive"),
        sa.CheckConstraint(
            "operation IN ('ARXIV_INGESTION')", name="ck_daily_runs_operation_allowed"
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE', 'PARTIAL', 'FAILED')",
            name="ck_daily_runs_status_allowed",
        ),
        sa.CheckConstraint("cursor_from <= cursor_to", name="ck_daily_runs_cursor_order"),
        sa.CheckConstraint(
            "normalized_count <= discovered_count", name="ck_daily_runs_normalized_lte_discovered"
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL)",
            name="ck_daily_runs_completion_state",
        ),
        sa.CheckConstraint(
            "status <> 'FAILED' OR error_code IS NOT NULL", name="ck_daily_runs_failed_error"
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "topic_id", "logical_date", "operation", name="uq_daily_runs_topic_date_operation"
        ),
    )
    op.create_index("ix_daily_runs_started_at", "daily_runs", ["started_at"], unique=False)

    op.create_table(
        "run_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failed_stage", sa.String(length=40), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("error_detail", sa.String(length=1000), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_run_items_schema_version_positive"),
        sa.CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED')",
            name="ck_run_items_status_allowed",
        ),
        sa.CheckConstraint(
            "stage IN ('DISCOVERED', 'NORMALIZED', 'ENRICHED', 'RELEVANCE_SCORED', "
            "'SELECTED', 'PDF_DOWNLOADED', 'PARSED', 'ANALYZED', 'EVIDENCE_EXTRACTED', "
            "'PRIOR_WORK_RETRIEVED', 'COMPARED', 'GRAPH_UPDATED', 'PUBLISHED')",
            name="ck_run_items_stage_allowed",
        ),
        sa.CheckConstraint(
            "(status = 'FAILED' AND failed_stage IS NOT NULL AND error_code IS NOT NULL "
            "AND retryable IS NOT NULL) OR "
            "(status <> 'FAILED' AND failed_stage IS NULL AND error_code IS NULL "
            "AND retryable IS NULL AND error_detail IS NULL)",
            name="ck_run_items_failure_metadata",
        ),
        sa.ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_run_items_version_paper",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["daily_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "paper_version_id", name="uq_run_items_run_version"),
    )


def downgrade() -> None:
    op.drop_table("run_items")
    op.drop_index("ix_daily_runs_started_at", table_name="daily_runs")
    op.drop_table("daily_runs")
    op.drop_table("ingestion_cursors")
    op.drop_table("topic_papers")
    op.drop_table("paper_version_authors")
    op.drop_table("paper_source_identities")
    op.drop_table("paper_versions")
    op.drop_table("authors")
    op.drop_index("ix_papers_latest_updated_at", table_name="papers")
    op.drop_table("papers")
    op.drop_table("topics")
