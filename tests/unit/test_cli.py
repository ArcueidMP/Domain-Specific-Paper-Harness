from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from click import unstyle
from typer.core import TyperGroup, TyperOption
from typer.main import get_command
from typer.testing import CliRunner

import paper_harness.entrypoints.cli as cli_module
from paper_harness.domain.analysis import AnalysisScope
from paper_harness.domain.models import DailyRun, RunOperation, RunStatus
from paper_harness.entrypoints.cli import app


def test_cli_accepts_string_logical_date_option() -> None:
    result = CliRunner().invoke(
        app,
        ["ingest-arxiv", "--help"],
        env={"FORCE_COLOR": "1"},
    )
    assert result.exit_code == 0
    assert "--logical-date" in unstyle(result.stdout)


def test_related_search_cli_exposes_every_execution_bound() -> None:
    root = get_command(app)
    assert isinstance(root, TyperGroup)
    command = root.commands["search-related"]
    exposed = {
        name
        for parameter in command.params
        if isinstance(parameter, TyperOption)
        for name in parameter.opts
    }
    assert {
        "--max-steps",
        "--max-queries",
        "--max-queue-size",
        "--max-citation-depth",
        "--max-candidates",
        "--max-selected-candidates",
        "--per-operation-timeout-seconds",
        "--overall-timeout-seconds",
    } <= exposed


@pytest.mark.parametrize(
    ("command_name", "expected_options"),
    [
        ("publish-product", {"--topic-config", "--logical-date", "--narrative-mode"}),
        (
            "generate-periodic-report",
            {
                "--topic-config",
                "--report-type",
                "--period-start",
                "--period-end",
                "--narrative-mode",
            },
        ),
    ],
)
def test_m4_cli_commands_expose_explicit_report_inputs(
    command_name: str,
    expected_options: set[str],
) -> None:
    root = get_command(app)
    assert isinstance(root, TyperGroup)
    command = root.commands[command_name]
    exposed = {
        name
        for parameter in command.params
        if isinstance(parameter, TyperOption)
        for name in parameter.opts
    }
    assert expected_options <= exposed


@pytest.mark.parametrize(
    ("status", "completed_count", "failed_count", "exit_code", "level", "event"),
    [
        (RunStatus.COMPLETE, 1, 0, 0, "INFO", "structured_analysis_completed"),
        (RunStatus.PARTIAL, 1, 1, 0, "WARNING", "structured_analysis_partial"),
        (RunStatus.FAILED, 0, 1, 1, "ERROR", "structured_analysis_failed"),
    ],
)
def test_analysis_cli_exit_and_log_severity_follow_persisted_run_status(
    monkeypatch: pytest.MonkeyPatch,
    status: RunStatus,
    completed_count: int,
    failed_count: int,
    exit_code: int,
    level: str,
    event: str,
) -> None:
    selected_count = completed_count + failed_count
    run = DailyRun(
        id=UUID("b0a47819-8190-4ff4-8bfc-68bd94e50325"),
        topic_id=UUID("4b7db6d4-349c-5c06-bc41-f84091580fcb"),
        logical_date=date(2026, 8, 8),
        operation=RunOperation.STRUCTURED_ANALYSIS,
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        status=status,
        started_at=datetime(2026, 8, 8, 5, tzinfo=UTC),
        completed_at=datetime(2026, 8, 8, 5, 1, tzinfo=UTC),
        cursor_from=None,
        cursor_to=None,
        discovered_count=0,
        normalized_count=0,
        selected_count=selected_count,
        completed_count=completed_count,
        failed_count=failed_count,
        error_code="NO_SELECTED_PAPER_COMPLETED" if status is RunStatus.FAILED else None,
        error_detail=(
            "No selected paper completed evidence extraction."
            if status is RunStatus.FAILED
            else None
        ),
        schema_version=1,
        created_at=datetime(2026, 8, 8, 5, tzinfo=UTC),
    )

    def execute_stub(
        *,
        topic_config: Path,
        paper_ids: tuple[UUID, ...],
        analysis_scope: AnalysisScope,
        logical_date: date | None,
    ) -> DailyRun:
        del topic_config, paper_ids, analysis_scope, logical_date
        return run

    monkeypatch.setattr(cli_module, "execute_structured_analysis", execute_stub)

    result = CliRunner().invoke(
        app,
        [
            "analyze-papers",
            "--paper-id",
            "91c198f8-c23a-40e3-bd86-246b92be7813",
            "--analysis-scope",
            "abstract_only",
        ],
    )

    assert result.exit_code == exit_code
    assert f'"level":"{level}"' in result.output
    assert f'"event":"{event}"' in result.output


@pytest.mark.parametrize(
    ("status", "exit_code", "level", "event"),
    [
        (RunStatus.COMPLETE, 0, "INFO", "product_publication_completed"),
        (RunStatus.PARTIAL, 0, "WARNING", "product_publication_partial"),
        (RunStatus.FAILED, 1, "ERROR", "product_publication_failed"),
    ],
)
def test_product_cli_exit_and_log_severity_follow_persisted_run_status(
    monkeypatch: pytest.MonkeyPatch,
    status: RunStatus,
    exit_code: int,
    level: str,
    event: str,
) -> None:
    run = DailyRun(
        id=UUID("9d74e855-fc9d-4947-bcbf-d1d7218a0427"),
        topic_id=UUID("4b7db6d4-349c-5c06-bc41-f84091580fcb"),
        source_run_id=UUID("b0a47819-8190-4ff4-8bfc-68bd94e50325"),
        logical_date=date(2026, 8, 10),
        operation=RunOperation.PRODUCT_PUBLICATION,
        analysis_scope=None,
        status=status,
        started_at=datetime(2026, 8, 10, 5, tzinfo=UTC),
        completed_at=datetime(2026, 8, 10, 5, 1, tzinfo=UTC),
        cursor_from=None,
        cursor_to=None,
        discovered_count=0,
        normalized_count=0,
        selected_count=1,
        completed_count=0 if status is RunStatus.FAILED else 1,
        failed_count=0 if status is RunStatus.COMPLETE else 1,
        error_code="NO_SELECTED_PAPER_COMPLETED" if status is RunStatus.FAILED else None,
        error_detail="No selected paper completed graph construction."
        if status is RunStatus.FAILED
        else None,
        schema_version=1,
        created_at=datetime(2026, 8, 10, 5, tzinfo=UTC),
    )

    def execute_stub(
        *,
        topic_config: Path,
        logical_date: date | None,
        narrative_mode: object,
    ) -> DailyRun:
        del topic_config, logical_date, narrative_mode
        return run

    monkeypatch.setattr(cli_module, "execute_product_publication", execute_stub)
    result = CliRunner().invoke(
        app,
        ["publish-product", "--narrative-mode", "structured_only"],
    )

    assert result.exit_code == exit_code
    assert f'"level":"{level}"' in result.output
    assert f'"event":"{event}"' in result.output
