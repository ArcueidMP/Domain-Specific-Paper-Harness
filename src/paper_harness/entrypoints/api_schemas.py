"""Pydantic response schemas that define the frontend OpenAPI contract."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from paper_harness.domain.analysis import (
    AnalysisScope,
    ClaimType,
    EvidenceType,
    VerificationStatus,
)
from paper_harness.domain.models import PaperStage, RunItemStatus, RunOperation, RunStatus


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


class ReportResponse(ApiModel):
    id: UUID
    run_id: UUID
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


class RunDetailResponse(RunSummary):
    items: list[RunItemResponse]
    report: ReportResponse | None


class RunListResponse(ApiModel):
    items: list[RunSummary]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class ModelUsageResponse(ApiModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    call_count: int = Field(ge=1)
    duration_ms: int = Field(ge=0)
    estimated_cost_usd: Decimal | None


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
