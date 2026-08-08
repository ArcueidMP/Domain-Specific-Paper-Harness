"""Normalized SQLAlchemy models for M1 ingestion and read APIs."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TopicRow(Base):
    __tablename__ = "topics"
    __table_args__ = (
        CheckConstraint("schema_version > 0", name="ck_topics_schema_version_positive"),
        CheckConstraint("overlap_hours > 0", name="ck_topics_overlap_positive"),
        CheckConstraint("initial_lookback_days > 0", name="ck_topics_lookback_positive"),
        CheckConstraint("max_results > 0", name="ck_topics_max_results_positive"),
        CheckConstraint(
            "representative_full_text_count > 0", name="ck_topics_representative_count_positive"
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


class DailyRunRow(Base):
    __tablename__ = "daily_runs"
    __table_args__ = (
        UniqueConstraint(
            "topic_id", "logical_date", "operation", name="uq_daily_runs_topic_date_operation"
        ),
        CheckConstraint("discovered_count >= 0", name="ck_daily_runs_discovered_nonnegative"),
        CheckConstraint("normalized_count >= 0", name="ck_daily_runs_normalized_nonnegative"),
        CheckConstraint("failed_count >= 0", name="ck_daily_runs_failed_nonnegative"),
        CheckConstraint("schema_version > 0", name="ck_daily_runs_schema_version_positive"),
        CheckConstraint("operation IN ('ARXIV_INGESTION')", name="ck_daily_runs_operation_allowed"),
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE', 'PARTIAL', 'FAILED')",
            name="ck_daily_runs_status_allowed",
        ),
        CheckConstraint("cursor_from <= cursor_to", name="ck_daily_runs_cursor_order"),
        CheckConstraint(
            "normalized_count <= discovered_count", name="ck_daily_runs_normalized_lte_discovered"
        ),
        CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL)",
            name="ck_daily_runs_completion_state",
        ),
        CheckConstraint(
            "status <> 'FAILED' OR error_code IS NOT NULL", name="ck_daily_runs_failed_error"
        ),
        Index("ix_daily_runs_started_at", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("topics.id", ondelete="RESTRICT"), nullable=False
    )
    logical_date: Mapped[date] = mapped_column(Date, nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cursor_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cursor_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    normalized_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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
        CheckConstraint("schema_version > 0", name="ck_run_items_schema_version_positive"),
        CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED')",
            name="ck_run_items_status_allowed",
        ),
        CheckConstraint(
            "stage IN ('DISCOVERED', 'NORMALIZED', 'ENRICHED', 'RELEVANCE_SCORED', "
            "'SELECTED', 'PDF_DOWNLOADED', 'PARSED', 'ANALYZED', 'EVIDENCE_EXTRACTED', "
            "'PRIOR_WORK_RETRIEVED', 'COMPARED', 'GRAPH_UPDATED', 'PUBLISHED')",
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
