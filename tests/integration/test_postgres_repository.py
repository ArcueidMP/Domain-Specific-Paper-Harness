# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeArxiv

from paper_harness.adapters.postgres import PostgresRepository
from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.domain.errors import DuplicateDailyRunError
from paper_harness.domain.models import RunStatus, TopicConfig
from paper_harness.entrypoints.api import create_app
from paper_harness.ports.arxiv import ArxivPaperRecord
from paper_harness.ports.repository import RepositoryError

pytestmark = pytest.mark.integration


def test_migration_readiness_and_versioned_idempotent_ingestion(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    postgres_repository.check_ready()
    first_time = datetime(2026, 1, 10, 5, tzinfo=UTC)
    first_arxiv = FakeArxiv((arxiv_record_v1, arxiv_record_v1))
    first_run = IngestArxiv(
        arxiv=first_arxiv, repository=postgres_repository, clock=lambda: first_time
    ).execute(topic_config, logical_date=date(2026, 1, 10))
    assert first_run.status is RunStatus.COMPLETE
    assert first_run.discovered_count == 1

    v2 = replace(
        arxiv_record_v1,
        version=2,
        title="A Reliable LLM Agent, Revised",
        updated_at=arxiv_record_v1.updated_at + timedelta(days=1),
        pdf_url="https://arxiv.org/pdf/2601.01234v2",
        source_url="https://arxiv.org/abs/2601.01234v2",
    )
    second_time = first_time + timedelta(days=1)
    second_arxiv = FakeArxiv((arxiv_record_v1, v2))
    IngestArxiv(
        arxiv=second_arxiv, repository=postgres_repository, clock=lambda: second_time
    ).execute(topic_config, logical_date=date(2026, 1, 11))

    papers, total = postgres_repository.list_papers(
        topic_slug=topic_config.slug, limit=10, offset=0
    )
    assert total == 1
    assert papers[0].current_version == 2
    detail = postgres_repository.get_paper(papers[0].id)
    assert detail is not None
    assert [version.version for version in detail.versions] == [2, 1]
    assert second_arxiv.calls[0][1] == first_time - timedelta(hours=topic_config.overlap_hours)
    client = TestClient(create_app(postgres_repository))
    assert client.get("/health/ready").status_code == 200
    api_papers = client.get(f"/api/v1/papers?topic={topic_config.slug}").json()
    assert api_papers["total"] == 1
    assert api_papers["items"][0]["current_version"] == 2


def test_duplicate_logical_run_is_rejected_before_external_call(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=postgres_repository,
        clock=lambda: now,
    ).execute(topic_config, logical_date=now.date())
    second_arxiv = FakeArxiv((arxiv_record_v1,))
    with pytest.raises(DuplicateDailyRunError):
        IngestArxiv(
            arxiv=second_arxiv,
            repository=postgres_repository,
            clock=lambda: now,
        ).execute(topic_config, logical_date=now.date())
    assert second_arxiv.calls == []


def test_advisory_lock_prevents_concurrent_logical_run(
    postgres_repository: PostgresRepository, topic_config: TopicConfig
) -> None:
    logical_date = date(2026, 1, 10)
    with (
        postgres_repository.daily_run_lock(topic_config.id, logical_date),
        pytest.raises(DuplicateDailyRunError),
        postgres_repository.daily_run_lock(topic_config.id, logical_date),
    ):
        raise AssertionError("second lock must not be acquired")


def test_batch_cursor_items_counts_and_completion_roll_back_together(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    started_at = datetime(2026, 1, 10, 5, tzinfo=UTC)
    postgres_repository.upsert_topic(topic_config)
    run = postgres_repository.start_ingestion_run(
        topic_id=topic_config.id,
        logical_date=started_at.date(),
        started_at=started_at,
        cursor_from=started_at - timedelta(days=1),
        cursor_to=started_at,
    )
    postgres_repository.fail_ingestion_run(
        run.id,
        completed_at=started_at + timedelta(seconds=1),
        error_code="TEST_PRECONDITION",
        error_detail="Force the atomic completion update to reject this run.",
    )

    with pytest.raises(RepositoryError, match="no longer running"):
        postgres_repository.persist_arxiv_batch_and_complete(
            topic=topic_config,
            run_id=run.id,
            records=(arxiv_record_v1,),
            watermark=started_at,
            persisted_at=started_at + timedelta(seconds=2),
            completed_at=started_at + timedelta(seconds=3),
        )

    papers, total = postgres_repository.list_papers(topic_slug=None, limit=10, offset=0)
    assert papers == ()
    assert total == 0
    assert postgres_repository.get_ingestion_cursor(topic_config.id) is None
