"""Idempotent, version-aware arXiv ingestion use case."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from paper_harness.application.arxiv_query import build_arxiv_query
from paper_harness.domain.errors import DuplicateDailyRunError
from paper_harness.domain.models import (
    DailyRun,
    PipelineExecutionMode,
    RunStatus,
    TopicConfig,
)
from paper_harness.ports.arxiv import ArxivPort, ArxivPortError, normalize_arxiv_records
from paper_harness.ports.repository import RepositoryIntegrityError, RepositoryPort

SCHEDULE_TIME_ZONE = ZoneInfo("Asia/Kuala_Lumpur")


class IngestionResumeError(ValueError):
    error_code = "INGESTION_RESUME_CONFLICT"
    retryable = False


class IngestArxiv:
    def __init__(
        self,
        *,
        arxiv: ArxivPort,
        repository: RepositoryPort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._arxiv = arxiv
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        topic: TopicConfig,
        *,
        logical_date: date | None = None,
        pipeline_execution_mode: PipelineExecutionMode = PipelineExecutionMode.STANDALONE,
        pipeline_selection_limit: int | None = None,
        pipeline_execution_id: UUID | None = None,
        resume_existing: bool = False,
    ) -> DailyRun:
        started_at = self._aware_now()
        run_date = logical_date or started_at.astimezone(SCHEDULE_TIME_ZONE).date()

        with self._repository.daily_run_lock(topic.id, run_date):
            self._repository.upsert_topic(topic)
            existing = self._repository.get_run_for_date(
                topic.id,
                run_date,
                pipeline_execution_id=pipeline_execution_id,
            )
            if existing is not None:
                if not resume_existing:
                    raise DuplicateDailyRunError(
                        f"arXiv ingestion already exists for topic {topic.slug!r} on {run_date}"
                    )
                _require_matching_pipeline_provenance(
                    existing,
                    pipeline_execution_mode=pipeline_execution_mode,
                    pipeline_execution_id=pipeline_execution_id,
                )
                if existing.status is RunStatus.COMPLETE:
                    return existing
                if existing.status not in (RunStatus.RUNNING, RunStatus.FAILED):
                    raise IngestionResumeError(
                        f"arXiv ingestion in {existing.status.value} state cannot resume"
                    )
                if existing.status is RunStatus.FAILED:
                    cursor_from, cursor_to = _current_cursor_window(
                        self._repository,
                        topic,
                        started_at=started_at,
                    )
                else:
                    if existing.cursor_from is None or existing.cursor_to is None:
                        raise IngestionResumeError("resumed arXiv ingestion lost its cursor window")
                    cursor_from = existing.cursor_from
                    cursor_to = existing.cursor_to
                run = self._repository.restart_ingestion_run(
                    existing.id,
                    started_at=started_at,
                    cursor_from=cursor_from,
                    cursor_to=cursor_to,
                    pipeline_selection_limit=pipeline_selection_limit,
                )
            else:
                cursor_from, cursor_to = _current_cursor_window(
                    self._repository,
                    topic,
                    started_at=started_at,
                )
                run = self._repository.start_ingestion_run(
                    topic_id=topic.id,
                    logical_date=run_date,
                    started_at=started_at,
                    cursor_from=cursor_from,
                    cursor_to=cursor_to,
                    pipeline_execution_mode=pipeline_execution_mode,
                    pipeline_selection_limit=pipeline_selection_limit,
                    pipeline_execution_id=pipeline_execution_id,
                )

            try:
                records = self._arxiv.search(
                    query=build_arxiv_query(topic),
                    updated_from=cursor_from,
                    updated_until=cursor_to,
                    max_results=topic.max_results,
                )
                unique_records = normalize_arxiv_records(records)
            except ArxivPortError as error:
                self._repository.fail_ingestion_run(
                    run.id,
                    completed_at=self._aware_now(),
                    error_code=error.error_code,
                    error_detail=str(error)[:1000],
                )
                raise

            try:
                return self._repository.persist_arxiv_batch_and_complete(
                    topic=topic,
                    run_id=run.id,
                    records=unique_records,
                    watermark=cursor_to,
                    advance_shared_cursor=(
                        pipeline_execution_mode is not PipelineExecutionMode.SMOKE
                    ),
                    persisted_at=self._aware_now(),
                    completed_at=self._aware_now(),
                )
            except RepositoryIntegrityError as error:
                self._repository.fail_ingestion_run(
                    run.id,
                    completed_at=self._aware_now(),
                    error_code=error.error_code,
                    error_detail=str(error)[:1000],
                )
                raise

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ingestion clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def _require_matching_pipeline_provenance(
    run: DailyRun,
    *,
    pipeline_execution_mode: PipelineExecutionMode,
    pipeline_execution_id: UUID | None,
) -> None:
    if (
        run.pipeline_execution_mode is not pipeline_execution_mode
        or run.pipeline_execution_id != pipeline_execution_id
    ):
        raise IngestionResumeError(
            "persisted arXiv ingestion provenance does not match the requested pipeline"
        )


def _current_cursor_window(
    repository: RepositoryPort,
    topic: TopicConfig,
    *,
    started_at: datetime,
) -> tuple[datetime, datetime]:
    cursor = repository.get_ingestion_cursor(topic.id)
    base_watermark = (
        cursor.watermark
        if cursor is not None
        else started_at - timedelta(days=topic.initial_lookback_days)
    )
    return base_watermark - timedelta(hours=topic.overlap_hours), started_at
