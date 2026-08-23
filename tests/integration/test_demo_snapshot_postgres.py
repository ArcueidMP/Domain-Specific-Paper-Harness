"""PostgreSQL acceptance coverage for the isolated public-demo snapshot."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from paper_harness.adapters.postgres import PostgresRepository, create_postgres_engine
from paper_harness.adapters.postgres.database import normalize_database_url
from paper_harness.adapters.postgres.demo_schema import (
    DEMO_READ_ROLE,
    DEMO_SCHEMA,
    DEMO_SYNC_ROLE,
)
from paper_harness.adapters.postgres.demo_snapshot import (
    DEMO_REDACTED_DIAGNOSTIC,
    DemoSnapshotError,
    DemoSnapshotSynchronizer,
)
from paper_harness.adapters.postgres.models import (
    DailyRunRow,
    IngestionCursorRow,
    PaperRow,
    PaperVersionRow,
    PipelineExecutionRow,
    ReportFailureRow,
    ReportRow,
    TopicPaperRow,
    TopicRow,
)
from paper_harness.entrypoints.api import create_app
from paper_harness.entrypoints.demo import (
    execute_demo_schema_bootstrap,
    execute_demo_snapshot_sync,
)


@dataclass(frozen=True, slots=True)
class _RevisionFixture:
    topic_id: UUID
    execution_ids: tuple[UUID, ...]
    run_ids: tuple[UUID, ...]
    report_ids: tuple[UUID, ...]


def test_demo_roles_snapshot_and_read_api_are_isolated(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    sync_password = "demo-sync-password"
    read_password = "demo-read-password"
    _remove_demo_boundary(postgres_engine)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATABASE_SCHEMA", "public")
    monkeypatch.setenv("DEMO_SYNC_DB_PASSWORD", sync_password)
    monkeypatch.setenv("DEMO_READ_DB_PASSWORD", read_password)

    sync_engine: Engine | None = None
    read_engine: Engine | None = None
    topic_id = uuid4()
    paper_id = uuid4()
    paper_version_id = uuid4()
    report_id = uuid4()
    revision_ids: _RevisionFixture | None = None
    now = datetime(2026, 8, 23, 5, tzinfo=UTC)
    try:
        bootstrap = execute_demo_schema_bootstrap()
        assert bootstrap.schema == DEMO_SCHEMA
        assert bootstrap.demo_table_count > 1
        assert execute_demo_schema_bootstrap() == bootstrap

        sync_url = _role_url(database_url, DEMO_SYNC_ROLE, sync_password)
        read_url = _role_url(database_url, DEMO_READ_ROLE, read_password)
        sync_engine = create_postgres_engine(
            sync_url, production=False, database_schema=DEMO_SCHEMA
        )
        read_engine = create_postgres_engine(
            read_url, production=False, database_schema=DEMO_SCHEMA
        )

        _seed_public_snapshot(
            postgres_engine,
            topic_id=topic_id,
            paper_id=paper_id,
            paper_version_id=paper_version_id,
            report_id=report_id,
            now=now,
        )
        revision_ids = _seed_public_canonical_revisions(postgres_engine, now=now)

        monkeypatch.setenv("DATABASE_URL", sync_url)
        monkeypatch.setenv("DATABASE_SCHEMA", DEMO_SCHEMA)
        synchronizer = DemoSnapshotSynchronizer(sync_engine)
        first = execute_demo_snapshot_sync()
        second = synchronizer.synchronize()

        assert first == second
        assert dict(first.table_counts)["topics"] >= 2
        assert dict(first.table_counts)["reports"] >= 2
        assert dict(first.table_counts)["report_failures"] >= 1
        assert dict(first.table_counts)["pipeline_executions"] >= 1
        assert dict(first.table_counts)["daily_runs"] >= 2
        with read_engine.connect() as connection:
            failure = connection.execute(
                text(
                    "SELECT error_code, error_detail FROM report_failures "
                    "WHERE report_id = :report_id"
                ),
                {"report_id": report_id},
            ).one()
            report_counts = connection.execute(
                text(
                    "SELECT selected_count, completed_count, failed_count, "
                    "graph_entity_count FROM reports WHERE id = :report_id"
                ),
                {"report_id": report_id},
            ).one()
            assert failure == ("ANALYSIS_UNAVAILABLE", DEMO_REDACTED_DIAGNOSTIC)
            assert report_counts == (2, 1, 1, 2)
            report_titles = tuple(
                connection.scalars(text("SELECT title FROM reports ORDER BY title"))
            )
            assert "Demo monthly report" in report_titles
            assert "new publication" in report_titles
            assert "old publication" not in report_titles
            assert "smoke publication" not in report_titles
            assert connection.scalar(text("SELECT count(*) FROM ingestion_cursors")) == 0
            with pytest.raises(DBAPIError):
                connection.execute(text("SELECT id FROM public.topics"))
        with read_engine.connect() as connection, pytest.raises(DBAPIError):
            connection.execute(text("DELETE FROM demo.reports WHERE false"))

        with sync_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(id) FROM public.topics WHERE id IN (:first, :second)"),
                    {"first": topic_id, "second": revision_ids.topic_id},
                )
                == 2
            )
            with pytest.raises(DBAPIError):
                connection.execute(text("SELECT error_detail FROM public.report_failures"))
        with sync_engine.connect() as connection, pytest.raises(DBAPIError):
            connection.execute(text("SELECT * FROM public.ingestion_cursors"))

        repository = PostgresRepository(read_engine)
        repository.check_ready()
        client = TestClient(create_app(repository))
        response = client.get("/api/v1/reports/monthly/2026-08?topic=demo-topic")
        assert response.status_code == 200
        assert response.json()["title"] == "Demo monthly report"
        papers = client.get("/api/v1/papers?topic=demo-topic")
        assert papers.status_code == 200
        assert papers.json()["total"] == 0

        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE demo.alembic_version SET version_num = '0005_m5_pipeline_provenance'")
            )
        with pytest.raises(DemoSnapshotError, match="application revision"):
            synchronizer.synchronize()
        with read_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT count(*) FROM reports"))
                == dict(first.table_counts)["reports"]
            )
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE demo.alembic_version SET version_num = '0006_topic_reprocessing'")
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM public.report_failures "
                        "WHERE error_detail = 'private diagnostic canary'"
                    )
                )
                == 1
            )
    finally:
        if read_engine is not None:
            read_engine.dispose()
        if sync_engine is not None:
            sync_engine.dispose()
        with postgres_engine.begin() as connection:
            if revision_ids is not None:
                connection.execute(
                    text("DELETE FROM reports WHERE id = ANY(:ids)"),
                    {"ids": list(revision_ids.report_ids)},
                )
                connection.execute(
                    text("DELETE FROM daily_runs WHERE id = ANY(:ids)"),
                    {"ids": list(revision_ids.run_ids)},
                )
                connection.execute(
                    text("DELETE FROM pipeline_executions WHERE id = ANY(:ids)"),
                    {"ids": list(revision_ids.execution_ids)},
                )
                connection.execute(
                    text("DELETE FROM topics WHERE id = :id"),
                    {"id": revision_ids.topic_id},
                )
            connection.execute(text("DELETE FROM reports WHERE id = :id"), {"id": report_id})
            connection.execute(text("DELETE FROM papers WHERE id = :id"), {"id": paper_id})
            connection.execute(text("DELETE FROM topics WHERE id = :id"), {"id": topic_id})
        _remove_demo_boundary(postgres_engine)


def _seed_public_snapshot(
    engine: Engine,
    *,
    topic_id: UUID,
    paper_id: UUID,
    paper_version_id: UUID,
    report_id: UUID,
    now: datetime,
) -> None:
    month_start = date(2026, 8, 1)
    month_end = date(2026, 8, 31)
    with engine.begin() as connection:
        connection.execute(
            insert(TopicRow),
            {
                "id": topic_id,
                "slug": "demo-topic",
                "name": "Demo Topic",
                "description": "A public snapshot fixture.",
                "categories": ["cs.AI"],
                "include_terms": ["demo"],
                "exclude_terms": [],
                "overlap_hours": 48,
                "initial_lookback_days": 7,
                "max_results": 100,
                "representative_full_text_count": 10,
                "schema_version": 1,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            insert(PaperRow),
            {
                "id": paper_id,
                "canonical_arxiv_id": "2608.99999",
                "title": "A Demo Paper",
                "abstract": "A public abstract.",
                "current_version": 1,
                "first_submitted_at": now,
                "latest_updated_at": now,
                "primary_category": "cs.AI",
                "categories": ["cs.AI"],
                "authors": ["Demo Author"],
                "pdf_url": "https://arxiv.org/pdf/2608.99999v1",
                "schema_version": 1,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            insert(PaperVersionRow),
            {
                "id": paper_version_id,
                "paper_id": paper_id,
                "version": 1,
                "title": "A Demo Paper",
                "abstract": "A public abstract.",
                "submitted_at": now,
                "updated_at": now,
                "primary_category": "cs.AI",
                "categories": ["cs.AI"],
                "authors": ["Demo Author"],
                "pdf_url": "https://arxiv.org/pdf/2608.99999v1",
                "source_url": "https://arxiv.org/abs/2608.99999v1",
                "schema_version": 1,
                "created_at": now,
            },
        )
        connection.execute(
            insert(TopicPaperRow),
            {
                "topic_id": topic_id,
                "paper_id": paper_id,
                "first_discovered_at": now,
                "last_discovered_at": now,
            },
        )
        connection.execute(
            insert(ReportRow),
            {
                "id": report_id,
                "run_id": None,
                "topic_id": topic_id,
                "logical_date": month_end,
                "report_type": "MONTHLY",
                "period_start": month_start,
                "period_end": month_end,
                "status": "PARTIAL",
                "title": "Demo monthly report",
                "summary": "A public report summary.",
                "source": "test",
                "generated_at": now,
                "retrieved_count": 2,
                "selected_count": 2,
                "processed_count": 2,
                "completed_count": 1,
                "failed_count": 1,
                "graph_entity_count": 2,
                "graph_edge_count": 1,
                "new_graph_entity_count": 1,
                "inferred_graph_edge_count": 1,
                "limitations": [],
                "missing_sections": [],
                "narrative_mode": "STRUCTURED_ONLY",
                "provider": None,
                "configured_model": None,
                "model_version": None,
                "prompt_version": None,
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "call_count": None,
                "duration_ms": None,
                "estimated_cost_usd": None,
                "verification_status": "UNVERIFIED",
                "schema_version": 1,
                "created_at": now,
            },
        )
        connection.execute(
            insert(ReportFailureRow),
            {
                "id": uuid4(),
                "report_id": report_id,
                "paper_id": paper_id,
                "paper_version_id": paper_version_id,
                "failed_stage": "ANALYZED",
                "error_code": "ANALYSIS_UNAVAILABLE",
                "retryable": False,
                "error_detail": "private diagnostic canary",
                "schema_version": 1,
                "created_at": now,
            },
        )
        connection.execute(
            insert(IngestionCursorRow),
            {
                "topic_id": topic_id,
                "watermark": now,
                "schema_version": 1,
                "created_at": now,
                "updated_at": now,
            },
        )


def _seed_public_canonical_revisions(
    engine: Engine,
    *,
    now: datetime,
) -> _RevisionFixture:
    topic_id = uuid4()
    logical_date = date(2026, 8, 23)
    execution_ids: list[UUID] = []
    run_ids: list[UUID] = []
    report_ids: list[UUID] = []
    revisions = (
        ("old", "REPROCESS", now, "COMPLETE"),
        ("new", "REPROCESS", now.replace(hour=6), "COMPLETE"),
        ("smoke", "SMOKE", now.replace(hour=7), "COMPLETE"),
        ("failed", "REPROCESS", now.replace(hour=8), "FAILED"),
    )
    with engine.begin() as connection:
        connection.execute(
            insert(TopicRow),
            {
                "id": topic_id,
                "slug": "canonical-topic",
                "name": "Canonical Topic",
                "description": "Canonical revision selection fixture.",
                "categories": ["cs.AI"],
                "include_terms": ["canonical"],
                "exclude_terms": [],
                "overlap_hours": 48,
                "initial_lookback_days": 7,
                "max_results": 100,
                "representative_full_text_count": 10,
                "schema_version": 1,
                "created_at": now,
                "updated_at": now,
            },
        )
        for label, mode, started_at, status in revisions:
            execution_id = uuid4()
            source_run_id = uuid4()
            product_run_id = uuid4()
            execution_ids.append(execution_id)
            run_ids.extend((source_run_id, product_run_id))
            completed_at = started_at.replace(minute=30)
            connection.execute(
                insert(PipelineExecutionRow),
                {
                    "id": execution_id,
                    "topic_id": topic_id,
                    "logical_date": logical_date,
                    "execution_mode": mode,
                    "execution_key": str(execution_id),
                    "analysis_scope": "FULL_TEXT",
                    "selection_limit": 1,
                    "execution_contract": {},
                    "status": status,
                    "deadline_at": started_at.replace(hour=started_at.hour + 1),
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "error_code": "PUBLICATION_FAILED" if status == "FAILED" else None,
                    "error_detail": "private pipeline diagnostic" if status == "FAILED" else None,
                    "schema_version": 1,
                    "created_at": started_at,
                    "updated_at": completed_at,
                },
            )
            connection.execute(
                insert(DailyRunRow),
                [
                    {
                        "id": source_run_id,
                        "topic_id": topic_id,
                        "logical_date": logical_date,
                        "operation": "STRUCTURED_ANALYSIS",
                        "source_run_id": None,
                        "pipeline_execution_id": execution_id,
                        "pipeline_execution_mode": mode,
                        "pipeline_selection_limit": 1,
                        "analysis_scope": "FULL_TEXT",
                        "status": "COMPLETE",
                        "started_at": started_at,
                        "completed_at": completed_at,
                        "cursor_from": None,
                        "cursor_to": None,
                        "discovered_count": 0,
                        "normalized_count": 0,
                        "selected_count": 0,
                        "completed_count": 0,
                        "failed_count": 0,
                        "error_code": None,
                        "error_detail": None,
                        "schema_version": 1,
                        "created_at": started_at,
                    },
                    {
                        "id": product_run_id,
                        "topic_id": topic_id,
                        "logical_date": logical_date,
                        "operation": "PRODUCT_PUBLICATION",
                        "source_run_id": source_run_id,
                        "pipeline_execution_id": execution_id,
                        "pipeline_execution_mode": mode,
                        "pipeline_selection_limit": 1,
                        "analysis_scope": None,
                        "status": status,
                        "started_at": started_at,
                        "completed_at": completed_at,
                        "cursor_from": None,
                        "cursor_to": None,
                        "discovered_count": 0,
                        "normalized_count": 0,
                        "selected_count": 0,
                        "completed_count": 0,
                        "failed_count": 0,
                        "error_code": "PUBLICATION_FAILED" if status == "FAILED" else None,
                        "error_detail": (
                            "private publication diagnostic" if status == "FAILED" else None
                        ),
                        "schema_version": 1,
                        "created_at": started_at,
                    },
                ],
            )
            if status == "COMPLETE":
                report_id = uuid4()
                report_ids.append(report_id)
                connection.execute(
                    insert(ReportRow),
                    {
                        "id": report_id,
                        "run_id": product_run_id,
                        "topic_id": topic_id,
                        "logical_date": logical_date,
                        "report_type": "DAILY",
                        "period_start": logical_date,
                        "period_end": logical_date,
                        "status": "COMPLETE",
                        "title": f"{label} publication",
                        "summary": "No relevant papers were found today.",
                        "source": "test",
                        "generated_at": completed_at,
                        "retrieved_count": 0,
                        "selected_count": 0,
                        "processed_count": 0,
                        "completed_count": 0,
                        "failed_count": 0,
                        "graph_entity_count": 0,
                        "graph_edge_count": 0,
                        "new_graph_entity_count": 0,
                        "inferred_graph_edge_count": 0,
                        "limitations": [],
                        "missing_sections": [],
                        "narrative_mode": "STRUCTURED_ONLY",
                        "provider": None,
                        "configured_model": None,
                        "model_version": None,
                        "prompt_version": None,
                        "prompt_tokens": None,
                        "completion_tokens": None,
                        "total_tokens": None,
                        "call_count": None,
                        "duration_ms": None,
                        "estimated_cost_usd": None,
                        "verification_status": "UNVERIFIED",
                        "schema_version": 1,
                        "created_at": completed_at,
                    },
                )
    return _RevisionFixture(
        topic_id=topic_id,
        execution_ids=tuple(execution_ids),
        run_ids=tuple(run_ids),
        report_ids=tuple(report_ids),
    )


def _role_url(database_url: str, role_name: str, password: str) -> str:
    return (
        make_url(normalize_database_url(database_url, production=False))
        .set(username=role_name, password=password)
        .render_as_string(hide_password=False)
    )


def _remove_demo_boundary(engine: Engine) -> None:
    engine.dispose()
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS demo CASCADE"))
        for role_name in (DEMO_READ_ROLE, DEMO_SYNC_ROLE):
            exists = bool(
                connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"),
                    {"role": role_name},
                )
            )
            if exists:
                connection.execute(text(f"DROP OWNED BY {role_name}"))
                connection.execute(text(f"DROP ROLE {role_name}"))
