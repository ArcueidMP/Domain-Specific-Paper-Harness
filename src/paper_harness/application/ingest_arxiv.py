"""Idempotent, version-aware arXiv ingestion use case."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from paper_harness.application.arxiv_query import build_arxiv_query, matches_arxiv_topic
from paper_harness.application.pipeline_budget import ARXIV_DISCOVERY_SECONDS
from paper_harness.domain.errors import DuplicateDailyRunError
from paper_harness.domain.models import (
    DailyRun,
    PipelineExecutionMode,
    RunStatus,
    TopicConfig,
)
from paper_harness.ports.arxiv import (
    MAX_ARXIV_ID_LOOKUP,
    ArxivDiscoveryIncompleteError,
    ArxivDiscoveryProgress,
    ArxivPaperRecord,
    ArxivPort,
    ArxivPortError,
    ArxivResponseError,
    ArxivTokenExpiredError,
    normalize_arxiv_records,
)
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
        monotonic: Callable[[], float] = time.monotonic,
        max_total_seconds: float = ARXIV_DISCOVERY_SECONDS,
        max_pages: int = 100,
    ) -> None:
        self._arxiv = arxiv
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        if not 1 <= max_total_seconds <= ARXIV_DISCOVERY_SECONDS or not 1 <= max_pages <= 100:
            raise ValueError("arXiv discovery budget must be within 900 seconds and 100 pages")
        self._monotonic = monotonic
        self._max_total_seconds = max_total_seconds
        self._max_pages = max_pages

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
                saved_progress = self._repository.get_ingestion_progress(existing.id)
                if existing.status is RunStatus.FAILED and saved_progress is None:
                    cursor_from, cursor_to = _current_cursor_window(
                        self._repository,
                        topic,
                        started_at=started_at,
                        logical_date=run_date,
                        pipeline_execution_mode=pipeline_execution_mode,
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
                    logical_date=run_date,
                    pipeline_execution_mode=pipeline_execution_mode,
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
                self._harvest(topic, run, cursor_from, cursor_to)
            except (ArxivPortError, RepositoryIntegrityError, IngestionResumeError) as error:
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
                    records=(),
                    watermark=cursor_to,
                    advance_shared_cursor=(
                        pipeline_execution_mode
                        not in (
                            PipelineExecutionMode.REPROCESS,
                            PipelineExecutionMode.SMOKE,
                        )
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

    def _harvest(
        self, topic: TopicConfig, run: DailyRun, cursor_from: datetime, cursor_to: datetime
    ) -> None:
        query = build_arxiv_query(topic)
        progress = self._repository.get_ingestion_progress(run.id)
        if progress is None:
            progress = ArxivDiscoveryProgress(
                query=query, categories=topic.categories, day=cursor_from.date()
            )
            baseline_records: tuple[ArxivPaperRecord, ...] = ()
            if run.pipeline_execution_mode is PipelineExecutionMode.REPROCESS:
                baseline_ids = self._repository.get_reprocessing_baseline_paper_version_ids(
                    topic.id, run.logical_date
                )
                targets = self._repository.get_analysis_targets_by_version_ids(
                    topic.id, tuple(sorted(baseline_ids, key=str))
                )
                if frozenset(target.version.id for target in targets) != baseline_ids:
                    raise RepositoryIntegrityError(
                        "reprocessing baseline lost its topic-owned versions"
                    )
                baseline_records = tuple(
                    ArxivPaperRecord(
                        canonical_arxiv_id=target.version.canonical_arxiv_id,
                        version=target.version.version,
                        title=target.version.title,
                        abstract=target.version.abstract,
                        submitted_at=target.version.submitted_at,
                        updated_at=target.version.updated_at,
                        primary_category=target.version.primary_category,
                        categories=target.version.categories,
                        authors=target.version.authors,
                        pdf_url=target.version.pdf_url,
                        source_url=target.version.source_url,
                    )
                    for target in targets
                )
            self._repository.persist_ingestion_progress(
                topic=topic,
                run_id=run.id,
                progress=progress,
                records=baseline_records,
                persisted_at=self._aware_now(),
            )
        elif progress.query != query or progress.categories != topic.categories:
            raise IngestionResumeError("resumable arXiv discovery topic scope changed")
        deadline = self._monotonic() + self._max_total_seconds
        pages = 0
        token_restarts = 0
        while not progress.complete:
            if self._monotonic() >= deadline:
                raise ArxivDiscoveryIncompleteError(
                    "arXiv discovery time budget exhausted; "
                    "resume the same logical date to continue its saved window"
                )
            if progress.pending_ids:
                ids = progress.pending_ids[: min(topic.max_results, MAX_ARXIV_ID_LOOKUP)]
                records = self._arxiv.get_papers_by_ids(
                    canonical_arxiv_ids=ids, timeout_seconds=deadline - self._monotonic()
                )
                returned_ids = {record.canonical_arxiv_id for record in records}
                missing = tuple(identifier for identifier in ids if identifier not in returned_ids)
                normalized = normalize_arxiv_records(records)
                accepted = tuple(
                    record
                    for record in normalized
                    if matches_arxiv_topic(topic, record)
                    and (
                        run.pipeline_execution_mode is not PipelineExecutionMode.REPROCESS
                        or record.updated_at <= cursor_to
                    )
                )
                progress = replace(progress, pending_ids=missing + progress.pending_ids[len(ids) :])
                self._repository.persist_ingestion_progress(
                    topic=topic,
                    run_id=run.id,
                    progress=progress,
                    records=accepted,
                    persisted_at=self._aware_now(),
                )
                if missing:
                    raise ArxivResponseError(
                        f"arXiv metadata lookup omitted {len(missing)} required identities; "
                        "valid records were saved and missing identities remain pending"
                    )
                continue
            if progress.page_exhausted:
                if progress.category_index + 1 < len(progress.categories):
                    progress = replace(
                        progress, category_index=progress.category_index + 1, page_exhausted=False
                    )
                elif progress.day < cursor_to.date():
                    progress = replace(
                        progress,
                        day=progress.day + timedelta(days=1),
                        category_index=0,
                        page_exhausted=False,
                    )
                else:
                    progress = replace(progress, complete=True)
                self._checkpoint(topic, run, progress)
                continue
            if pages >= self._max_pages:
                raise ArxivDiscoveryIncompleteError(
                    "arXiv discovery page budget exhausted; "
                    "resume the same logical date to continue its saved window"
                )
            pages += 1
            try:
                page = self._arxiv.list_updated_identifiers(
                    day=progress.day,
                    category=progress.categories[progress.category_index],
                    resumption_token=progress.resumption_token,
                    timeout_seconds=deadline - self._monotonic(),
                )
            except ArxivTokenExpiredError:
                if progress.resumption_token is None or token_restarts >= 2:
                    raise
                token_restarts += 1
                progress = replace(progress, resumption_token=None)
                self._checkpoint(topic, run, progress)
                continue
            if (
                page.resumption_token is not None
                and page.resumption_token == progress.resumption_token
            ):
                raise ArxivResponseError(
                    "arXiv OAI returned the same continuation token without advancing"
                )
            progress = replace(
                progress,
                pending_ids=page.canonical_arxiv_ids,
                resumption_token=page.resumption_token,
                page_exhausted=page.resumption_token is None,
            )
            self._checkpoint(topic, run, progress)

    def _checkpoint(
        self, topic: TopicConfig, run: DailyRun, progress: ArxivDiscoveryProgress
    ) -> None:
        self._repository.persist_ingestion_progress(
            topic=topic,
            run_id=run.id,
            progress=progress,
            records=(),
            persisted_at=self._aware_now(),
        )

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
    logical_date: date,
    pipeline_execution_mode: PipelineExecutionMode,
) -> tuple[datetime, datetime]:
    if pipeline_execution_mode is PipelineExecutionMode.REPROCESS:
        logical_date_end = datetime.combine(
            logical_date + timedelta(days=1),
            datetime.min.time(),
            tzinfo=SCHEDULE_TIME_ZONE,
        ).astimezone(UTC)
        cursor_to = min(started_at, logical_date_end)
        return (
            cursor_to - timedelta(days=topic.initial_lookback_days, hours=topic.overlap_hours),
            cursor_to,
        )
    cursor = repository.get_ingestion_cursor(topic.id)
    base_watermark = (
        cursor.watermark
        if cursor is not None
        else started_at - timedelta(days=topic.initial_lookback_days)
    )
    return base_watermark - timedelta(hours=topic.overlap_hours), started_at
