# pyright: reportPrivateUsage=false

"""Operator retries preserve the explicitly selected reprocessing revision."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from tests.unit.test_pipeline_execution import _execution
from tests.unit.test_runtime import _configure_reused_pipeline
from typer.testing import CliRunner

from paper_harness.domain.analysis import AnalysisScope
from paper_harness.domain.models import (
    PipelineExecution,
    PipelineExecutionContract,
    PipelineExecutionMode,
    RunStatus,
)
from paper_harness.domain.reports import ReportNarrativeMode
from paper_harness.entrypoints import cli as cli_module
from paper_harness.entrypoints.runtime import (
    DailyPipelineResult,
    DailyPipelineResumeError,
    execute_daily_pipeline,
)
from paper_harness.ports.llm import LLMAuthenticationError

EXECUTION_ID = UUID("e6d358f4-92a2-41bf-b174-33351db83b2c")
LOGICAL_DATE = date(2026, 8, 10)


def _resume(
    *, logical_date: date | None = LOGICAL_DATE, reprocess: bool = True
) -> DailyPipelineResult:
    return execute_daily_pipeline(
        topic_config=Path("unused.yaml"),
        logical_date=logical_date,
        analysis_scope=AnalysisScope.FULL_TEXT,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        max_selected_papers=1,
        reprocess=reprocess,
        resume_execution_id=EXECUTION_ID,
    )


@pytest.mark.parametrize("reprocess,logical_date", [(False, LOGICAL_DATE), (True, None)])
def test_resume_requires_explicit_mode_and_date_before_dependency_work(
    reprocess: bool, logical_date: date | None
) -> None:
    with pytest.raises(ValueError, match="requires --reprocess"):
        _resume(reprocess=reprocess, logical_date=logical_date)


@pytest.mark.parametrize("mismatch", ["missing", "topic", "date", "mode", "scope"])
def test_resume_rejects_unknown_or_foreign_execution_before_starting_work(
    monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    harness = _configure_reused_pipeline(monkeypatch)
    existing = replace(
        _execution(), id=EXECUTION_ID, execution_mode=PipelineExecutionMode.REPROCESS
    )
    if mismatch == "missing":
        existing = None
    elif mismatch == "topic":
        existing = replace(existing, topic_id=UUID(int=100))
    elif mismatch == "date":
        existing = replace(existing, logical_date=LOGICAL_DATE - timedelta(days=1))
    elif mismatch == "mode":
        existing = _execution()
    elif mismatch == "scope":
        existing = replace(existing, analysis_scope=AnalysisScope.ABSTRACT_ONLY)
    harness.repository.get_pipeline_execution.return_value = existing

    with pytest.raises(DailyPipelineResumeError, match="existing reprocess"):
        _resume()

    harness.repository.start_pipeline_execution.assert_not_called()
    harness.ingest_execute.assert_not_called()
    harness.embedding_loader.assert_not_called()


def test_failed_reprocess_resume_reuses_parent_and_child_execution_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _configure_reused_pipeline(
        monkeypatch,
        execution_mode=PipelineExecutionMode.REPROCESS,
        pipeline_execution_id=EXECUTION_ID,
    )
    original = _execution()
    failed = replace(
        original,
        id=EXECUTION_ID,
        execution_mode=PipelineExecutionMode.REPROCESS,
        status=RunStatus.FAILED,
        completed_at=original.started_at + timedelta(minutes=15),
        error_code="ARXIV_DISCOVERY_INCOMPLETE",
        error_detail="A saved identifier page remains pending.",
    )
    harness.repository.get_pipeline_execution.return_value = failed

    def existing_execution(_requested: PipelineExecution) -> PipelineExecution:
        return failed

    harness.repository.start_pipeline_execution.side_effect = existing_execution

    def restart_execution(
        _execution_id: UUID,
        *,
        started_at: datetime,
        deadline_at: datetime,
        contract: PipelineExecutionContract,
    ) -> PipelineExecution:
        return replace(
            failed,
            status=RunStatus.RUNNING,
            started_at=started_at,
            deadline_at=deadline_at,
            contract=contract,
            completed_at=None,
            error_code=None,
            error_detail=None,
        )

    harness.repository.restart_pipeline_execution.side_effect = restart_execution

    result = _resume()

    assert result.product_run.pipeline_execution_id == EXECUTION_ID
    harness.repository.daily_pipeline_lock.assert_called_once_with(EXECUTION_ID)
    assert harness.repository.restart_pipeline_execution.call_args.args[0] == EXECUTION_ID
    assert harness.ingest_execute.call_args.kwargs["pipeline_execution_id"] == EXECUTION_ID
    assert harness.ingest_execute.call_args.kwargs["resume_existing"] is True
    assert harness.publication_execute.call_args.kwargs["pipeline_execution_id"] == EXECUTION_ID


@pytest.mark.parametrize("via_environment", [False, True])
def test_cli_passes_explicit_resume_uuid(
    via_environment: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def execute_stub(**kwargs: object) -> DailyPipelineResult:
        captured.update(kwargs)
        raise LLMAuthenticationError("stop after argument capture")

    monkeypatch.setattr(cli_module, "execute_daily_pipeline", execute_stub)
    arguments = ["run-pipeline", "--logical-date", LOGICAL_DATE.isoformat(), "--reprocess"]
    if via_environment:
        monkeypatch.setenv("PIPELINE_RESUME_EXECUTION_ID", str(EXECUTION_ID))
    else:
        arguments += ["--resume-execution-id", str(EXECUTION_ID)]

    result = CliRunner().invoke(cli_module.app, arguments)

    assert result.exit_code == 1
    assert captured["resume_execution_id"] == EXECUTION_ID
    assert captured["logical_date"] == LOGICAL_DATE
    assert captured["reprocess"] is True
