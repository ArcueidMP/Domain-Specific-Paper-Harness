from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from tests.fakes import FakeArxiv, FakeRepository

from paper_harness.application.ingest_arxiv import (
    SCHEDULE_TIME_ZONE,
    IngestArxiv,
    IngestionResumeError,
)
from paper_harness.domain.errors import DuplicateDailyRunError
from paper_harness.domain.models import (
    DailyRun,
    IngestionCursor,
    PipelineExecutionMode,
    RunStatus,
    TopicConfig,
)
from paper_harness.ports.arxiv import (
    ArxivPaperRecord,
    ArxivResultLimitError,
    ArxivUnavailableError,
    normalize_arxiv_records,
)
from paper_harness.ports.repository import RepositoryIntegrityError

PIPELINE_EXECUTION_ID = UUID("b1f599e0-6b87-54af-a3b4-2a3d1473de93")


class WindowAwareFakeArxiv(FakeArxiv):
    def search(
        self,
        *,
        query: str,
        updated_from: datetime,
        updated_until: datetime,
        max_results: int,
    ) -> tuple[ArxivPaperRecord, ...]:
        records = super().search(
            query=query,
            updated_from=updated_from,
            updated_until=updated_until,
            max_results=max_results,
        )
        return normalize_arxiv_records(
            records,
            updated_from=updated_from,
            updated_until=updated_until,
        )[:max_results]


def test_ingestion_uses_overlap_deduplicates_and_advances_cursor(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    arxiv = FakeArxiv((arxiv_record_v1, arxiv_record_v1))
    repository = FakeRepository()

    run = IngestArxiv(arxiv=arxiv, repository=repository, clock=lambda: now).execute(
        topic_config, logical_date=date(2026, 1, 10)
    )

    assert run.status is RunStatus.COMPLETE
    assert run.discovered_count == run.normalized_count == 1
    assert arxiv.calls[0][1] == now - timedelta(days=7, hours=48)
    assert arxiv.calls[0][2] == now
    assert repository.cursor is not None
    assert repository.cursor.watermark == now


def test_week_overlap_captures_record_that_became_visible_after_submission(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    prior_watermark = datetime(2026, 8, 24, 2, tzinfo=UTC)
    now = prior_watermark + timedelta(days=1)
    delayed_updated_at = prior_watermark - timedelta(hours=100)
    delayed_record = replace(
        arxiv_record_v1,
        submitted_at=delayed_updated_at - timedelta(hours=1),
        updated_at=delayed_updated_at,
    )
    topic = replace(topic_config, overlap_hours=168)
    repository = FakeRepository()
    repository.cursor = IngestionCursor(
        topic_id=topic.id,
        watermark=prior_watermark,
        schema_version=1,
        created_at=prior_watermark,
        updated_at=prior_watermark,
    )
    arxiv = WindowAwareFakeArxiv((delayed_record,))

    run = IngestArxiv(arxiv=arxiv, repository=repository, clock=lambda: now).execute(
        topic,
        logical_date=now.date(),
    )

    expected_from = prior_watermark - timedelta(hours=168)
    assert arxiv.calls[0][1:] == (expected_from, now, topic.max_results)
    assert expected_from <= delayed_record.updated_at <= now
    assert run.discovered_count == run.normalized_count == 1
    assert len(repository.items) == 1


def test_cursor_overlap_persists_locally_sorted_records_from_disordered_input(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior_watermark = datetime(2026, 1, 9, 5, tzinfo=UTC)
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    older = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.00001",
        title="Older paper",
        submitted_at=datetime(2026, 1, 8, 2, tzinfo=UTC),
        updated_at=datetime(2026, 1, 8, 3, tzinfo=UTC),
        pdf_url="https://arxiv.org/pdf/2601.00001v1",
        source_url="https://arxiv.org/abs/2601.00001v1",
    )
    newer = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.00002",
        title="Newer paper",
        submitted_at=datetime(2026, 1, 9, 2, tzinfo=UTC),
        updated_at=datetime(2026, 1, 9, 3, tzinfo=UTC),
        pdf_url="https://arxiv.org/pdf/2601.00002v1",
        source_url="https://arxiv.org/abs/2601.00002v1",
    )
    repository = FakeRepository()
    repository.cursor = IngestionCursor(
        topic_id=topic_config.id,
        watermark=prior_watermark,
        schema_version=1,
        created_at=prior_watermark,
        updated_at=prior_watermark,
    )
    persisted: list[tuple[ArxivPaperRecord, ...]] = []
    original_persist = repository.persist_arxiv_batch_and_complete

    def capture_persist(
        *,
        topic: TopicConfig,
        run_id: UUID,
        records: tuple[ArxivPaperRecord, ...],
        watermark: datetime,
        advance_shared_cursor: bool,
        persisted_at: datetime,
        completed_at: datetime,
    ) -> DailyRun:
        persisted.append(records)
        return original_persist(
            topic=topic,
            run_id=run_id,
            records=records,
            watermark=watermark,
            advance_shared_cursor=advance_shared_cursor,
            persisted_at=persisted_at,
            completed_at=completed_at,
        )

    monkeypatch.setattr(repository, "persist_arxiv_batch_and_complete", capture_persist)
    arxiv = FakeArxiv((older, newer, older))
    run = IngestArxiv(arxiv=arxiv, repository=repository, clock=lambda: now).execute(
        topic_config,
        logical_date=now.date(),
    )

    assert run.status is RunStatus.COMPLETE
    assert [record.canonical_arxiv_id for record in persisted[0]] == [
        "2601.00002",
        "2601.00001",
    ]
    assert arxiv.calls[0][1:] == (
        prior_watermark - timedelta(hours=topic_config.overlap_hours),
        now,
        topic_config.max_results,
    )
    assert repository.cursor is not None
    assert repository.cursor.watermark == now


def test_smoke_ingestion_persists_results_without_advancing_the_shared_cursor(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    prior_watermark = datetime(2026, 1, 8, 5, tzinfo=UTC)
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    repository = FakeRepository()
    prior_cursor = IngestionCursor(
        topic_id=topic_config.id,
        watermark=prior_watermark,
        schema_version=1,
        created_at=prior_watermark,
        updated_at=prior_watermark,
    )
    repository.cursor = prior_cursor
    arxiv = FakeArxiv((arxiv_record_v1,))

    run = IngestArxiv(arxiv=arxiv, repository=repository, clock=lambda: now).execute(
        topic_config,
        logical_date=now.date(),
        pipeline_execution_mode=PipelineExecutionMode.SMOKE,
        pipeline_selection_limit=1,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
    )

    assert run.status is RunStatus.COMPLETE
    assert run.pipeline_execution_mode is PipelineExecutionMode.SMOKE
    assert run.discovered_count == run.normalized_count == 1
    assert len(repository.items) == 1
    assert repository.cursor == prior_cursor
    assert arxiv.calls[0][1:] == (
        prior_watermark - timedelta(hours=topic_config.overlap_hours),
        now,
        topic_config.max_results,
    )


def test_reprocess_uses_logical_date_lookback_without_advancing_shared_cursor(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    prior_watermark = datetime(2026, 1, 10, 4, tzinfo=UTC)
    now = datetime(2026, 1, 12, 5, tzinfo=UTC)
    logical_date = date(2026, 1, 10)
    repository = FakeRepository()
    prior_cursor = IngestionCursor(
        topic_id=topic_config.id,
        watermark=prior_watermark,
        schema_version=1,
        created_at=prior_watermark,
        updated_at=prior_watermark,
    )
    repository.cursor = prior_cursor
    arxiv = FakeArxiv((arxiv_record_v1,))

    run = IngestArxiv(arxiv=arxiv, repository=repository, clock=lambda: now).execute(
        topic_config,
        logical_date=logical_date,
        pipeline_execution_mode=PipelineExecutionMode.REPROCESS,
        pipeline_selection_limit=1,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
    )

    logical_date_end = datetime(2026, 1, 11, 0, tzinfo=SCHEDULE_TIME_ZONE).astimezone(UTC)
    assert run.status is RunStatus.COMPLETE
    assert run.pipeline_execution_mode is PipelineExecutionMode.REPROCESS
    assert repository.cursor == prior_cursor
    assert arxiv.calls[0][1:] == (
        logical_date_end
        - timedelta(
            days=topic_config.initial_lookback_days,
            hours=topic_config.overlap_hours,
        ),
        logical_date_end,
        topic_config.max_results,
    )


def test_duplicate_logical_run_does_not_call_arxiv(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    arxiv = FakeArxiv((arxiv_record_v1,))
    repository = FakeRepository()
    use_case = IngestArxiv(arxiv=arxiv, repository=repository, clock=lambda: now)
    use_case.execute(topic_config, logical_date=now.date())

    with pytest.raises(DuplicateDailyRunError):
        use_case.execute(topic_config, logical_date=now.date())
    assert len(arxiv.calls) == 1


def test_global_arxiv_failure_is_recorded_as_failed(topic_config: TopicConfig) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    repository = FakeRepository()
    arxiv = FakeArxiv(error=ArxivUnavailableError("timeout after bounded retries"))

    with pytest.raises(ArxivUnavailableError):
        IngestArxiv(arxiv=arxiv, repository=repository, clock=lambda: now).execute(topic_config)

    assert repository.run is not None
    assert repository.run.status is RunStatus.FAILED
    assert repository.run.error_code == "ARXIV_UNAVAILABLE"


def test_default_logical_date_uses_kuala_lumpur_calendar(topic_config: TopicConfig) -> None:
    # 16:30 UTC is already the next calendar day in Kuala Lumpur (UTC+08:00).
    now = datetime(2026, 1, 10, 16, 30, tzinfo=UTC)
    repository = FakeRepository()
    run = IngestArxiv(arxiv=FakeArxiv(), repository=repository, clock=lambda: now).execute(
        topic_config
    )
    assert run.logical_date == date(2026, 1, 11)


def test_saturated_arxiv_window_fails_without_advancing_cursor(
    topic_config: TopicConfig,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    repository = FakeRepository()
    arxiv = FakeArxiv(error=ArxivResultLimitError("timestamp tie exceeded result cap"))
    with pytest.raises(ArxivResultLimitError):
        IngestArxiv(arxiv=arxiv, repository=repository, clock=lambda: now).execute(topic_config)
    assert repository.cursor is None
    assert repository.run is not None
    assert repository.run.status is RunStatus.FAILED
    assert repository.run.error_code == "ARXIV_RESULT_LIMIT"


def test_persistence_integrity_failure_marks_child_failed_and_reuses_it_on_resume(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior_watermark = datetime(2026, 1, 8, 5, tzinfo=UTC)
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    repository = FakeRepository()
    repository.cursor = IngestionCursor(
        topic_id=topic_config.id,
        watermark=prior_watermark,
        schema_version=1,
        created_at=prior_watermark,
        updated_at=prior_watermark,
    )
    original_persist = repository.persist_arxiv_batch_and_complete
    persist_calls = 0

    def fail_once(
        *,
        topic: TopicConfig,
        run_id: UUID,
        records: tuple[ArxivPaperRecord, ...],
        watermark: datetime,
        advance_shared_cursor: bool,
        persisted_at: datetime,
        completed_at: datetime,
    ) -> DailyRun:
        nonlocal persist_calls
        persist_calls += 1
        if persist_calls == 1:
            raise RepositoryIntegrityError("PostgreSQL rejected arXiv batch persistence")
        return original_persist(
            topic=topic,
            run_id=run_id,
            records=records,
            watermark=watermark,
            advance_shared_cursor=advance_shared_cursor,
            persisted_at=persisted_at,
            completed_at=completed_at,
        )

    monkeypatch.setattr(repository, "persist_arxiv_batch_and_complete", fail_once)
    use_case = IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=repository,
        clock=lambda: now,
    )

    with pytest.raises(
        RepositoryIntegrityError,
        match="^PostgreSQL rejected arXiv batch persistence$",
    ):
        use_case.execute(
            topic_config,
            logical_date=now.date(),
            pipeline_execution_mode=PipelineExecutionMode.NORMAL,
            pipeline_selection_limit=1,
            pipeline_execution_id=PIPELINE_EXECUTION_ID,
        )

    assert repository.run is not None
    failed_run_id = repository.run.id
    assert repository.run.status is RunStatus.FAILED
    assert repository.run.error_code == "PERSISTENCE_INTEGRITY_FAILED"
    assert repository.run.error_detail == "PostgreSQL rejected arXiv batch persistence"
    assert repository.cursor is not None
    assert repository.cursor.watermark == prior_watermark
    assert repository.items == ()

    resumed = use_case.execute(
        topic_config,
        logical_date=now.date(),
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=1,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
        resume_existing=True,
    )
    assert resumed.id == failed_run_id
    assert resumed.status is RunStatus.COMPLETE
    assert resumed.normalized_count == 1
    assert persist_calls == 2


def test_running_pipeline_ingestion_resumes_same_run_and_window(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    original_start = datetime(2026, 1, 10, 5, tzinfo=UTC)
    resumed_at = original_start + timedelta(days=2)
    cursor_from = original_start - timedelta(days=7, hours=48)
    cursor_to = original_start
    repository = FakeRepository()
    original = repository.start_ingestion_run(
        topic_id=topic_config.id,
        logical_date=original_start.date(),
        started_at=original_start,
        cursor_from=cursor_from,
        cursor_to=cursor_to,
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
    )
    arxiv = FakeArxiv((arxiv_record_v1,))

    resumed = IngestArxiv(
        arxiv=arxiv,
        repository=repository,
        clock=lambda: resumed_at,
    ).execute(
        topic_config,
        logical_date=original.logical_date,
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
        resume_existing=True,
    )

    assert resumed.id == original.id
    assert resumed.status is RunStatus.COMPLETE
    assert arxiv.calls[0][1:] == (cursor_from, cursor_to, topic_config.max_results)
    assert repository.cursor is not None
    assert repository.cursor.watermark == cursor_to


def test_failed_pipeline_ingestion_replans_current_window_and_selection_limit(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    original_start = datetime(2026, 1, 10, 5, tzinfo=UTC)
    resumed_at = original_start + timedelta(days=2)
    repository = FakeRepository()
    original = repository.start_ingestion_run(
        topic_id=topic_config.id,
        logical_date=original_start.date(),
        started_at=original_start,
        cursor_from=original_start - timedelta(days=7, hours=48),
        cursor_to=original_start,
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
    )
    repository.fail_ingestion_run(
        original.id,
        completed_at=original_start + timedelta(minutes=1),
        error_code="ARXIV_UNAVAILABLE",
        error_detail="bounded retries were exhausted",
    )
    current_watermark = resumed_at - timedelta(hours=12)
    repository.cursor = IngestionCursor(
        topic_id=topic_config.id,
        watermark=current_watermark,
        schema_version=1,
        created_at=resumed_at,
        updated_at=resumed_at,
    )
    arxiv = FakeArxiv((arxiv_record_v1,))

    resumed = IngestArxiv(
        arxiv=arxiv,
        repository=repository,
        clock=lambda: resumed_at,
    ).execute(
        topic_config,
        logical_date=original.logical_date,
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=3,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
        resume_existing=True,
    )

    expected_from = current_watermark - timedelta(hours=topic_config.overlap_hours)
    assert resumed.id == original.id
    assert resumed.status is RunStatus.COMPLETE
    assert resumed.pipeline_selection_limit == 3
    assert arxiv.calls[0][1:] == (expected_from, resumed_at, topic_config.max_results)
    assert repository.cursor is not None
    assert repository.cursor.watermark == resumed_at


def test_pipeline_ingestion_reuses_matching_complete_run_without_external_call(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    repository = FakeRepository()
    original = IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=repository,
        clock=lambda: now,
    ).execute(
        topic_config,
        logical_date=now.date(),
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
    )
    retry_arxiv = FakeArxiv((arxiv_record_v1,))

    reused = IngestArxiv(
        arxiv=retry_arxiv,
        repository=repository,
        clock=lambda: now + timedelta(hours=1),
    ).execute(
        topic_config,
        logical_date=now.date(),
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
        resume_existing=True,
    )

    assert reused == original
    assert retry_arxiv.calls == []


def test_pipeline_ingestion_rejects_resume_mode_mismatch(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    repository = FakeRepository()
    IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=repository,
        clock=lambda: now,
    ).execute(
        topic_config,
        logical_date=now.date(),
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
    )
    retry_arxiv = FakeArxiv((arxiv_record_v1,))

    with pytest.raises(IngestionResumeError, match="provenance"):
        IngestArxiv(
            arxiv=retry_arxiv,
            repository=repository,
            clock=lambda: now + timedelta(hours=1),
        ).execute(
            topic_config,
            logical_date=now.date(),
            pipeline_execution_mode=PipelineExecutionMode.SMOKE,
            pipeline_selection_limit=2,
            pipeline_execution_id=PIPELINE_EXECUTION_ID,
            resume_existing=True,
        )

    assert retry_arxiv.calls == []
