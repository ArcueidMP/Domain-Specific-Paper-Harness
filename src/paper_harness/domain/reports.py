"""Deterministic M2 publication records and visible item failures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.models import PaperStage, RunStatus


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainInvariantError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ReportFailure:
    id: UUID
    report_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    failed_stage: PaperStage
    error_code: str
    retryable: bool
    error_detail: str
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.error_code or not self.error_detail:
            raise DomainInvariantError("report failure requires code and detail")
        if len(self.error_detail) > 1000:
            raise DomainInvariantError("report failure detail must be concise")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class Report:
    id: UUID
    run_id: UUID
    topic_id: UUID
    logical_date: date
    status: RunStatus
    title: str
    summary: str
    source: str
    generated_at: datetime
    schema_version: int
    created_at: datetime
    failures: tuple[ReportFailure, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in (RunStatus.COMPLETE, RunStatus.PARTIAL):
            raise DomainInvariantError("only complete or partial runs may publish reports")
        if not self.title or not self.summary or not self.source:
            raise DomainInvariantError("report title, summary, and source are required")
        if self.status is RunStatus.COMPLETE and self.failures:
            raise DomainInvariantError("complete report cannot list failures")
        if self.status is RunStatus.PARTIAL and not self.failures:
            raise DomainInvariantError("partial report must list item failures")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")
        _require_aware(self.generated_at, "generated_at")
        _require_aware(self.created_at, "created_at")
