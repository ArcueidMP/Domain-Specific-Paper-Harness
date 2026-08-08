from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from tests.fakes import FakeArxiv, FakeRepository

from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.domain.errors import DuplicateDailyRunError
from paper_harness.domain.models import RunStatus, TopicConfig
from paper_harness.ports.arxiv import (
    ArxivPaperRecord,
    ArxivResultLimitError,
    ArxivUnavailableError,
)


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
