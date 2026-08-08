"""Explicit opt-in real arXiv-to-PostgreSQL M1 smoke test."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from paper_harness.adapters.arxiv import ArxivClient
from paper_harness.adapters.postgres import PostgresRepository
from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.domain.models import RunStatus, TopicConfig
from paper_harness.ports.arxiv import ArxivPaperRecord

pytestmark = [pytest.mark.integration, pytest.mark.live]


class KnownArxivPaper:
    """Narrow a live probe to one stable arXiv identity while using the real adapter."""

    def __init__(self) -> None:
        self._client = ArxivClient(page_size=5, max_retries=1)

    def search(
        self,
        *,
        query: str,
        updated_from: datetime,
        updated_until: datetime,
        max_results: int,
    ) -> tuple[ArxivPaperRecord, ...]:
        del query
        return self._client.search(
            query="id:1706.03762",
            updated_from=updated_from,
            updated_until=updated_until,
            max_results=min(max_results, 2),
        )


def test_real_arxiv_record_reaches_postgresql(
    postgres_repository: PostgresRepository,
) -> None:
    if os.environ.get("RUN_LIVE_ARXIV_TEST") != "1":
        pytest.skip("set RUN_LIVE_ARXIV_TEST=1 for the explicit live arXiv smoke test")
    now = datetime.now(UTC)
    topic = TopicConfig(
        id=UUID("1d8bc968-f3f2-5e92-899a-17e70db26900"),
        slug="live-arxiv-probe",
        name="Live arXiv Probe",
        description="Explicit opt-in ingestion of one stable arXiv paper.",
        categories=("cs.CL",),
        include_terms=("Attention Is All You Need",),
        exclude_terms=(),
        overlap_hours=1,
        initial_lookback_days=5000,
        max_results=2,
        representative_full_text_count=1,
    )
    run = IngestArxiv(
        arxiv=KnownArxivPaper(), repository=postgres_repository, clock=lambda: now
    ).execute(topic, logical_date=date(2099, 1, 1))
    assert run.status is RunStatus.COMPLETE
    assert run.normalized_count == 1
    papers, total = postgres_repository.list_papers(topic_slug=topic.slug, limit=10, offset=0)
    assert total == 1
    assert papers[0].canonical_arxiv_id == "1706.03762"
