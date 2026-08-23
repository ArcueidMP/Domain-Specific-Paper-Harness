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
from paper_harness.adapters.postgres.demo_schema import DemoSchemaBootstrapResult
from paper_harness.adapters.postgres.demo_snapshot import DemoSnapshotResult
from paper_harness.application.pipeline_accounting import PipelineAccountingSnapshot
from paper_harness.domain.analysis import AnalysisScope
from paper_harness.domain.historical import BackfillStatus, HistoricalBackfillRun
from paper_harness.domain.models import (
    DailyRun,
    PipelineExecutionMode,
    RunOperation,
    RunStatus,
)
from paper_harness.entrypoints.cli import app
from paper_harness.entrypoints.runtime import (
    DailyPipelineFailure,
    DailyPipelineResult,
    DailyPipelineRunFailedError,
)
from paper_harness.ports.llm import LLMAuthenticationError, LLMUnavailableError
from paper_harness.ports.repository import RepositoryIntegrityError

PIPELINE_EXECUTION_ID = UUID("bc432395-115e-52d8-91f8-5910376b7984")


def test_demo_operator_commands_are_explicit_cli_operations() -> None:
    root = get_command(app)

    assert isinstance(root, TyperGroup)
    assert "bootstrap-demo-schema" in root.commands
    assert "sync-demo-schema" in root.commands


def test_demo_bootstrap_cli_emits_only_non_sensitive_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "execute_demo_schema_bootstrap",
        lambda: DemoSchemaBootstrapResult(
            schema="demo",
            sync_role="paper_harness_demo_sync",
            read_role="paper_harness_demo_read",
            source_table_count=45,
            readable_source_column_count=320,
            demo_table_count=52,
        ),
    )

    result = CliRunner().invoke(app, ["bootstrap-demo-schema"])

    assert result.exit_code == 0
    assert '"event":"demo_schema_bootstrap_completed"' in result.stdout
    assert '"demo_table_count":52' in result.stdout


def test_demo_sync_cli_emits_revision_and_table_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "execute_demo_snapshot_sync",
        lambda: DemoSnapshotResult(
            source_revision="0006_topic_reprocessing",
            target_revision="0006_topic_reprocessing",
            table_counts=(("topics", 3), ("reports", 6)),
        ),
    )

    result = CliRunner().invoke(app, ["sync-demo-schema"])

    assert result.exit_code == 0
    assert '"event":"demo_snapshot_sync_completed"' in result.stdout
    assert '"total_rows":9' in result.stdout
    assert '"table_counts":{"topics":3,"reports":6}' in result.stdout


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


def test_daily_cli_uses_one_direct_pipeline_contract() -> None:
    root = get_command(app)
    assert isinstance(root, TyperGroup)
    command = root.commands["run-pipeline"]
    exposed = {
        name
        for parameter in command.params
        if isinstance(parameter, TyperOption)
        for name in parameter.opts
    }
    assert "--execution-mode" not in exposed
    assert "--execution-key" not in exposed
    assert "--reprocess" in exposed
    assert "verify-publication" not in root.commands


def test_daily_cli_passes_the_direct_reprocess_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def execute_stub(**kwargs: object) -> DailyPipelineResult:
        captured.update(kwargs)
        raise LLMAuthenticationError("stop after argument capture")

    monkeypatch.setattr(cli_module, "execute_daily_pipeline", execute_stub)
    result = CliRunner().invoke(
        app,
        [
            "run-pipeline",
            "--logical-date",
            "2026-08-22",
            "--reprocess",
            "--analysis-scope",
            "abstract_only",
            "--narrative-mode",
            "structured_only",
            "--max-selected-papers",
            "1",
        ],
    )

    assert result.exit_code == 1
    assert captured["reprocess"] is True
    assert '"execution_mode":"REPROCESS"' in result.output


@pytest.mark.parametrize(
    (
        "failure_code",
        "failure_retryable",
        "expected_level",
        "expected_exhausted_events",
    ),
    [
        (
            "COMPARISON_UNAVAILABLE",
            False,
            "WARNING",
            0,
        ),
        (
            None,
            False,
            "INFO",
            0,
        ),
        (
            "LLM_UNAVAILABLE",
            True,
            "WARNING",
            1,
        ),
    ],
)
def test_full_pipeline_cli_reports_complete_and_partial_results(
    monkeypatch: pytest.MonkeyPatch,
    failure_code: str | None,
    failure_retryable: bool,
    expected_level: str,
    expected_exhausted_events: int,
) -> None:
    execution_mode = PipelineExecutionMode.NORMAL
    now = datetime(2026, 8, 10, 5, tzinfo=UTC)
    topic_id = UUID("4b7db6d4-349c-5c06-bc41-f84091580fcb")
    ingestion = DailyRun(
        id=UUID("04a6195a-4267-4d72-b882-16fa95acbc12"),
        topic_id=topic_id,
        logical_date=now.date(),
        operation=RunOperation.ARXIV_INGESTION,
        analysis_scope=None,
        status=RunStatus.COMPLETE,
        started_at=now,
        completed_at=now,
        cursor_from=now,
        cursor_to=now,
        discovered_count=2,
        normalized_count=2,
        selected_count=0,
        completed_count=0,
        failed_count=0,
        error_code=None,
        error_detail=None,
        schema_version=1,
        created_at=now,
        pipeline_execution_mode=execution_mode,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
    )
    analysis = DailyRun(
        id=UUID("a3069769-e9af-43aa-9b51-2d1863ef453f"),
        topic_id=topic_id,
        logical_date=now.date(),
        operation=RunOperation.STRUCTURED_ANALYSIS,
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        status=RunStatus.COMPLETE,
        started_at=now,
        completed_at=now,
        cursor_from=None,
        cursor_to=None,
        discovered_count=0,
        normalized_count=0,
        selected_count=2,
        completed_count=2,
        failed_count=0,
        error_code=None,
        error_detail=None,
        schema_version=1,
        created_at=now,
        pipeline_execution_mode=execution_mode,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
    )
    product = DailyRun(
        id=UUID("7b9eb955-e227-4f97-a5f8-3956552fd7da"),
        topic_id=topic_id,
        source_run_id=analysis.id,
        logical_date=now.date(),
        operation=RunOperation.PRODUCT_PUBLICATION,
        analysis_scope=None,
        status=RunStatus.PARTIAL if failure_code is not None else RunStatus.COMPLETE,
        started_at=now,
        completed_at=now,
        cursor_from=None,
        cursor_to=None,
        discovered_count=0,
        normalized_count=0,
        selected_count=2,
        completed_count=1 if failure_code is not None else 2,
        failed_count=1 if failure_code is not None else 0,
        error_code=None,
        error_detail=None,
        schema_version=1,
        created_at=now,
        pipeline_execution_mode=execution_mode,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
    )
    backfill = HistoricalBackfillRun(
        id=UUID("9fa450e2-a968-47ae-815f-4847b902f7c3"),
        topic_id=topic_id,
        window_from=date(2026, 2, 10),
        window_to=now.date(),
        query_plan=("LLM agent",),
        max_results_per_query=100,
        overall_timeout_seconds=1800,
        embedding_model_identifier="allenai/specter2_base",
        embedding_model_revision="revision",
        embedding_tokenizer_identifier="allenai/specter2_base",
        embedding_tokenizer_revision="revision",
        embedding_dimension=768,
        embedding_preprocessing_contract="title + separator + abstract; CLS; max_length=512",
        embedding_model_provenance="huggingface:allenai/specter2_base@revision",
        embedding_source="specter2_base_title_abstract_cls",
        status=BackfillStatus.COMPLETE,
        next_query_index=1,
        discovered_count=1,
        persisted_count=1,
        representative_count=1,
        started_at=now,
        completed_at=now,
        error_code=None,
        error_detail=None,
        schema_version=1,
        created_at=now,
    )
    pipeline_result = DailyPipelineResult(
        ingestion_run=ingestion,
        analysis_run=analysis,
        historical_backfill=backfill,
        product_run=product,
        evaluated_count=2,
        relevant_count=2,
        selected_count=2,
        search_session_count=2,
        comparison_count=1,
        failures=(
            (
                DailyPipelineFailure(
                    paper_id=UUID("d8fdbf73-cf9a-487f-9b6a-237e13272d55"),
                    stage="COMPARED",
                    error_code=failure_code,
                    retryable=failure_retryable,
                    detail="A bounded item operation did not complete.",
                ),
            )
            if failure_code is not None
            else ()
        ),
        duration_ms=1250,
        accounting=PipelineAccountingSnapshot(
            arxiv_operation_count=3,
            semantic_scholar_operation_count=4,
            grobid_api_call_count=2,
            model_api_call_count=5,
            prompt_tokens=120,
            completion_tokens=30,
            total_tokens=150,
            model_duration_ms=900,
            estimated_cost_usd=None,
        ),
    )

    def execute_stub(**kwargs: object) -> DailyPipelineResult:
        del kwargs
        return pipeline_result

    monkeypatch.setattr(cli_module, "execute_daily_pipeline", execute_stub)
    result = CliRunner().invoke(
        app,
        [
            "run-pipeline",
            "--logical-date",
            "2026-08-10",
            "--analysis-scope",
            "abstract_only",
            "--narrative-mode",
            "structured_only",
            "--max-selected-papers",
            "2",
        ],
    )

    assert result.exit_code == 0
    assert result.output.count('"event":"daily_job_started"') == 1
    assert result.output.count('"event":"daily_job_finished"') == 1
    assert f'"level":"{expected_level}"' in result.output
    assert f'"execution_mode":"{execution_mode.value}"' in result.output
    assert '"pipeline_execution_id":"bc432395-115e-52d8-91f8-5910376b7984"' in result.output
    assert '"publication_run_id":"7b9eb955-e227-4f97-a5f8-3956552fd7da"' in result.output
    if failure_code is not None:
        assert f'"error_code":"{failure_code}"' in result.output
    else:
        assert '"error_code":"COMPARISON_UNAVAILABLE"' not in result.output
    exhausted_event_count = result.output.count('"event":"external_dependency_exhausted"')
    assert exhausted_event_count == expected_exhausted_events
    if expected_exhausted_events:
        assert '"dependency":"deepseek"' in result.output
        assert '"affected_item_count":1' in result.output
        assert '"stages":["COMPARED"]' in result.output
    assert '"external_call_count_lower_bound":14' in result.output
    assert '"model_total_tokens":150' in result.output
    assert '"estimated_cost_usd":null' in result.output


@pytest.mark.parametrize(
    ("error", "expected_exhausted_events"),
    [
        (LLMUnavailableError("DeepSeek exhausted bounded transient retries"), 1),
        (LLMAuthenticationError("DeepSeek rejected authentication"), 0),
    ],
)
def test_full_pipeline_cli_identifies_only_exhausted_transient_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_exhausted_events: int,
) -> None:
    def execute_stub(**kwargs: object) -> DailyPipelineResult:
        del kwargs
        raise error

    monkeypatch.setattr(cli_module, "execute_daily_pipeline", execute_stub)
    result = CliRunner().invoke(
        app,
        [
            "run-pipeline",
            "--logical-date",
            "2026-08-10",
            "--analysis-scope",
            "abstract_only",
            "--narrative-mode",
            "structured_only",
            "--max-selected-papers",
            "1",
        ],
    )

    assert result.exit_code == 1
    assert result.output.count('"event":"daily_job_started"') == 1
    assert result.output.count('"event":"daily_job_failed"') == 1
    exhausted_event_count = result.output.count('"event":"external_dependency_exhausted"')
    assert exhausted_event_count == expected_exhausted_events
    if expected_exhausted_events:
        assert '"dependency":"deepseek"' in result.output
        assert '"error_code":"LLM_UNAVAILABLE"' in result.output
        assert result.output.count(str(error)) == 1
    else:
        assert '"dependency":"deepseek"' not in result.output


def test_full_pipeline_cli_sanitizes_repository_integrity_failure_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def execute_stub(**kwargs: object) -> DailyPipelineResult:
        del kwargs
        raise RepositoryIntegrityError("PostgreSQL rejected arXiv batch persistence")

    monkeypatch.setattr(cli_module, "execute_daily_pipeline", execute_stub)
    result = CliRunner().invoke(
        app,
        [
            "run-pipeline",
            "--logical-date",
            "2026-08-10",
            "--analysis-scope",
            "abstract_only",
            "--narrative-mode",
            "structured_only",
            "--max-selected-papers",
            "1",
        ],
    )

    assert result.exit_code == 1
    assert result.output.count('"event":"daily_job_started"') == 1
    assert result.output.count('"event":"daily_job_failed"') == 1
    assert '"error_code":"PERSISTENCE_INTEGRITY_FAILED"' in result.output
    assert result.output.count("PostgreSQL rejected arXiv batch persistence") == 1
    assert "paper_version_authors" not in result.output
    assert "uq_version_author_position" not in result.output
    assert "Traceback" not in result.output


def test_full_pipeline_cli_preserves_exhaustion_from_failed_child_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 10, 5, tzinfo=UTC)
    failed_analysis = DailyRun(
        id=UUID("a3069769-e9af-43aa-9b51-2d1863ef453f"),
        topic_id=UUID("4b7db6d4-349c-5c06-bc41-f84091580fcb"),
        logical_date=now.date(),
        operation=RunOperation.STRUCTURED_ANALYSIS,
        analysis_scope=AnalysisScope.FULL_TEXT,
        status=RunStatus.FAILED,
        started_at=now,
        completed_at=now,
        cursor_from=None,
        cursor_to=None,
        discovered_count=0,
        normalized_count=0,
        selected_count=1,
        completed_count=0,
        failed_count=1,
        error_code="PUBLICATION_TRANSACTION_FAILED",
        error_detail="Analysis failure metadata could not be committed.",
        schema_version=1,
        created_at=now,
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=1,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
    )
    error = DailyPipelineRunFailedError(
        failed_analysis,
        failures=(
            DailyPipelineFailure(
                paper_id=UUID("d8fdbf73-cf9a-487f-9b6a-237e13272d55"),
                stage="ANALYZED",
                error_code="LLM_UNAVAILABLE",
                retryable=True,
                detail="DeepSeek exhausted bounded transient retries.",
            ),
        ),
    )

    def execute_stub(**kwargs: object) -> DailyPipelineResult:
        del kwargs
        raise error

    monkeypatch.setattr(cli_module, "execute_daily_pipeline", execute_stub)
    result = CliRunner().invoke(
        app,
        [
            "run-pipeline",
            "--logical-date",
            "2026-08-10",
            "--analysis-scope",
            "full_text",
            "--narrative-mode",
            "structured_only",
            "--max-selected-papers",
            "1",
        ],
    )

    assert result.exit_code == 1
    assert result.output.count('"event":"external_dependency_exhausted"') == 1
    assert '"dependency":"deepseek"' in result.output
    assert '"affected_item_count":1' in result.output
    assert '"stages":["ANALYZED"]' in result.output
    assert result.output.count('"event":"daily_job_failed"') == 1
    assert "DeepSeek exhausted bounded transient retries" not in result.output


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
        error_code="PUBLICATION_TRANSACTION_FAILED" if status is RunStatus.FAILED else None,
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
        error_code="PUBLICATION_TRANSACTION_FAILED" if status is RunStatus.FAILED else None,
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
