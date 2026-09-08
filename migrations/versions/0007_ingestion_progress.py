"""Persist resumable arXiv OAI discovery before advancing topic watermarks."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_ingestion_progress"
down_revision: str | None = "0006_topic_reprocessing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "arxiv_discovery_progress",
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("daily_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("categories", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("category_index", sa.Integer(), nullable=False),
        sa.Column("resumption_token", sa.Text()),
        sa.Column("pending_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("page_exhausted", sa.Boolean(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category_index >= 0 AND category_index < cardinality(categories)",
            name="ck_arxiv_discovery_category_index",
        ),
        sa.CheckConstraint(
            "cardinality(pending_ids) <= 10000", name="ck_arxiv_discovery_pending_bound"
        ),
        sa.CheckConstraint(
            "NOT complete OR (cardinality(pending_ids) = 0 AND resumption_token IS NULL)",
            name="ck_arxiv_discovery_complete",
        ),
    )


def downgrade() -> None:
    pending = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM arxiv_discovery_progress WHERE NOT complete"))
        .scalar_one()
    )
    if pending:
        raise RuntimeError(
            "Ingestion downgrade refused while resumable discovery is incomplete; "
            "finish the harvest before downgrade"
        )
    op.drop_table("arxiv_discovery_progress")
