"""Framework-independent M1 domain models."""

from __future__ import annotations

import math
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
    HISTORICAL_ANALYSIS = "HISTORICAL_ANALYSIS"
    PRODUCT_PUBLICATION = "PRODUCT_PUBLICATION"


class PipelineExecutionMode(StrEnum):
    """Preselected execution policy for an operator command or full Daily pipeline."""

    STANDALONE = "STANDALONE"
    NORMAL = "NORMAL"
    REPROCESS = "REPROCESS"
    SMOKE = "SMOKE"


@dataclass(frozen=True, slots=True)
class PipelineExecutionContract:
    narrative_mode: str
    llm_provider: str
    llm_configured_model: str
    analysis_prompt_version: str
    parser_name: str | None
    parser_version: str | None
    backfill_max_queries: int
    backfill_per_query_limit: int
    backfill_timeout_seconds: float
    search_max_steps: int
    search_max_queries: int
    search_max_queue_size: int
    search_max_citation_depth: int
    search_max_candidates: int
    search_max_selected_candidates: int
    search_per_operation_timeout_seconds: float
    search_overall_timeout_seconds: float
    max_comparisons_per_paper: int
    pipeline_timeout_seconds: int
    crawler_prompt_version: str
    selector_prompt_version: str
    comparison_prompt_version: str
    report_prompt_version: str
    daily_selection_policy_version: str
    pipeline_orchestration_version: str
    embedding_model_identifier: str
    embedding_model_revision: str
    embedding_tokenizer_identifier: str
    embedding_tokenizer_revision: str
    embedding_dimension: int
    embedding_preprocessing_contract: str
    embedding_model_provenance: str
    embedding_source: str
    topic_categories: tuple[str, ...]
    topic_include_terms: tuple[str, ...]
    topic_exclude_terms: tuple[str, ...]
    topic_overlap_hours: int
    topic_initial_lookback_days: int
    topic_max_results: int
    topic_representative_full_text_count: int

    def __post_init__(self) -> None:
        bounded_text_values = (
            self.narrative_mode,
            self.llm_provider,
            self.llm_configured_model,
            self.analysis_prompt_version,
            self.crawler_prompt_version,
            self.selector_prompt_version,
            self.comparison_prompt_version,
            self.report_prompt_version,
            self.daily_selection_policy_version,
            self.pipeline_orchestration_version,
            self.embedding_model_identifier,
            self.embedding_model_revision,
            self.embedding_tokenizer_identifier,
            self.embedding_tokenizer_revision,
            self.embedding_source,
        )
        if any(not value.strip() or len(value) > 300 for value in bounded_text_values):
            raise DomainInvariantError("pipeline execution contract text is invalid")
        if any(
            not value.strip() or len(value) > 1000
            for value in (
                self.embedding_preprocessing_contract,
                self.embedding_model_provenance,
            )
        ):
            raise DomainInvariantError("pipeline embedding contract text is invalid")
        if (self.parser_name is None) != (self.parser_version is None):
            raise DomainInvariantError("pipeline execution parser contract must be complete")
        if any(
            value is not None and (not value.strip() or len(value) > 200)
            for value in (self.parser_name, self.parser_version)
        ):
            raise DomainInvariantError("pipeline execution parser contract is invalid")
        positive_counts = (
            self.backfill_max_queries,
            self.backfill_per_query_limit,
            self.search_max_steps,
            self.search_max_queries,
            self.search_max_queue_size,
            self.search_max_candidates,
            self.search_max_selected_candidates,
            self.max_comparisons_per_paper,
            self.pipeline_timeout_seconds,
            self.embedding_dimension,
            self.topic_overlap_hours,
            self.topic_initial_lookback_days,
            self.topic_max_results,
            self.topic_representative_full_text_count,
        )
        if any(value < 1 for value in positive_counts):
            raise DomainInvariantError("pipeline execution contract bounds must be positive")
        if not 0 <= self.search_max_citation_depth <= 5:
            raise DomainInvariantError("pipeline execution citation depth is invalid")
        timeout_values = (
            self.backfill_timeout_seconds,
            self.search_per_operation_timeout_seconds,
            self.search_overall_timeout_seconds,
        )
        if any(not math.isfinite(value) or value <= 0 for value in timeout_values):
            raise DomainInvariantError("pipeline execution contract timeout is invalid")
        for values, name in (
            (self.topic_categories, "categories"),
            (self.topic_include_terms, "include terms"),
        ):
            if not values or any(not value.strip() or len(value) > 500 for value in values):
                raise DomainInvariantError(f"pipeline topic {name} are invalid")
        if any(not value.strip() or len(value) > 500 for value in self.topic_exclude_terms):
            raise DomainInvariantError("pipeline topic exclude terms are invalid")


@dataclass(frozen=True, slots=True)
class PipelineExecution:
    """Durable owner and terminal outcome for one full Daily pipeline."""

    id: UUID
    topic_id: UUID
    logical_date: date
    execution_mode: PipelineExecutionMode
    analysis_scope: AnalysisScope
    selection_limit: int
    contract: PipelineExecutionContract
    status: RunStatus
    deadline_at: datetime
    started_at: datetime
    completed_at: datetime | None
    error_code: str | None
    error_detail: str | None
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        from paper_harness.domain.identity import stable_pipeline_execution_id

        if self.execution_mode not in (
            PipelineExecutionMode.NORMAL,
            PipelineExecutionMode.REPROCESS,
        ):
            raise DomainInvariantError("pipeline execution must use a publishable mode")
        if not 1 <= self.selection_limit <= 200:
            raise DomainInvariantError("pipeline selection limit is outside the supported bound")
        if (
            self.execution_mode is PipelineExecutionMode.NORMAL
            and self.id
            != stable_pipeline_execution_id(
                self.topic_id,
                self.logical_date,
            )
        ):
            raise DomainInvariantError("pipeline execution ID is not stable for its scope")
        _require_aware(self.deadline_at, "deadline_at")
        _require_aware(self.started_at, "started_at")
        _require_aware(self.created_at, "created_at")
        if self.deadline_at <= self.started_at:
            raise DomainInvariantError("pipeline deadline must follow its start time")
        if self.status is RunStatus.RUNNING:
            if self.completed_at is not None or self.error_code is not None:
                raise DomainInvariantError("running pipeline execution cannot be terminal")
        else:
            if self.completed_at is None:
                raise DomainInvariantError("terminal pipeline execution needs completed_at")
            _require_aware(self.completed_at, "completed_at")
        if self.status is RunStatus.FAILED and not self.error_code:
            raise DomainInvariantError("failed pipeline execution needs a stable error code")
        if self.error_code is not None and len(self.error_code) > 80:
            raise DomainInvariantError("pipeline error code exceeds the persistence bound")
        if self.error_detail is not None and len(self.error_detail) > 1000:
            raise DomainInvariantError("pipeline error detail exceeds the persistence bound")
        if self.status is not RunStatus.FAILED and any(
            value is not None for value in (self.error_code, self.error_detail)
        ):
            raise DomainInvariantError("non-failed pipeline execution cannot carry failure data")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")


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
    pipeline_execution_mode: PipelineExecutionMode = PipelineExecutionMode.STANDALONE
    pipeline_selection_limit: int | None = None
    pipeline_execution_id: UUID | None = None

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
        elif self.operation in (
            RunOperation.STRUCTURED_ANALYSIS,
            RunOperation.HISTORICAL_ANALYSIS,
        ):
            if self.source_run_id is not None:
                raise DomainInvariantError("analysis run cannot reference a source run")
            if self.analysis_scope is None:
                raise DomainInvariantError("analysis run requires a preselected scope")
            if self.cursor_from is not None or self.cursor_to is not None:
                raise DomainInvariantError("analysis run cannot carry an ingestion cursor window")
        elif self.operation is RunOperation.PRODUCT_PUBLICATION:
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
        else:
            raise DomainInvariantError("daily run operation is unsupported")
        if self.completed_count > self.selected_count:
            raise DomainInvariantError("completed count cannot exceed selected count")
        if self.failed_count > self.selected_count and self.operation in (
            RunOperation.STRUCTURED_ANALYSIS,
            RunOperation.HISTORICAL_ANALYSIS,
            RunOperation.PRODUCT_PUBLICATION,
        ):
            raise DomainInvariantError("failed count cannot exceed selected count")
        if self.status is RunStatus.RUNNING and self.completed_at is not None:
            raise DomainInvariantError("a running run cannot be completed")
        if self.status is not RunStatus.RUNNING and self.completed_at is None:
            raise DomainInvariantError("a terminal run needs completed_at")
        if self.status is RunStatus.FAILED and not self.error_code:
            raise DomainInvariantError("a failed run needs a stable error code")
        if self.pipeline_execution_mode is PipelineExecutionMode.STANDALONE:
            if self.pipeline_selection_limit is not None or self.pipeline_execution_id is not None:
                raise DomainInvariantError("standalone runs cannot carry full-pipeline provenance")
        else:
            if (
                self.pipeline_selection_limit is None
                or not 1 <= self.pipeline_selection_limit <= 200
                or self.pipeline_execution_id is None
            ):
                raise DomainInvariantError(
                    "full-pipeline runs require an execution and bounded paper limit"
                )
            if (
                self.pipeline_execution_mode is PipelineExecutionMode.SMOKE
                and self.pipeline_selection_limit > 5
            ):
                raise DomainInvariantError("smoke pipeline selection cannot exceed five papers")


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
