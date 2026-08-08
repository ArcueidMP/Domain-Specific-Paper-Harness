"""Normalized SQLAlchemy models for M1 ingestion and read APIs."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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
        CheckConstraint(
            "operation IN ('ARXIV_INGESTION', 'STRUCTURED_ANALYSIS')",
            name="ck_daily_runs_operation_allowed",
        ),
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE', 'PARTIAL', 'FAILED')",
            name="ck_daily_runs_status_allowed",
        ),
        CheckConstraint(
            "(operation = 'ARXIV_INGESTION' AND cursor_from IS NOT NULL "
            "AND cursor_to IS NOT NULL AND cursor_from <= cursor_to "
            "AND analysis_scope IS NULL AND selected_count = 0 AND completed_count = 0) OR "
            "(operation = 'STRUCTURED_ANALYSIS' AND cursor_from IS NULL "
            "AND cursor_to IS NULL AND analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT'))",
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
        UniqueConstraint("run_id", name="uq_reports_run"),
        UniqueConstraint("id", "run_id", name="uq_reports_ownership"),
        CheckConstraint("status IN ('COMPLETE', 'PARTIAL')", name="ck_reports_status_allowed"),
        CheckConstraint("schema_version > 0", name="ck_reports_schema_version_positive"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("daily_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    topic_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("topics.id", ondelete="RESTRICT"),
        nullable=False,
    )
    logical_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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
