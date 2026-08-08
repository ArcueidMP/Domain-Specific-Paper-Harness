"""Deterministic test doubles kept outside production wiring."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID, uuid4, uuid5

from paper_harness.application.read_models import PaperDetail, RunDetail, StoredTopic
from paper_harness.domain.models import (
    DailyRun,
    IngestionCursor,
    Paper,
    PaperStage,
    RunItem,
    RunItemStatus,
    RunOperation,
    RunStatus,
    TopicConfig,
)
from paper_harness.ports.arxiv import ArxivPaperRecord, ArxivPortError


class FakeArxiv:
    def __init__(
        self,
        records: tuple[ArxivPaperRecord, ...] = (),
        error: ArxivPortError | None = None,
    ) -> None:
        self.records = records
        self.error = error
        self.calls: list[tuple[str, datetime, datetime, int]] = []

    def search(
        self,
        *,
        query: str,
        updated_from: datetime,
        updated_until: datetime,
        max_results: int,
    ) -> tuple[ArxivPaperRecord, ...]:
        self.calls.append((query, updated_from, updated_until, max_results))
        if self.error is not None:
            raise self.error
        return self.records


class FakeRepository:
    def __init__(self) -> None:
        self.topic: StoredTopic | None = None
        self.cursor: IngestionCursor | None = None
        self.run: DailyRun | None = None
        self.items: tuple[RunItem, ...] = ()
        self.papers: tuple[Paper, ...] = ()
        self.paper_detail: PaperDetail | None = None
        self.ready_error: Exception | None = None
        self.locked = False

    @contextmanager
    def daily_run_lock(self, topic_id: UUID, logical_date: date) -> Generator[None]:
        del topic_id, logical_date
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def upsert_topic(self, topic: TopicConfig) -> StoredTopic:
        created_at = datetime(2026, 1, 1, tzinfo=UTC)
        self.topic = StoredTopic(config=topic, created_at=created_at)
        return self.topic

    def get_ingestion_cursor(self, topic_id: UUID) -> IngestionCursor | None:
        del topic_id
        return self.cursor

    def get_run_for_date(self, topic_id: UUID, logical_date: date) -> DailyRun | None:
        del topic_id, logical_date
        return self.run

    def start_ingestion_run(
        self,
        *,
        topic_id: UUID,
        logical_date: date,
        started_at: datetime,
        cursor_from: datetime,
        cursor_to: datetime,
    ) -> DailyRun:
        self.run = DailyRun(
            id=uuid4(),
            topic_id=topic_id,
            logical_date=logical_date,
            operation=RunOperation.ARXIV_INGESTION,
            status=RunStatus.RUNNING,
            started_at=started_at,
            completed_at=None,
            cursor_from=cursor_from,
            cursor_to=cursor_to,
            discovered_count=0,
            normalized_count=0,
            failed_count=0,
            error_code=None,
            error_detail=None,
            schema_version=1,
            created_at=started_at,
        )
        return self.run

    def persist_arxiv_batch_and_complete(
        self,
        *,
        topic: TopicConfig,
        run_id: UUID,
        records: tuple[ArxivPaperRecord, ...],
        watermark: datetime,
        persisted_at: datetime,
        completed_at: datetime,
    ) -> DailyRun:
        del topic
        self.items = tuple(
            RunItem(
                id=uuid5(run_id, f"{record.canonical_arxiv_id}:v{record.version}"),
                run_id=run_id,
                paper_id=uuid5(run_id, record.canonical_arxiv_id),
                paper_version_id=uuid5(
                    run_id, f"version:{record.canonical_arxiv_id}:v{record.version}"
                ),
                stage=PaperStage.NORMALIZED,
                status=RunItemStatus.COMPLETED,
                failed_stage=None,
                error_code=None,
                retryable=None,
                error_detail=None,
                schema_version=1,
                created_at=persisted_at,
                updated_at=persisted_at,
            )
            for record in records
        )
        self.cursor = IngestionCursor(
            topic_id=self.run.topic_id if self.run is not None else uuid4(),
            watermark=watermark,
            schema_version=1,
            created_at=persisted_at,
            updated_at=persisted_at,
        )
        if self.run is None or self.run.id != run_id:
            raise AssertionError("run was not started")
        self.run = replace(
            self.run,
            status=RunStatus.COMPLETE,
            completed_at=completed_at,
            discovered_count=len(records),
            normalized_count=len(self.items),
        )
        return self.run

    def fail_ingestion_run(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> DailyRun:
        if self.run is None or self.run.id != run_id:
            raise AssertionError("run was not started")
        self.run = replace(
            self.run,
            status=RunStatus.FAILED,
            completed_at=completed_at,
            error_code=error_code,
            error_detail=error_detail,
        )
        return self.run

    def check_ready(self) -> None:
        if self.ready_error is not None:
            raise self.ready_error

    def list_topics(self) -> tuple[StoredTopic, ...]:
        return () if self.topic is None else (self.topic,)

    def list_papers(
        self, *, topic_slug: str | None, limit: int, offset: int
    ) -> tuple[tuple[Paper, ...], int]:
        del topic_slug
        return self.papers[offset : offset + limit], len(self.papers)

    def get_paper(self, paper_id: UUID) -> PaperDetail | None:
        del paper_id
        return self.paper_detail

    def list_runs(
        self, *, topic_slug: str | None, limit: int, offset: int
    ) -> tuple[tuple[DailyRun, ...], int]:
        del topic_slug, limit, offset
        return (() if self.run is None else (self.run,)), int(self.run is not None)

    def get_latest_run(self, *, topic_slug: str | None) -> RunDetail | None:
        del topic_slug
        return None if self.run is None else RunDetail(run=self.run, items=self.items)
