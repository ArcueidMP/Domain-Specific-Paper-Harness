"""Focused policy tests for the isolated public demo snapshot."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import cast

import pytest
from sqlalchemy import Engine

from paper_harness.adapters.postgres import demo_snapshot
from paper_harness.adapters.postgres.demo_snapshot import (
    DEMO_EXCLUDED_TABLES,
    DEMO_REDACTED_DIAGNOSTIC,
    DemoSnapshotError,
    DemoSnapshotSynchronizer,
    build_demo_insert_statement,
    build_demo_selection_statements,
    default_demo_snapshot_manifest,
)
from paper_harness.adapters.postgres.models import Base
from paper_harness.adapters.postgres.repository import EXPECTED_DATABASE_REVISION


def test_manifest_explicitly_classifies_every_persistence_table() -> None:
    manifest = default_demo_snapshot_manifest()
    included = {table.name for table in manifest.tables}

    assert included | manifest.excluded_tables == set(Base.metadata.tables)
    assert included.isdisjoint(manifest.excluded_tables)
    assert manifest.excluded_tables == DEMO_EXCLUDED_TABLES
    assert manifest.excluded_tables == {
        "citation_contexts",
        "historical_backfill_runs",
        "historical_corpus_entries",
        "ingestion_cursors",
        "parsed_passages",
        "parsed_references",
        "parsed_sections",
        "scientific_embeddings",
    }


def test_manifest_rejects_include_exclude_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        demo_snapshot,
        "DEMO_EXCLUDED_TABLES",
        DEMO_EXCLUDED_TABLES | {"reports"},
    )

    with pytest.raises(DemoSnapshotError, match="includes and excludes"):
        default_demo_snapshot_manifest()


def test_manifest_redacts_only_free_form_diagnostics_and_keeps_metrics() -> None:
    manifest = default_demo_snapshot_manifest()

    for table_name in (
        "daily_runs",
        "pipeline_executions",
        "run_items",
        "search_actions",
        "search_sessions",
    ):
        table = manifest.table(table_name)
        assert table.redactions == (("error_detail", "NULL"),)
        assert "error_detail" not in table.source_columns

    report_failures = manifest.table("report_failures")
    assert report_failures.redactions == (("error_detail", f"'{DEMO_REDACTED_DIAGNOSTIC}'"),)
    assert "error_code" in report_failures.source_columns
    assert "retryable" in report_failures.source_columns

    for table_name, metrics in {
        "daily_runs": ("selected_count", "completed_count", "failed_count"),
        "paper_analyses": ("prompt_tokens", "duration_ms", "estimated_cost_usd"),
        "reports": ("graph_entity_count", "total_tokens", "estimated_cost_usd"),
        "search_candidates": ("final_score", "rank"),
        "trend_snapshots": ("paper_growth_rate", "data_sufficiency"),
    }.items():
        source_columns = manifest.table(table_name).source_columns
        assert all(metric in source_columns for metric in metrics)


def test_selection_roots_are_latest_terminal_non_smoke_publications_and_periodic_reports() -> None:
    sql = " ".join(build_demo_selection_statements("public"))

    assert "r.operation = 'PRODUCT_PUBLICATION'" in sql
    assert "r.status IN ('COMPLETE', 'PARTIAL')" in sql
    assert "r.pipeline_execution_mode <> 'SMOKE'" in sql
    assert "newer.started_at > r.started_at" in sql
    assert "newer.id > r.id" in sql
    assert "report.report_type IN ('WEEKLY', 'MONTHLY')" in sql
    assert "report.status IN ('COMPLETE', 'PARTIAL')" in sql
    assert all(table not in sql for table in DEMO_EXCLUDED_TABLES)


def test_insert_sql_is_explicit_server_side_and_never_reads_redacted_detail() -> None:
    manifest = default_demo_snapshot_manifest()
    run_sql = build_demo_insert_statement(
        manifest.table("daily_runs"),
        source_schema="public",
        target_schema="demo",
    )
    failure_sql = build_demo_insert_statement(
        manifest.table("report_failures"),
        source_schema="public",
        target_schema="demo",
    )
    analysis_sql = build_demo_insert_statement(
        manifest.table("paper_analyses"),
        source_schema="public",
        target_schema="demo",
    )

    assert 'INSERT INTO "demo"."daily_runs"' in run_sql
    assert 'FROM "public"."daily_runs" AS src' in run_sql
    assert "SELECT *" not in run_sql
    assert 'src."error_detail"' not in run_sql
    assert DEMO_REDACTED_DIAGNOSTIC in failure_sql
    assert 'src."error_detail"' not in failure_sql
    assert 'src."prompt_tokens"' in analysis_sql
    assert 'src."estimated_cost_usd"' in analysis_sql


@pytest.mark.parametrize(
    ("source_schema", "target_schema"),
    [
        ("public", "public"),
        ("public", "pg_catalog"),
        ("public", "Demo"),
        ("public", "demo-data"),
        ("public", "1demo"),
    ],
)
def test_synchronizer_rejects_unsafe_schema_boundaries(
    source_schema: str,
    target_schema: str,
) -> None:
    engine = cast(Engine, object())

    with pytest.raises(ValueError):
        DemoSnapshotSynchronizer(
            engine,
            source_schema=source_schema,
            target_schema=target_schema,
        )


class _FakeResult:
    def __init__(self, value: str | int | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> str | int | None:
        return self._value

    def scalar_one(self) -> str | int:
        assert self._value is not None
        return self._value


class _FakeConnection:
    def __init__(self, source_revision: str, target_revision: str) -> None:
        self._revisions = iter((source_revision, target_revision))
        self.statements: list[str] = []

    def execute(self, statement: object) -> _FakeResult:
        sql = str(statement)
        self.statements.append(sql)
        if "alembic_version" in sql:
            return _FakeResult(next(self._revisions))
        if sql.startswith("SELECT count(*)"):
            return _FakeResult(1)
        return _FakeResult(None)


class _FakeEngine:
    def __init__(self, source_revision: str, target_revision: str) -> None:
        self.connection = _FakeConnection(source_revision, target_revision)
        self.begin_count = 0

    @contextmanager
    def begin(self) -> Generator[_FakeConnection]:
        self.begin_count += 1
        yield self.connection


def test_revision_mismatch_stops_before_target_mutation() -> None:
    engine = _FakeEngine("0006", "0005")
    synchronizer = DemoSnapshotSynchronizer(cast(Engine, engine))

    with pytest.raises(DemoSnapshotError, match="application revision"):
        synchronizer.synchronize()

    assert engine.begin_count == 1
    assert not any(sql.startswith(("DELETE", "INSERT")) for sql in engine.connection.statements)


def test_sync_replaces_all_target_tables_in_one_transaction_and_returns_counts() -> None:
    engine = _FakeEngine(EXPECTED_DATABASE_REVISION, EXPECTED_DATABASE_REVISION)
    manifest = default_demo_snapshot_manifest()
    result = DemoSnapshotSynchronizer(cast(Engine, engine), manifest=manifest).synchronize()

    assert engine.begin_count == 1
    assert result.source_revision == EXPECTED_DATABASE_REVISION
    assert result.target_revision == EXPECTED_DATABASE_REVISION
    assert result.total_rows == len(manifest.tables)
    assert dict(result.table_counts)["reports"] == 1

    statements = engine.connection.statements
    assert statements[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
    first_delete = next(index for index, sql in enumerate(statements) if sql.startswith("DELETE"))
    first_copy = next(
        index for index, sql in enumerate(statements) if sql.startswith('INSERT INTO "demo"')
    )
    assert first_delete < first_copy
    assert any(sql == 'DELETE FROM "demo"."scientific_embeddings"' for sql in statements)
    assert not any('INSERT INTO "demo"."scientific_embeddings"' in sql for sql in statements)
    assert all(
        not any(f'INSERT INTO "demo"."{table_name}"' in sql for sql in statements)
        for table_name in DEMO_EXCLUDED_TABLES
    )
