# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from tests.fakes import FakeArxiv, FakeRepository

from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.domain.identity import stable_paper_id
from paper_harness.domain.models import Paper, TopicConfig
from paper_harness.entrypoints.api import create_app
from paper_harness.ports.arxiv import ArxivPaperRecord
from paper_harness.ports.repository import MigrationIncompatibleError


def _paper(record: ArxivPaperRecord) -> Paper:
    return Paper(
        id=stable_paper_id(record.canonical_arxiv_id),
        canonical_arxiv_id=record.canonical_arxiv_id,
        title=record.title,
        abstract=record.abstract,
        current_version=record.version,
        first_submitted_at=record.submitted_at,
        latest_updated_at=record.updated_at,
        primary_category=record.primary_category,
        categories=record.categories,
        authors=record.authors,
        pdf_url=record.pdf_url,
        schema_version=1,
        created_at=datetime(2026, 1, 10, 5, tzinfo=UTC),
    )


def test_m1_read_api_exposes_persisted_topics_papers_and_latest_run(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    repository = FakeRepository()
    repository.papers = (_paper(arxiv_record_v1),)
    IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)), repository=repository, clock=lambda: now
    ).execute(topic_config, logical_date=date(2026, 1, 10))
    client = TestClient(create_app(repository))

    assert client.get("/health/live").json() == {"status": "alive"}
    assert client.get("/health/ready").json() == {
        "status": "ready",
        "database": "ready",
        "migrations": "current",
    }
    topics = client.get("/api/v1/topics").json()
    assert topics["total"] == 1
    assert topics["items"][0]["slug"] == "broad-llm-agents"
    papers = client.get("/api/v1/papers?limit=20&offset=0").json()
    assert papers["total"] == 1
    assert papers["items"][0]["canonical_arxiv_id"] == "2601.01234"
    run = client.get("/api/v1/runs/latest").json()
    assert run["status"] == "COMPLETE"
    assert run["items"][0]["stage"] == "NORMALIZED"


def test_readiness_reports_incompatible_migration() -> None:
    repository = FakeRepository()
    repository.ready_error = MigrationIncompatibleError("database revision is behind")
    response = TestClient(create_app(repository)).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MIGRATION_INCOMPATIBLE"


def test_checked_in_openapi_is_generated_from_fastapi() -> None:
    expected = json.loads(Path("apps/api/openapi.json").read_text(encoding="utf-8"))
    assert create_app(FakeRepository()).openapi() == expected
