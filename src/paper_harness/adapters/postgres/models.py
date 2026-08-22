"""Normalized SQLAlchemy models for ingestion, analysis, and historical comparison."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _report_logical_date_default(context: Any) -> date:
    """Preserve the legacy analysis-report insert contract at the ORM boundary."""

    return context.get_current_parameters()["logical_date"]


class TopicRow(Base):
    __tablename__ = "topics"
    __table_args__ = (
        CheckConstraint("schema_version > 0", name="ck_topics_schema_version_positive"),
        CheckConstraint("overlap_hours > 0", name="ck_topics_overlap_positive"),
        CheckConstraint("initial_lookback_days > 0", name="ck_topics_lookback_positive"),
        CheckConstraint("max_results > 0", name="ck_topics_max_results_positive"),
        CheckConstraint(
            "representative_full_text_count BETWEEN 1 AND 200",
            name="ck_topics_representative_count_bounded",
        ),
        CheckConstraint("cardinality(categories) > 0", name="ck_topics_categories_nonempty"),
        CheckConstraint("cardinality(include_terms) > 0", name="ck_topics_include_terms_nonempty"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    categories: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    include_terms: Mapped[list[str]] = mapped_column(ARRAY(String(120)), nullable=False)
    exclude_terms: Mapped[list[str]] = mapped_column(ARRAY(String(120)), nullable=False)
    overlap_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    initial_lookback_days: Mapped[int] = mapped_column(Integer, nullable=False)
    max_results: Mapped[int] = mapped_column(Integer, nullable=False)
    representative_full_text_count: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PaperRow(Base):
    __tablename__ = "papers"
    __table_args__ = (
        CheckConstraint("current_version > 0", name="ck_papers_current_version_positive"),
        CheckConstraint("schema_version > 0", name="ck_papers_schema_version_positive"),
        CheckConstraint("latest_updated_at >= first_submitted_at", name="ck_papers_update_order"),
        CheckConstraint("cardinality(categories) > 0", name="ck_papers_categories_nonempty"),
        CheckConstraint("cardinality(authors) > 0", name="ck_papers_authors_nonempty"),
        Index("ix_papers_latest_updated_at", "latest_updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    canonical_arxiv_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str] = mapped_column(Text, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    first_submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latest_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    primary_category: Mapped[str] = mapped_column(String(32), nullable=False)
    categories: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    authors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    pdf_url: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PaperVersionRow(Base):
    __tablename__ = "paper_versions"
    __table_args__ = (
        UniqueConstraint("paper_id", "version", name="uq_paper_versions_paper_version"),
        UniqueConstraint("id", "paper_id", name="uq_paper_versions_id_paper"),
        CheckConstraint("version > 0", name="ck_paper_versions_version_positive"),
        CheckConstraint("schema_version > 0", name="ck_paper_versions_schema_version_positive"),
        CheckConstraint("updated_at >= submitted_at", name="ck_paper_versions_update_order"),
        CheckConstraint(
            "cardinality(categories) > 0", name="ck_paper_versions_categories_nonempty"
        ),
        CheckConstraint("cardinality(authors) > 0", name="ck_paper_versions_authors_nonempty"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    paper_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    primary_category: Mapped[str] = mapped_column(String(32), nullable=False)
    categories: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    authors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    pdf_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaperSourceIdentityRow(Base):
    __tablename__ = "paper_source_identities"
    __table_args__ = (
        UniqueConstraint(
            "source", "external_id", "source_version", name="uq_source_identity_external_version"
        ),
        CheckConstraint("schema_version > 0", name="ck_source_identities_schema_version_positive"),
        ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_source_identities_version_paper",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuthorRow(Base):
    __tablename__ = "authors"
    __table_args__ = (
        CheckConstraint("schema_version > 0", name="ck_authors_schema_version_positive"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    normalized_name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaperVersionAuthorRow(Base):
    __tablename__ = "paper_version_authors"
    __table_args__ = (
        UniqueConstraint("paper_version_id", "position", name="uq_version_author_position"),
        CheckConstraint("position >= 0", name="ck_version_authors_position_nonnegative"),
    )

    paper_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("paper_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    author_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("authors.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class TopicPaperRow(Base):
    __tablename__ = "topic_papers"

    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    first_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IngestionCursorRow(Base):
    __tablename__ = "ingestion_cursors"
    __table_args__ = (
        CheckConstraint("schema_version > 0", name="ck_ingestion_cursors_schema_version_positive"),
    )

    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    watermark: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PipelineExecutionRow(Base):
    __tablename__ = "pipeline_executions"
    __table_args__ = (
        UniqueConstraint(
            "topic_id",
            "logical_date",
            "execution_mode",
            "execution_key",
            name="uq_pipeline_executions_scope",
        ),
        UniqueConstraint(
            "id",
            "topic_id",
            "logical_date",
            "execution_mode",
            name="uq_pipeline_executions_child_ownership",
        ),
        UniqueConstraint(
            "id",
            "topic_id",
            name="uq_pipeline_executions_topic_ownership",
        ),
        CheckConstraint(
            "execution_mode IN ('NORMAL', 'SMOKE')",
            name="ck_pipeline_executions_mode",
        ),
        CheckConstraint(
            "(execution_mode = 'NORMAL' AND execution_key = 'canonical') OR "
            "(execution_mode = 'SMOKE' AND execution_key <> 'canonical' "
            "AND execution_key = btrim(execution_key) "
            "AND length(execution_key) BETWEEN 1 AND 200)",
            name="ck_pipeline_executions_key",
        ),
        CheckConstraint(
            "analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT')",
            name="ck_pipeline_executions_analysis_scope",
        ),
        CheckConstraint(
            "jsonb_typeof(execution_contract) = 'object'",
            name="ck_pipeline_executions_contract",
        ),
        CheckConstraint(
            "selection_limit BETWEEN 1 AND 200 "
            "AND (execution_mode <> 'SMOKE' OR selection_limit <= 5)",
            name="ck_pipeline_executions_selection_limit",
        ),
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE', 'PARTIAL', 'FAILED')",
            name="ck_pipeline_executions_status",
        ),
        CheckConstraint(
            "deadline_at > started_at",
            name="ck_pipeline_executions_deadline",
        ),
        CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL)",
            name="ck_pipeline_executions_completion",
        ),
        CheckConstraint(
            "(status = 'FAILED' AND error_code IS NOT NULL) OR "
            "(status <> 'FAILED' AND error_code IS NULL AND error_detail IS NULL)",
            name="ck_pipeline_executions_failure",
        ),
        CheckConstraint("schema_version > 0", name="ck_pipeline_executions_schema_version"),
        Index("ix_pipeline_executions_topic_date", "topic_id", "logical_date"),
        Index("ix_pipeline_executions_status_deadline", "status", "deadline_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("topics.id", ondelete="RESTRICT"), nullable=False
    )
    logical_date: Mapped[date] = mapped_column(Date, nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_key: Mapped[str] = mapped_column(String(200), nullable=False)
    analysis_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    selection_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_contract: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DailyRunRow(Base):
    __tablename__ = "daily_runs"
    __table_args__ = (
        UniqueConstraint("id", "topic_id", name="uq_daily_runs_topic_ownership"),
        UniqueConstraint(
            "id",
            "topic_id",
            "logical_date",
            name="uq_daily_runs_topic_date_ownership",
        ),
        UniqueConstraint(
            "id",
            "topic_id",
            "source_run_id",
            name="uq_daily_runs_product_input_ownership",
        ),
        CheckConstraint("discovered_count >= 0", name="ck_daily_runs_discovered_nonnegative"),
        CheckConstraint("normalized_count >= 0", name="ck_daily_runs_normalized_nonnegative"),
        CheckConstraint("failed_count >= 0", name="ck_daily_runs_failed_nonnegative"),
        CheckConstraint("schema_version > 0", name="ck_daily_runs_schema_version_positive"),
        CheckConstraint(
            "operation IN ('ARXIV_INGESTION', 'STRUCTURED_ANALYSIS', "
            "'HISTORICAL_ANALYSIS', 'PRODUCT_PUBLICATION')",
            name="ck_daily_runs_operation_allowed",
        ),
        CheckConstraint(
            "(operation = 'PRODUCT_PUBLICATION' AND source_run_id IS NOT NULL) OR "
            "(operation <> 'PRODUCT_PUBLICATION' AND source_run_id IS NULL)",
            name="ck_daily_runs_source_run",
        ),
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE', 'PARTIAL', 'FAILED')",
            name="ck_daily_runs_status_allowed",
        ),
        CheckConstraint(
            "pipeline_execution_mode IN ('STANDALONE', 'NORMAL', 'SMOKE')",
            name="ck_daily_runs_pipeline_execution_mode_allowed",
        ),
        CheckConstraint(
            "(pipeline_execution_mode = 'STANDALONE' AND pipeline_execution_id IS NULL "
            "AND pipeline_selection_limit IS NULL) OR "
            "(pipeline_execution_mode = 'NORMAL' AND pipeline_execution_id IS NOT NULL "
            "AND pipeline_selection_limit BETWEEN 1 AND 200) OR "
            "(pipeline_execution_mode = 'SMOKE' AND pipeline_execution_id IS NOT NULL "
            "AND pipeline_selection_limit BETWEEN 1 AND 5)",
            name="ck_daily_runs_pipeline_selection_limit",
        ),
        CheckConstraint(
            "(operation = 'ARXIV_INGESTION' AND cursor_from IS NOT NULL "
            "AND cursor_to IS NOT NULL AND cursor_from <= cursor_to "
            "AND analysis_scope IS NULL AND selected_count = 0 AND completed_count = 0) OR "
            "(operation IN ('STRUCTURED_ANALYSIS', 'HISTORICAL_ANALYSIS') "
            "AND cursor_from IS NULL "
            "AND cursor_to IS NULL AND analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT')) OR "
            "(operation = 'PRODUCT_PUBLICATION' AND cursor_from IS NULL "
            "AND cursor_to IS NULL AND analysis_scope IS NULL "
            "AND discovered_count = 0 AND normalized_count = 0)",
            name="ck_daily_runs_operation_fields",
        ),
        CheckConstraint(
            "normalized_count <= discovered_count", name="ck_daily_runs_normalized_lte_discovered"
        ),
        CheckConstraint("selected_count >= 0", name="ck_daily_runs_selected_nonnegative"),
        CheckConstraint("completed_count >= 0", name="ck_daily_runs_completed_nonnegative"),
        CheckConstraint(
            "completed_count <= selected_count", name="ck_daily_runs_completed_lte_selected"
        ),
        CheckConstraint(
            "operation = 'ARXIV_INGESTION' OR failed_count <= selected_count",
            name="ck_daily_runs_failed_lte_selected",
        ),
        CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL)",
            name="ck_daily_runs_completion_state",
        ),
        CheckConstraint(
            "status <> 'FAILED' OR error_code IS NOT NULL", name="ck_daily_runs_failed_error"
        ),
        ForeignKeyConstraint(
            ["source_run_id", "topic_id", "logical_date"],
            ["daily_runs.id", "daily_runs.topic_id", "daily_runs.logical_date"],
            name="fk_daily_runs_source_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "pipeline_execution_id",
                "topic_id",
                "logical_date",
                "pipeline_execution_mode",
            ],
            [
                "pipeline_executions.id",
                "pipeline_executions.topic_id",
                "pipeline_executions.logical_date",
                "pipeline_executions.execution_mode",
            ],
            name="fk_daily_runs_pipeline_execution",
            ondelete="RESTRICT",
        ),
        Index("ix_daily_runs_started_at", "started_at"),
        Index(
            "uq_daily_runs_standalone_topic_date_operation",
            "topic_id",
            "logical_date",
            "operation",
            unique=True,
            postgresql_where=text("pipeline_execution_id IS NULL"),
        ),
        Index(
            "uq_daily_runs_pipeline_execution_operation",
            "pipeline_execution_id",
            "operation",
            unique=True,
            postgresql_where=text("pipeline_execution_id IS NOT NULL"),
        ),
        Index(
            "uq_daily_runs_product_source_date",
            "source_run_id",
            "logical_date",
            unique=True,
            postgresql_where=text("operation = 'PRODUCT_PUBLICATION'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("topics.id", ondelete="RESTRICT"), nullable=False
    )
    logical_date: Mapped[date] = mapped_column(Date, nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    source_run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
    )
    pipeline_execution_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    pipeline_execution_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="STANDALONE", server_default="STANDALONE"
    )
    pipeline_selection_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analysis_scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cursor_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cursor_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    normalized_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RunItemRow(Base):
    __tablename__ = "run_items"
    __table_args__ = (
        UniqueConstraint("run_id", "paper_version_id", name="uq_run_items_run_version"),
        UniqueConstraint(
            "run_id",
            "paper_id",
            "paper_version_id",
            name="uq_run_items_run_paper_version",
        ),
        CheckConstraint("schema_version > 0", name="ck_run_items_schema_version_positive"),
        CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED')",
            name="ck_run_items_status_allowed",
        ),
        CheckConstraint(
            "stage IN ('DISCOVERED', 'NORMALIZED', 'ENRICHED', 'RELEVANCE_SCORED', "
            "'SELECTED', 'PDF_DOWNLOADED', 'PARSED', 'ANALYZED', 'EVIDENCE_EXTRACTED', "
            "'PRIOR_WORK_RETRIEVED', 'COMPARED', 'GRAPH_UPDATED', "
            "'TREND_SNAPSHOTS_GENERATED', 'REPORT_GENERATED', 'PUBLISHED')",
            name="ck_run_items_stage_allowed",
        ),
        CheckConstraint(
            "(status = 'FAILED' AND failed_stage IS NOT NULL AND error_code IS NOT NULL "
            "AND retryable IS NOT NULL) OR "
            "(status <> 'FAILED' AND failed_stage IS NULL AND error_code IS NULL "
            "AND retryable IS NULL AND error_detail IS NULL)",
            name="ck_run_items_failure_metadata",
        ),
        ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_run_items_version_paper",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("daily_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failed_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ParsedPaperRow(Base):
    __tablename__ = "parsed_papers"
    __table_args__ = (
        UniqueConstraint(
            "paper_version_id",
            "parser_name",
            "parser_version",
            name="uq_parsed_papers_version_parser",
        ),
        UniqueConstraint("id", "paper_id", "paper_version_id", name="uq_parsed_papers_ownership"),
        CheckConstraint("schema_version > 0", name="ck_parsed_papers_schema_version_positive"),
        CheckConstraint(
            "call_count BETWEEN 1 AND 4 AND duration_ms BETWEEN 0 AND 1000000",
            name="ck_parsed_papers_usage",
        ),
        ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_parsed_papers_version_paper",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    parser_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(100), nullable=False)
    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ParsedSectionRow(Base):
    __tablename__ = "parsed_sections"
    __table_args__ = (
        UniqueConstraint("parsed_paper_id", "position", name="uq_parsed_sections_position"),
        UniqueConstraint("id", "parsed_paper_id", name="uq_parsed_sections_ownership"),
        CheckConstraint("position >= 0", name="ck_parsed_sections_position_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    parsed_paper_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("parsed_papers.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)


class ParsedPassageRow(Base):
    __tablename__ = "parsed_passages"
    __table_args__ = (
        UniqueConstraint("parsed_paper_id", "source_id", name="uq_parsed_passages_source"),
        UniqueConstraint("id", "parsed_paper_id", name="uq_parsed_passages_ownership"),
        UniqueConstraint(
            "parsed_section_id", "position", name="uq_parsed_passages_section_position"
        ),
        CheckConstraint("position >= 0", name="ck_parsed_passages_position_nonnegative"),
        ForeignKeyConstraint(
            ["parsed_section_id", "parsed_paper_id"],
            ["parsed_sections.id", "parsed_sections.parsed_paper_id"],
            name="fk_parsed_passages_section_paper",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    parsed_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    parsed_section_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    coordinates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)


class ParsedReferenceRow(Base):
    __tablename__ = "parsed_references"
    __table_args__ = (
        UniqueConstraint("parsed_paper_id", "source_id", name="uq_parsed_references_source"),
        UniqueConstraint("id", "parsed_paper_id", name="uq_parsed_references_ownership"),
        CheckConstraint(
            "publication_year IS NULL OR publication_year BETWEEN 1000 AND 9999",
            name="ck_parsed_references_year",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    parsed_paper_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("parsed_papers.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    authors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class CitationContextRow(Base):
    __tablename__ = "citation_contexts"
    __table_args__ = (
        UniqueConstraint("id", "parsed_paper_id", name="uq_citation_contexts_ownership"),
        ForeignKeyConstraint(
            ["parsed_passage_id", "parsed_paper_id"],
            ["parsed_passages.id", "parsed_passages.parsed_paper_id"],
            name="fk_citation_contexts_passage_paper",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["parsed_reference_id", "parsed_paper_id"],
            ["parsed_references.id", "parsed_references.parsed_paper_id"],
            name="fk_citation_contexts_reference_paper",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    parsed_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    parsed_passage_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    parsed_reference_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    reference_source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    coordinates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)


class PaperAnalysisRow(Base):
    __tablename__ = "paper_analyses"
    __table_args__ = (
        UniqueConstraint(
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
        UniqueConstraint("id", "paper_id", "paper_version_id", name="uq_paper_analyses_ownership"),
        UniqueConstraint(
            "id",
            "paper_id",
            "paper_version_id",
            "analysis_scope",
            name="uq_paper_analyses_m3_ownership",
        ),
        CheckConstraint(
            "analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT')",
            name="ck_paper_analyses_scope_allowed",
        ),
        CheckConstraint(
            "(analysis_scope = 'ABSTRACT_ONLY' AND parsed_paper_id IS NULL) OR "
            "(analysis_scope = 'FULL_TEXT' AND parsed_paper_id IS NOT NULL)",
            name="ck_paper_analyses_parsed_scope",
        ),
        CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_paper_analyses_verification_allowed",
        ),
        CheckConstraint("schema_version > 0", name="ck_paper_analyses_schema_version_positive"),
        CheckConstraint(
            "prompt_tokens BETWEEN 0 AND 1000000 AND "
            "completion_tokens BETWEEN 0 AND 16000 AND "
            "total_tokens BETWEEN 0 AND 1000000 AND "
            "total_tokens = prompt_tokens + completion_tokens AND "
            "call_count BETWEEN 1 AND 4 AND duration_ms BETWEEN 0 AND 1800000 AND "
            "(estimated_cost_usd IS NULL OR estimated_cost_usd >= 0)",
            name="ck_paper_analyses_usage",
        ),
        ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_paper_analyses_version_paper",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["parsed_paper_id", "paper_id", "paper_version_id"],
            ["parsed_papers.id", "parsed_papers.paper_id", "parsed_papers.paper_version_id"],
            name="fk_paper_analyses_parsed_ownership",
            ondelete="CASCADE",
        ),
        Index("ix_paper_analyses_generated_at", "generated_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    parsed_paper_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    analysis_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    research_problem: Mapped[str] = mapped_column(Text, nullable=False)
    method_summary: Mapped[str] = mapped_column(Text, nullable=False)
    key_contributions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    limitations: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    configured_model: Mapped[str] = mapped_column(String(200), nullable=False)
    model_version: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnalysisClaimRow(Base):
    __tablename__ = "analysis_claims"
    __table_args__ = (
        UniqueConstraint("analysis_id", "claim_key", name="uq_analysis_claims_key"),
        UniqueConstraint("id", "analysis_id", name="uq_analysis_claims_ownership"),
        CheckConstraint(
            "claim_type IN ('RESEARCH_PROBLEM', 'METHOD', 'CONTRIBUTION', 'RESULT', 'LIMITATION')",
            name="ck_analysis_claims_type_allowed",
        ),
        CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_analysis_claims_verification_allowed",
        ),
        CheckConstraint("schema_version > 0", name="ck_analysis_claims_schema_version_positive"),
        ForeignKeyConstraint(
            ["analysis_id", "paper_id", "paper_version_id"],
            ["paper_analyses.id", "paper_analyses.paper_id", "paper_analyses.paper_version_id"],
            name="fk_analysis_claims_analysis_ownership",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    analysis_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    claim_key: Mapped[str] = mapped_column(String(80), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvidenceRow(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("analysis_id", "evidence_key", name="uq_evidence_key"),
        UniqueConstraint("id", "analysis_id", name="uq_evidence_ownership"),
        UniqueConstraint(
            "id",
            "paper_id",
            "paper_version_id",
            name="uq_evidence_paper_version_ownership",
        ),
        CheckConstraint(
            "evidence_type IN ('SUPPORTS', 'QUALIFIES', 'CONTRADICTS')",
            name="ck_evidence_type_allowed",
        ),
        CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_evidence_verification_allowed",
        ),
        CheckConstraint("schema_version > 0", name="ck_evidence_schema_version_positive"),
        CheckConstraint("char_length(excerpt) <= 600", name="ck_evidence_excerpt_concise"),
        ForeignKeyConstraint(
            ["analysis_id", "paper_id", "paper_version_id"],
            ["paper_analyses.id", "paper_analyses.paper_id", "paper_analyses.paper_version_id"],
            name="fk_evidence_analysis_ownership",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    analysis_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_key: Mapped[str] = mapped_column(String(80), nullable=False)
    section: Mapped[str] = mapped_column(Text, nullable=False)
    passage_id: Mapped[str] = mapped_column(String(200), nullable=False)
    coordinates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    excerpt: Mapped[str] = mapped_column(String(600), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    extraction_source: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvidenceClaimRow(Base):
    __tablename__ = "evidence_claims"
    __table_args__ = (
        ForeignKeyConstraint(
            ["evidence_id", "analysis_id"],
            ["evidence.id", "evidence.analysis_id"],
            name="fk_evidence_claims_evidence_analysis",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["claim_id", "analysis_id"],
            ["analysis_claims.id", "analysis_claims.analysis_id"],
            name="fk_evidence_claims_claim_analysis",
            ondelete="CASCADE",
        ),
    )

    evidence_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    claim_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    analysis_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)


class ReportRow(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("id", "topic_id", name="uq_reports_topic_ownership"),
        UniqueConstraint("run_id", name="uq_reports_run"),
        CheckConstraint("status IN ('COMPLETE', 'PARTIAL')", name="ck_reports_status_allowed"),
        CheckConstraint(
            "report_type IN ('ANALYSIS', 'DAILY', 'WEEKLY', 'MONTHLY')",
            name="ck_reports_type_allowed",
        ),
        CheckConstraint(
            "(report_type IN ('ANALYSIS', 'DAILY') AND run_id IS NOT NULL) OR "
            "(report_type IN ('WEEKLY', 'MONTHLY') AND run_id IS NULL)",
            name="ck_reports_run_ownership",
        ),
        CheckConstraint(
            "(report_type IN ('ANALYSIS', 'DAILY') "
            "AND period_start = logical_date AND period_end = logical_date) OR "
            "(report_type = 'WEEKLY' AND logical_date = period_end "
            "AND EXTRACT(ISODOW FROM period_start) = 1 "
            "AND period_end = period_start + 6) OR "
            "(report_type = 'MONTHLY' AND logical_date = period_end "
            "AND period_start = date_trunc('month', period_start)::date "
            "AND period_end = (period_start + INTERVAL '1 month - 1 day')::date)",
            name="ck_reports_calendar_period",
        ),
        CheckConstraint(
            "retrieved_count >= 0 AND selected_count >= 0 AND processed_count >= 0 "
            "AND completed_count >= 0 AND failed_count >= 0 "
            "AND selected_count <= retrieved_count AND processed_count <= selected_count "
            "AND completed_count <= processed_count AND failed_count <= selected_count "
            "AND processed_count = completed_count + failed_count",
            name="ck_reports_counts",
        ),
        CheckConstraint(
            "graph_entity_count >= 0 AND graph_edge_count >= 0 "
            "AND new_graph_entity_count BETWEEN 0 AND graph_entity_count "
            "AND inferred_graph_edge_count BETWEEN 0 AND graph_edge_count",
            name="ck_reports_graph_counts",
        ),
        CheckConstraint(
            "narrative_mode IN ('STRUCTURED_ONLY', 'DEEPSEEK')",
            name="ck_reports_narrative_mode",
        ),
        CheckConstraint(
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
            name="ck_reports_model_provenance",
        ),
        CheckConstraint(
            "prompt_tokens IS NULL OR (prompt_tokens BETWEEN 0 AND 1000000 "
            "AND completion_tokens BETWEEN 0 AND 16000 "
            "AND total_tokens = prompt_tokens + completion_tokens "
            "AND total_tokens BETWEEN 0 AND 1000000 "
            "AND call_count BETWEEN 1 AND 4 AND duration_ms BETWEEN 0 AND 1800000 "
            "AND (estimated_cost_usd IS NULL OR estimated_cost_usd >= 0))",
            name="ck_reports_usage",
        ),
        CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_reports_verification",
        ),
        CheckConstraint("schema_version > 0", name="ck_reports_schema_version_positive"),
        Index(
            "uq_reports_aggregate_period",
            "topic_id",
            "report_type",
            "period_start",
            "period_end",
            unique=True,
            postgresql_where=text("report_type IN ('WEEKLY', 'MONTHLY')"),
        ),
        ForeignKeyConstraint(
            ["run_id", "topic_id"],
            ["daily_runs.id", "daily_runs.topic_id"],
            name="fk_reports_run_topic",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
    )
    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("topics.id", ondelete="RESTRICT"),
        nullable=False,
    )
    logical_date: Mapped[date] = mapped_column(Date, nullable=False)
    report_type: Mapped[str] = mapped_column(String(16), nullable=False, default="ANALYSIS")
    period_start: Mapped[date] = mapped_column(
        Date, nullable=False, default=_report_logical_date_default
    )
    period_end: Mapped[date] = mapped_column(
        Date, nullable=False, default=_report_logical_date_default
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    graph_entity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    graph_edge_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_graph_entity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inferred_graph_edge_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    limitations: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    missing_sections: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)), nullable=False, default=list
    )
    narrative_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="STRUCTURED_ONLY"
    )
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    configured_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    call_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="UNVERIFIED"
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReportFailureRow(Base):
    __tablename__ = "report_failures"
    __table_args__ = (
        UniqueConstraint("report_id", "paper_version_id", name="uq_report_failures_version"),
        CheckConstraint("schema_version > 0", name="ck_report_failures_schema_version_positive"),
        ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_report_failures_version_paper",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    report_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    failed_stage: Mapped[str] = mapped_column(String(40), nullable=False)
    error_code: Mapped[str] = mapped_column(String(80), nullable=False)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_detail: Mapped[str] = mapped_column(String(1000), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExternalPaperStubRow(Base):
    __tablename__ = "external_paper_stubs"
    __table_args__ = (
        CheckConstraint(
            "publication_year IS NULL OR publication_year BETWEEN 1000 AND 9999",
            name="ck_external_paper_stubs_year",
        ),
        CheckConstraint(
            "citation_count >= 0 AND influential_citation_count >= 0 "
            "AND influential_citation_count <= citation_count",
            name="ck_external_paper_stubs_citations",
        ),
        CheckConstraint(
            "(full_text_available AND arxiv_id IS NOT NULL) OR "
            "(NOT full_text_available AND arxiv_id IS NULL)",
            name="ck_external_paper_stubs_full_text",
        ),
        CheckConstraint("source = 'semantic_scholar'", name="ck_external_paper_stubs_source"),
        CheckConstraint("updated_at >= created_at", name="ck_external_paper_stubs_update_order"),
        CheckConstraint("schema_version > 0", name="ck_external_paper_stubs_schema_version"),
        UniqueConstraint(
            "id",
            "semantic_scholar_id",
            name="uq_external_paper_stubs_ownership",
        ),
        Index("ix_external_paper_stubs_arxiv_id", "arxiv_id", unique=True),
        Index("ix_external_paper_stubs_doi", "doi", unique=True),
        Index("ix_external_paper_stubs_publication_date", "publication_date"),
        Index(
            "ix_external_paper_stubs_lexical",
            text(
                "to_tsvector('english'::regconfig, "
                "(COALESCE(title, ''::text) || ' '::text) || "
                "COALESCE(abstract, ''::text))"
            ),
            postgresql_using="gin",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    semantic_scholar_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    venue: Mapped[str | None] = mapped_column(Text, nullable=True)
    authors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    doi: Mapped[str | None] = mapped_column(Text, nullable=True)
    citation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    influential_citation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    full_text_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExternalPaperIdentifierRow(Base):
    __tablename__ = "external_paper_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "identifier_type",
            "identifier_value",
            name="uq_external_paper_identifiers_external",
        ),
    )

    external_paper_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("external_paper_stubs.id", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
    )
    identifier_type: Mapped[str] = mapped_column(String(40), primary_key=True)
    identifier_value: Mapped[str] = mapped_column(String(512), nullable=False)


class HistoricalBackfillRunRow(Base):
    __tablename__ = "historical_backfill_runs"
    __table_args__ = (
        UniqueConstraint(
            "topic_id", "window_from", "window_to", name="uq_historical_backfill_window"
        ),
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE', 'FAILED')",
            name="ck_historical_backfill_status",
        ),
        CheckConstraint("window_from <= window_to", name="ck_historical_backfill_window_order"),
        CheckConstraint(
            "cardinality(query_plan) BETWEEN 1 AND 40 "
            "AND next_query_index <= cardinality(query_plan) "
            "AND max_results_per_query BETWEEN 1 AND 500 "
            "AND overall_timeout_seconds BETWEEN 1 AND 7200",
            name="ck_historical_backfill_plan_bounds",
        ),
        CheckConstraint(
            "next_query_index >= 0 AND discovered_count >= 0 AND persisted_count >= 0 "
            "AND representative_count >= 0 AND persisted_count <= discovered_count "
            "AND representative_count <= persisted_count",
            name="ck_historical_backfill_counts",
        ),
        CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL)",
            name="ck_historical_backfill_completion",
        ),
        CheckConstraint(
            "status <> 'COMPLETE' OR next_query_index = cardinality(query_plan)",
            name="ck_historical_backfill_complete_cursor",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_historical_backfill_completion_order",
        ),
        CheckConstraint(
            "embedding_dimension = 768",
            name="ck_historical_backfill_embedding_dimension",
        ),
        CheckConstraint(
            "(status = 'FAILED' AND error_code IS NOT NULL) OR "
            "(status <> 'FAILED' AND error_code IS NULL AND error_detail IS NULL)",
            name="ck_historical_backfill_failure",
        ),
        CheckConstraint("schema_version > 0", name="ck_historical_backfill_schema_version"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False
    )
    window_from: Mapped[date] = mapped_column(Date, nullable=False)
    window_to: Mapped[date] = mapped_column(Date, nullable=False)
    query_plan: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False)
    max_results_per_query: Mapped[int] = mapped_column(Integer, nullable=False)
    overall_timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    embedding_model_identifier: Mapped[str] = mapped_column(String(300), nullable=False)
    embedding_model_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_tokenizer_identifier: Mapped[str] = mapped_column(String(300), nullable=False)
    embedding_tokenizer_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_preprocessing_contract: Mapped[str] = mapped_column(String(1000), nullable=False)
    embedding_model_provenance: Mapped[str] = mapped_column(String(1000), nullable=False)
    embedding_source: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    next_query_index: Mapped[int] = mapped_column(Integer, nullable=False)
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False)
    persisted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    representative_count: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HistoricalCorpusEntryRow(Base):
    __tablename__ = "historical_corpus_entries"
    __table_args__ = (
        UniqueConstraint("topic_id", "external_paper_id", name="uq_historical_corpus_paper"),
        CheckConstraint(
            "(local_paper_id IS NULL AND local_paper_version_id IS NULL) OR "
            "(local_paper_id IS NOT NULL AND local_paper_version_id IS NOT NULL)",
            name="ck_historical_corpus_local_owner",
        ),
        CheckConstraint(
            "representative_rank IS NULL OR representative_rank > 0",
            name="ck_historical_corpus_representative_rank",
        ),
        CheckConstraint("last_seen_at >= first_seen_at", name="ck_historical_corpus_seen_order"),
        CheckConstraint("schema_version > 0", name="ck_historical_corpus_schema_version"),
        ForeignKeyConstraint(
            ["local_paper_version_id", "local_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_historical_corpus_local_version",
            ondelete="CASCADE",
        ),
        Index(
            "uq_historical_corpus_representative",
            "topic_id",
            "representative_rank",
            unique=True,
            postgresql_where=text("representative_rank IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False
    )
    external_paper_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("external_paper_stubs.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    local_paper_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    local_paper_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    representative_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)


class SearchSessionRow(Base):
    __tablename__ = "search_sessions"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "source_paper_id",
            "source_paper_version_id",
            "source_analysis_id",
            "source_analysis_scope",
            name="uq_search_sessions_source_ownership",
        ),
        CheckConstraint(
            "source_analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT')",
            name="ck_search_sessions_source_analysis_scope",
        ),
        CheckConstraint(
            "requested_year_from BETWEEN 1000 AND 9999 "
            "AND effective_year_to BETWEEN 1000 AND 9999 "
            "AND requested_year_from <= effective_year_to",
            name="ck_search_sessions_year_scope",
        ),
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE', 'FAILED')", name="ck_search_sessions_status"
        ),
        CheckConstraint(
            "stop_reason IS NULL OR stop_reason IN ('QUEUE_EXHAUSTED', 'MAX_STEPS', "
            "'MAX_QUERIES', 'MAX_QUEUE_SIZE', 'MAX_CANDIDATES', "
            "'MAX_SELECTED_CANDIDATES', 'OVERALL_TIMEOUT', 'FAILED')",
            name="ck_search_sessions_stop_reason",
        ),
        CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL AND stop_reason IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL AND stop_reason IS NOT NULL)",
            name="ck_search_sessions_completion",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_search_sessions_completion_order",
        ),
        CheckConstraint(
            "(status = 'FAILED' AND stop_reason = 'FAILED' AND error_code IS NOT NULL) OR "
            "(status <> 'FAILED' AND stop_reason IS DISTINCT FROM 'FAILED' "
            "AND error_code IS NULL AND error_detail IS NULL)",
            name="ck_search_sessions_failure",
        ),
        CheckConstraint(
            "max_steps BETWEEN 1 AND 100 AND max_queries BETWEEN 1 AND 40 "
            "AND max_queue_size BETWEEN 1 AND 2000 AND max_citation_depth BETWEEN 0 AND 5 "
            "AND max_candidates BETWEEN 1 AND 5000 "
            "AND max_selected_candidates BETWEEN 1 AND 100 "
            "AND max_selected_candidates <= max_candidates",
            name="ck_search_sessions_limits",
        ),
        CheckConstraint(
            "per_operation_timeout_seconds BETWEEN 1 AND 600 "
            "AND overall_timeout_seconds BETWEEN per_operation_timeout_seconds AND 3600",
            name="ck_search_sessions_timeouts",
        ),
        CheckConstraint(
            "(crawler_queries IS NULL AND crawler_use_recommendations IS NULL "
            "AND crawler_expand_references IS NULL AND crawler_expand_citations IS NULL "
            "AND crawler_decision_reason IS NULL AND crawler_generated_at IS NULL) OR "
            "(crawler_queries IS NOT NULL "
            "AND cardinality(crawler_queries) BETWEEN 1 AND max_queries "
            "AND crawler_use_recommendations IS NOT NULL "
            "AND crawler_expand_references IS NOT NULL "
            "AND crawler_expand_citations IS NOT NULL "
            "AND crawler_decision_reason IS NOT NULL AND crawler_generated_at IS NOT NULL "
            "AND provider IS NOT NULL)",
            name="ck_search_sessions_crawler_plan",
        ),
        CheckConstraint(
            "(provider IS NULL AND configured_model IS NULL AND model_version IS NULL "
            "AND prompt_version IS NULL AND prompt_tokens IS NULL "
            "AND completion_tokens IS NULL AND total_tokens IS NULL "
            "AND call_count IS NULL AND model_duration_ms IS NULL "
            "AND estimated_cost_usd IS NULL) OR "
            "(provider IS NOT NULL AND configured_model IS NOT NULL "
            "AND model_version IS NOT NULL AND prompt_version IS NOT NULL "
            "AND prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL "
            "AND total_tokens IS NOT NULL AND call_count IS NOT NULL "
            "AND model_duration_ms IS NOT NULL)",
            name="ck_search_sessions_model_provenance",
        ),
        CheckConstraint(
            "prompt_tokens IS NULL OR (prompt_tokens >= 0 AND completion_tokens >= 0 "
            "AND total_tokens = prompt_tokens + completion_tokens "
            "AND total_tokens <= 1000000 AND completion_tokens <= 16000 "
            "AND call_count BETWEEN 1 AND 4 "
            "AND model_duration_ms BETWEEN 0 AND 1800000 "
            "AND (estimated_cost_usd IS NULL "
            "OR estimated_cost_usd BETWEEN 0 AND 9999999999.99999999))",
            name="ck_search_sessions_model_usage",
        ),
        CheckConstraint("schema_version > 0", name="ck_search_sessions_schema_version"),
        ForeignKeyConstraint(
            ["source_paper_version_id", "source_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_search_sessions_source_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "source_analysis_id",
                "source_paper_id",
                "source_paper_version_id",
                "source_analysis_scope",
            ],
            [
                "paper_analyses.id",
                "paper_analyses.paper_id",
                "paper_analyses.paper_version_id",
                "paper_analyses.analysis_scope",
            ],
            name="fk_search_sessions_source_analysis",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["pipeline_execution_id", "topic_id"],
            ["pipeline_executions.id", "pipeline_executions.topic_id"],
            name="fk_search_sessions_pipeline_execution",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_search_sessions_source_started",
            "source_paper_id",
            "started_at",
            "id",
        ),
        Index("ix_search_sessions_pipeline_execution", "pipeline_execution_id"),
        Index(
            "uq_search_sessions_pipeline_execution_source_analysis",
            "pipeline_execution_id",
            "source_analysis_id",
            unique=True,
            postgresql_where=text("pipeline_execution_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    pipeline_execution_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False
    )
    source_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_paper_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    source_analysis_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_analysis_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_year_from: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_year_to: Mapped[int] = mapped_column(Integer, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    crawler_queries: Mapped[list[str] | None] = mapped_column(ARRAY(String(500)), nullable=True)
    crawler_use_recommendations: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    crawler_expand_references: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    crawler_expand_citations: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    crawler_decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    crawler_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    max_queries: Mapped[int] = mapped_column(Integer, nullable=False)
    max_queue_size: Mapped[int] = mapped_column(Integer, nullable=False)
    max_citation_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    max_candidates: Mapped[int] = mapped_column(Integer, nullable=False)
    max_selected_candidates: Mapped[int] = mapped_column(Integer, nullable=False)
    per_operation_timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    overall_timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    configured_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    call_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SearchActionRow(Base):
    __tablename__ = "search_actions"
    __table_args__ = (
        UniqueConstraint("session_id", "step", name="uq_search_actions_session_step"),
        UniqueConstraint("id", "session_id", name="uq_search_actions_ownership"),
        CheckConstraint(
            "tool IN ('search_papers', 'get_paper', 'get_references', 'get_citations', "
            "'get_recommendations', 'read_arxiv_paper')",
            name="ck_search_actions_tool",
        ),
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'FAILED')", name="ck_search_actions_status"
        ),
        CheckConstraint(
            "step >= 1 AND requested_limit BETWEEN 1 AND 1000 "
            "AND result_count BETWEEN 0 AND requested_limit "
            "AND relation_depth BETWEEN 0 AND 5 AND duration_ms BETWEEN 0 AND 600000",
            name="ck_search_actions_bounds",
        ),
        CheckConstraint(
            "(year_from IS NULL OR year_from BETWEEN 1000 AND 9999) "
            "AND (year_to IS NULL OR year_to BETWEEN 1000 AND 9999) "
            "AND (year_from IS NULL OR year_to IS NULL OR year_from <= year_to)",
            name="ck_search_actions_years",
        ),
        CheckConstraint(
            "(tool = 'search_papers' AND query IS NOT NULL "
            "AND target_semantic_scholar_id IS NULL AND target_arxiv_id IS NULL "
            "AND cardinality(positive_paper_ids) = 0) OR "
            "(tool IN ('get_paper', 'get_references', 'get_citations') AND query IS NULL "
            "AND target_semantic_scholar_id IS NOT NULL AND target_arxiv_id IS NULL "
            "AND cardinality(positive_paper_ids) = 0) OR "
            "(tool = 'get_recommendations' AND query IS NULL "
            "AND target_semantic_scholar_id IS NULL AND target_arxiv_id IS NULL "
            "AND cardinality(positive_paper_ids) > 0) OR "
            "(tool = 'read_arxiv_paper' AND query IS NULL "
            "AND target_semantic_scholar_id IS NULL AND target_arxiv_id IS NOT NULL "
            "AND cardinality(positive_paper_ids) = 0)",
            name="ck_search_actions_tool_parameters",
        ),
        CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL AND error_code IS NULL "
            "AND retryable IS NULL AND error_detail IS NULL AND result_count = 0 "
            "AND duration_ms = 0) OR "
            "(status = 'COMPLETED' AND completed_at IS NOT NULL AND error_code IS NULL "
            "AND retryable IS NULL AND error_detail IS NULL) OR "
            "(status = 'FAILED' AND completed_at IS NOT NULL AND error_code IS NOT NULL "
            "AND retryable IS NOT NULL)",
            name="ck_search_actions_lifecycle",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name="ck_search_actions_completion_order",
        ),
        CheckConstraint("schema_version > 0", name="ck_search_actions_schema_version"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("search_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    tool: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    query: Mapped[str | None] = mapped_column(String(500), nullable=True)
    target_semantic_scholar_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    positive_paper_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String(128)), nullable=False, default=list
    )
    year_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    year_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False)
    relation_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)


class SearchCandidateRow(Base):
    __tablename__ = "search_candidates"
    __table_args__ = (
        UniqueConstraint("session_id", "external_paper_id", name="uq_search_candidates_paper"),
        UniqueConstraint("id", "session_id", name="uq_search_candidates_ownership"),
        CheckConstraint(
            "(local_paper_id IS NULL AND local_paper_version_id IS NULL) OR "
            "(local_paper_id IS NOT NULL AND local_paper_version_id IS NOT NULL)",
            name="ck_search_candidates_local_owner",
        ),
        CheckConstraint(
            "cardinality(origins) > 0 AND relation_depth BETWEEN 0 AND 5 AND rank > 0",
            name="ck_search_candidates_bounds",
        ),
        CheckConstraint(
            "origins <@ ARRAY['SEARCH', 'REFERENCES', 'CITATIONS', 'RECOMMENDATIONS', "
            "'LOCAL_LEXICAL', 'LOCAL_VECTOR']::varchar[]",
            name="ck_search_candidates_origins",
        ),
        CheckConstraint(
            "semantic_scholar_score BETWEEN 0 AND 1 AND lexical_score BETWEEN 0 AND 1 "
            "AND vector_score BETWEEN 0 AND 1 AND entity_overlap_score BETWEEN 0 AND 1 "
            "AND citation_score BETWEEN 0 AND 1 AND recommendation_score BETWEEN 0 AND 1 "
            "AND final_score BETWEEN 0 AND 1",
            name="ck_search_candidates_scores",
        ),
        CheckConstraint(
            "decision IN ('PENDING', 'SELECTED', 'REJECTED')",
            name="ck_search_candidates_decision",
        ),
        CheckConstraint(
            "(comparison_target_decision IS NULL AND comparison_target_reason IS NULL) OR "
            "(comparison_target_decision IN ('TARGET', 'NOT_TARGET', 'INELIGIBLE') "
            "AND comparison_target_reason IS NOT NULL)",
            name="ck_search_candidates_comparison_target",
        ),
        CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_search_candidates_verification",
        ),
        CheckConstraint(
            "(decision = 'PENDING' AND provider IS NULL AND configured_model IS NULL "
            "AND model_version IS NULL AND prompt_version IS NULL AND generated_at IS NULL) OR "
            "(decision <> 'PENDING' AND provider IS NOT NULL AND configured_model IS NOT NULL "
            "AND model_version IS NOT NULL AND prompt_version IS NOT NULL "
            "AND generated_at IS NOT NULL)",
            name="ck_search_candidates_model_provenance",
        ),
        CheckConstraint("schema_version > 0", name="ck_search_candidates_schema_version"),
        ForeignKeyConstraint(
            ["external_paper_id", "semantic_scholar_id"],
            ["external_paper_stubs.id", "external_paper_stubs.semantic_scholar_id"],
            name="fk_search_candidates_external_identity",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        ForeignKeyConstraint(
            ["local_paper_version_id", "local_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_search_candidates_local_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["discovered_by_action_id", "session_id"],
            ["search_actions.id", "search_actions.session_id"],
            name="fk_search_candidates_discovery_action",
        ),
        Index("ix_search_candidates_session_rank", "session_id", "rank", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("search_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_paper_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    semantic_scholar_id: Mapped[str] = mapped_column(String(128), nullable=False)
    local_paper_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    local_paper_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    discovered_by_action_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    origins: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    relation_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    semantic_scholar_score: Mapped[float] = mapped_column(Float, nullable=False)
    lexical_score: Mapped[float] = mapped_column(Float, nullable=False)
    vector_score: Mapped[float] = mapped_column(Float, nullable=False)
    entity_overlap_score: Mapped[float] = mapped_column(Float, nullable=False)
    citation_score: Mapped[float] = mapped_column(Float, nullable=False)
    recommendation_score: Mapped[float] = mapped_column(Float, nullable=False)
    final_score: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    comparison_target_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    comparison_target_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    configured_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SearchCandidateDiscoveryRow(Base):
    __tablename__ = "search_candidate_discoveries"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "action_id",
            "origin",
            name="uq_search_candidate_discoveries_origin",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "origin IN ('SEARCH', 'REFERENCES', 'CITATIONS', 'RECOMMENDATIONS', "
            "'LOCAL_LEXICAL', 'LOCAL_VECTOR')",
            name="ck_search_candidate_discoveries_origin",
        ),
        CheckConstraint(
            "relation_depth BETWEEN 0 AND 5", name="ck_search_candidate_discoveries_depth"
        ),
        CheckConstraint(
            "(origin IN ('SEARCH', 'REFERENCES', 'CITATIONS', 'RECOMMENDATIONS') "
            "AND action_id IS NOT NULL) OR "
            "(origin IN ('LOCAL_LEXICAL', 'LOCAL_VECTOR') AND action_id IS NULL)",
            name="ck_search_candidate_discoveries_action",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "session_id"],
            ["search_candidates.id", "search_candidates.session_id"],
            name="fk_search_candidate_discoveries_candidate",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        ForeignKeyConstraint(
            ["action_id", "session_id"],
            ["search_actions.id", "search_actions.session_id"],
            name="fk_search_candidate_discoveries_action",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    action_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    relation_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScientificEmbeddingRow(Base):
    __tablename__ = "scientific_embeddings"
    __table_args__ = (
        UniqueConstraint(
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
            name="uq_scientific_embeddings_owner_model",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "(paper_version_id IS NOT NULL AND external_paper_id IS NULL) OR "
            "(paper_version_id IS NULL AND external_paper_id IS NOT NULL)",
            name="ck_scientific_embeddings_owner",
        ),
        CheckConstraint("dimension = 768", name="ck_scientific_embeddings_dimension"),
        CheckConstraint("schema_version > 0", name="ck_scientific_embeddings_schema_version"),
        Index(
            "ix_scientific_embeddings_model",
            "model_identifier",
            "model_revision",
            "tokenizer_identifier",
            "tokenizer_revision",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    paper_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("paper_versions.id", ondelete="CASCADE"),
        nullable=True,
    )
    external_paper_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("external_paper_stubs.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=True,
    )
    model_identifier: Mapped[str] = mapped_column(String(300), nullable=False)
    model_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    tokenizer_identifier: Mapped[str] = mapped_column(String(300), nullable=False)
    tokenizer_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    preprocessing_contract: Mapped[str] = mapped_column(String(1000), nullable=False)
    model_provenance: Mapped[str] = mapped_column(String(1000), nullable=False)
    vector: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ComparisonRow(Base):
    __tablename__ = "comparisons"
    __table_args__ = (
        UniqueConstraint(
            "search_session_id",
            "source_paper_version_id",
            "source_analysis_id",
            "target_paper_version_id",
            "target_analysis_id",
            "provider",
            "configured_model",
            "model_version",
            "prompt_version",
            name="uq_comparisons_provenance",
        ),
        UniqueConstraint("id", "source_paper_id", name="uq_comparisons_source_ownership"),
        UniqueConstraint("id", "target_paper_id", name="uq_comparisons_target_ownership"),
        UniqueConstraint(
            "id",
            "source_paper_id",
            "source_paper_version_id",
            "target_paper_id",
            "target_paper_version_id",
            name="uq_comparisons_relation_ownership",
        ),
        UniqueConstraint(
            "id",
            "source_paper_id",
            "source_paper_version_id",
            "source_analysis_id",
            name="uq_comparisons_product_source_ownership",
        ),
        CheckConstraint(
            "source_paper_version_id <> target_paper_version_id",
            name="ck_comparisons_distinct_versions",
        ),
        CheckConstraint(
            "source_analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT') "
            "AND target_analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT')",
            name="ck_comparisons_analysis_scopes",
        ),
        CheckConstraint(
            "comparability_status IN ('DIRECTLY_COMPARABLE', 'PARTIALLY_COMPARABLE', "
            "'NOT_DIRECTLY_COMPARABLE', 'INSUFFICIENT_EVIDENCE')",
            name="ck_comparisons_comparability",
        ),
        CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_comparisons_verification",
        ),
        CheckConstraint(
            "prompt_tokens BETWEEN 0 AND 1000000 AND completion_tokens BETWEEN 0 AND 16000 "
            "AND total_tokens BETWEEN 0 AND 1000000 "
            "AND total_tokens = prompt_tokens + completion_tokens "
            "AND call_count BETWEEN 1 AND 4 AND duration_ms BETWEEN 0 AND 1800000 "
            "AND (estimated_cost_usd IS NULL OR estimated_cost_usd >= 0)",
            name="ck_comparisons_usage",
        ),
        CheckConstraint("schema_version > 0", name="ck_comparisons_schema_version"),
        ForeignKeyConstraint(
            [
                "search_session_id",
                "source_paper_id",
                "source_paper_version_id",
                "source_analysis_id",
                "source_analysis_scope",
            ],
            [
                "search_sessions.id",
                "search_sessions.source_paper_id",
                "search_sessions.source_paper_version_id",
                "search_sessions.source_analysis_id",
                "search_sessions.source_analysis_scope",
            ],
            name="fk_comparisons_search_session_source",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_paper_version_id", "source_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_comparisons_source_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["target_paper_version_id", "target_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_comparisons_target_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "source_analysis_id",
                "source_paper_id",
                "source_paper_version_id",
                "source_analysis_scope",
            ],
            [
                "paper_analyses.id",
                "paper_analyses.paper_id",
                "paper_analyses.paper_version_id",
                "paper_analyses.analysis_scope",
            ],
            name="fk_comparisons_source_analysis",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "target_analysis_id",
                "target_paper_id",
                "target_paper_version_id",
                "target_analysis_scope",
            ],
            [
                "paper_analyses.id",
                "paper_analyses.paper_id",
                "paper_analyses.paper_version_id",
                "paper_analyses.analysis_scope",
            ],
            name="fk_comparisons_target_analysis",
            ondelete="CASCADE",
        ),
        Index("ix_comparisons_generated_at", "generated_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    search_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    source_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_paper_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    source_analysis_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_analysis_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    target_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    target_paper_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    target_analysis_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    target_analysis_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    comparability_status: Mapped[str] = mapped_column(String(32), nullable=False)
    comparability_reason: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    configured_model: Mapped[str] = mapped_column(String(200), nullable=False)
    model_version: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ComparisonDimensionRow(Base):
    __tablename__ = "comparison_dimensions"
    __table_args__ = (
        UniqueConstraint("comparison_id", "name", name="uq_comparison_dimensions_name"),
        UniqueConstraint("comparison_id", "position", name="uq_comparison_dimensions_position"),
        UniqueConstraint("id", "comparison_id", name="uq_comparison_dimensions_ownership"),
        CheckConstraint("position BETWEEN 0 AND 13", name="ck_comparison_dimensions_position"),
        CheckConstraint(
            "name IN ('RESEARCH_PROBLEM', 'TASK', 'METHOD', 'ARCHITECTURE', 'DATASETS', "
            "'BENCHMARKS', 'BASELINES', 'METRICS', 'REPORTED_RESULTS', "
            "'COMPUTE_OR_INFERENCE_BUDGET', 'CLAIMED_NOVELTY', 'LIMITATIONS', "
            "'CODE_AVAILABILITY', 'RESULT_COMPARABILITY')",
            name="ck_comparison_dimensions_name",
        ),
        CheckConstraint(
            "(name = 'RESEARCH_PROBLEM' AND position = 0) OR "
            "(name = 'TASK' AND position = 1) OR "
            "(name = 'METHOD' AND position = 2) OR "
            "(name = 'ARCHITECTURE' AND position = 3) OR "
            "(name = 'DATASETS' AND position = 4) OR "
            "(name = 'BENCHMARKS' AND position = 5) OR "
            "(name = 'BASELINES' AND position = 6) OR "
            "(name = 'METRICS' AND position = 7) OR "
            "(name = 'REPORTED_RESULTS' AND position = 8) OR "
            "(name = 'COMPUTE_OR_INFERENCE_BUDGET' AND position = 9) OR "
            "(name = 'CLAIMED_NOVELTY' AND position = 10) OR "
            "(name = 'LIMITATIONS' AND position = 11) OR "
            "(name = 'CODE_AVAILABILITY' AND position = 12) OR "
            "(name = 'RESULT_COMPARABILITY' AND position = 13)",
            name="ck_comparison_dimensions_canonical_order",
        ),
        CheckConstraint("schema_version > 0", name="ck_comparison_dimensions_schema_version"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    comparison_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("comparisons.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_value: Mapped[str] = mapped_column(Text, nullable=False)
    target_value: Mapped[str] = mapped_column(Text, nullable=False)
    assessment: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ComparisonEvidenceLinkRow(Base):
    __tablename__ = "comparison_evidence_links"
    __table_args__ = (
        CheckConstraint(
            "evidence_role IN ('SOURCE', 'TARGET')", name="ck_comparison_evidence_links_role"
        ),
        ForeignKeyConstraint(
            ["comparison_dimension_id", "comparison_id"],
            ["comparison_dimensions.id", "comparison_dimensions.comparison_id"],
            name="fk_comparison_evidence_links_dimension",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "evidence_paper_id", "evidence_paper_version_id"],
            ["evidence.id", "evidence.paper_id", "evidence.paper_version_id"],
            name="fk_comparison_evidence_links_evidence",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "evidence_analysis_id"],
            ["evidence.id", "evidence.analysis_id"],
            name="fk_comparison_evidence_links_evidence_analysis",
            ondelete="CASCADE",
        ),
    )

    comparison_dimension_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    evidence_analysis_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_role: Mapped[str] = mapped_column(String(8), primary_key=True)
    comparison_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_paper_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )


class PaperRelationRow(Base):
    __tablename__ = "paper_relations"
    __table_args__ = (
        UniqueConstraint(
            "comparison_id",
            "source_paper_version_id",
            "target_paper_version_id",
            "relation_type",
            "provenance",
            name="uq_paper_relations_provenance",
        ),
        UniqueConstraint("id", "comparison_id", name="uq_paper_relations_ownership"),
        CheckConstraint(
            "source_paper_version_id <> target_paper_version_id",
            name="ck_paper_relations_distinct_versions",
        ),
        CheckConstraint(
            "relation_type IN ('CITES', 'SIMILAR_TO', 'EXTENDS', 'COMPARES_WITH', "
            "'CONTRADICTS', 'IMPROVES_ON')",
            name="ck_paper_relations_type",
        ),
        CheckConstraint(
            "provenance IN ('METADATA_EXPLICIT', 'TEXT_EXPLICIT', "
            "'DETERMINISTICALLY_DERIVED', 'LLM_INFERRED', 'HUMAN_VERIFIED')",
            name="ck_paper_relations_provenance",
        ),
        CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_paper_relations_verification",
        ),
        CheckConstraint(
            "(provenance = 'LLM_INFERRED' AND provider IS NOT NULL "
            "AND model_version IS NOT NULL AND prompt_version IS NOT NULL "
            "AND confidence BETWEEN 0 AND 1) OR "
            "(provenance <> 'LLM_INFERRED' AND provider IS NULL "
            "AND model_version IS NULL AND prompt_version IS NULL AND confidence IS NULL)",
            name="ck_paper_relations_model_provenance",
        ),
        CheckConstraint("schema_version > 0", name="ck_paper_relations_schema_version"),
        ForeignKeyConstraint(
            [
                "comparison_id",
                "source_paper_id",
                "source_paper_version_id",
                "target_paper_id",
                "target_paper_version_id",
            ],
            [
                "comparisons.id",
                "comparisons.source_paper_id",
                "comparisons.source_paper_version_id",
                "comparisons.target_paper_id",
                "comparisons.target_paper_version_id",
            ],
            name="fk_paper_relations_comparison_ownership",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_paper_version_id", "source_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_paper_relations_source_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["target_paper_version_id", "target_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_paper_relations_target_version",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    comparison_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    source_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_paper_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    target_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    target_paper_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RelationEvidenceLinkRow(Base):
    __tablename__ = "relation_evidence_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["relation_id", "comparison_id"],
            ["paper_relations.id", "paper_relations.comparison_id"],
            name="fk_relation_evidence_links_relation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "evidence_paper_id", "evidence_paper_version_id"],
            ["evidence.id", "evidence.paper_id", "evidence.paper_version_id"],
            name="fk_relation_evidence_links_evidence",
            ondelete="CASCADE",
        ),
    )

    relation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    comparison_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_paper_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )


class ProductRunPaperInputRow(Base):
    __tablename__ = "product_run_paper_inputs"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "topic_id",
            "source_run_id",
            "paper_id",
            "paper_version_id",
            "analysis_id",
            name="uq_product_run_paper_inputs_ownership",
        ),
        CheckConstraint("schema_version > 0", name="ck_product_run_paper_inputs_schema"),
        ForeignKeyConstraint(
            ["run_id", "topic_id", "source_run_id"],
            ["daily_runs.id", "daily_runs.topic_id", "daily_runs.source_run_id"],
            name="fk_product_run_paper_inputs_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["run_id", "paper_id", "paper_version_id"],
            ["run_items.run_id", "run_items.paper_id", "run_items.paper_version_id"],
            name="fk_product_run_paper_inputs_item",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
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
    )

    run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    topic_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    analysis_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    analysis_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProductRunComparisonInputRow(Base):
    __tablename__ = "product_run_comparison_inputs"
    __table_args__ = (
        CheckConstraint("schema_version > 0", name="ck_product_run_comparison_inputs_schema"),
        ForeignKeyConstraint(
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
        ForeignKeyConstraint(
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
    )

    run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    topic_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    analysis_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    comparison_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GraphEntityRow(Base):
    __tablename__ = "graph_entities"
    __table_args__ = (
        UniqueConstraint("id", "topic_id", name="uq_graph_entities_topic_ownership"),
        UniqueConstraint(
            "topic_id",
            "entity_type",
            "normalized_key",
            name="uq_graph_entities_canonical_key",
        ),
        CheckConstraint(
            "entity_type IN ('PAPER', 'RESEARCH_PROBLEM', 'METHOD', 'TASK', "
            "'DATASET', 'BENCHMARK')",
            name="ck_graph_entities_type",
        ),
        CheckConstraint(
            "(entity_type = 'PAPER' AND paper_id IS NOT NULL) OR "
            "(entity_type <> 'PAPER' AND paper_id IS NULL)",
            name="ck_graph_entities_paper_owner",
        ),
        CheckConstraint(
            "provenance IN ('METADATA_EXPLICIT', 'TEXT_EXPLICIT', "
            "'DETERMINISTICALLY_DERIVED', 'LLM_INFERRED', 'HUMAN_VERIFIED')",
            name="ck_graph_entities_provenance",
        ),
        CheckConstraint(
            "char_length(normalized_key) BETWEEN 1 AND 500",
            name="ck_graph_entities_key_length",
        ),
        CheckConstraint("schema_version > 0", name="ck_graph_entities_schema_version"),
        Index(
            "uq_graph_entities_topic_paper",
            "topic_id",
            "paper_id",
            unique=True,
            postgresql_where=text("entity_type = 'PAPER'"),
        ),
        Index("ix_graph_entities_topic_type", "topic_id", "entity_type"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("topics.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    paper_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=True
    )
    canonical_label: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(500), nullable=False)
    display_label: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, onupdate=func.now()
    )


class GraphEntityMentionRow(Base):
    __tablename__ = "graph_entity_mentions"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "paper_id",
            "paper_version_id",
            name="uq_graph_entity_mentions_paper_ownership",
        ),
        UniqueConstraint(
            "entity_id",
            "paper_version_id",
            "analysis_id",
            "comparison_id",
            "observed_label",
            "provenance",
            "pipeline_execution_id",
            name="uq_graph_entity_mentions_source",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "provenance IN ('METADATA_EXPLICIT', 'TEXT_EXPLICIT', "
            "'DETERMINISTICALLY_DERIVED', 'LLM_INFERRED', 'HUMAN_VERIFIED')",
            name="ck_graph_entity_mentions_provenance",
        ),
        CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_graph_entity_mentions_verification",
        ),
        CheckConstraint(
            "(provenance = 'LLM_INFERRED' AND provider IS NOT NULL "
            "AND configured_model IS NOT NULL AND model_version IS NOT NULL "
            "AND prompt_version IS NOT NULL AND confidence BETWEEN 0 AND 1) OR "
            "(provenance = 'DETERMINISTICALLY_DERIVED' AND confidence IS NULL "
            "AND ((provider IS NULL AND configured_model IS NULL AND model_version IS NULL "
            "AND prompt_version IS NULL) OR (provider IS NOT NULL "
            "AND configured_model IS NOT NULL AND model_version IS NOT NULL "
            "AND prompt_version IS NOT NULL))) OR "
            "(provenance NOT IN ('LLM_INFERRED', 'DETERMINISTICALLY_DERIVED') "
            "AND provider IS NULL "
            "AND configured_model IS NULL AND model_version IS NULL "
            "AND prompt_version IS NULL AND confidence IS NULL)",
            name="ck_graph_entity_mentions_model_provenance",
        ),
        CheckConstraint(
            "provenance <> 'HUMAN_VERIFIED' OR verification_status = 'HUMAN_VERIFIED'",
            name="ck_graph_entity_mentions_human_verified",
        ),
        CheckConstraint(
            "(analysis_id IS NOT NULL AND comparison_id IS NULL) OR "
            "(analysis_id IS NULL AND comparison_id IS NOT NULL)",
            name="ck_graph_entity_mentions_owner_shape",
        ),
        CheckConstraint("schema_version > 0", name="ck_graph_entity_mentions_schema_version"),
        ForeignKeyConstraint(
            ["entity_id", "topic_id"],
            ["graph_entities.id", "graph_entities.topic_id"],
            name="fk_graph_entity_mentions_entity_topic",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_graph_entity_mentions_paper_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["analysis_id", "paper_id", "paper_version_id"],
            ["paper_analyses.id", "paper_analyses.paper_id", "paper_analyses.paper_version_id"],
            name="fk_graph_entity_mentions_analysis",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["publication_run_id", "topic_id"],
            ["daily_runs.id", "daily_runs.topic_id"],
            name="fk_graph_entity_mentions_publication_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["pipeline_execution_id"],
            ["pipeline_executions.id"],
            name="fk_graph_entity_mentions_pipeline_execution",
            ondelete="RESTRICT",
        ),
        Index("ix_graph_entity_mentions_entity", "entity_id", "generated_at"),
        Index("ix_graph_entity_mentions_paper", "paper_id", "paper_version_id"),
        Index("ix_graph_entity_mentions_publication_run", "publication_run_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    publication_run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    pipeline_execution_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    topic_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    analysis_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    comparison_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("comparisons.id", ondelete="CASCADE"),
        nullable=True,
    )
    observed_label: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    configured_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GraphMentionEvidenceLinkRow(Base):
    __tablename__ = "graph_mention_evidence_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["mention_id", "mention_paper_id", "mention_paper_version_id"],
            [
                "graph_entity_mentions.id",
                "graph_entity_mentions.paper_id",
                "graph_entity_mentions.paper_version_id",
            ],
            name="fk_graph_mention_evidence_links_mention",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "evidence_paper_id", "evidence_paper_version_id"],
            ["evidence.id", "evidence.paper_id", "evidence.paper_version_id"],
            name="fk_graph_mention_evidence_links_evidence",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "evidence_analysis_id"],
            ["evidence.id", "evidence.analysis_id"],
            name="fk_graph_mention_evidence_links_analysis",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "mention_paper_id = evidence_paper_id AND "
            "mention_paper_version_id = evidence_paper_version_id",
            name="ck_graph_mention_evidence_links_paper",
        ),
    )

    mention_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    mention_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    mention_paper_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    evidence_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_paper_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    evidence_analysis_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)


class GraphEdgeRow(Base):
    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint("id", "topic_id", name="uq_graph_edges_topic_ownership"),
        UniqueConstraint(
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
            name="uq_graph_edges_source",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("source_entity_id <> target_entity_id", name="ck_graph_edges_no_self"),
        CheckConstraint(
            "relation_type IN ('CITES', 'SIMILAR_TO', 'EXTENDS', 'COMPARES_WITH', "
            "'CONTRADICTS', 'IMPROVES_ON', 'ADDRESSES', 'USES_METHOD', "
            "'TARGETS_TASK', 'USES_DATASET', 'EVALUATES_ON')",
            name="ck_graph_edges_relation_type",
        ),
        CheckConstraint(
            "provenance IN ('METADATA_EXPLICIT', 'TEXT_EXPLICIT', "
            "'DETERMINISTICALLY_DERIVED', 'LLM_INFERRED', 'HUMAN_VERIFIED')",
            name="ck_graph_edges_provenance",
        ),
        CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_graph_edges_verification",
        ),
        CheckConstraint(
            "(provenance = 'LLM_INFERRED' AND provider IS NOT NULL "
            "AND configured_model IS NOT NULL AND model_version IS NOT NULL "
            "AND prompt_version IS NOT NULL AND confidence BETWEEN 0 AND 1 "
            "AND char_length(justification) > 0) OR "
            "(provenance = 'DETERMINISTICALLY_DERIVED' AND confidence IS NULL "
            "AND ((provider IS NULL AND configured_model IS NULL AND model_version IS NULL "
            "AND prompt_version IS NULL) OR (provider IS NOT NULL "
            "AND configured_model IS NOT NULL AND model_version IS NOT NULL "
            "AND prompt_version IS NOT NULL))) OR "
            "(provenance NOT IN ('LLM_INFERRED', 'DETERMINISTICALLY_DERIVED') "
            "AND provider IS NULL "
            "AND configured_model IS NULL AND model_version IS NULL "
            "AND prompt_version IS NULL AND confidence IS NULL)",
            name="ck_graph_edges_model_provenance",
        ),
        CheckConstraint(
            "provenance <> 'HUMAN_VERIFIED' OR verification_status = 'HUMAN_VERIFIED'",
            name="ck_graph_edges_human_verified",
        ),
        CheckConstraint(
            "(target_paper_id IS NULL AND target_paper_version_id IS NULL) OR "
            "(target_paper_id IS NOT NULL AND target_paper_version_id IS NOT NULL)",
            name="ck_graph_edges_target_paper_pair",
        ),
        CheckConstraint(
            "paper_relation_id IS NULL OR comparison_id IS NOT NULL",
            name="ck_graph_edges_paper_relation_comparison",
        ),
        CheckConstraint("char_length(justification) > 0", name="ck_graph_edges_justification"),
        CheckConstraint(
            "(relation_type IN ('CITES', 'SIMILAR_TO', 'EXTENDS', 'COMPARES_WITH', "
            "'CONTRADICTS', 'IMPROVES_ON') AND target_paper_version_id IS NOT NULL "
            "AND target_paper_id IS NOT NULL AND paper_relation_id IS NOT NULL "
            "AND comparison_id IS NOT NULL AND analysis_id IS NULL "
            "AND source_paper_version_id <> target_paper_version_id) OR "
            "(relation_type IN ('ADDRESSES', 'USES_METHOD', 'TARGETS_TASK', "
            "'USES_DATASET', 'EVALUATES_ON') AND target_paper_version_id IS NULL "
            "AND target_paper_id IS NULL AND paper_relation_id IS NULL "
            "AND ((analysis_id IS NOT NULL AND comparison_id IS NULL) OR "
            "(analysis_id IS NULL AND comparison_id IS NOT NULL)))",
            name="ck_graph_edges_owner_shape",
        ),
        CheckConstraint("schema_version > 0", name="ck_graph_edges_schema_version"),
        ForeignKeyConstraint(
            ["source_entity_id", "topic_id"],
            ["graph_entities.id", "graph_entities.topic_id"],
            name="fk_graph_edges_source_entity",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["target_entity_id", "topic_id"],
            ["graph_entities.id", "graph_entities.topic_id"],
            name="fk_graph_edges_target_entity",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_paper_version_id", "source_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_graph_edges_source_paper_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["target_paper_version_id", "target_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_graph_edges_target_paper_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["analysis_id", "source_paper_id", "source_paper_version_id"],
            ["paper_analyses.id", "paper_analyses.paper_id", "paper_analyses.paper_version_id"],
            name="fk_graph_edges_analysis",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["paper_relation_id", "comparison_id"],
            ["paper_relations.id", "paper_relations.comparison_id"],
            name="fk_graph_edges_paper_relation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["publication_run_id", "topic_id"],
            ["daily_runs.id", "daily_runs.topic_id"],
            name="fk_graph_edges_publication_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["pipeline_execution_id"],
            ["pipeline_executions.id"],
            name="fk_graph_edges_pipeline_execution",
            ondelete="RESTRICT",
        ),
        Index("ix_graph_edges_topic_relation", "topic_id", "relation_type"),
        Index("ix_graph_edges_source", "source_entity_id"),
        Index("ix_graph_edges_target", "target_entity_id"),
        Index("ix_graph_edges_publication_run", "publication_run_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    publication_run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    pipeline_execution_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    topic_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_entity_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    target_entity_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_paper_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    target_paper_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    target_paper_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    analysis_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    comparison_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("comparisons.id", ondelete="CASCADE"),
        nullable=True,
    )
    paper_relation_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    configured_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GraphEdgeEvidenceLinkRow(Base):
    __tablename__ = "graph_edge_evidence_links"
    __table_args__ = (
        CheckConstraint(
            "evidence_role IN ('SOURCE', 'TARGET', 'RELATION')",
            name="ck_graph_edge_evidence_links_role",
        ),
        ForeignKeyConstraint(
            ["graph_edge_id", "topic_id"],
            ["graph_edges.id", "graph_edges.topic_id"],
            name="fk_graph_edge_evidence_links_edge",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "evidence_paper_id", "evidence_paper_version_id"],
            ["evidence.id", "evidence.paper_id", "evidence.paper_version_id"],
            name="fk_graph_edge_evidence_links_evidence",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "evidence_analysis_id"],
            ["evidence.id", "evidence.analysis_id"],
            name="fk_graph_edge_evidence_links_analysis",
            ondelete="CASCADE",
        ),
    )

    graph_edge_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    evidence_role: Mapped[str] = mapped_column(String(16), primary_key=True)
    topic_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_paper_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    evidence_analysis_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)


class TrendSnapshotRow(Base):
    __tablename__ = "trend_snapshots"
    __table_args__ = (
        UniqueConstraint("id", "topic_id", name="uq_trend_snapshots_topic_ownership"),
        UniqueConstraint(
            "topic_id",
            "as_of_date",
            "window",
            "aggregation_version",
            "pipeline_execution_id",
            name="uq_trend_snapshots_identity",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("\"window\" IN ('7D', '30D', '90D')", name="ck_trend_snapshots_window"),
        CheckConstraint(
            "(\"window\" = '7D' AND window_size_days = 7) OR "
            "(\"window\" = '30D' AND window_size_days = 30) OR "
            "(\"window\" = '90D' AND window_size_days = 90)",
            name="ck_trend_snapshots_window_size",
        ),
        CheckConstraint(
            "window_end = as_of_date AND window_end - window_start = window_size_days - 1 "
            "AND preceding_end = window_start - 1 "
            "AND preceding_end - preceding_start = window_size_days - 1",
            name="ck_trend_snapshots_periods",
        ),
        CheckConstraint(
            "included_paper_count >= 0 AND preceding_paper_count >= 0 "
            "AND paper_count_change = included_paper_count - preceding_paper_count "
            "AND paper_count_denominator = preceding_paper_count",
            name="ck_trend_snapshots_paper_counts",
        ),
        CheckConstraint(
            "entity_count >= 0 AND relation_count >= 0 "
            "AND new_entity_count BETWEEN 0 AND entity_count "
            "AND recurring_entity_count BETWEEN 0 AND entity_count",
            name="ck_trend_snapshots_graph_counts",
        ),
        CheckConstraint(
            "data_sufficiency IN ('SUFFICIENT', 'LIMITED', 'INSUFFICIENT') "
            "AND preceding_data_sufficiency IN ('SUFFICIENT', 'LIMITED', 'INSUFFICIENT')",
            name="ck_trend_snapshots_sufficiency",
        ),
        CheckConstraint(
            "growth_status IN ('AVAILABLE', 'ZERO_DENOMINATOR', 'LIMITED_SAMPLE')",
            name="ck_trend_snapshots_growth_status",
        ),
        CheckConstraint(
            "(growth_status = 'AVAILABLE' AND paper_count_denominator > 0 "
            "AND paper_growth_rate IS NOT NULL) OR "
            "(growth_status = 'ZERO_DENOMINATOR' AND paper_count_denominator = 0 "
            "AND paper_growth_rate IS NULL) OR "
            "(growth_status = 'LIMITED_SAMPLE' AND paper_growth_rate IS NULL)",
            name="ck_trend_snapshots_growth",
        ),
        CheckConstraint(
            "limited_paper_count BETWEEN 1 AND sufficient_paper_count "
            "AND minimum_growth_denominator > 0",
            name="ck_trend_snapshots_thresholds",
        ),
        CheckConstraint("schema_version > 0", name="ck_trend_snapshots_schema_version"),
        ForeignKeyConstraint(
            ["publication_run_id", "topic_id"],
            ["daily_runs.id", "daily_runs.topic_id"],
            name="fk_trend_snapshots_publication_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["pipeline_execution_id"],
            ["pipeline_executions.id"],
            name="fk_trend_snapshots_pipeline_execution",
            ondelete="RESTRICT",
        ),
        Index("ix_trend_snapshots_topic_as_of", "topic_id", "as_of_date"),
        Index("ix_trend_snapshots_publication_run", "publication_run_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    publication_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    pipeline_execution_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("topics.id", ondelete="CASCADE"),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    window: Mapped[str] = mapped_column(String(8), nullable=False)
    window_size_days: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    preceding_start: Mapped[date] = mapped_column(Date, nullable=False)
    preceding_end: Mapped[date] = mapped_column(Date, nullable=False)
    included_paper_count: Mapped[int] = mapped_column(Integer, nullable=False)
    preceding_paper_count: Mapped[int] = mapped_column(Integer, nullable=False)
    paper_count_change: Mapped[int] = mapped_column(Integer, nullable=False)
    paper_count_denominator: Mapped[int] = mapped_column(Integer, nullable=False)
    paper_growth_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    growth_status: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False)
    relation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    new_entity_count: Mapped[int] = mapped_column(Integer, nullable=False)
    recurring_entity_count: Mapped[int] = mapped_column(Integer, nullable=False)
    limited_paper_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sufficient_paper_count: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_growth_denominator: Mapped[int] = mapped_column(Integer, nullable=False)
    data_sufficiency: Mapped[str] = mapped_column(String(16), nullable=False)
    preceding_data_sufficiency: Mapped[str] = mapped_column(String(16), nullable=False)
    aggregation_version: Mapped[str] = mapped_column(String(100), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TrendMetricRow(Base):
    __tablename__ = "trend_metrics"
    __table_args__ = (
        UniqueConstraint("id", "snapshot_id", name="uq_trend_metrics_snapshot_ownership"),
        UniqueConstraint(
            "snapshot_id",
            "metric_kind",
            "entity_id",
            "relation_type",
            name="uq_trend_metrics_dimension",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "metric_kind IN ('PAPER_VOLUME', 'ENTITY', 'RELATION')",
            name="ck_trend_metrics_kind",
        ),
        CheckConstraint(
            "(metric_kind = 'PAPER_VOLUME' AND entity_id IS NULL "
            "AND entity_type IS NULL AND relation_type IS NULL) OR "
            "(metric_kind = 'ENTITY' AND entity_id IS NOT NULL "
            "AND entity_type IN ('RESEARCH_PROBLEM', 'METHOD', 'TASK', 'DATASET', 'BENCHMARK') "
            "AND relation_type IS NULL) OR "
            "(metric_kind = 'RELATION' AND entity_id IS NULL AND entity_type IS NULL "
            "AND relation_type IN ('CITES', 'SIMILAR_TO', 'EXTENDS', 'COMPARES_WITH', "
            "'CONTRADICTS', 'IMPROVES_ON', 'ADDRESSES', 'USES_METHOD', "
            "'TARGETS_TASK', 'USES_DATASET', 'EVALUATES_ON'))",
            name="ck_trend_metrics_dimension",
        ),
        CheckConstraint(
            "current_count >= 0 AND preceding_count >= 0 "
            "AND absolute_change = current_count - preceding_count "
            "AND denominator_count = preceding_count",
            name="ck_trend_metrics_counts",
        ),
        CheckConstraint(
            "growth_status IN ('AVAILABLE', 'ZERO_DENOMINATOR', 'LIMITED_SAMPLE')",
            name="ck_trend_metrics_growth_status",
        ),
        CheckConstraint(
            "(growth_status = 'AVAILABLE' AND preceding_count > 0 "
            "AND relative_change IS NOT NULL) OR "
            "(growth_status = 'ZERO_DENOMINATOR' AND preceding_count = 0 "
            "AND relative_change IS NULL) OR "
            "(growth_status = 'LIMITED_SAMPLE' AND relative_change IS NULL)",
            name="ck_trend_metrics_growth",
        ),
        CheckConstraint(
            "NOT (newly_appearing AND recurring) "
            "AND (NOT newly_appearing OR (current_count > 0 AND preceding_count = 0)) "
            "AND (NOT recurring OR (current_count > 0 AND preceding_count > 0))",
            name="ck_trend_metrics_lifecycle",
        ),
        CheckConstraint("schema_version > 0", name="ck_trend_metrics_schema_version"),
        ForeignKeyConstraint(
            ["snapshot_id", "topic_id"],
            ["trend_snapshots.id", "trend_snapshots.topic_id"],
            name="fk_trend_metrics_snapshot_topic",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["entity_id", "topic_id"],
            ["graph_entities.id", "graph_entities.topic_id"],
            name="fk_trend_metrics_entity_topic",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    topic_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    metric_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    relation_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    current_count: Mapped[int] = mapped_column(Integer, nullable=False)
    preceding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    absolute_change: Mapped[int] = mapped_column(Integer, nullable=False)
    denominator_count: Mapped[int] = mapped_column(Integer, nullable=False)
    relative_change: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    growth_status: Mapped[str] = mapped_column(String(32), nullable=False)
    newly_appearing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recurring: Mapped[bool] = mapped_column(Boolean, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TrendRepresentativePaperRow(Base):
    __tablename__ = "trend_representative_papers"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "position", name="uq_trend_representative_papers_position"),
        CheckConstraint(
            "position BETWEEN 0 AND 19",
            name="ck_trend_representative_papers_position",
        ),
        CheckConstraint("schema_version > 0", name="ck_trend_representative_papers_schema_version"),
        ForeignKeyConstraint(
            ["snapshot_id", "topic_id"],
            ["trend_snapshots.id", "trend_snapshots.topic_id"],
            name="fk_trend_representative_papers_snapshot",
            ondelete="CASCADE",
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    topic_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LineageSnapshotRow(Base):
    __tablename__ = "lineage_snapshots"
    __table_args__ = (
        UniqueConstraint("id", "topic_id", name="uq_lineage_snapshots_topic_ownership"),
        UniqueConstraint(
            "id",
            "topic_id",
            "root_paper_id",
            name="uq_lineage_snapshots_root_ownership",
        ),
        UniqueConstraint(
            "topic_id",
            "root_paper_id",
            "as_of_date",
            "max_depth",
            "max_nodes",
            "max_edges",
            "permitted_relation_types",
            "lineage_version",
            "pipeline_execution_id",
            name="uq_lineage_snapshots_identity",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "cardinality(permitted_relation_types) BETWEEN 1 AND 3 "
            "AND permitted_relation_types <@ "
            "ARRAY['CITES', 'EXTENDS', 'IMPROVES_ON']::varchar[]",
            name="ck_lineage_snapshots_relations",
        ),
        CheckConstraint(
            "max_depth BETWEEN 1 AND 10 AND max_nodes BETWEEN 1 AND 200 "
            "AND max_edges BETWEEN 1 AND 1000",
            name="ck_lineage_snapshots_limits",
        ),
        CheckConstraint(
            "node_count BETWEEN 1 AND max_nodes AND edge_count BETWEEN 0 AND max_edges",
            name="ck_lineage_snapshots_counts",
        ),
        CheckConstraint(
            "corpus_scope = 'CURRENTLY_RETRIEVED_CORPUS'",
            name="ck_lineage_snapshots_corpus_scope",
        ),
        CheckConstraint("schema_version > 0", name="ck_lineage_snapshots_schema_version"),
        ForeignKeyConstraint(
            ["publication_run_id", "topic_id"],
            ["daily_runs.id", "daily_runs.topic_id"],
            name="fk_lineage_snapshots_publication_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["pipeline_execution_id"],
            ["pipeline_executions.id"],
            name="fk_lineage_snapshots_pipeline_execution",
            ondelete="RESTRICT",
        ),
        Index("ix_lineage_snapshots_topic_as_of", "topic_id", "as_of_date"),
        Index("ix_lineage_snapshots_publication_run", "publication_run_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    publication_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    pipeline_execution_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("topics.id", ondelete="CASCADE"),
        nullable=False,
    )
    root_paper_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    permitted_relation_types: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    max_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    max_nodes: Mapped[int] = mapped_column(Integer, nullable=False)
    max_edges: Mapped[int] = mapped_column(Integer, nullable=False)
    node_count: Mapped[int] = mapped_column(Integer, nullable=False)
    edge_count: Mapped[int] = mapped_column(Integer, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    corpus_scope: Mapped[str] = mapped_column(String(40), nullable=False)
    explicit_predecessor_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    verified_predecessor_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    limitations: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False)
    lineage_version: Mapped[str] = mapped_column(String(100), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LineageNodeRow(Base):
    __tablename__ = "lineage_nodes"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "graph_entity_id",
            "topic_id",
            name="uq_lineage_nodes_snapshot_ownership",
        ),
        UniqueConstraint("snapshot_id", "paper_id", name="uq_lineage_nodes_paper"),
        UniqueConstraint("snapshot_id", "position", name="uq_lineage_nodes_position"),
        CheckConstraint("depth BETWEEN 0 AND 10", name="ck_lineage_nodes_depth"),
        CheckConstraint("position BETWEEN 0 AND 199", name="ck_lineage_nodes_position"),
        CheckConstraint("schema_version > 0", name="ck_lineage_nodes_schema_version"),
        ForeignKeyConstraint(
            ["snapshot_id", "topic_id"],
            ["lineage_snapshots.id", "lineage_snapshots.topic_id"],
            name="fk_lineage_nodes_snapshot_topic",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["graph_entity_id", "topic_id"],
            ["graph_entities.id", "graph_entities.topic_id"],
            name="fk_lineage_nodes_entity_topic",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["paper_id"],
            ["papers.id"],
            name="fk_lineage_nodes_paper",
            ondelete="CASCADE",
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    topic_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    graph_entity_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LineageEdgeRow(Base):
    __tablename__ = "lineage_edges"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "position", name="uq_lineage_edges_position"),
        CheckConstraint(
            "source_entity_id <> target_entity_id", name="ck_lineage_edges_distinct_nodes"
        ),
        CheckConstraint(
            "relation_type IN ('CITES', 'EXTENDS', 'IMPROVES_ON', 'COMPARES_WITH', 'CONTRADICTS')",
            name="ck_lineage_edges_relation_type",
        ),
        CheckConstraint(
            "provenance IN ('METADATA_EXPLICIT', 'TEXT_EXPLICIT', "
            "'DETERMINISTICALLY_DERIVED', 'LLM_INFERRED', 'HUMAN_VERIFIED')",
            name="ck_lineage_edges_provenance",
        ),
        CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_lineage_edges_verification",
        ),
        CheckConstraint("position BETWEEN 0 AND 999", name="ck_lineage_edges_position"),
        CheckConstraint("schema_version > 0", name="ck_lineage_edges_schema_version"),
        ForeignKeyConstraint(
            ["snapshot_id", "topic_id"],
            ["lineage_snapshots.id", "lineage_snapshots.topic_id"],
            name="fk_lineage_edges_snapshot_topic",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["graph_edge_id", "topic_id"],
            ["graph_edges.id", "graph_edges.topic_id"],
            name="fk_lineage_edges_graph_edge_topic",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "source_entity_id", "topic_id"],
            [
                "lineage_nodes.snapshot_id",
                "lineage_nodes.graph_entity_id",
                "lineage_nodes.topic_id",
            ],
            name="fk_lineage_edges_source_node",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "target_entity_id", "topic_id"],
            [
                "lineage_nodes.snapshot_id",
                "lineage_nodes.graph_entity_id",
                "lineage_nodes.topic_id",
            ],
            name="fk_lineage_edges_target_node",
            ondelete="CASCADE",
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    topic_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    graph_edge_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    source_entity_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    target_entity_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    uncertain: Mapped[bool] = mapped_column(Boolean, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReportSectionRow(Base):
    __tablename__ = "report_sections"
    __table_args__ = (
        UniqueConstraint("id", "report_id", name="uq_report_sections_ownership"),
        UniqueConstraint("report_id", "kind", name="uq_report_sections_kind"),
        UniqueConstraint("report_id", "position", name="uq_report_sections_position"),
        CheckConstraint(
            "kind IN ('OVERVIEW', 'TRENDS', 'COMPARISONS', 'LINEAGE', 'LIMITATIONS')",
            name="ck_report_sections_kind",
        ),
        CheckConstraint(
            "(kind = 'OVERVIEW' AND position = 0) OR "
            "(kind = 'TRENDS' AND position = 1) OR "
            "(kind = 'COMPARISONS' AND position = 2) OR "
            "(kind = 'LINEAGE' AND position = 3) OR "
            "(kind = 'LIMITATIONS' AND position = 4)",
            name="ck_report_sections_canonical_order",
        ),
        CheckConstraint("schema_version > 0", name="ck_report_sections_schema_version"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    report_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReportPaperHighlightRow(Base):
    __tablename__ = "report_paper_highlights"
    __table_args__ = (
        UniqueConstraint("id", "report_id", name="uq_report_paper_highlights_ownership"),
        UniqueConstraint(
            "report_id", "paper_version_id", name="uq_report_paper_highlights_version"
        ),
        UniqueConstraint("report_id", "position", name="uq_report_paper_highlights_position"),
        CheckConstraint("position BETWEEN 0 AND 199", name="ck_report_paper_highlights_position"),
        CheckConstraint("schema_version > 0", name="ck_report_paper_highlights_schema_version"),
        ForeignKeyConstraint(
            ["paper_version_id", "paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_report_paper_highlights_version",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    report_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReportEntityHighlightRow(Base):
    __tablename__ = "report_entity_highlights"
    __table_args__ = (
        UniqueConstraint("id", "report_id", name="uq_report_entity_highlights_ownership"),
        UniqueConstraint("report_id", "graph_entity_id", name="uq_report_entity_highlights_entity"),
        UniqueConstraint("report_id", "position", name="uq_report_entity_highlights_position"),
        CheckConstraint(
            "entity_type IN ('PAPER', 'RESEARCH_PROBLEM', 'METHOD', 'TASK', "
            "'DATASET', 'BENCHMARK')",
            name="ck_report_entity_highlights_type",
        ),
        CheckConstraint(
            "distinct_paper_count > 0",
            name="ck_report_entity_highlights_distinct_papers",
        ),
        CheckConstraint("position BETWEEN 0 AND 199", name="ck_report_entity_highlights_position"),
        CheckConstraint("schema_version > 0", name="ck_report_entity_highlights_schema_version"),
        ForeignKeyConstraint(
            ["report_id", "topic_id"],
            ["reports.id", "reports.topic_id"],
            name="fk_report_entity_highlights_report_topic",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["graph_entity_id", "topic_id"],
            ["graph_entities.id", "graph_entities.topic_id"],
            name="fk_report_entity_highlights_entity_topic",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    report_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    topic_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    graph_entity_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    distinct_paper_count: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReportComparisonHighlightRow(Base):
    __tablename__ = "report_comparison_highlights"
    __table_args__ = (
        UniqueConstraint("id", "report_id", name="uq_report_comparison_highlights_ownership"),
        UniqueConstraint(
            "report_id", "comparison_id", name="uq_report_comparison_highlights_comparison"
        ),
        UniqueConstraint("report_id", "position", name="uq_report_comparison_highlights_position"),
        CheckConstraint(
            "comparability_status IN ('DIRECTLY_COMPARABLE', 'PARTIALLY_COMPARABLE', "
            "'NOT_DIRECTLY_COMPARABLE', 'INSUFFICIENT_EVIDENCE')",
            name="ck_report_comparison_highlights_comparability",
        ),
        CheckConstraint(
            "position BETWEEN 0 AND 199", name="ck_report_comparison_highlights_position"
        ),
        CheckConstraint(
            "schema_version > 0", name="ck_report_comparison_highlights_schema_version"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    report_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    comparison_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("comparisons.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    comparability_status: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReportTrendLinkRow(Base):
    __tablename__ = "report_trend_links"
    __table_args__ = (
        UniqueConstraint("report_id", "snapshot_id", name="uq_report_trend_links_snapshot"),
        UniqueConstraint("report_id", "position", name="uq_report_trend_links_position"),
        CheckConstraint("position BETWEEN 0 AND 2", name="ck_report_trend_links_position"),
        CheckConstraint("schema_version > 0", name="ck_report_trend_links_schema_version"),
        ForeignKeyConstraint(
            ["report_id", "topic_id"],
            ["reports.id", "reports.topic_id"],
            name="fk_report_trend_links_report_topic",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "topic_id"],
            ["trend_snapshots.id", "trend_snapshots.topic_id"],
            name="fk_report_trend_links_snapshot_topic",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    report_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    topic_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReportLineageHighlightRow(Base):
    __tablename__ = "report_lineage_highlights"
    __table_args__ = (
        UniqueConstraint("id", "report_id", name="uq_report_lineage_highlights_ownership"),
        UniqueConstraint(
            "report_id", "lineage_snapshot_id", name="uq_report_lineage_highlights_snapshot"
        ),
        UniqueConstraint("report_id", "position", name="uq_report_lineage_highlights_position"),
        CheckConstraint("position BETWEEN 0 AND 199", name="ck_report_lineage_highlights_position"),
        CheckConstraint("schema_version > 0", name="ck_report_lineage_highlights_schema_version"),
        ForeignKeyConstraint(
            ["report_id", "topic_id"],
            ["reports.id", "reports.topic_id"],
            name="fk_report_lineage_highlights_report_topic",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["lineage_snapshot_id", "topic_id", "root_paper_id"],
            [
                "lineage_snapshots.id",
                "lineage_snapshots.topic_id",
                "lineage_snapshots.root_paper_id",
            ],
            name="fk_report_lineage_highlights_snapshot_topic",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    report_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    topic_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    lineage_snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    root_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    uncertain: Mapped[bool] = mapped_column(Boolean, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReportEvidenceLinkRow(Base):
    __tablename__ = "report_evidence_links"
    __table_args__ = (
        UniqueConstraint(
            "report_id",
            "evidence_id",
            "context_type",
            "report_section_id",
            "paper_highlight_id",
            "comparison_highlight_id",
            name="uq_report_evidence_links_context",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "(context_type = 'REPORT' AND report_section_id IS NULL "
            "AND paper_highlight_id IS NULL AND comparison_highlight_id IS NULL) OR "
            "(context_type = 'SECTION' AND report_section_id IS NOT NULL "
            "AND paper_highlight_id IS NULL AND comparison_highlight_id IS NULL) OR "
            "(context_type = 'PAPER_HIGHLIGHT' AND report_section_id IS NULL "
            "AND paper_highlight_id IS NOT NULL AND comparison_highlight_id IS NULL) OR "
            "(context_type = 'COMPARISON_HIGHLIGHT' AND report_section_id IS NULL "
            "AND paper_highlight_id IS NULL AND comparison_highlight_id IS NOT NULL)",
            name="ck_report_evidence_links_context",
        ),
        CheckConstraint("schema_version > 0", name="ck_report_evidence_links_schema_version"),
        ForeignKeyConstraint(
            ["report_section_id", "report_id"],
            ["report_sections.id", "report_sections.report_id"],
            name="fk_report_evidence_links_section",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["paper_highlight_id", "report_id"],
            ["report_paper_highlights.id", "report_paper_highlights.report_id"],
            name="fk_report_evidence_links_paper_highlight",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["comparison_highlight_id", "report_id"],
            ["report_comparison_highlights.id", "report_comparison_highlights.report_id"],
            name="fk_report_evidence_links_comparison_highlight",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "evidence_paper_id", "evidence_paper_version_id"],
            ["evidence.id", "evidence.paper_id", "evidence.paper_version_id"],
            name="fk_report_evidence_links_evidence",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "evidence_analysis_id"],
            ["evidence.id", "evidence.analysis_id"],
            name="fk_report_evidence_links_analysis",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    report_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_paper_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_paper_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    evidence_analysis_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    context_type: Mapped[str] = mapped_column(String(32), nullable=False)
    report_section_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    paper_highlight_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    comparison_highlight_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
