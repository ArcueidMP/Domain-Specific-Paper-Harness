"""Persist optional computation failures with each immutable publication.

Revision ID: 0008_enrichment_failures
Revises: 0007_ingestion_progress
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0008_enrichment_failures"
down_revision: str | None = "0007_ingestion_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_enrichment_failures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("failed_stage", sa.String(40), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("error_detail", sa.String(1000), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_report_enrichment_failures_version_paper",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "report_id",
            "failed_stage",
            "paper_version_id",
            name="uq_report_enrichment_failures_scope",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(
            "schema_version > 0",
            name="ck_report_enrichment_failures_schema_version",
        ),
        sa.CheckConstraint(
            "(failed_stage = 'TREND_AGGREGATION' AND paper_id IS NULL "
            "AND paper_version_id IS NULL) OR "
            "(failed_stage IN ('GRAPH_EXTRACTION', 'LINEAGE_GENERATION') "
            "AND paper_id IS NOT NULL AND paper_version_id IS NOT NULL)",
            name="ck_report_enrichment_failures_scope",
        ),
    )


def downgrade() -> None:
    has_failures = bool(
        op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM report_enrichment_failures)"))
    )
    allow_data_loss = (
        context.get_x_argument(as_dictionary=True)
        .get("allow_enrichment_data_loss", "false")
        .lower()
        == "true"
    )
    if has_failures and not allow_data_loss:
        raise RuntimeError(
            "Downgrade deletes enrichment diagnostics; back up and verify a separate restore "
            "before using -x allow_enrichment_data_loss=true."
        )
    op.drop_table("report_enrichment_failures")
