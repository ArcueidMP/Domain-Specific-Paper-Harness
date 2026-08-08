"""Pydantic response schemas that define the frontend OpenAPI contract."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from paper_harness.domain.models import PaperStage, RunItemStatus, RunOperation, RunStatus


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    status: RunStatus
    started_at: datetime
    completed_at: datetime | None
    cursor_from: datetime
    cursor_to: datetime
    discovered_count: int = Field(ge=0)
    normalized_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    error_code: str | None
    error_detail: str | None
    schema_version: int = Field(ge=1)
    created_at: datetime


class RunDetailResponse(RunSummary):
    items: list[RunItemResponse]


class RunListResponse(ApiModel):
    items: list[RunSummary]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
