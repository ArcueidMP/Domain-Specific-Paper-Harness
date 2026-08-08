"""Idempotent, version-aware arXiv ingestion use case."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from paper_harness.application.arxiv_query import build_arxiv_query
from paper_harness.domain.errors import DuplicateDailyRunError
from paper_harness.domain.models import DailyRun, TopicConfig
from paper_harness.ports.arxiv import ArxivPaperRecord, ArxivPort, ArxivPortError
from paper_harness.ports.repository import RepositoryPort

SCHEDULE_TIME_ZONE = ZoneInfo("Asia/Kuala_Lumpur")


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

    def execute(self, topic: TopicConfig, *, logical_date: date | None = None) -> DailyRun:
        started_at = self._aware_now()
        run_date = logical_date or started_at.astimezone(SCHEDULE_TIME_ZONE).date()

        with self._repository.daily_run_lock(topic.id, run_date):
            self._repository.upsert_topic(topic)
            if self._repository.get_run_for_date(topic.id, run_date) is not None:
                raise DuplicateDailyRunError(
                    f"arXiv ingestion already exists for topic {topic.slug!r} on {run_date}"
                )

            cursor = self._repository.get_ingestion_cursor(topic.id)
            base_watermark = (
                cursor.watermark
                if cursor is not None
                else started_at - timedelta(days=topic.initial_lookback_days)
            )
            cursor_from = base_watermark - timedelta(hours=topic.overlap_hours)
            cursor_to = started_at
            run = self._repository.start_ingestion_run(
                topic_id=topic.id,
                logical_date=run_date,
                started_at=started_at,
                cursor_from=cursor_from,
                cursor_to=cursor_to,
            )

            try:
                records = self._arxiv.search(
                    query=build_arxiv_query(topic),
                    updated_from=cursor_from,
                    updated_until=cursor_to,
                    max_results=topic.max_results,
                )
            except ArxivPortError as error:
                self._repository.fail_ingestion_run(
                    run.id,
                    completed_at=self._aware_now(),
                    error_code=error.error_code,
                    error_detail=str(error)[:1000],
                )
                raise

            unique_records = _deduplicate_records(records)
            return self._repository.persist_arxiv_batch_and_complete(
                topic=topic,
                run_id=run.id,
                records=unique_records,
                watermark=cursor_to,
                persisted_at=self._aware_now(),
                completed_at=self._aware_now(),
            )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ingestion clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def _deduplicate_records(
    records: tuple[ArxivPaperRecord, ...],
) -> tuple[ArxivPaperRecord, ...]:
    by_identity: dict[tuple[str, int], ArxivPaperRecord] = {}
    for record in records:
        by_identity[(record.canonical_arxiv_id, record.version)] = record
    return tuple(by_identity[key] for key in sorted(by_identity))
