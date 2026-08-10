"""Framework-independent M1 domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from paper_harness.domain.analysis import AnalysisScope
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import normalize_author_name, validate_canonical_arxiv_id

MAX_REPRESENTATIVE_FULL_TEXT_COUNT = 200


class PaperStage(StrEnum):
    DISCOVERED = "DISCOVERED"
    NORMALIZED = "NORMALIZED"
    ENRICHED = "ENRICHED"
    RELEVANCE_SCORED = "RELEVANCE_SCORED"
    SELECTED = "SELECTED"
    PDF_DOWNLOADED = "PDF_DOWNLOADED"
    PARSED = "PARSED"
    ANALYZED = "ANALYZED"
    EVIDENCE_EXTRACTED = "EVIDENCE_EXTRACTED"
    PRIOR_WORK_RETRIEVED = "PRIOR_WORK_RETRIEVED"
    COMPARED = "COMPARED"
    GRAPH_UPDATED = "GRAPH_UPDATED"
    TREND_SNAPSHOTS_GENERATED = "TREND_SNAPSHOTS_GENERATED"
    REPORT_GENERATED = "REPORT_GENERATED"
    PUBLISHED = "PUBLISHED"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class RunItemStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RunOperation(StrEnum):
    ARXIV_INGESTION = "ARXIV_INGESTION"
    STRUCTURED_ANALYSIS = "STRUCTURED_ANALYSIS"
    PRODUCT_PUBLICATION = "PRODUCT_PUBLICATION"


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainInvariantError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class TopicConfig:
    id: UUID
    slug: str
    name: str
    description: str
    categories: tuple[str, ...]
    include_terms: tuple[str, ...]
    exclude_terms: tuple[str, ...]
    overlap_hours: int
    initial_lookback_days: int
    max_results: int
    representative_full_text_count: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.slug or not self.name or not self.description:
            raise DomainInvariantError("topic slug, name, and description are required")
        if not self.categories or not self.include_terms:
            raise DomainInvariantError("topic needs at least one category and include term")
        if self.overlap_hours < 1 or self.initial_lookback_days < 1:
            raise DomainInvariantError("discovery overlap and initial lookback must be positive")
        if self.max_results < 1 or self.representative_full_text_count < 1:
            raise DomainInvariantError("topic result limits must be positive")
        if self.representative_full_text_count > MAX_REPRESENTATIVE_FULL_TEXT_COUNT:
            raise DomainInvariantError("representative full-text count exceeds the run bound")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class Author:
    id: UUID
    name: str
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        normalize_author_name(self.name)
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class Paper:
    id: UUID
    canonical_arxiv_id: str
    title: str
    abstract: str
    current_version: int
    first_submitted_at: datetime
    latest_updated_at: datetime
    primary_category: str
    categories: tuple[str, ...]
    authors: tuple[str, ...]
    pdf_url: str
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        validate_canonical_arxiv_id(self.canonical_arxiv_id)
        if self.current_version < 1:
            raise DomainInvariantError("current paper version must be positive")
        if not self.title or not self.abstract or not self.primary_category:
            raise DomainInvariantError("paper title, abstract, and primary category are required")
        if not self.categories or not self.authors:
            raise DomainInvariantError("paper categories and authors are required")
        _require_aware(self.first_submitted_at, "first_submitted_at")
        _require_aware(self.latest_updated_at, "latest_updated_at")
        _require_aware(self.created_at, "created_at")
        if self.latest_updated_at < self.first_submitted_at:
            raise DomainInvariantError("paper update cannot precede submission")


@dataclass(frozen=True, slots=True)
class PaperVersion:
    id: UUID
    paper_id: UUID
    canonical_arxiv_id: str
    version: int
    title: str
    abstract: str
    submitted_at: datetime
    updated_at: datetime
    primary_category: str
    categories: tuple[str, ...]
    authors: tuple[str, ...]
    pdf_url: str
    source_url: str
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        validate_canonical_arxiv_id(self.canonical_arxiv_id)
        if self.version < 1:
            raise DomainInvariantError("paper version must be positive")
        if not self.title or not self.abstract or not self.source_url:
            raise DomainInvariantError("paper version metadata is incomplete")
        _require_aware(self.submitted_at, "submitted_at")
        _require_aware(self.updated_at, "updated_at")
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class PaperSourceIdentity:
    id: UUID
    paper_id: UUID
    paper_version_id: UUID
    source: str
    external_id: str
    source_version: str
    source_url: str
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        if self.source != "arxiv":
            raise DomainInvariantError("paper source identities must be versioned arXiv identities")
        if not self.external_id or not self.source_version or not self.source_url:
            raise DomainInvariantError("source identity metadata is incomplete")
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class IngestionCursor:
    topic_id: UUID
    watermark: datetime
    schema_version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.watermark, "watermark")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class DailyRun:
    id: UUID
    topic_id: UUID
    logical_date: date
    operation: RunOperation
    analysis_scope: AnalysisScope | None
    status: RunStatus
    started_at: datetime
    completed_at: datetime | None
    cursor_from: datetime | None
    cursor_to: datetime | None
    discovered_count: int
    normalized_count: int
    selected_count: int
    completed_count: int
    failed_count: int
    error_code: str | None
    error_detail: str | None
    schema_version: int
    created_at: datetime
    source_run_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_aware(self.started_at, "started_at")
        if self.cursor_from is not None:
            _require_aware(self.cursor_from, "cursor_from")
        if self.cursor_to is not None:
            _require_aware(self.cursor_to, "cursor_to")
        _require_aware(self.created_at, "created_at")
        if self.completed_at is not None:
            _require_aware(self.completed_at, "completed_at")
        if (
            min(
                self.discovered_count,
                self.normalized_count,
                self.selected_count,
                self.completed_count,
                self.failed_count,
            )
            < 0
        ):
            raise DomainInvariantError("run counts cannot be negative")
        if self.operation is RunOperation.ARXIV_INGESTION:
            if self.source_run_id is not None:
                raise DomainInvariantError("arXiv ingestion run cannot reference a source run")
            if self.analysis_scope is not None:
                raise DomainInvariantError("arXiv ingestion run cannot carry an analysis scope")
            if self.cursor_from is None or self.cursor_to is None:
                raise DomainInvariantError("arXiv ingestion run requires a cursor window")
            if self.cursor_from > self.cursor_to:
                raise DomainInvariantError("run cursor window is reversed")
            if self.selected_count or self.completed_count:
                raise DomainInvariantError("ingestion run cannot carry analysis counts")
        elif self.operation is RunOperation.STRUCTURED_ANALYSIS:
            if self.source_run_id is not None:
                raise DomainInvariantError("structured analysis run cannot reference a source run")
            if self.analysis_scope is None:
                raise DomainInvariantError("structured analysis run requires a preselected scope")
            if self.cursor_from is not None or self.cursor_to is not None:
                raise DomainInvariantError("analysis run cannot carry an ingestion cursor window")
        else:
            if self.source_run_id is None:
                raise DomainInvariantError("product publication run requires a source run")
            if self.analysis_scope is not None:
                raise DomainInvariantError("product publication run cannot carry an analysis scope")
            if self.cursor_from is not None or self.cursor_to is not None:
                raise DomainInvariantError(
                    "product publication run cannot carry an ingestion cursor window"
                )
            if self.discovered_count or self.normalized_count:
                raise DomainInvariantError("product publication run cannot carry ingestion counts")
        if self.completed_count > self.selected_count:
            raise DomainInvariantError("completed count cannot exceed selected count")
        if self.failed_count > self.selected_count and self.operation in (
            RunOperation.STRUCTURED_ANALYSIS,
            RunOperation.PRODUCT_PUBLICATION,
        ):
            raise DomainInvariantError("failed count cannot exceed selected count")
        if self.status is RunStatus.RUNNING and self.completed_at is not None:
            raise DomainInvariantError("a running run cannot be completed")
        if self.status is not RunStatus.RUNNING and self.completed_at is None:
            raise DomainInvariantError("a terminal run needs completed_at")
        if self.status is RunStatus.FAILED and not self.error_code:
            raise DomainInvariantError("a failed run needs a stable error code")


@dataclass(frozen=True, slots=True)
class RunItem:
    id: UUID
    run_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    stage: PaperStage
    status: RunItemStatus
    failed_stage: PaperStage | None
    error_code: str | None
    retryable: bool | None
    error_detail: str | None
    schema_version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.status is RunItemStatus.FAILED:
            if self.failed_stage is None or not self.error_code or self.retryable is None:
                raise DomainInvariantError("a failed item needs stage, code, and retryability")
        elif any(
            value is not None
            for value in (self.failed_stage, self.error_code, self.retryable, self.error_detail)
        ):
            raise DomainInvariantError("a non-failed item cannot carry failure metadata")
