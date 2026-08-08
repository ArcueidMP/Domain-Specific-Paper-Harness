"""PostgreSQL persistence boundary used by M1 application use cases."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from paper_harness.application.read_models import (
    AnalysisDetail,
    AnalysisTarget,
    PaperDetail,
    RunDetail,
    StoredTopic,
)
from paper_harness.domain.analysis import AnalysisBundle, AnalysisScope, Evidence, ParsedPaper
from paper_harness.domain.models import DailyRun, IngestionCursor, Paper, PaperStage, TopicConfig
from paper_harness.ports.arxiv import ArxivPaperRecord


class RepositoryError(RuntimeError):
    """Base persistence-boundary failure."""

    error_code = "REPOSITORY_FAILURE"
    retryable = False


class RepositoryUnavailableError(RepositoryError):
    """The configured PostgreSQL database is unavailable."""

    error_code = "REPOSITORY_UNAVAILABLE"
    retryable = True


class RepositoryIntegrityError(RepositoryError):
    """PostgreSQL rejected a validated write without exposing SQL parameters."""

    error_code = "PERSISTENCE_INTEGRITY_FAILED"


class MigrationIncompatibleError(RepositoryError):
    """The database migration revision differs from the application head."""

    error_code = "MIGRATION_INCOMPATIBLE"


class RepositoryPort(Protocol):
    def daily_run_lock(
        self, topic_id: UUID, logical_date: date
    ) -> AbstractContextManager[None]: ...

    def upsert_topic(self, topic: TopicConfig) -> StoredTopic: ...

    def get_ingestion_cursor(self, topic_id: UUID) -> IngestionCursor | None: ...

    def get_run_for_date(self, topic_id: UUID, logical_date: date) -> DailyRun | None: ...

    def start_ingestion_run(
        self,
        *,
        topic_id: UUID,
        logical_date: date,
        started_at: datetime,
        cursor_from: datetime,
        cursor_to: datetime,
    ) -> DailyRun: ...

    def persist_arxiv_batch_and_complete(
        self,
        *,
        topic: TopicConfig,
        run_id: UUID,
        records: tuple[ArxivPaperRecord, ...],
        watermark: datetime,
        persisted_at: datetime,
        completed_at: datetime,
    ) -> DailyRun: ...

    def fail_ingestion_run(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> DailyRun: ...

    def check_ready(self) -> None: ...

    def list_topics(self) -> tuple[StoredTopic, ...]: ...

    def list_papers(
        self, *, topic_slug: str | None, limit: int, offset: int
    ) -> tuple[tuple[Paper, ...], int]: ...

    def get_paper(self, paper_id: UUID) -> PaperDetail | None: ...

    def list_runs(
        self, *, topic_slug: str | None, limit: int, offset: int
    ) -> tuple[tuple[DailyRun, ...], int]: ...

    def get_latest_run(self, *, topic_slug: str | None) -> RunDetail | None: ...

    def get_analysis_targets(
        self, topic_id: UUID, paper_ids: tuple[UUID, ...]
    ) -> tuple[AnalysisTarget, ...]: ...

    def get_analysis_run_for_date(self, topic_id: UUID, logical_date: date) -> DailyRun | None: ...

    def start_analysis_run(
        self,
        *,
        topic_id: UUID,
        logical_date: date,
        analysis_scope: AnalysisScope,
        started_at: datetime,
        targets: tuple[AnalysisTarget, ...],
    ) -> DailyRun: ...

    def advance_analysis_item(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        expected_stage: PaperStage,
        next_stage: PaperStage,
        updated_at: datetime,
    ) -> None: ...

    def persist_parsed_paper(
        self,
        *,
        run_id: UUID,
        parsed_paper: ParsedPaper,
        expected_stage: PaperStage,
        updated_at: datetime,
    ) -> ParsedPaper: ...

    def persist_analysis_bundle(
        self,
        *,
        run_id: UUID,
        bundle: AnalysisBundle,
        expected_stage: PaperStage,
        updated_at: datetime,
    ) -> None: ...

    def fail_analysis_item(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        failed_stage: PaperStage,
        error_code: str,
        retryable: bool,
        error_detail: str,
        updated_at: datetime,
    ) -> None: ...

    def finalize_analysis_run(self, run_id: UUID, *, completed_at: datetime) -> DailyRun: ...

    def fail_analysis_run(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        failed_stage: PaperStage,
        error_code: str,
        retryable: bool,
        error_detail: str,
    ) -> DailyRun: ...

    def get_paper_analysis(
        self,
        paper_id: UUID,
        *,
        paper_version_id: UUID | None,
        analysis_scope: AnalysisScope | None = None,
    ) -> AnalysisDetail | None: ...

    def list_paper_evidence(
        self,
        paper_id: UUID,
        *,
        analysis_id: UUID,
        paper_version_id: UUID | None,
        analysis_scope: AnalysisScope | None = None,
    ) -> tuple[Evidence, ...] | None: ...
