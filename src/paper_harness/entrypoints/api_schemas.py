"""Pydantic response schemas that define the frontend OpenAPI contract."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from paper_harness.application.read_models import GraphEvidenceRole
from paper_harness.domain.analysis import (
    AnalysisScope,
    ClaimType,
    EvidenceType,
    VerificationStatus,
)
from paper_harness.domain.historical import (
    CandidateOrigin,
    ComparabilityStatus,
    ComparisonDimensionName,
    PaperRelationType,
    RelationProvenance,
    SearchActionStatus,
    SearchSessionStatus,
    SearchStopReason,
    SearchTool,
    SelectionDecision,
)
from paper_harness.domain.knowledge import (
    GraphEntityType,
    GraphRelationType,
    LineageCorpusScope,
    TrendDataSufficiency,
    TrendGrowthStatus,
    TrendWindow,
)
from paper_harness.domain.models import PaperStage, RunItemStatus, RunOperation, RunStatus
from paper_harness.domain.reports import (
    ReportNarrativeMode,
    ReportSectionKind,
    ReportType,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiErrorDetail(ApiModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=1000)


class ApiErrorResponse(ApiModel):
    detail: ApiErrorDetail


class LiveResponse(ApiModel):
    status: Literal["alive"] = "alive"


class ReadyResponse(ApiModel):
    status: Literal["ready"] = "ready"
    database: Literal["ready"] = "ready"
    migrations: Literal["current"] = "current"


class TopicSummary(ApiModel):
    id: UUID
    slug: str
    name: str
    description: str
    schema_version: int
    created_at: datetime


class TopicListResponse(ApiModel):
    items: list[TopicSummary]
    total: int = Field(ge=0)


class PaperSummary(ApiModel):
    id: UUID
    canonical_arxiv_id: str
    title: str
    abstract: str
    current_version: int = Field(ge=1)
    first_submitted_at: datetime
    latest_updated_at: datetime
    primary_category: str
    categories: list[str]
    authors: list[str]
    pdf_url: str
    schema_version: int = Field(ge=1)
    created_at: datetime


class PaperListResponse(ApiModel):
    items: list[PaperSummary]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class PaperVersionResponse(ApiModel):
    id: UUID
    paper_id: UUID
    canonical_arxiv_id: str
    version: int = Field(ge=1)
    title: str
    abstract: str
    submitted_at: datetime
    updated_at: datetime
    primary_category: str
    categories: list[str]
    authors: list[str]
    pdf_url: str
    source_url: str
    schema_version: int = Field(ge=1)
    created_at: datetime


class SourceIdentityResponse(ApiModel):
    id: UUID
    paper_id: UUID
    paper_version_id: UUID
    source: Literal["arxiv"]
    external_id: str
    source_version: str
    source_url: str
    schema_version: int = Field(ge=1)
    created_at: datetime


class PaperDetailResponse(PaperSummary):
    versions: list[PaperVersionResponse]
    source_identities: list[SourceIdentityResponse]
    topic_slugs: list[str]


class RunItemResponse(ApiModel):
    id: UUID
    run_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    canonical_arxiv_id: str
    paper_title: str
    stage: PaperStage
    status: RunItemStatus
    failed_stage: PaperStage | None
    error_code: str | None
    retryable: bool | None
    error_detail: str | None
    schema_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class RunSummary(ApiModel):
    id: UUID
    topic_id: UUID
    source_run_id: UUID | None
    logical_date: date
    operation: RunOperation
    analysis_scope: AnalysisScope | None
    status: RunStatus
    started_at: datetime
    completed_at: datetime | None
    cursor_from: datetime | None
    cursor_to: datetime | None
    discovered_count: int = Field(ge=0)
    normalized_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    error_code: str | None
    error_detail: str | None
    schema_version: int = Field(ge=1)
    created_at: datetime


class ModelUsageResponse(ApiModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    call_count: int = Field(ge=1)
    duration_ms: int = Field(ge=0)
    estimated_cost_usd: Decimal | None


class ReportFailureResponse(ApiModel):
    id: UUID
    report_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    failed_stage: PaperStage
    error_code: str
    retryable: bool
    error_detail: str
    schema_version: int = Field(ge=1)
    created_at: datetime


class ReportCountsResponse(ApiModel):
    retrieved: int = Field(ge=0)
    selected: int = Field(ge=0)
    processed: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)


class ReportPaperHighlightResponse(ApiModel):
    paper_id: UUID
    paper_version_id: UUID
    title: str
    reason: str
    evidence_ids: list[UUID]


class ReportEntityHighlightResponse(ApiModel):
    graph_entity_id: UUID
    entity_type: str
    label: str
    distinct_paper_count: int = Field(
        ge=1,
        description="Distinct papers for this entity in the report's latest 7-day snapshot.",
    )


class ReportComparisonHighlightResponse(ApiModel):
    comparison_id: UUID
    summary: str
    comparability_status: str
    evidence_ids: list[UUID]


class ReportLineageHighlightResponse(ApiModel):
    lineage_snapshot_id: UUID
    root_paper_id: UUID
    summary: str
    uncertain: bool


class ReportEvidenceReferenceResponse(ApiModel):
    id: UUID
    paper_id: UUID
    paper_version_id: UUID
    section: str
    excerpt: str
    evidence_type: str
    verification_status: VerificationStatus


class ReportGraphChangesResponse(ApiModel):
    entity_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    new_entity_count: int = Field(ge=0)
    inferred_edge_count: int = Field(ge=0)


class ReportSectionResponse(ApiModel):
    id: UUID
    report_id: UUID
    kind: ReportSectionKind
    narrative: str
    evidence_ids: list[UUID]
    schema_version: int = Field(ge=1)
    created_at: datetime


class ReportResponse(ApiModel):
    id: UUID
    run_id: UUID | None
    topic_id: UUID
    logical_date: date
    status: RunStatus
    title: str
    summary: str
    source: str
    generated_at: datetime
    schema_version: int = Field(ge=1)
    created_at: datetime
    failures: list[ReportFailureResponse]
    sections: list[ReportSectionResponse]
    report_type: ReportType
    period_start: date
    period_end: date
    counts: ReportCountsResponse
    highlighted_papers: list[ReportPaperHighlightResponse]
    major_entities: list[ReportEntityHighlightResponse]
    notable_comparisons: list[ReportComparisonHighlightResponse]
    graph_changes: ReportGraphChangesResponse
    trend_snapshot_ids: list[UUID]
    lineage_highlights: list[ReportLineageHighlightResponse]
    evidence: list[ReportEvidenceReferenceResponse]
    limitations: list[str]
    missing_sections: list[str]
    narrative_mode: ReportNarrativeMode
    provider: str | None
    configured_model: str | None
    model_version: str | None
    prompt_version: str | None
    usage: ModelUsageResponse | None
    verification_status: VerificationStatus


class RunDetailResponse(RunSummary):
    items: list[RunItemResponse]
    report: ReportResponse | None


class DailyRunEnvelopeResponse(ApiModel):
    run: RunSummary
    items: list[RunItemResponse]
    report: ReportResponse | None


class RunListResponse(ApiModel):
    items: list[RunSummary]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class ReportListResponse(ApiModel):
    items: list[ReportResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class GraphModelProvenanceResponse(ApiModel):
    provider: str
    configured_model: str
    model_version: str
    prompt_version: str


class GraphEntityMentionResponse(ApiModel):
    id: UUID
    paper_id: UUID
    paper_version_id: UUID
    analysis_id: UUID | None
    comparison_id: UUID | None
    observed_label: str
    provenance: RelationProvenance
    inferred: bool
    evidence_ids: list[UUID]
    model_provenance: GraphModelProvenanceResponse | None
    confidence: float | None = Field(default=None, ge=0, le=1)
    verification_status: VerificationStatus
    generated_at: datetime
    schema_version: int = Field(ge=1)
    created_at: datetime


class GraphNodeResponse(ApiModel):
    id: UUID
    topic_id: UUID
    entity_type: GraphEntityType
    paper_id: UUID | None
    canonical_label: str
    normalized_key: str
    display_label: str
    aliases: list[str]
    provenance: RelationProvenance
    inferred: bool
    source: str
    mention_count: int = Field(ge=0)
    mentions: list[GraphEntityMentionResponse]
    schema_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class GraphEdgeEvidenceResponse(ApiModel):
    evidence_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    role: GraphEvidenceRole


class GraphEdgeResponse(ApiModel):
    id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    relation_type: GraphRelationType
    source_paper_version_id: UUID
    target_paper_version_id: UUID | None
    analysis_id: UUID | None
    comparison_id: UUID | None
    paper_relation_id: UUID | None
    provenance: RelationProvenance
    inferred: bool
    evidence_ids: list[UUID]
    evidence: list[GraphEdgeEvidenceResponse]
    justification: str
    model_provenance: GraphModelProvenanceResponse | None
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_meaning: str | None
    verification_status: VerificationStatus
    generated_at: datetime
    schema_version: int = Field(ge=1)
    created_at: datetime


class KnowledgeGraphResponse(ApiModel):
    topic_id: UUID
    as_of: date | None
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    total_nodes: int = Field(ge=0)
    total_edges: int = Field(ge=0)
    total_mentions: int = Field(ge=0)
    truncated: bool


class TrendChangeResponse(ApiModel):
    current_count: int = Field(ge=0)
    preceding_count: int = Field(ge=0)
    absolute_change: int
    denominator_count: int = Field(ge=0)
    relative_change: Decimal | None
    growth_status: TrendGrowthStatus


class TrendEntityCountResponse(ApiModel):
    entity_id: UUID
    entity_type: GraphEntityType
    label: str
    change: TrendChangeResponse
    newly_appearing: bool
    recurring: bool


class TrendRelationCountResponse(ApiModel):
    relation_type: GraphRelationType
    change: TrendChangeResponse


class TrendThresholdsResponse(ApiModel):
    limited_paper_count: int = Field(ge=1)
    sufficient_paper_count: int = Field(ge=1)
    minimum_growth_denominator: int = Field(ge=1)


class TrendRepresentativePaperResponse(ApiModel):
    paper_id: UUID
    paper_version_id: UUID
    activity_date: date
    title: str


class TrendSnapshotResponse(ApiModel):
    id: UUID
    topic_id: UUID
    as_of_date: date
    window: TrendWindow
    window_start: date
    window_end: date
    preceding_window_start: date
    preceding_window_end: date
    included_paper_count: int = Field(ge=0)
    preceding_paper_count: int = Field(ge=0)
    paper_count_change: TrendChangeResponse
    entity_counts: list[TrendEntityCountResponse]
    total_entities: int = Field(ge=0)
    truncated: bool
    relation_counts: list[TrendRelationCountResponse]
    new_entity_ids: list[UUID]
    recurring_entity_ids: list[UUID]
    representative_papers: list[TrendRepresentativePaperResponse]
    data_sufficiency: TrendDataSufficiency
    preceding_data_sufficiency: TrendDataSufficiency
    thresholds: TrendThresholdsResponse
    aggregation_version: str
    generated_at: datetime
    schema_version: int = Field(ge=1)


class TrendsResponse(ApiModel):
    items: list[TrendSnapshotResponse]
    total: int = Field(ge=0, le=3)


class LineageNodeResponse(ApiModel):
    graph_entity_id: UUID
    paper_id: UUID
    title: str
    publication_date: date | None
    depth: int = Field(ge=0, le=5)


class LineageResponse(ApiModel):
    id: UUID
    topic_id: UUID
    root_paper_id: UUID
    as_of_date: date
    nodes: list[LineageNodeResponse]
    edges: list[GraphEdgeResponse]
    permitted_relation_types: list[GraphRelationType]
    max_depth: int = Field(ge=1, le=5)
    max_nodes: int = Field(ge=1, le=100)
    max_edges: int = Field(ge=1, le=400)
    truncated: bool
    explicit_predecessor_available: bool
    verified_predecessor_available: bool
    corpus_scope: LineageCorpusScope
    limitations: list[str]
    lineage_version: str
    generated_at: datetime
    schema_version: int = Field(ge=1)


class AnalysisClaimResponse(ApiModel):
    id: UUID
    analysis_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    key: str
    claim_type: ClaimType
    text: str
    provider: str
    model_version: str
    prompt_version: str
    generated_at: datetime
    source: str
    verification_status: VerificationStatus
    schema_version: int = Field(ge=1)
    created_at: datetime


class PaperAnalysisResponse(ApiModel):
    id: UUID
    paper_id: UUID
    paper_version_id: UUID
    arxiv_version: int = Field(ge=1)
    analysis_scope: AnalysisScope
    parsed_paper_id: UUID | None
    parser_name: str | None
    parser_version: str | None
    summary: str
    research_problem: str
    method_summary: str
    key_contributions: list[str]
    limitations: list[str]
    provider: str
    configured_model: str
    model_version: str
    prompt_version: str
    generated_at: datetime
    source: str
    verification_status: VerificationStatus
    usage: ModelUsageResponse
    schema_version: int = Field(ge=1)
    created_at: datetime
    claims: list[AnalysisClaimResponse]


class PageCoordinatesResponse(ApiModel):
    page: int = Field(ge=1)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class EvidenceResponse(ApiModel):
    id: UUID
    analysis_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    key: str
    section: str
    passage_id: str
    coordinates: list[PageCoordinatesResponse]
    excerpt: str
    evidence_type: EvidenceType
    supported_claim_ids: list[UUID]
    extraction_source: str
    provider: str
    model_version: str
    prompt_version: str
    generated_at: datetime
    verification_status: VerificationStatus
    schema_version: int = Field(ge=1)
    created_at: datetime


class EvidenceListResponse(ApiModel):
    items: list[EvidenceResponse]
    total: int = Field(ge=0)


class SearchLimitsResponse(ApiModel):
    max_steps: int = Field(ge=1, le=100)
    max_queries: int = Field(ge=1, le=40)
    max_queue_size: int = Field(ge=1, le=2000)
    max_citation_depth: int = Field(ge=0, le=5)
    max_candidates: int = Field(ge=1, le=5000)
    max_selected_candidates: int = Field(ge=1, le=100)
    per_operation_timeout_seconds: float = Field(ge=1, le=600)
    overall_timeout_seconds: float = Field(ge=1, le=3600)


class SearchSessionResponse(ApiModel):
    id: UUID
    topic_id: UUID
    source_paper_id: UUID
    source_paper_version_id: UUID
    source_analysis_id: UUID
    source_analysis_scope: AnalysisScope
    requested_year_from: int = Field(ge=1000, le=9999)
    effective_year_to: int = Field(ge=1000, le=9999)
    objective: str
    crawler_queries: list[str] | None
    crawler_use_recommendations: bool | None
    crawler_expand_references: bool | None
    crawler_expand_citations: bool | None
    crawler_decision_reason: str | None
    crawler_generated_at: datetime | None
    status: SearchSessionStatus
    limits: SearchLimitsResponse
    started_at: datetime
    completed_at: datetime | None
    stop_reason: SearchStopReason | None
    error_code: str | None
    error_detail: str | None
    provider: str | None
    configured_model: str | None
    model_version: str | None
    prompt_version: str | None
    usage: ModelUsageResponse | None
    schema_version: int = Field(ge=1)
    created_at: datetime


class SearchActionResponse(ApiModel):
    id: UUID
    session_id: UUID
    step: int = Field(ge=1)
    tool: SearchTool
    status: SearchActionStatus
    query: str | None
    target_semantic_scholar_id: str | None
    target_arxiv_id: str | None
    positive_paper_ids: list[str]
    year_from: Annotated[int | None, Field(ge=1000, le=9999)]
    year_to: Annotated[int | None, Field(ge=1000, le=9999)]
    requested_limit: int = Field(ge=1, le=1000)
    result_count: int = Field(ge=0)
    relation_depth: int = Field(ge=0, le=5)
    decision_reason: str
    error_code: str | None
    retryable: bool | None
    error_detail: str | None
    duration_ms: int = Field(ge=0, le=600_000)
    created_at: datetime
    completed_at: datetime | None
    schema_version: int = Field(ge=1)


class CandidateScoreComponentsResponse(ApiModel):
    semantic_scholar: float = Field(ge=0, le=1)
    lexical: float = Field(ge=0, le=1)
    vector: float = Field(ge=0, le=1)
    entity_overlap: float = Field(ge=0, le=1)
    citation: float = Field(ge=0, le=1)
    recommendation: float = Field(ge=0, le=1)
    final: float = Field(ge=0, le=1)


class SearchCandidateResponse(ApiModel):
    id: UUID
    session_id: UUID
    external_paper_id: UUID
    semantic_scholar_id: str
    local_paper_id: UUID | None
    local_paper_version_id: UUID | None
    discovered_by_action_id: UUID | None
    origins: list[CandidateOrigin]
    relation_depth: int = Field(ge=0, le=5)
    scores: CandidateScoreComponentsResponse
    rank: int = Field(ge=1)
    decision: SelectionDecision
    decision_reason: str
    provider: str | None
    configured_model: str | None
    model_version: str | None
    prompt_version: str | None
    generated_at: datetime | None
    verification_status: VerificationStatus
    schema_version: int = Field(ge=1)
    created_at: datetime


class CandidateDiscoveryResponse(ApiModel):
    id: UUID
    candidate_id: UUID
    action_id: UUID | None
    origin: CandidateOrigin
    relation_depth: int = Field(ge=0, le=5)
    discovered_at: datetime


class ExternalPaperResponse(ApiModel):
    id: UUID
    semantic_scholar_id: str
    title: str
    abstract: str | None
    year: Annotated[int | None, Field(ge=1000, le=9999)]
    publication_date: date | None
    venue: str | None
    authors: list[str]
    external_ids: dict[str, str]
    arxiv_id: str | None
    doi: str | None
    citation_count: int = Field(ge=0)
    influential_citation_count: int = Field(ge=0)
    full_text_available: bool
    source: Literal["semantic_scholar"]
    schema_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class PaperRelationResponse(ApiModel):
    id: UUID
    source_paper_id: UUID
    source_paper_version_id: UUID
    target_paper_id: UUID
    target_paper_version_id: UUID
    relation_type: PaperRelationType
    provenance: RelationProvenance
    evidence_ids: list[UUID]
    justification: str
    provider: str | None
    model_version: str | None
    prompt_version: str | None
    confidence: Annotated[float | None, Field(ge=0, le=1)]
    verification_status: VerificationStatus
    generated_at: datetime
    schema_version: int = Field(ge=1)
    created_at: datetime


class RelatedComparisonSummaryResponse(ApiModel):
    id: UUID
    target_paper_id: UUID
    target_paper_version_id: UUID
    target_analysis_id: UUID
    target_analysis_scope: AnalysisScope
    comparability_status: ComparabilityStatus
    summary: str
    provider: str
    model_version: str
    prompt_version: str
    generated_at: datetime
    verification_status: VerificationStatus


class RelatedWorkItemResponse(ApiModel):
    candidate: SearchCandidateResponse
    paper: ExternalPaperResponse
    discoveries: list[CandidateDiscoveryResponse]
    relations: list[PaperRelationResponse]
    comparison_id: UUID | None


class RelatedWorkResponse(ApiModel):
    paper_id: UUID
    session: SearchSessionResponse | None
    actions: list[SearchActionResponse]
    items: list[RelatedWorkItemResponse]
    comparisons: list[RelatedComparisonSummaryResponse]
    total: int = Field(ge=0)


class ComparisonDimensionResponse(ApiModel):
    id: UUID
    comparison_id: UUID
    name: ComparisonDimensionName
    position: int = Field(ge=0, le=13)
    source_value: str
    target_value: str
    assessment: str
    source_evidence_ids: list[UUID]
    target_evidence_ids: list[UUID]
    schema_version: int = Field(ge=1)
    created_at: datetime


class ComparisonEvidenceResponse(ApiModel):
    id: UUID
    analysis_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    analysis_scope: AnalysisScope
    section: str
    excerpt: str
    evidence_type: EvidenceType
    verification_status: VerificationStatus


class ComparisonResponse(ApiModel):
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
    dimensions: list[ComparisonDimensionResponse]
    relations: list[PaperRelationResponse]
    evidence: list[ComparisonEvidenceResponse]
    provider: str
    configured_model: str
    model_version: str
    prompt_version: str
    generated_at: datetime
    source: str
    verification_status: VerificationStatus
    usage: ModelUsageResponse
    schema_version: int = Field(ge=1)
    created_at: datetime
