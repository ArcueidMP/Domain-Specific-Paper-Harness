"""PostgreSQL persistence boundary used by M1 application use cases."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from paper_harness.application.read_models import PaperDetail, RunDetail, StoredTopic
from paper_harness.domain.models import DailyRun, IngestionCursor, Paper, TopicConfig
from paper_harness.ports.arxiv import ArxivPaperRecord


class RepositoryError(RuntimeError):
    """Base persistence-boundary failure."""


class RepositoryUnavailableError(RepositoryError):
    """The configured PostgreSQL database is unavailable."""


class MigrationIncompatibleError(RepositoryError):
    """The database migration revision differs from the application head."""


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
