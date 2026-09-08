from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from tests.fakes import FakeArxiv

from paper_harness.adapters.postgres import PostgresRepository
from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.domain.models import RunStatus, TopicConfig
from paper_harness.ports.arxiv import (
    ArxivDiscoveryIncompleteError,
    ArxivIdentifierPage,
    ArxivPaperRecord,
)
from paper_harness.ports.repository import RepositoryIntegrityError

pytestmark = pytest.mark.integration
NOW = datetime(2026, 1, 10, 12, tzinfo=UTC)


class TwoPageArxiv(FakeArxiv):
    def list_updated_identifiers(
        self,
        *,
        day: date,
        category: str,
        resumption_token: str | None = None,
        timeout_seconds: float | None = None,
    ) -> ArxivIdentifierPage:
        if day != NOW.date():
            return ArxivIdentifierPage((), None)
        identifiers = tuple(record.canonical_arxiv_id for record in self.records)
        return (
            ArxivIdentifierPage(identifiers[:2], "second-page")
            if resumption_token is None
            else ArxivIdentifierPage(identifiers[1:], None)
        )


def test_checkpointed_metadata_survives_budget_failure_and_resume(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    topic = replace(topic_config, categories=("cs.AI",), overlap_hours=1, max_results=1)
    IngestArxiv(
        arxiv=FakeArxiv(), repository=postgres_repository, clock=lambda: NOW - timedelta(hours=2)
    ).execute(topic, logical_date=NOW.date() - timedelta(days=1))
    prior_cursor = postgres_repository.get_ingestion_cursor(topic.id)
    records = tuple(
        replace(
            arxiv_record_v1,
            canonical_arxiv_id=f"2601.{index:05}",
            pdf_url=f"https://arxiv.org/pdf/2601.{index:05}v1",
            source_url=f"https://arxiv.org/abs/2601.{index:05}v1",
        )
        for index in range(1, 5)
    )
    arxiv = TwoPageArxiv(records)
    with pytest.raises(ArxivDiscoveryIncompleteError):
        IngestArxiv(
            arxiv=arxiv, repository=postgres_repository, clock=lambda: NOW, max_pages=1
        ).execute(topic)
    failed = postgres_repository.get_run_for_date(topic.id, NOW.date())
    assert failed is not None and failed.status is RunStatus.FAILED
    assert failed.normalized_count == 2
    assert postgres_repository.get_ingestion_cursor(topic.id) == prior_cursor
    progress = postgres_repository.get_ingestion_progress(failed.id)
    assert progress is not None and progress.resumption_token == "second-page"
    assert not progress.pending_ids and not progress.complete

    postgres_repository.restart_ingestion_run(
        failed.id,
        started_at=NOW,
        cursor_from=failed.cursor_from or NOW,
        cursor_to=failed.cursor_to or NOW,
        pipeline_selection_limit=None,
    )
    with pytest.raises(RepositoryIntegrityError, match="incomplete arXiv discovery"):
        postgres_repository.persist_arxiv_batch_and_complete(
            topic=topic,
            run_id=failed.id,
            records=(),
            watermark=NOW,
            advance_shared_cursor=True,
            persisted_at=NOW,
            completed_at=NOW,
        )
    resumed = IngestArxiv(
        arxiv=arxiv, repository=postgres_repository, clock=lambda: NOW + timedelta(days=1)
    ).execute(topic, logical_date=NOW.date(), resume_existing=True)
    assert resumed.id == failed.id
    assert resumed.status is RunStatus.COMPLETE and resumed.normalized_count == 4
    assert resumed.cursor_to == NOW
    cursor = postgres_repository.get_ingestion_cursor(topic.id)
    assert cursor is not None and cursor.watermark == NOW
    _, total = postgres_repository.list_papers(topic_slug=topic.slug, limit=10, offset=0)
    assert total == 4
