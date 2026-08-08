from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from click import unstyle
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
