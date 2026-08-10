"""Framework-independent M3 historical-search and comparison models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from paper_harness.domain.analysis import AnalysisScope, ModelUsage, VerificationStatus
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import validate_canonical_arxiv_id

M3_CRAWLER_PROMPT_VERSION = "m3-crawler-v1"
M3_SELECTOR_PROMPT_VERSION = "m3-selector-v1"
M3_COMPARISON_PROMPT_VERSION = "m3-comparison-v1"
MAX_SELECTOR_CANDIDATES = 300
MAX_HISTORICAL_QUERIES = 40
MAX_HISTORICAL_RESULTS_PER_QUERY = 500
MAX_HISTORICAL_TIMEOUT_SECONDS = 7200.0


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainInvariantError(f"{name} must be timezone-aware")


def _require_text(value: str, name: str, *, maximum: int) -> None:
    if not value.strip():
        raise DomainInvariantError(f"{name} must not be empty")
    if "\x00" in value or len(value) > maximum:
        raise DomainInvariantError(f"{name} is invalid or exceeds {maximum} characters")


def _require_unit_score(value: float, name: str) -> None:
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise DomainInvariantError(f"{name} must be a finite score between zero and one")


class SearchSessionStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class SearchStopReason(StrEnum):
    QUEUE_EXHAUSTED = "QUEUE_EXHAUSTED"
    MAX_STEPS = "MAX_STEPS"
    MAX_QUERIES = "MAX_QUERIES"
    MAX_QUEUE_SIZE = "MAX_QUEUE_SIZE"
    MAX_CANDIDATES = "MAX_CANDIDATES"
    MAX_SELECTED_CANDIDATES = "MAX_SELECTED_CANDIDATES"
    OVERALL_TIMEOUT = "OVERALL_TIMEOUT"
    FAILED = "FAILED"


class SearchActionStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SearchTool(StrEnum):
    SEARCH_PAPERS = "search_papers"
    GET_PAPER = "get_paper"
    GET_REFERENCES = "get_references"
    GET_CITATIONS = "get_citations"
    GET_RECOMMENDATIONS = "get_recommendations"
    READ_ARXIV_PAPER = "read_arxiv_paper"


class CandidateOrigin(StrEnum):
    SEARCH = "SEARCH"
    REFERENCES = "REFERENCES"
    CITATIONS = "CITATIONS"
    RECOMMENDATIONS = "RECOMMENDATIONS"
    LOCAL_LEXICAL = "LOCAL_LEXICAL"
    LOCAL_VECTOR = "LOCAL_VECTOR"


class SelectionDecision(StrEnum):
    PENDING = "PENDING"
    SELECTED = "SELECTED"
    REJECTED = "REJECTED"


class BackfillStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class ComparabilityStatus(StrEnum):
    DIRECTLY_COMPARABLE = "DIRECTLY_COMPARABLE"
    PARTIALLY_COMPARABLE = "PARTIALLY_COMPARABLE"
    NOT_DIRECTLY_COMPARABLE = "NOT_DIRECTLY_COMPARABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ComparisonDimensionName(StrEnum):
    RESEARCH_PROBLEM = "RESEARCH_PROBLEM"
    TASK = "TASK"
    METHOD = "METHOD"
    ARCHITECTURE = "ARCHITECTURE"
    DATASETS = "DATASETS"
    BENCHMARKS = "BENCHMARKS"
    BASELINES = "BASELINES"
    METRICS = "METRICS"
    REPORTED_RESULTS = "REPORTED_RESULTS"
    COMPUTE_OR_INFERENCE_BUDGET = "COMPUTE_OR_INFERENCE_BUDGET"
    CLAIMED_NOVELTY = "CLAIMED_NOVELTY"
    LIMITATIONS = "LIMITATIONS"
    CODE_AVAILABILITY = "CODE_AVAILABILITY"
    RESULT_COMPARABILITY = "RESULT_COMPARABILITY"


COMPARISON_DIMENSION_ORDER = tuple(ComparisonDimensionName)


class PaperRelationType(StrEnum):
    CITES = "CITES"
    SIMILAR_TO = "SIMILAR_TO"
    EXTENDS = "EXTENDS"
    COMPARES_WITH = "COMPARES_WITH"
    CONTRADICTS = "CONTRADICTS"
    IMPROVES_ON = "IMPROVES_ON"


class RelationProvenance(StrEnum):
    METADATA_EXPLICIT = "METADATA_EXPLICIT"
    TEXT_EXPLICIT = "TEXT_EXPLICIT"
    DETERMINISTICALLY_DERIVED = "DETERMINISTICALLY_DERIVED"
    LLM_INFERRED = "LLM_INFERRED"
    HUMAN_VERIFIED = "HUMAN_VERIFIED"


@dataclass(frozen=True, slots=True)
class SearchLimits:
    max_steps: int = 24
    max_queries: int = 8
    max_queue_size: int = 200
    max_citation_depth: int = 2
    max_candidates: int = 300
    max_selected_candidates: int = 20
    per_operation_timeout_seconds: float = 60.0
    overall_timeout_seconds: float = 600.0

    def __post_init__(self) -> None:
        bounds = (
            (self.max_steps, 1, 100, "max_steps"),
            (self.max_queries, 1, 40, "max_queries"),
            (self.max_queue_size, 1, 2000, "max_queue_size"),
            (self.max_citation_depth, 0, 5, "max_citation_depth"),
            (self.max_candidates, 1, 5000, "max_candidates"),
            (self.max_selected_candidates, 1, 100, "max_selected_candidates"),
        )
        for value, minimum, maximum, name in bounds:
            if not minimum <= value <= maximum:
                raise DomainInvariantError(f"{name} must be between {minimum} and {maximum}")
        if self.max_selected_candidates > self.max_candidates:
            raise DomainInvariantError("selected-candidate limit cannot exceed candidate limit")
        if self.max_selected_candidates > self.max_queue_size:
            raise DomainInvariantError("selected-candidate limit cannot exceed queue limit")
        if min(self.max_queue_size, self.max_candidates) > MAX_SELECTOR_CANDIDATES:
            raise DomainInvariantError(
                f"effective candidate bound cannot exceed {MAX_SELECTOR_CANDIDATES}"
            )
        if not 1 <= self.per_operation_timeout_seconds <= 600:
            raise DomainInvariantError("per-operation timeout must be between 1 and 600 seconds")
        if not self.per_operation_timeout_seconds <= self.overall_timeout_seconds <= 3600:
            raise DomainInvariantError(
                "overall timeout must be bounded and at least the per-operation timeout"
            )


@dataclass(frozen=True, slots=True)
class ExternalPaperStub:
    id: UUID
    semantic_scholar_id: str
    title: str
    abstract: str | None
    year: int | None
    publication_date: date | None
    venue: str | None
    authors: tuple[str, ...]
    external_ids: tuple[tuple[str, str], ...]
    arxiv_id: str | None
    doi: str | None
    citation_count: int
    influential_citation_count: int
    full_text_available: bool
    source: str
    schema_version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if len(self.semantic_scholar_id) != 40 or any(
            character not in "0123456789abcdef" for character in self.semantic_scholar_id
        ):
            raise DomainInvariantError(
                "Semantic Scholar paper identity must be 40 lowercase hexadecimal characters"
            )
        _require_text(self.title, "external paper title", maximum=4000)
        if self.abstract is not None:
            _require_text(self.abstract, "external paper abstract", maximum=100_000)
        if self.year is not None and not 1000 <= self.year <= 9999:
            raise DomainInvariantError("external paper year must have four digits")
        if min(self.citation_count, self.influential_citation_count) < 0:
            raise DomainInvariantError("citation counts cannot be negative")
        if self.influential_citation_count > self.citation_count:
            raise DomainInvariantError("influential citation count cannot exceed citation count")
        if self.full_text_available != (self.arxiv_id is not None):
            raise DomainInvariantError("only an arXiv identity may mark full text as available")
        if self.arxiv_id is not None:
            validate_canonical_arxiv_id(self.arxiv_id)
        if len({key.casefold() for key, _ in self.external_ids}) != len(self.external_ids):
            raise DomainInvariantError("external paper identifier types must be unique")
        for key, value in self.external_ids:
            _require_text(key, "external identifier type", maximum=40)
            _require_text(value, "external identifier value", maximum=512)
        identifiers = {key.casefold(): value for key, value in self.external_ids}
        if identifiers.get("arxiv") != self.arxiv_id or identifiers.get("doi") != self.doi:
            raise DomainInvariantError(
                "external paper canonical identifiers must agree with external identifier metadata"
            )
        _require_text(self.source, "external paper source", maximum=100)
        if self.source != "semantic_scholar":
            raise DomainInvariantError("historical external stubs must come from Semantic Scholar")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise DomainInvariantError("external paper update cannot precede creation")


@dataclass(frozen=True, slots=True)
class CandidateScoreComponents:
    semantic_scholar: float = 0.0
    lexical: float = 0.0
    vector: float = 0.0
    entity_overlap: float = 0.0
    citation: float = 0.0
    recommendation: float = 0.0
    final: float = 0.0

    def __post_init__(self) -> None:
        for name, value in (
            ("semantic_scholar", self.semantic_scholar),
            ("lexical", self.lexical),
            ("vector", self.vector),
            ("entity_overlap", self.entity_overlap),
            ("citation", self.citation),
            ("recommendation", self.recommendation),
            ("final", self.final),
        ):
            _require_unit_score(value, name)


@dataclass(frozen=True, slots=True)
class SearchSession:
    id: UUID
    topic_id: UUID
    source_paper_id: UUID
    source_paper_version_id: UUID
    source_analysis_id: UUID
    source_analysis_scope: AnalysisScope
    requested_year_from: int
    effective_year_to: int
    objective: str
    status: SearchSessionStatus
    limits: SearchLimits
    started_at: datetime
    completed_at: datetime | None
    stop_reason: SearchStopReason | None
    error_code: str | None
    error_detail: str | None
    provider: str | None
    configured_model: str | None
    model_version: str | None
    prompt_version: str | None
    usage: ModelUsage | None
    schema_version: int
    created_at: datetime
    crawler_queries: tuple[str, ...] | None = None
    crawler_use_recommendations: bool | None = None
    crawler_expand_references: bool | None = None
    crawler_expand_citations: bool | None = None
    crawler_decision_reason: str | None = None
    crawler_generated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not 1000 <= self.requested_year_from <= self.effective_year_to <= 9999:
            raise DomainInvariantError("search session year scope is invalid")
        _require_text(self.objective, "search objective", maximum=8000)
        _require_aware(self.started_at, "started_at")
        _require_aware(self.created_at, "created_at")
        if self.status is SearchSessionStatus.RUNNING:
            if self.completed_at is not None or self.stop_reason is not None:
                raise DomainInvariantError("running search session cannot be terminal")
        else:
            if self.completed_at is None or self.stop_reason is None:
                raise DomainInvariantError("terminal search session needs time and stop reason")
            _require_aware(self.completed_at, "completed_at")
        if self.status is SearchSessionStatus.FAILED and not self.error_code:
            raise DomainInvariantError("failed search session requires an error code")
        if self.status is not SearchSessionStatus.FAILED and any(
            value is not None for value in (self.error_code, self.error_detail)
        ):
            raise DomainInvariantError("non-failed search session cannot carry failure metadata")
        if (self.status is SearchSessionStatus.FAILED) != (
            self.stop_reason is SearchStopReason.FAILED
        ):
            raise DomainInvariantError("failed search status and stop reason must agree")
        provenance = (
            self.provider,
            self.configured_model,
            self.model_version,
            self.prompt_version,
        )
        if any(value is not None for value in provenance) and any(
            value is None for value in provenance
        ):
            raise DomainInvariantError("search model provenance must be complete when present")
        if (self.usage is None) != all(value is None for value in provenance):
            raise DomainInvariantError(
                "search model provenance and usage must either both be present or absent"
            )
        crawler_plan = (
            self.crawler_queries,
            self.crawler_use_recommendations,
            self.crawler_expand_references,
            self.crawler_expand_citations,
            self.crawler_decision_reason,
            self.crawler_generated_at,
        )
        if any(value is not None for value in crawler_plan):
            if any(value is None for value in crawler_plan):
                raise DomainInvariantError("crawler plan provenance must be complete when present")
            if self.usage is None:
                raise DomainInvariantError("crawler plan requires persisted model provenance")
            queries = self.crawler_queries or ()
            if not 1 <= len(queries) <= self.limits.max_queries:
                raise DomainInvariantError("crawler plan exceeds the session query limit")
            if len(set(queries)) != len(queries):
                raise DomainInvariantError("crawler plan queries must be unique")
            for query in queries:
                _require_text(query, "crawler plan query", maximum=500)
            _require_text(
                self.crawler_decision_reason or "",
                "crawler decision reason",
                maximum=1000,
            )
            if self.crawler_generated_at is not None:
                _require_aware(self.crawler_generated_at, "crawler_generated_at")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class SearchModelProvenance:
    provider: str
    configured_model: str
    model_version: str
    prompt_version: str
    usage: ModelUsage

    def __post_init__(self) -> None:
        for value, name, maximum in (
            (self.provider, "search provider", 100),
            (self.configured_model, "configured search model", 200),
            (self.model_version, "search model version", 200),
            (self.prompt_version, "search prompt version", 100),
        ):
            _require_text(value, name, maximum=maximum)


@dataclass(frozen=True, slots=True)
class SearchAction:
    id: UUID
    session_id: UUID
    step: int
    tool: SearchTool
    status: SearchActionStatus
    query: str | None
    target_semantic_scholar_id: str | None
    target_arxiv_id: str | None
    positive_paper_ids: tuple[str, ...]
    year_from: int | None
    year_to: int | None
    requested_limit: int
    result_count: int
    relation_depth: int
    decision_reason: str
    error_code: str | None
    retryable: bool | None
    error_detail: str | None
    duration_ms: int
    created_at: datetime
    completed_at: datetime | None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.step < 1 or not 1 <= self.requested_limit <= 1000 or self.result_count < 0:
            raise DomainInvariantError("search action counts must be positive or non-negative")
        if self.result_count > self.requested_limit:
            raise DomainInvariantError("search action result count exceeds its requested limit")
        if not 0 <= self.relation_depth <= 5:
            raise DomainInvariantError("search relation depth exceeds the configured bound")
        if not 0 <= self.duration_ms <= 600_000:
            raise DomainInvariantError("search action duration exceeds the persistence bound")
        _require_text(self.decision_reason, "search action decision reason", maximum=1000)
        if self.query is not None:
            _require_text(self.query, "search query", maximum=500)
        if self.target_semantic_scholar_id is not None:
            _require_text(
                self.target_semantic_scholar_id,
                "search action target paper ID",
                maximum=128,
            )
        if self.target_arxiv_id is not None:
            validate_canonical_arxiv_id(self.target_arxiv_id)
        for value, name in ((self.year_from, "year_from"), (self.year_to, "year_to")):
            if value is not None and not 1000 <= value <= 9999:
                raise DomainInvariantError(f"{name} must have four digits")
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_from > self.year_to
        ):
            raise DomainInvariantError("search action year range is reversed")
        if self.tool is SearchTool.SEARCH_PAPERS and self.query is None:
            raise DomainInvariantError("search_papers action requires a query")
        if (
            self.tool
            in {
                SearchTool.GET_PAPER,
                SearchTool.GET_REFERENCES,
                SearchTool.GET_CITATIONS,
            }
            and self.target_semantic_scholar_id is None
        ):
            raise DomainInvariantError(f"{self.tool.value} action requires a target paper")
        if self.tool is SearchTool.GET_RECOMMENDATIONS and not self.positive_paper_ids:
            raise DomainInvariantError("recommendation action requires positive paper IDs")
        if self.tool is SearchTool.READ_ARXIV_PAPER and self.target_arxiv_id is None:
            raise DomainInvariantError("read_arxiv_paper action requires an arXiv target")
        if self.tool is not SearchTool.SEARCH_PAPERS and self.query is not None:
            raise DomainInvariantError("only search_papers may carry a query")
        if (
            self.tool
            not in {
                SearchTool.GET_PAPER,
                SearchTool.GET_REFERENCES,
                SearchTool.GET_CITATIONS,
            }
            and self.target_semantic_scholar_id is not None
        ):
            raise DomainInvariantError("tool cannot carry a Semantic Scholar target")
        if self.tool is not SearchTool.GET_RECOMMENDATIONS and self.positive_paper_ids:
            raise DomainInvariantError("only recommendations may carry positive paper IDs")
        if self.tool is not SearchTool.READ_ARXIV_PAPER and self.target_arxiv_id is not None:
            raise DomainInvariantError("only read_arxiv_paper may carry an arXiv target")
        if self.status is SearchActionStatus.RUNNING:
            if any(
                value is not None
                for value in (
                    self.error_code,
                    self.retryable,
                    self.error_detail,
                    self.completed_at,
                )
            ):
                raise DomainInvariantError("running search action cannot be terminal")
            if self.result_count or self.duration_ms:
                raise DomainInvariantError("running search action cannot have result totals")
        elif self.status is SearchActionStatus.FAILED:
            if not self.error_code or self.retryable is None:
                raise DomainInvariantError("failed search action requires failure metadata")
            if self.completed_at is None:
                raise DomainInvariantError("failed search action requires completed_at")
        elif any(
            value is not None for value in (self.error_code, self.retryable, self.error_detail)
        ):
            raise DomainInvariantError("completed search action cannot carry failure metadata")
        elif self.completed_at is None:
            raise DomainInvariantError("completed search action requires completed_at")
        _require_aware(self.created_at, "created_at")
        if self.completed_at is not None:
            _require_aware(self.completed_at, "completed_at")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    id: UUID
    session_id: UUID
    external_paper_id: UUID
    semantic_scholar_id: str
    local_paper_id: UUID | None
    local_paper_version_id: UUID | None
    discovered_by_action_id: UUID | None
    origins: tuple[CandidateOrigin, ...]
    relation_depth: int
    scores: CandidateScoreComponents
    rank: int
    decision: SelectionDecision
    decision_reason: str
    provider: str | None
    configured_model: str | None
    model_version: str | None
    prompt_version: str | None
    generated_at: datetime | None
    verification_status: VerificationStatus
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.semantic_scholar_id, "candidate paper ID", maximum=128)
        if (self.local_paper_id is None) != (self.local_paper_version_id is None):
            raise DomainInvariantError("candidate local paper and version identities are paired")
        if not self.origins or len(set(self.origins)) != len(self.origins):
            raise DomainInvariantError("candidate needs unique provenance origins")
        if not 0 <= self.relation_depth <= 5 or self.rank < 1:
            raise DomainInvariantError("candidate depth or rank is invalid")
        _require_text(self.decision_reason, "candidate decision reason", maximum=1000)
        provenance = (
            self.provider,
            self.configured_model,
            self.model_version,
            self.prompt_version,
            self.generated_at,
        )
        if self.decision is SelectionDecision.PENDING:
            if any(value is not None for value in provenance):
                raise DomainInvariantError("pending candidate cannot carry LLM decision provenance")
        elif any(value is None for value in provenance):
            raise DomainInvariantError("terminal candidate decision requires complete provenance")
        if self.generated_at is not None:
            _require_aware(self.generated_at, "generated_at")
        _require_aware(self.created_at, "created_at")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class SearchCandidateDiscovery:
    id: UUID
    candidate_id: UUID
    action_id: UUID | None
    origin: CandidateOrigin
    relation_depth: int
    discovered_at: datetime

    def __post_init__(self) -> None:
        if not 0 <= self.relation_depth <= 5:
            raise DomainInvariantError("candidate discovery depth exceeds the configured bound")
        if (
            self.origin
            in {
                CandidateOrigin.SEARCH,
                CandidateOrigin.REFERENCES,
                CandidateOrigin.CITATIONS,
                CandidateOrigin.RECOMMENDATIONS,
            }
            and self.action_id is None
        ):
            raise DomainInvariantError("remote candidate discovery requires a search action")
        if self.origin in {CandidateOrigin.LOCAL_LEXICAL, CandidateOrigin.LOCAL_VECTOR} and (
            self.action_id is not None
        ):
            raise DomainInvariantError(
                "local retrieval provenance cannot reference a remote action"
            )
        _require_aware(self.discovered_at, "discovered_at")


@dataclass(frozen=True, slots=True)
class HistoricalBackfillRun:
    id: UUID
    topic_id: UUID
    window_from: date
    window_to: date
    query_plan: tuple[str, ...]
    max_results_per_query: int
    overall_timeout_seconds: float
    embedding_model_identifier: str
    embedding_model_revision: str
    embedding_tokenizer_identifier: str
    embedding_tokenizer_revision: str
    embedding_dimension: int
    embedding_preprocessing_contract: str
    embedding_model_provenance: str
    embedding_source: str
    status: BackfillStatus
    next_query_index: int
    discovered_count: int
    persisted_count: int
    representative_count: int
    started_at: datetime
    completed_at: datetime | None
    error_code: str | None
    error_detail: str | None
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        if self.window_from > self.window_to:
            raise DomainInvariantError("historical backfill window is reversed")
        if not 1 <= len(self.query_plan) <= MAX_HISTORICAL_QUERIES:
            raise DomainInvariantError("historical backfill query plan is not bounded")
        if len(set(self.query_plan)) != len(self.query_plan):
            raise DomainInvariantError("historical backfill queries must be unique")
        for query in self.query_plan:
            _require_text(query, "historical backfill query", maximum=500)
        if not 1 <= self.max_results_per_query <= MAX_HISTORICAL_RESULTS_PER_QUERY:
            raise DomainInvariantError("historical backfill result limit is not bounded")
        if not 1 <= self.overall_timeout_seconds <= MAX_HISTORICAL_TIMEOUT_SECONDS:
            raise DomainInvariantError("historical backfill timeout is not bounded")
        _require_text(
            self.embedding_model_identifier,
            "historical backfill embedding model",
            maximum=300,
        )
        _require_text(
            self.embedding_model_revision,
            "historical backfill embedding revision",
            maximum=128,
        )
        _require_text(
            self.embedding_tokenizer_identifier,
            "historical backfill tokenizer",
            maximum=300,
        )
        _require_text(
            self.embedding_tokenizer_revision,
            "historical backfill tokenizer revision",
            maximum=128,
        )
        if self.embedding_dimension != 768:
            raise DomainInvariantError("historical backfill embedding dimension must be 768")
        _require_text(
            self.embedding_preprocessing_contract,
            "historical backfill preprocessing contract",
            maximum=1000,
        )
        _require_text(
            self.embedding_model_provenance,
            "historical backfill model provenance",
            maximum=1000,
        )
        _require_text(
            self.embedding_source,
            "historical backfill embedding source",
            maximum=100,
        )
        if (
            min(
                self.next_query_index,
                self.discovered_count,
                self.persisted_count,
                self.representative_count,
            )
            < 0
        ):
            raise DomainInvariantError("historical backfill counts cannot be negative")
        if self.persisted_count > self.discovered_count:
            raise DomainInvariantError("persisted backfill count cannot exceed discoveries")
        if self.representative_count > self.persisted_count:
            raise DomainInvariantError("representative count cannot exceed persisted papers")
        if self.next_query_index > len(self.query_plan):
            raise DomainInvariantError("historical backfill cursor exceeds its query plan")
        if self.status is BackfillStatus.COMPLETE and self.next_query_index != len(self.query_plan):
            raise DomainInvariantError(
                "completed historical backfill must finish its persisted query plan"
            )
        _require_aware(self.started_at, "started_at")
        _require_aware(self.created_at, "created_at")
        if self.status is BackfillStatus.RUNNING:
            if self.completed_at is not None:
                raise DomainInvariantError("running backfill cannot be completed")
        elif self.completed_at is None:
            raise DomainInvariantError("terminal backfill needs completed_at")
        else:
            _require_aware(self.completed_at, "completed_at")
        if self.status is BackfillStatus.FAILED and not self.error_code:
            raise DomainInvariantError("failed backfill requires an error code")
        if self.status is not BackfillStatus.FAILED and any(
            value is not None for value in (self.error_code, self.error_detail)
        ):
            raise DomainInvariantError("non-failed backfill cannot carry failure metadata")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class HistoricalCorpusEntry:
    id: UUID
    topic_id: UUID
    external_paper_id: UUID
    local_paper_id: UUID | None
    local_paper_version_id: UUID | None
    representative_rank: int | None
    first_seen_at: datetime
    last_seen_at: datetime
    schema_version: int

    def __post_init__(self) -> None:
        if (self.local_paper_id is None) != (self.local_paper_version_id is None):
            raise DomainInvariantError("historical local paper and version identities are paired")
        if self.representative_rank is not None and self.representative_rank < 1:
            raise DomainInvariantError("representative rank must be positive")
        _require_aware(self.first_seen_at, "first_seen_at")
        _require_aware(self.last_seen_at, "last_seen_at")
        if self.last_seen_at < self.first_seen_at:
            raise DomainInvariantError("historical corpus last-seen time is reversed")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class ScientificEmbedding:
    id: UUID
    paper_version_id: UUID | None
    external_paper_id: UUID | None
    model_identifier: str
    model_revision: str
    tokenizer_identifier: str
    tokenizer_revision: str
    dimension: int
    preprocessing_contract: str
    model_provenance: str
    vector: tuple[float, ...]
    generated_at: datetime
    source: str
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        if (self.paper_version_id is None) == (self.external_paper_id is None):
            raise DomainInvariantError("embedding must own exactly one paper identity")
        _require_text(self.model_identifier, "embedding model identifier", maximum=300)
        _require_text(self.model_revision, "embedding model revision", maximum=128)
        _require_text(self.tokenizer_identifier, "embedding tokenizer identifier", maximum=300)
        _require_text(self.tokenizer_revision, "embedding tokenizer revision", maximum=128)
        if self.dimension != len(self.vector) or self.dimension < 1:
            raise DomainInvariantError("embedding dimension must match its vector")
        if self.dimension != 768:
            raise DomainInvariantError("scientific embedding dimension must be 768")
        _require_text(
            self.preprocessing_contract,
            "embedding preprocessing contract",
            maximum=1000,
        )
        _require_text(self.model_provenance, "embedding model provenance", maximum=1000)
        if any(not math.isfinite(value) for value in self.vector):
            raise DomainInvariantError("embedding vector values must be finite")
        if not any(value != 0 for value in self.vector):
            raise DomainInvariantError("embedding vector must be non-zero for cosine retrieval")
        _require_text(self.source, "embedding source", maximum=100)
        _require_aware(self.generated_at, "generated_at")
        _require_aware(self.created_at, "created_at")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class ComparisonEvidenceInput:
    id: UUID
    analysis_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    section: str
    excerpt: str

    def __post_init__(self) -> None:
        _require_text(self.section, "comparison evidence section", maximum=500)
        _require_text(self.excerpt, "comparison evidence excerpt", maximum=600)


@dataclass(frozen=True, slots=True)
class ComparisonPaperInput:
    paper_id: UUID
    paper_version_id: UUID
    analysis_id: UUID
    analysis_scope: AnalysisScope
    title: str
    summary: str
    research_problem: str
    method_summary: str
    limitations: tuple[str, ...]
    evidence: tuple[ComparisonEvidenceInput, ...]

    def __post_init__(self) -> None:
        _require_text(self.title, "comparison paper title", maximum=4000)
        _require_text(self.summary, "comparison paper summary", maximum=8000)
        _require_text(self.research_problem, "comparison research problem", maximum=4000)
        _require_text(self.method_summary, "comparison method summary", maximum=4000)
        if len({item.id for item in self.evidence}) != len(self.evidence):
            raise DomainInvariantError("comparison input evidence IDs must be unique")
        if any(
            item.analysis_id != self.analysis_id
            or item.paper_id != self.paper_id
            or item.paper_version_id != self.paper_version_id
            for item in self.evidence
        ):
            raise DomainInvariantError("comparison evidence must belong to its exact analysis")


@dataclass(frozen=True, slots=True)
class ComparisonRequest:
    source: ComparisonPaperInput
    target: ComparisonPaperInput

    def __post_init__(self) -> None:
        if self.source.paper_version_id == self.target.paper_version_id:
            raise DomainInvariantError("comparison requires two different paper versions")


@dataclass(frozen=True, slots=True)
class GeneratedComparisonDimension:
    name: ComparisonDimensionName
    source_value: str
    target_value: str
    assessment: str
    source_evidence_ids: tuple[UUID, ...]
    target_evidence_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        for value, name in (
            (self.source_value, "source comparison value"),
            (self.target_value, "target comparison value"),
            (self.assessment, "comparison assessment"),
        ):
            _require_text(value, name, maximum=4000)
        if len(set(self.source_evidence_ids)) != len(self.source_evidence_ids) or len(
            set(self.target_evidence_ids)
        ) != len(self.target_evidence_ids):
            raise DomainInvariantError("comparison evidence references must be unique")


@dataclass(frozen=True, slots=True)
class GeneratedRelation:
    relation_type: PaperRelationType
    justification: str
    evidence_ids: tuple[UUID, ...]
    confidence: float

    def __post_init__(self) -> None:
        if self.relation_type is PaperRelationType.CITES:
            raise DomainInvariantError("citation relations must come from explicit metadata")
        _require_text(self.justification, "relation justification", maximum=2000)
        if not self.evidence_ids or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise DomainInvariantError("inferred relation requires unique evidence references")
        _require_unit_score(self.confidence, "relation confidence")


@dataclass(frozen=True, slots=True)
class GeneratedComparison:
    provider: str
    configured_model: str
    model_version: str
    prompt_version: str
    generated_at: datetime
    comparability_status: ComparabilityStatus
    comparability_reason: str
    summary: str
    dimensions: tuple[GeneratedComparisonDimension, ...]
    relations: tuple[GeneratedRelation, ...]
    usage: ModelUsage

    def __post_init__(self) -> None:
        for value, name, maximum in (
            (self.provider, "comparison provider", 100),
            (self.configured_model, "configured comparison model", 200),
            (self.model_version, "comparison model version", 200),
            (self.prompt_version, "comparison prompt version", 100),
            (self.comparability_reason, "comparability reason", 4000),
            (self.summary, "comparison summary", 8000),
        ):
            _require_text(value, name, maximum=maximum)
        _require_aware(self.generated_at, "generated_at")
        names = tuple(item.name for item in self.dimensions)
        if names != COMPARISON_DIMENSION_ORDER:
            raise DomainInvariantError("comparison dimensions must be complete and ordered")
        relation_types = tuple(item.relation_type for item in self.relations)
        if len(set(relation_types)) != len(relation_types):
            raise DomainInvariantError("generated comparison relation types must be unique")
        if (
            PaperRelationType.IMPROVES_ON in relation_types
            and self.comparability_status is not ComparabilityStatus.DIRECTLY_COMPARABLE
        ):
            raise DomainInvariantError(
                "improves_on requires directly comparable evidence and evaluation setup"
            )
        if self.comparability_status is ComparabilityStatus.DIRECTLY_COMPARABLE:
            required = {
                ComparisonDimensionName.BENCHMARKS,
                ComparisonDimensionName.METRICS,
                ComparisonDimensionName.REPORTED_RESULTS,
                ComparisonDimensionName.RESULT_COMPARABILITY,
            }
            by_name = {item.name: item for item in self.dimensions}
            if any(
                not by_name[name].source_evidence_ids or not by_name[name].target_evidence_ids
                for name in required
            ):
                raise DomainInvariantError(
                    "direct comparability requires bilateral benchmark, metric, result, and "
                    "comparability evidence"
                )


@dataclass(frozen=True, slots=True)
class CrawlerPlanRequest:
    objective: str
    source_title: str
    source_research_problem: str
    source_method: str
    topic_include_terms: tuple[str, ...]
    topic_exclude_terms: tuple[str, ...]
    year_from: int
    year_to: int
    max_queries: int

    def __post_init__(self) -> None:
        for value, name, maximum in (
            (self.objective, "crawler objective", 8000),
            (self.source_title, "crawler source title", 4000),
            (self.source_research_problem, "crawler source problem", 4000),
            (self.source_method, "crawler source method", 4000),
        ):
            _require_text(value, name, maximum=maximum)
        if not 1000 <= self.year_from <= self.year_to <= 9999:
            raise DomainInvariantError("crawler year range is invalid")
        if not 1 <= self.max_queries <= 40:
            raise DomainInvariantError("crawler query limit must be between 1 and 40")
        if not self.topic_include_terms:
            raise DomainInvariantError("crawler requires topic include terms")
        for term in self.topic_include_terms + self.topic_exclude_terms:
            _require_text(term, "crawler topic term", maximum=120)


@dataclass(frozen=True, slots=True)
class GeneratedCrawlerPlan:
    provider: str
    configured_model: str
    model_version: str
    prompt_version: str
    generated_at: datetime
    queries: tuple[str, ...]
    use_recommendations: bool
    expand_references: bool
    expand_citations: bool
    decision_reason: str
    usage: ModelUsage

    def __post_init__(self) -> None:
        for value, name, maximum in (
            (self.provider, "crawler provider", 100),
            (self.configured_model, "configured crawler model", 200),
            (self.model_version, "crawler model version", 200),
            (self.prompt_version, "crawler prompt version", 100),
            (self.decision_reason, "crawler decision reason", 1000),
        ):
            _require_text(value, name, maximum=maximum)
        _require_aware(self.generated_at, "generated_at")
        if not 1 <= len(self.queries) <= 40:
            raise DomainInvariantError("crawler query plan must contain 1 to 40 queries")
        normalized = tuple(" ".join(query.split()) for query in self.queries)
        if len(set(normalized)) != len(normalized):
            raise DomainInvariantError("crawler queries must be unique")
        for query in normalized:
            _require_text(query, "crawler query", maximum=500)


@dataclass(frozen=True, slots=True)
class CandidateSelectionInput:
    semantic_scholar_id: str
    title: str
    abstract: str | None
    year: int | None
    venue: str | None
    scores: CandidateScoreComponents

    def __post_init__(self) -> None:
        _require_text(self.semantic_scholar_id, "selection candidate ID", maximum=128)
        _require_text(self.title, "selection candidate title", maximum=4000)
        if self.abstract is not None:
            _require_text(self.abstract, "selection candidate abstract", maximum=100_000)


@dataclass(frozen=True, slots=True)
class CandidateSelectionRequest:
    objective: str
    source_title: str
    source_research_problem: str
    source_method: str
    candidates: tuple[CandidateSelectionInput, ...]
    max_selected_candidates: int

    def __post_init__(self) -> None:
        for value, name, maximum in (
            (self.objective, "selection objective", 8000),
            (self.source_title, "selection source title", 4000),
            (self.source_research_problem, "selection source problem", 4000),
            (self.source_method, "selection source method", 4000),
        ):
            _require_text(value, name, maximum=maximum)
        if not self.candidates:
            raise DomainInvariantError("selector requires at least one candidate")
        if len(self.candidates) > MAX_SELECTOR_CANDIDATES:
            raise DomainInvariantError(
                f"selector input cannot exceed {MAX_SELECTOR_CANDIDATES} candidates"
            )
        if not 1 <= self.max_selected_candidates <= 100:
            raise DomainInvariantError("selector bound must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class GeneratedCandidateDecision:
    semantic_scholar_id: str
    decision: SelectionDecision
    reason: str

    def __post_init__(self) -> None:
        _require_text(self.semantic_scholar_id, "selection decision paper ID", maximum=128)
        if self.decision is SelectionDecision.PENDING:
            raise DomainInvariantError("generated selection decision must be terminal")
        _require_text(self.reason, "selection decision reason", maximum=1000)


@dataclass(frozen=True, slots=True)
class GeneratedCandidateSelection:
    provider: str
    configured_model: str
    model_version: str
    prompt_version: str
    generated_at: datetime
    decisions: tuple[GeneratedCandidateDecision, ...]
    usage: ModelUsage

    def __post_init__(self) -> None:
        for value, name, maximum in (
            (self.provider, "selector provider", 100),
            (self.configured_model, "configured selector model", 200),
            (self.model_version, "selector model version", 200),
            (self.prompt_version, "selector prompt version", 100),
        ):
            _require_text(value, name, maximum=maximum)
        _require_aware(self.generated_at, "generated_at")
        if not self.decisions or len({item.semantic_scholar_id for item in self.decisions}) != len(
            self.decisions
        ):
            raise DomainInvariantError("selector decisions must be non-empty and unique")


@dataclass(frozen=True, slots=True)
class ComparisonDimension:
    id: UUID
    comparison_id: UUID
    name: ComparisonDimensionName
    position: int
    source_value: str
    target_value: str
    assessment: str
    source_evidence_ids: tuple[UUID, ...]
    target_evidence_ids: tuple[UUID, ...]
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        if self.position != COMPARISON_DIMENSION_ORDER.index(self.name):
            raise DomainInvariantError("comparison dimension position is not canonical")
        GeneratedComparisonDimension(
            name=self.name,
            source_value=self.source_value,
            target_value=self.target_value,
            assessment=self.assessment,
            source_evidence_ids=self.source_evidence_ids,
            target_evidence_ids=self.target_evidence_ids,
        )
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class Comparison:
    id: UUID
    search_session_id: UUID
    source_paper_id: UUID
    source_paper_version_id: UUID
    source_analysis_id: UUID
    source_analysis_scope: AnalysisScope
    target_paper_id: UUID
    target_paper_version_id: UUID
    target_analysis_id: UUID
    target_analysis_scope: AnalysisScope
    comparability_status: ComparabilityStatus
    comparability_reason: str
    summary: str
    dimensions: tuple[ComparisonDimension, ...]
    provider: str
    configured_model: str
    model_version: str
    prompt_version: str
    generated_at: datetime
    source: str
    verification_status: VerificationStatus
    usage: ModelUsage
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        if self.source_paper_version_id == self.target_paper_version_id:
            raise DomainInvariantError("comparison source and target versions must differ")
        if tuple(item.name for item in self.dimensions) != COMPARISON_DIMENSION_ORDER:
            raise DomainInvariantError("persisted comparison dimensions must be complete")
        if any(item.comparison_id != self.id for item in self.dimensions):
            raise DomainInvariantError("comparison dimensions must belong to their comparison")
        for value, name, maximum in (
            (self.comparability_reason, "comparability reason", 4000),
            (self.summary, "comparison summary", 8000),
            (self.provider, "comparison provider", 100),
            (self.configured_model, "configured comparison model", 200),
            (self.model_version, "comparison model version", 200),
            (self.prompt_version, "comparison prompt version", 100),
            (self.source, "comparison source", 100),
        ):
            _require_text(value, name, maximum=maximum)
        _require_aware(self.generated_at, "generated_at")
        _require_aware(self.created_at, "created_at")
        if self.comparability_status is ComparabilityStatus.DIRECTLY_COMPARABLE:
            required = {
                ComparisonDimensionName.BENCHMARKS,
                ComparisonDimensionName.METRICS,
                ComparisonDimensionName.REPORTED_RESULTS,
                ComparisonDimensionName.RESULT_COMPARABILITY,
            }
            by_name = {item.name: item for item in self.dimensions}
            if any(
                not by_name[name].source_evidence_ids or not by_name[name].target_evidence_ids
                for name in required
            ):
                raise DomainInvariantError(
                    "direct comparability requires bilateral benchmark, metric, result, and "
                    "comparability evidence"
                )
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class PaperRelation:
    id: UUID
    source_paper_id: UUID
    source_paper_version_id: UUID
    target_paper_id: UUID
    target_paper_version_id: UUID
    relation_type: PaperRelationType
    provenance: RelationProvenance
    evidence_ids: tuple[UUID, ...]
    justification: str
    provider: str | None
    model_version: str | None
    prompt_version: str | None
    confidence: float | None
    verification_status: VerificationStatus
    generated_at: datetime
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        if self.source_paper_version_id == self.target_paper_version_id:
            raise DomainInvariantError("paper relation requires different paper versions")
        _require_text(self.justification, "relation justification", maximum=2000)
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise DomainInvariantError("relation evidence IDs must be unique")
        model_values = (self.provider, self.model_version, self.prompt_version, self.confidence)
        if self.provenance is RelationProvenance.LLM_INFERRED:
            if not self.evidence_ids or any(value is None for value in model_values):
                raise DomainInvariantError(
                    "LLM-inferred relation requires evidence, model provenance, and confidence"
                )
        elif any(value is not None for value in model_values):
            raise DomainInvariantError("non-LLM relation cannot carry LLM provenance")
        if self.confidence is not None:
            _require_unit_score(self.confidence, "relation confidence")
        _require_aware(self.generated_at, "generated_at")
        _require_aware(self.created_at, "created_at")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class ComparisonBundle:
    comparison: Comparison
    relations: tuple[PaperRelation, ...]

    def __post_init__(self) -> None:
        evidence_ids = {
            evidence_id
            for dimension in self.comparison.dimensions
            for evidence_id in dimension.source_evidence_ids + dimension.target_evidence_ids
        }
        for relation in self.relations:
            if (
                relation.source_paper_id != self.comparison.source_paper_id
                or relation.source_paper_version_id != self.comparison.source_paper_version_id
                or relation.target_paper_id != self.comparison.target_paper_id
                or relation.target_paper_version_id != self.comparison.target_paper_version_id
            ):
                raise DomainInvariantError("relation ownership must match its comparison")
            if not set(relation.evidence_ids).issubset(evidence_ids):
                raise DomainInvariantError("relation evidence must be present in the comparison")
