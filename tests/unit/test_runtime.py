# pyright: reportPrivateUsage=false

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import ANY, MagicMock
from uuid import UUID

import pytest

from paper_harness.application.analyze_papers import AnalysisResumeError
from paper_harness.application.ingest_arxiv import IngestionResumeError
from paper_harness.application.reporting import ReportNarrativeModeConflictError
from paper_harness.domain.analysis import AnalysisScope, VerificationStatus
from paper_harness.domain.historical import (
    BackfillStatus,
    CandidateOrigin,
    CandidateScoreComponents,
    ComparisonTargetDecision,
    HistoricalBackfillRun,
    SearchCandidate,
    SelectionDecision,
)
from paper_harness.domain.identity import stable_pipeline_execution_id
from paper_harness.domain.models import (
    DailyRun,
    PaperStage,
    PipelineExecution,
    PipelineExecutionMode,
    RunItemStatus,
    RunOperation,
    RunStatus,
)
from paper_harness.domain.reports import ReportNarrativeMode
from paper_harness.entrypoints import runtime as runtime_module
from paper_harness.entrypoints.runtime import (
    DailyPipelineRunFailedError,
    _grobid_parser,
    _scholarly_retry_policy,
    execute_daily_pipeline,
    execute_historical_backfill,
    execute_product_publication,
)
from paper_harness.ports.llm import LLMConfigurationError
from paper_harness.ports.scholarly_search import ScholarlySearchConfigurationError
from paper_harness.ports.scientific_embedding import ScientificEmbeddingConfigurationError

PIPELINE_EXECUTION_ID = stable_pipeline_execution_id(
    UUID("4b7db6d4-349c-5c06-bc41-f84091580fcb"),
    date(2026, 8, 10),
)
REPROCESS_EXECUTION_ID = UUID("3b301c07-9aa3-4a3d-b13a-e7b3ba4db146")


def _embedding_stub() -> SimpleNamespace:
    return SimpleNamespace(
        model_identifier="allenai/specter2_base",
        model_revision="3447645e1def9117997203454fa4495937bfbd83",
        tokenizer_identifier="allenai/specter2_base",
        tokenizer_revision="3447645e1def9117997203454fa4495937bfbd83",
        dimension=768,
        preprocessing_contract="title [SEP] abstract; max_length=512",
        model_provenance=(
            "huggingface:allenai/specter2_base@3447645e1def9117997203454fa4495937bfbd83"
        ),
        source="specter2_base_title_abstract_cls",
    )


def test_full_text_parser_rejects_unknown_application_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "prodution")
    monkeypatch.setenv("GROBID_URL", "http://grobid:8070")
    monkeypatch.setenv("GROBID_AUTH_MODE", "none")

    with pytest.raises(ValueError, match="APP_ENV must be"):
        _grobid_parser(AnalysisScope.FULL_TEXT)


def test_google_identity_audience_must_match_the_private_grobid_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("GROBID_URL", "https://paper-harness-grobid.example.run.app")
    monkeypatch.setenv("GROBID_AUDIENCE", "https://another-service.example.run.app")
    monkeypatch.setenv("GROBID_AUTH_MODE", "google_identity")

    with pytest.raises(ValueError, match="exactly match"):
        _grobid_parser(AnalysisScope.FULL_TEXT)


def test_production_full_text_parser_accepts_matching_google_identity_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("GROBID_URL", "https://paper-harness-grobid.example.run.app/")
    monkeypatch.setenv("GROBID_AUDIENCE", "https://paper-harness-grobid.example.run.app")
    monkeypatch.setenv("GROBID_AUTH_MODE", "google_identity")

    assert _grobid_parser(AnalysisScope.FULL_TEXT) is not None


def test_abstract_only_scope_does_not_require_unused_grobid_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROBID_URL", raising=False)
    monkeypatch.delenv("GROBID_AUDIENCE", raising=False)
    monkeypatch.delenv("GROBID_AUTH_MODE", raising=False)

    assert _grobid_parser(AnalysisScope.ABSTRACT_ONLY) is None


def test_historical_backfill_fails_before_database_work_without_semantic_scholar_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(
        ScholarlySearchConfigurationError,
        match="SEMANTIC_SCHOLAR_API_KEY",
    ):
        execute_historical_backfill(
            topic_config=Path("unused.yaml"),
            through=date(2026, 8, 9),
        )


def test_historical_backfill_requires_the_prepared_specter2_base_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "test-key")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(
        ScientificEmbeddingConfigurationError,
        match="artifact directory does not exist",
    ):
        execute_historical_backfill(
            topic_config=Path("unused.yaml"),
            through=date(2026, 8, 9),
        )


def test_specter2_model_path_can_be_selected_explicitly_for_local_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[str | Path] = []
    sentinel = object()

    def fake_load(model_path: str | Path = Path("/opt/models/specter2_base")) -> object:
        selected.append(model_path)
        return sentinel

    monkeypatch.setenv("SPECTER2_MODEL_PATH", "D:/models/specter2_base")
    monkeypatch.setattr(runtime_module, "load_specter2_encoder", fake_load)

    assert runtime_module._specter2_embeddings() is sentinel
    assert selected == ["D:/models/specter2_base"]


def test_related_work_semantic_scholar_policy_uses_the_operator_timeout() -> None:
    policy = _scholarly_retry_policy(7)

    assert policy.request_timeout_seconds == 7
    assert policy.total_timeout_seconds == 7


def test_deepseek_product_publication_fails_before_database_work_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(LLMConfigurationError, match="DEEPSEEK_API_KEY"):
        execute_product_publication(
            topic_config=Path("unused.yaml"),
            logical_date=date(2026, 8, 10),
            narrative_mode=ReportNarrativeMode.DEEPSEEK,
        )


def test_structured_only_product_publication_does_not_require_deepseek_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="DATABASE_URL"):
        execute_product_publication(
            topic_config=Path("unused.yaml"),
            logical_date=date(2026, 8, 10),
            narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        )


def test_daily_pipeline_preflights_every_dependency_before_ingestion_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    repository = MagicMock()

    def start_execution_stub(execution: PipelineExecution) -> PipelineExecution:
        return execution

    repository.start_pipeline_execution.side_effect = start_execution_stub

    def load_topic_stub(_path: Path) -> object:
        events.append("topic")
        return _pipeline_topic()

    def deepseek_settings_stub() -> object:
        events.append("deepseek-settings")
        return SimpleNamespace(provider="deepseek", model="deepseek-v4-flash")

    def scholarly_settings_stub() -> object:
        events.append("scholarly-settings")
        return object()

    def parser_stub(_scope: AnalysisScope) -> object:
        events.append("parser")
        return object()

    def repository_stub(_operation: str) -> MagicMock:
        events.append("repository-ready")
        return repository

    def embeddings_stub() -> SimpleNamespace:
        events.append("embeddings")
        return _embedding_stub()

    def arxiv_stub() -> object:
        events.append("arxiv-client")
        return object()

    def deepseek_stub(_settings: object) -> object:
        events.append("deepseek-client")
        return object()

    def scholarly_stub(_settings: object, **_kwargs: object) -> object:
        events.append("scholarly-client")
        return object()

    def existing_run_stub(_topic_id: UUID, _logical_date: date) -> None:
        events.append("read-ingestion")
        return None

    class IngestionStub:
        def __init__(self, **_kwargs: object) -> None:
            events.append("ingestion-use-case")

        def execute(self, *_args: object, **_kwargs: object) -> DailyRun:
            events.append("persist-ingestion")
            raise RuntimeError("stop after the first persisted mutation boundary")

    class PipelineLockStub:
        def __enter__(self) -> None:
            events.append("pipeline-lock-acquired")

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            del exc_type, exc_value, traceback
            events.append("pipeline-lock-released")

    repository.get_run_for_date.side_effect = existing_run_stub
    repository.daily_pipeline_lock.return_value = PipelineLockStub()
    monkeypatch.setattr(runtime_module, "load_topic_config", load_topic_stub)
    monkeypatch.setattr(
        runtime_module.DeepSeekSettings,
        "from_environment",
        staticmethod(deepseek_settings_stub),
    )
    monkeypatch.setattr(
        runtime_module.SemanticScholarSettings,
        "from_environment",
        staticmethod(scholarly_settings_stub),
    )
    monkeypatch.setattr(runtime_module, "_grobid_parser", parser_stub)
    monkeypatch.setattr(runtime_module, "_ready_repository", repository_stub)
    monkeypatch.setattr(runtime_module, "_specter2_embeddings", embeddings_stub)
    monkeypatch.setattr(runtime_module, "ArxivClient", arxiv_stub)
    monkeypatch.setattr(runtime_module, "DeepSeekClient", deepseek_stub)
    monkeypatch.setattr(runtime_module, "SemanticScholarClient", scholarly_stub)
    monkeypatch.setattr(runtime_module, "IngestArxiv", IngestionStub)

    with pytest.raises(RuntimeError, match="persisted mutation boundary"):
        execute_daily_pipeline(
            topic_config=Path("unused.yaml"),
            logical_date=date(2026, 8, 10),
            analysis_scope=AnalysisScope.FULL_TEXT,
            narrative_mode=ReportNarrativeMode.DEEPSEEK,
            max_selected_papers=1,
        )

    required_preflight = [
        "topic",
        "deepseek-settings",
        "scholarly-settings",
        "parser",
        "repository-ready",
        "embeddings",
        "arxiv-client",
        "deepseek-client",
        "scholarly-client",
    ]
    mutation_index = events.index("persist-ingestion")
    assert all(events.index(event) < mutation_index for event in required_preflight)
    assert events.index("pipeline-lock-acquired") < mutation_index
    assert events[-1] == "pipeline-lock-released"
    repository.daily_pipeline_lock.assert_called_once_with(PIPELINE_EXECUTION_ID)
    assert events.count("persist-ingestion") == 1
    repository.persist_ingestion_selection.assert_not_called()


@pytest.mark.parametrize("analysis_status", [RunStatus.COMPLETE, RunStatus.PARTIAL])
def test_daily_pipeline_reuses_compatible_terminal_ingestion_and_analysis_runs(
    monkeypatch: pytest.MonkeyPatch,
    analysis_status: RunStatus,
) -> None:
    harness = _configure_reused_pipeline(
        monkeypatch,
        analysis_status=analysis_status,
    )

    result = _execute_pipeline(max_selected_papers=1)

    harness.ingest_execute.assert_called_once()
    assert harness.ingest_execute.call_args.kwargs["resume_existing"] is True
    harness.analyze_execute.assert_called_once()
    assert harness.analyze_execute.call_args.kwargs["resume_existing"] is True
    assert harness.analyze_execute.call_args.kwargs["paper_version_ids"] == tuple(
        candidate.paper_version_id for candidate in harness.candidates
    )
    assert not harness.analyze_execute.call_args.kwargs.get("paper_ids")
    assert (
        harness.analyze_execute.call_args.kwargs["run_operation"]
        is RunOperation.STRUCTURED_ANALYSIS
    )
    harness.repository.persist_ingestion_selection.assert_called_once()
    harness.repository.start_ingestion_run.assert_not_called()
    harness.repository.restart_ingestion_run.assert_not_called()
    harness.repository.start_analysis_run.assert_not_called()
    harness.repository.restart_analysis_run.assert_not_called()
    harness.backfill_execute.assert_called_once()
    harness.publication_execute.assert_called_once()
    assert result.ingestion_run is harness.ingestion
    assert result.analysis_run is harness.analysis
    assert result.product_run is harness.product


def test_daily_reprocess_creates_a_fresh_revision_and_regenerates_source_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module, "uuid4", lambda: REPROCESS_EXECUTION_ID)
    harness = _configure_reused_pipeline(
        monkeypatch,
        execution_mode=PipelineExecutionMode.REPROCESS,
        pipeline_execution_id=REPROCESS_EXECUTION_ID,
    )

    result = _execute_pipeline(max_selected_papers=1, reprocess=True)

    requested = harness.repository.start_pipeline_execution.call_args.args[0]
    assert requested.id == REPROCESS_EXECUTION_ID
    assert requested.execution_mode is PipelineExecutionMode.REPROCESS
    assert harness.ingest_execute.call_args.kwargs["pipeline_execution_mode"] is (
        PipelineExecutionMode.REPROCESS
    )
    assert harness.analyze_execute.call_args.kwargs["pipeline_execution_mode"] is (
        PipelineExecutionMode.REPROCESS
    )
    assert harness.analyze_execute.call_args.kwargs["reuse_contract"] is None
    assert result.product_run.pipeline_execution_id == REPROCESS_EXECUTION_ID


def test_daily_pipeline_holds_outer_lock_through_product_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _configure_reused_pipeline(monkeypatch)
    lock_state = {"active": False}

    class PipelineLockStub:
        def __enter__(self) -> None:
            assert lock_state["active"] is False
            lock_state["active"] = True

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            del exc_type, exc_value, traceback
            lock_state["active"] = False

    def publication_stub(*_args: object, **_kwargs: object) -> DailyRun:
        assert lock_state["active"] is True
        return harness.product

    harness.repository.daily_pipeline_lock.return_value = PipelineLockStub()
    harness.publication_execute.side_effect = publication_stub

    result = _execute_pipeline(max_selected_papers=1)

    assert result.product_run is harness.product
    assert lock_state["active"] is False
    harness.repository.daily_pipeline_lock.assert_called_once_with(PIPELINE_EXECUTION_ID)


@pytest.mark.parametrize("product_status", [RunStatus.COMPLETE, RunStatus.PARTIAL])
def test_daily_pipeline_reuses_terminal_product_without_new_search_or_comparison(
    monkeypatch: pytest.MonkeyPatch,
    product_status: RunStatus,
) -> None:
    harness = _configure_reused_pipeline(monkeypatch)
    terminal_product = replace(
        harness.product,
        status=product_status,
        completed_count=1 if product_status is RunStatus.COMPLETE else 0,
        failed_count=0 if product_status is RunStatus.COMPLETE else 1,
    )
    original_get_run = harness.repository.get_run.side_effect
    failure_item = SimpleNamespace(
        item=SimpleNamespace(
            paper_id=harness.candidates[0].paper_id,
            paper_version_id=harness.candidates[0].paper_version_id,
            stage=PaperStage.COMPARED,
            status=RunItemStatus.FAILED,
            failed_stage=PaperStage.COMPARED,
            error_code="COMPARISON_MISSING",
            retryable=False,
            error_detail="no current comparison was available",
        )
    )

    def get_run_stub(run_id: UUID) -> object:
        if run_id == terminal_product.id:
            return SimpleNamespace(
                run=terminal_product,
                items=() if product_status is RunStatus.COMPLETE else (failure_item,),
            )
        assert callable(original_get_run)
        return original_get_run(run_id)

    harness.repository.get_product_run_for_date.return_value = terminal_product
    harness.repository.get_run.side_effect = get_run_stub
    harness.publication_execute.return_value = terminal_product

    result = _execute_pipeline(max_selected_papers=1)

    harness.ingest_execute.assert_called_once()
    harness.repository.persist_ingestion_selection.assert_called_once()
    harness.analyze_execute.assert_called_once()
    harness.backfill_execute.assert_called_once()
    harness.search_constructor.assert_not_called()
    harness.compare_constructor.assert_not_called()
    harness.search_execute.assert_not_called()
    harness.compare_execute.assert_not_called()
    harness.repository.start_product_run.assert_not_called()
    harness.repository.restart_product_run.assert_not_called()
    harness.publication_execute.assert_called_once_with(
        _pipeline_topic(),
        logical_date=date(2026, 8, 10),
        narrative_mode=ReportNarrativeMode.DEEPSEEK,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
        upstream_failures=(),
    )
    assert result.product_run is terminal_product
    assert result.search_session_count == 0
    assert result.comparison_count == 0
    assert result.historical_backfill is harness.backfill_execute.return_value
    assert result.accounting is not None
    assert result.accounting.external_call_count_lower_bound == 0
    assert tuple(failure.error_code for failure in result.failures) == (
        () if product_status is RunStatus.COMPLETE else ("COMPARISON_MISSING",)
    )


def test_terminal_product_replay_preserves_publisher_narrative_mode_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _configure_reused_pipeline(monkeypatch)
    harness.repository.get_product_run_for_date.return_value = harness.product
    harness.publication_execute.side_effect = ReportNarrativeModeConflictError(
        "persisted report narrative mode conflicts with the request"
    )

    with pytest.raises(ReportNarrativeModeConflictError, match="narrative mode conflicts"):
        _execute_pipeline(max_selected_papers=1)

    harness.publication_execute.assert_called_once()
    harness.search_constructor.assert_not_called()
    harness.compare_constructor.assert_not_called()
    harness.repository.start_product_run.assert_not_called()
    harness.repository.restart_product_run.assert_not_called()


@pytest.mark.parametrize("execution_status", [RunStatus.COMPLETE, RunStatus.PARTIAL])
def test_terminal_pipeline_execution_replays_after_its_deadline_without_external_work(
    monkeypatch: pytest.MonkeyPatch,
    execution_status: RunStatus,
) -> None:
    first_candidate = _selection_candidate(
        UUID("d8fdbf73-cf9a-487f-9b6a-237e13272d55"),
        UUID("1c27b53f-e172-469e-808f-33d0495968c0"),
        "2608.00001",
        datetime(2026, 8, 10, 5, tzinfo=UTC),
    )
    second_candidate = _selection_candidate(
        UUID("2b1cdf06-a820-44a6-8187-0c34ce71dff7"),
        UUID("c8597621-50f3-4a59-a2e5-6ee8609c1438"),
        "2608.00002",
        datetime(2026, 8, 10, 5, 1, tzinfo=UTC),
    )
    analysis_items = (
        _completed_item(first_candidate.paper_id, first_candidate.paper_version_id),
        (
            _failed_item(
                second_candidate.paper_id,
                second_candidate.paper_version_id,
                error_code="ANALYSIS_MODEL_OUTPUT_INVALID",
            )
            if execution_status is RunStatus.PARTIAL
            else _completed_item(
                second_candidate.paper_id,
                second_candidate.paper_version_id,
            )
        ),
    )
    harness = _configure_reused_pipeline(
        monkeypatch,
        analysis_status=(
            RunStatus.PARTIAL if execution_status is RunStatus.PARTIAL else RunStatus.COMPLETE
        ),
        max_selected_papers=2,
        candidates=(first_candidate, second_candidate),
        analysis_items=analysis_items,
    )
    terminal_product = replace(
        harness.product,
        status=RunStatus.COMPLETE,
        completed_count=2,
        failed_count=0,
    )
    terminal_execution: PipelineExecution | None = None
    execution_lookup_count = 0

    def start_execution_stub(requested: PipelineExecution) -> PipelineExecution:
        nonlocal terminal_execution
        expired_start = requested.started_at - timedelta(days=2)
        terminal_execution = replace(
            requested,
            status=execution_status,
            started_at=expired_start,
            deadline_at=expired_start + timedelta(hours=1),
            completed_at=expired_start + timedelta(hours=2),
        )
        return terminal_execution

    def get_execution_stub(_execution_id: UUID) -> PipelineExecution | object:
        nonlocal execution_lookup_count
        execution_lookup_count += 1
        # The first lookup only selects the existing-execution validation path;
        # start_pipeline_execution returns its exact frozen projection.
        return object() if terminal_execution is None else terminal_execution

    ingestion_detail = SimpleNamespace(
        run=harness.ingestion,
        items=tuple(
            _completed_item(
                candidate.paper_id,
                candidate.paper_version_id,
                stage=PaperStage.SELECTED,
            )
            for candidate in harness.candidates
        ),
    )
    analysis_detail = SimpleNamespace(run=harness.analysis, items=analysis_items)
    product_detail = SimpleNamespace(
        run=terminal_product,
        items=(),
        report=SimpleNamespace(report=SimpleNamespace(narrative_mode=ReportNarrativeMode.DEEPSEEK)),
    )
    details = {
        harness.ingestion.id: ingestion_detail,
        harness.analysis.id: analysis_detail,
        terminal_product.id: product_detail,
    }
    backfill = harness.backfill_execute.return_value
    backfill.status = BackfillStatus.COMPLETE
    harness.repository.start_pipeline_execution.side_effect = start_execution_stub
    harness.repository.get_pipeline_execution.side_effect = get_execution_stub
    harness.repository.get_product_run_for_date.return_value = terminal_product
    harness.repository.get_historical_backfill.return_value = backfill
    harness.repository.get_run.side_effect = details.get
    harness.repository.get_product_run.return_value = product_detail

    result = _execute_pipeline(max_selected_papers=2)

    assert execution_lookup_count == 2
    assert result.status is execution_status
    assert result.product_run.status is RunStatus.COMPLETE
    assert result.search_session_count == 0
    assert result.comparison_count == 0
    assert result.historical_materialized_count == 0
    assert result.accounting is not None
    assert result.accounting.external_call_count_lower_bound == 0
    assert tuple(failure.error_code for failure in result.failures) == (
        ("ANALYSIS_MODEL_OUTPUT_INVALID",) if execution_status is RunStatus.PARTIAL else ()
    )
    harness.ingest_constructor.assert_not_called()
    harness.analyze_constructor.assert_not_called()
    harness.backfill_constructor.assert_not_called()
    harness.search_constructor.assert_not_called()
    harness.compare_constructor.assert_not_called()
    harness.publication_constructor.assert_not_called()
    harness.repository.persist_ingestion_selection.assert_not_called()
    harness.repository.complete_pipeline_execution.assert_not_called()
    harness.repository.fail_pipeline_execution.assert_not_called()


def test_partial_analysis_failure_keeps_product_and_parent_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = (
        _selection_candidate(
            UUID("d8fdbf73-cf9a-487f-9b6a-237e13272d55"),
            UUID("1c27b53f-e172-469e-808f-33d0495968c0"),
            "2608.00001",
            datetime(2026, 8, 10, 5, tzinfo=UTC),
        ),
        _selection_candidate(
            UUID("2b1cdf06-a820-44a6-8187-0c34ce71dff7"),
            UUID("c8597621-50f3-4a59-a2e5-6ee8609c1438"),
            "2608.00002",
            datetime(2026, 8, 10, 5, 1, tzinfo=UTC),
        ),
    )
    harness = _configure_reused_pipeline(
        monkeypatch,
        analysis_status=RunStatus.PARTIAL,
        max_selected_papers=2,
        candidates=candidates,
        analysis_items=(
            _completed_item(candidates[0].paper_id, candidates[0].paper_version_id),
            _failed_item(
                candidates[1].paper_id,
                candidates[1].paper_version_id,
                error_code="ANALYSIS_MODEL_OUTPUT_INVALID",
            ),
        ),
    )
    harness.product = replace(
        harness.product,
        status=RunStatus.PARTIAL,
        selected_count=2,
        completed_count=1,
        failed_count=1,
    )
    harness.publication_execute.return_value = harness.product
    harness.repository.get_run.side_effect = {
        harness.ingestion.id: SimpleNamespace(
            run=harness.ingestion,
            items=tuple(
                _completed_item(
                    candidate.paper_id,
                    candidate.paper_version_id,
                    stage=PaperStage.NORMALIZED,
                )
                for candidate in candidates
            ),
        ),
        harness.analysis.id: SimpleNamespace(
            run=harness.analysis,
            items=(
                _completed_item(candidates[0].paper_id, candidates[0].paper_version_id),
                _failed_item(
                    candidates[1].paper_id,
                    candidates[1].paper_version_id,
                    error_code="ANALYSIS_MODEL_OUTPUT_INVALID",
                ),
            ),
        ),
        harness.product.id: SimpleNamespace(
            run=harness.product,
            items=(
                _completed_item(candidates[0].paper_id, candidates[0].paper_version_id),
                _failed_item(
                    candidates[1].paper_id,
                    candidates[1].paper_version_id,
                    error_code="ANALYSIS_MODEL_OUTPUT_INVALID",
                ),
            ),
        ),
    }.get

    result = _execute_pipeline(max_selected_papers=2)

    assert result.product_run.status is RunStatus.PARTIAL
    assert result.status is RunStatus.PARTIAL
    assert tuple(failure.error_code for failure in result.failures) == (
        "ANALYSIS_MODEL_OUTPUT_INVALID",
    )
    harness.repository.complete_pipeline_execution.assert_called_once_with(
        PIPELINE_EXECUTION_ID,
        status=RunStatus.PARTIAL,
        completed_at=ANY,
    )


def test_failed_product_marks_the_parent_execution_failed_instead_of_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _configure_reused_pipeline(monkeypatch)
    failed_product = replace(
        harness.product,
        status=RunStatus.FAILED,
        completed_count=0,
        failed_count=1,
        error_code="NO_SELECTED_PAPER_COMPLETED",
        error_detail="No selected paper completed graph construction.",
    )
    execution_state: dict[str, PipelineExecution] = {}

    def get_execution_stub(_execution_id: UUID) -> PipelineExecution | None:
        return execution_state.get("execution")

    def start_execution_stub(execution: PipelineExecution) -> PipelineExecution:
        execution_state["execution"] = execution
        return execution

    def fail_execution_stub(
        execution_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> PipelineExecution:
        execution = execution_state["execution"]
        assert execution.id == execution_id
        failed = replace(
            execution,
            status=RunStatus.FAILED,
            completed_at=completed_at,
            error_code=error_code,
            error_detail=error_detail,
        )
        execution_state["execution"] = failed
        return failed

    harness.repository.get_pipeline_execution.side_effect = get_execution_stub
    harness.repository.start_pipeline_execution.side_effect = start_execution_stub
    harness.repository.fail_pipeline_execution.side_effect = fail_execution_stub
    harness.publication_execute.return_value = failed_product

    with pytest.raises(
        DailyPipelineRunFailedError,
        match="PRODUCT_PUBLICATION finished FAILED",
    ):
        _execute_pipeline(max_selected_papers=1)

    parent = execution_state["execution"]
    assert parent.status is RunStatus.FAILED
    assert parent.error_code == "NO_SELECTED_PAPER_COMPLETED"
    harness.repository.complete_pipeline_execution.assert_not_called()
    harness.repository.fail_pipeline_execution.assert_called_once_with(
        PIPELINE_EXECUTION_ID,
        completed_at=ANY,
        error_code="NO_SELECTED_PAPER_COMPLETED",
        error_detail=(
            "PRODUCT_PUBLICATION finished FAILED for 2026-08-10: NO_SELECTED_PAPER_COMPLETED"
        ),
    )


def _with_abstract_only_scope(run: DailyRun) -> DailyRun:
    return replace(run, analysis_scope=AnalysisScope.ABSTRACT_ONLY)


def _with_failed_ingestion(run: DailyRun) -> DailyRun:
    return replace(
        run,
        status=RunStatus.FAILED,
        error_code="ARXIV_UNAVAILABLE",
        error_detail="transient dependency exhausted",
    )


def _with_failed_analysis(run: DailyRun) -> DailyRun:
    return replace(
        run,
        status=RunStatus.FAILED,
        completed_count=0,
        failed_count=1,
        error_code="ANALYSIS_FAILED",
        error_detail="no selected paper completed",
    )


@pytest.mark.parametrize(
    ("conflicting_run", "expected_exception"),
    [
        pytest.param(
            _with_failed_ingestion,
            DailyPipelineRunFailedError,
            id="failed",
        ),
    ],
)
def test_daily_pipeline_rejects_incompatible_or_failed_ingestion_before_downstream_mutation(
    monkeypatch: pytest.MonkeyPatch,
    conflicting_run: Callable[[DailyRun], DailyRun],
    expected_exception: type[Exception],
) -> None:
    harness = _configure_reused_pipeline(monkeypatch)
    harness.run_state["ingestion"] = conflicting_run(harness.ingestion)

    with pytest.raises(expected_exception):
        _execute_pipeline(max_selected_papers=1)

    harness.repository.persist_ingestion_selection.assert_not_called()
    harness.analyze_constructor.assert_not_called()
    harness.repository.start_ingestion_run.assert_not_called()
    harness.repository.restart_ingestion_run.assert_not_called()
    harness.repository.start_analysis_run.assert_not_called()
    harness.repository.restart_analysis_run.assert_not_called()
    harness.backfill_constructor.assert_not_called()
    harness.search_constructor.assert_not_called()
    harness.compare_constructor.assert_not_called()
    harness.publication_constructor.assert_not_called()


@pytest.mark.parametrize(
    ("conflicting_run", "expected_exception"),
    [
        pytest.param(
            _with_abstract_only_scope,
            AnalysisResumeError,
            id="analysis-scope",
        ),
        pytest.param(
            _with_failed_analysis,
            DailyPipelineRunFailedError,
            id="failed",
        ),
    ],
)
def test_daily_pipeline_rejects_incompatible_or_failed_analysis_before_later_stages(
    monkeypatch: pytest.MonkeyPatch,
    conflicting_run: Callable[[DailyRun], DailyRun],
    expected_exception: type[Exception],
) -> None:
    harness = _configure_reused_pipeline(monkeypatch)
    harness.run_state["analysis"] = conflicting_run(harness.analysis)

    with pytest.raises(expected_exception):
        _execute_pipeline(max_selected_papers=1)

    harness.repository.persist_ingestion_selection.assert_called_once()
    harness.repository.start_ingestion_run.assert_not_called()
    harness.repository.restart_ingestion_run.assert_not_called()
    harness.repository.start_analysis_run.assert_not_called()
    harness.repository.restart_analysis_run.assert_not_called()
    harness.backfill_constructor.assert_not_called()
    harness.search_constructor.assert_not_called()
    harness.compare_constructor.assert_not_called()
    harness.publication_constructor.assert_not_called()


def test_failed_analysis_preserves_exhausted_dependency_item_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = UUID("d8fdbf73-cf9a-487f-9b6a-237e13272d55")
    paper_version_id = UUID("1c27b53f-e172-469e-808f-33d0495968c0")
    harness = _configure_reused_pipeline(
        monkeypatch,
        analysis_items=(
            _failed_item(
                paper_id,
                paper_version_id,
                error_code="LLM_UNAVAILABLE",
                retryable=True,
            ),
        ),
    )
    harness.run_state["analysis"] = _with_failed_analysis(harness.analysis)

    with pytest.raises(DailyPipelineRunFailedError) as caught:
        _execute_pipeline(max_selected_papers=1)

    assert caught.value.failures == (
        runtime_module.DailyPipelineFailure(
            paper_id=paper_id,
            stage=PaperStage.ANALYZED.value,
            error_code="LLM_UNAVAILABLE",
            retryable=True,
            detail="Persisted selected-paper failure.",
        ),
    )
    harness.backfill_constructor.assert_not_called()


def test_daily_pipeline_constructs_specter2_once_and_reuses_it_for_backfill_and_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embeddings = _embedding_stub()
    harness = _configure_reused_pipeline(monkeypatch, embeddings=embeddings)

    result = _execute_pipeline(max_selected_papers=1)

    harness.embedding_loader.assert_called_once_with()
    assert harness.backfill_constructor.call_args.kwargs["embeddings"] is embeddings
    assert harness.search_constructor.call_args.kwargs["embeddings"] is embeddings
    assert harness.search_execute.call_args.kwargs["objective"] == (
        "Identify historical and related work for Broad LLM Agents: "
        "Broad LLM-agent research. Use persisted evidence for systematic comparison "
        "to the source paper."
    )
    assert result.search_session_count == 1


def test_daily_pipeline_preserves_search_and_comparison_failures_after_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 10, 5, tzinfo=UTC)
    paper_id = UUID("d8fdbf73-cf9a-487f-9b6a-237e13272d55")
    second_paper_id = UUID("6eddb148-a9a6-4e7f-93fb-22cd2a70ba87")
    version_id = UUID("1c27b53f-e172-469e-808f-33d0495968c0")
    second_version_id = UUID("a6cf8ba8-a696-4e10-b46e-fda9d2023260")
    target_version_id = UUID("d5ecbf3f-446a-4166-b0a2-699c9a63ad08")
    candidates = (
        _selection_candidate(paper_id, version_id, "2608.00001", now),
        _selection_candidate(second_paper_id, second_version_id, "2608.00002", now),
    )
    analysis_items = (
        _completed_item(paper_id, version_id),
        _completed_item(second_paper_id, second_version_id),
    )
    harness = _configure_reused_pipeline(
        monkeypatch,
        max_selected_papers=2,
        candidates=candidates,
        analysis_items=analysis_items,
    )

    search_detail = SimpleNamespace(
        session=SimpleNamespace(id=UUID("cdd83223-d4c2-477f-b62f-c5d1e5675889")),
        candidates=(
            SimpleNamespace(
                rank=1,
                decision=SelectionDecision.SELECTED,
                local_paper_version_id=target_version_id,
            ),
        ),
    )

    def search_side_effect(**kwargs: object) -> object:
        if kwargs["source_paper_id"] == paper_id:
            raise runtime_module.RelatedWorkInputError("source analysis is incomplete")
        return search_detail

    harness.search_execute.side_effect = search_side_effect
    harness.repository.get_related_work.return_value = _related_detail((target_version_id,))
    harness.repository.get_reusable_analyzed_paper_version_ids.side_effect = _all_versions_reusable
    harness.compare_execute.side_effect = runtime_module.ComparisonInputMissingError(
        "target analysis is missing"
    )

    result = _execute_pipeline(max_selected_papers=2)

    assert result.product_run is harness.product
    assert result.product_run.status is RunStatus.COMPLETE
    assert result.search_session_count == 1
    assert result.comparison_count == 0
    assert {
        (failure.paper_id, failure.stage, failure.error_code) for failure in result.failures
    } == {
        (paper_id, "PRIOR_WORK_RETRIEVED", "RELATED_WORK_INPUT_INVALID"),
        (second_paper_id, "COMPARED", "COMPARISON_INPUT_MISSING"),
    }
    harness.publication_execute.assert_called_once()
    assert harness.publication_execute.call_args.kwargs["comparison_ids"] == frozenset()


def test_daily_pipeline_scopes_publication_to_current_comparisons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _configure_reused_pipeline(monkeypatch)
    comparison_id = UUID("b3fe29eb-5379-43b6-8698-dc16e266ac73")
    target_version_id = UUID("d5ecbf3f-446a-4166-b0a2-699c9a63ad08")
    harness.search_execute.return_value = SimpleNamespace(
        session=SimpleNamespace(id=UUID("cdd83223-d4c2-477f-b62f-c5d1e5675889")),
        candidates=(
            SimpleNamespace(
                rank=1,
                decision=SelectionDecision.SELECTED,
                local_paper_version_id=target_version_id,
            ),
        ),
    )
    harness.compare_execute.return_value = SimpleNamespace(
        comparison=SimpleNamespace(id=comparison_id)
    )
    harness.repository.get_related_work.return_value = _related_detail((target_version_id,))
    harness.repository.get_reusable_analyzed_paper_version_ids.side_effect = _all_versions_reusable

    result = _execute_pipeline(max_selected_papers=1)

    assert result.comparison_count == 1
    harness.compare_execute.assert_called_once()
    harness.publication_execute.assert_called_once()
    assert harness.publication_execute.call_args.kwargs["comparison_ids"] == frozenset(
        {comparison_id}
    )


def test_comparison_failure_preserves_other_current_comparisons_for_that_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 10, 5, tzinfo=UTC)
    failed_source_id = UUID("d8fdbf73-cf9a-487f-9b6a-237e13272d55")
    healthy_source_id = UUID("6eddb148-a9a6-4e7f-93fb-22cd2a70ba87")
    failed_source_version_id = UUID("1c27b53f-e172-469e-808f-33d0495968c0")
    healthy_source_version_id = UUID("a6cf8ba8-a696-4e10-b46e-fda9d2023260")
    first_target_id = UUID("d5ecbf3f-446a-4166-b0a2-699c9a63ad08")
    failing_target_id = UUID("81b46073-d481-4ad7-83e7-8b5a5c756ae3")
    healthy_target_id = UUID("64aeb3d1-0534-40e4-ad09-4d776a44ceaa")
    recovered_comparison_id = UUID("80da150e-650f-454a-bb36-69a21df117af")
    healthy_comparison_id = UUID("7772feb9-f7cc-4f9d-87d8-ef6a829e810d")
    candidates = (
        _selection_candidate(
            failed_source_id,
            failed_source_version_id,
            "2608.00001",
            now,
        ),
        _selection_candidate(
            healthy_source_id,
            healthy_source_version_id,
            "2608.00002",
            now,
        ),
    )
    harness = _configure_reused_pipeline(
        monkeypatch,
        max_selected_papers=2,
        candidates=candidates,
    )

    def search_side_effect(**kwargs: object) -> object:
        target_ids = (
            (failing_target_id, first_target_id)
            if kwargs["source_paper_id"] == failed_source_id
            else (healthy_target_id,)
        )
        return SimpleNamespace(
            session=SimpleNamespace(id=UUID("cdd83223-d4c2-477f-b62f-c5d1e5675889")),
            candidates=tuple(
                SimpleNamespace(
                    rank=rank,
                    decision=SelectionDecision.SELECTED,
                    local_paper_version_id=target_id,
                )
                for rank, target_id in enumerate(target_ids, start=1)
            ),
        )

    def comparison_side_effect(**kwargs: object) -> object:
        target_id = kwargs["target_paper_version_id"]
        if target_id == failing_target_id:
            raise runtime_module.ComparisonInputMissingError("target analysis is missing")
        comparison_id = (
            recovered_comparison_id
            if kwargs["source_paper_version_id"] == failed_source_version_id
            else healthy_comparison_id
        )
        return SimpleNamespace(comparison=SimpleNamespace(id=comparison_id))

    harness.search_execute.side_effect = search_side_effect
    harness.compare_execute.side_effect = comparison_side_effect

    def related_work_side_effect(
        paper_id: UUID,
        **_kwargs: object,
    ) -> SimpleNamespace:
        return _related_detail(
            (failing_target_id, first_target_id)
            if paper_id == failed_source_id
            else (healthy_target_id,)
        )

    harness.repository.get_related_work.side_effect = related_work_side_effect
    harness.repository.get_reusable_analyzed_paper_version_ids.side_effect = _all_versions_reusable

    result = _execute_pipeline(max_selected_papers=2)

    assert result.search_session_count == 2
    assert result.comparison_count == 2
    assert tuple((failure.paper_id, failure.error_code) for failure in result.failures) == (
        (failed_source_id, "COMPARISON_INPUT_MISSING"),
    )
    assert harness.publication_execute.call_args.kwargs["comparison_ids"] == frozenset(
        {recovered_comparison_id, healthy_comparison_id}
    )
    assert harness.publication_execute.call_args.kwargs["upstream_failures"] == ()


def test_daily_pipeline_materializes_and_analyzes_historical_versions_in_rank_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _configure_reused_pipeline(monkeypatch)
    historical_version_id = UUID("efc89774-bb75-4570-9f37-6f2d6bc4ae7e")
    historical_run = _pipeline_run(
        run_id=UUID("d402977c-8087-4698-b789-ef7f848bfc66"),
        topic_id=harness.analysis.topic_id,
        operation=RunOperation.HISTORICAL_ANALYSIS,
        now=datetime(2026, 8, 10, 5, tzinfo=UTC),
    )
    representative_records = (object(),)
    harness.repository.list_historical_representative_arxiv_ids.return_value = ("2601.00001",)
    harness.repository.list_historical_representative_version_ids.return_value = ()
    harness.arxiv.get_papers_by_ids.return_value = representative_records
    harness.repository.persist_historical_arxiv_records.return_value = (historical_version_id,)
    original_analysis_side_effect = harness.analyze_execute.side_effect
    original_get_run = harness.repository.get_run.side_effect

    def get_run_side_effect(run_id: UUID) -> object | None:
        if run_id == historical_run.id:
            return SimpleNamespace(run=historical_run, items=())
        assert callable(original_get_run)
        return original_get_run(run_id)

    harness.repository.get_run.side_effect = get_run_side_effect

    def analysis_side_effect(topic_value: SimpleNamespace, **kwargs: object) -> DailyRun:
        if kwargs["run_operation"] is RunOperation.HISTORICAL_ANALYSIS:
            return historical_run
        assert callable(original_analysis_side_effect)
        return cast(DailyRun, original_analysis_side_effect(topic_value, **kwargs))

    harness.analyze_execute.side_effect = analysis_side_effect

    result = _execute_pipeline(max_selected_papers=1)

    assert result.historical_analysis_run is historical_run
    assert result.historical_materialized_count == 1
    harness.backfill_execute.assert_called_once()
    assert harness.backfill_execute.call_args.kwargs["through"] == date(2026, 8, 10)
    harness.arxiv.get_papers_by_ids.assert_called_once_with(
        canonical_arxiv_ids=("2601.00001",),
    )
    materialization_call = harness.repository.persist_historical_arxiv_records.call_args
    assert materialization_call.kwargs["topic"] == _pipeline_topic()
    assert materialization_call.kwargs["records"] == representative_records
    assert materialization_call.kwargs["persisted_at"].tzinfo is not None
    historical_call = harness.analyze_execute.call_args_list[-1]
    assert historical_call.kwargs["paper_version_ids"] == (historical_version_id,)
    assert historical_call.kwargs["run_operation"] is RunOperation.HISTORICAL_ANALYSIS
    assert historical_call.kwargs["resume_existing"] is True


def test_unrelated_historical_analysis_failure_remains_on_its_child_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _configure_reused_pipeline(monkeypatch)
    historical_paper_id = UUID("a2bbd8a7-91d5-4aba-b4d3-c2c6cf9c7605")
    historical_version_id = UUID("efc89774-bb75-4570-9f37-6f2d6bc4ae7e")
    historical_run = _pipeline_run(
        run_id=UUID("d402977c-8087-4698-b789-ef7f848bfc66"),
        topic_id=harness.analysis.topic_id,
        operation=RunOperation.HISTORICAL_ANALYSIS,
        now=datetime(2026, 8, 10, 5, tzinfo=UTC),
        status=RunStatus.FAILED,
    )
    harness.repository.list_historical_representative_arxiv_ids.return_value = ("2601.00001",)
    harness.repository.list_historical_representative_version_ids.return_value = (
        historical_version_id,
    )
    original_analysis_side_effect = harness.analyze_execute.side_effect
    original_get_run = harness.repository.get_run.side_effect

    def get_run_side_effect(run_id: UUID) -> object | None:
        if run_id == historical_run.id:
            return SimpleNamespace(
                run=historical_run,
                items=(
                    _failed_item(
                        historical_paper_id,
                        historical_version_id,
                        error_code="ANALYSIS_MODEL_OUTPUT_INVALID",
                    ),
                ),
            )
        assert callable(original_get_run)
        return original_get_run(run_id)

    def analysis_side_effect(topic_value: SimpleNamespace, **kwargs: object) -> DailyRun:
        if kwargs["run_operation"] is RunOperation.HISTORICAL_ANALYSIS:
            return historical_run
        assert callable(original_analysis_side_effect)
        return cast(DailyRun, original_analysis_side_effect(topic_value, **kwargs))

    harness.repository.get_run.side_effect = get_run_side_effect
    harness.analyze_execute.side_effect = analysis_side_effect

    result = _execute_pipeline(max_selected_papers=1)

    assert result.historical_analysis_run is historical_run
    assert result.status is RunStatus.COMPLETE
    assert result.failures == ()
    assert harness.publication_execute.call_args.kwargs["upstream_failures"] == ()
    harness.repository.complete_pipeline_execution.assert_called_once_with(
        PIPELINE_EXECUTION_ID,
        status=RunStatus.COMPLETE,
        completed_at=ANY,
    )


def test_failed_historical_target_is_attributed_to_its_daily_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _configure_reused_pipeline(monkeypatch)
    source = harness.candidates[0]
    historical_paper_id = UUID("a2bbd8a7-91d5-4aba-b4d3-c2c6cf9c7605")
    target_version_id = UUID("efc89774-bb75-4570-9f37-6f2d6bc4ae7e")
    historical_run = _pipeline_run(
        run_id=UUID("d402977c-8087-4698-b789-ef7f848bfc66"),
        topic_id=harness.analysis.topic_id,
        operation=RunOperation.HISTORICAL_ANALYSIS,
        now=datetime(2026, 8, 10, 5, tzinfo=UTC),
        status=RunStatus.FAILED,
    )
    harness.repository.get_related_work.return_value = _related_detail((target_version_id,))
    harness.repository.get_comparison_paper_input.return_value = None
    original_analysis_side_effect = harness.analyze_execute.side_effect
    original_get_run = harness.repository.get_run.side_effect

    def get_run_side_effect(run_id: UUID) -> object | None:
        if run_id == historical_run.id:
            return SimpleNamespace(
                run=historical_run,
                items=(
                    _failed_item(
                        historical_paper_id,
                        target_version_id,
                        error_code="ANALYSIS_MODEL_OUTPUT_INVALID",
                    ),
                ),
            )
        assert callable(original_get_run)
        return original_get_run(run_id)

    def analysis_side_effect(topic_value: SimpleNamespace, **kwargs: object) -> DailyRun:
        if kwargs["run_operation"] is RunOperation.HISTORICAL_ANALYSIS:
            return historical_run
        assert callable(original_analysis_side_effect)
        return cast(DailyRun, original_analysis_side_effect(topic_value, **kwargs))

    harness.repository.get_run.side_effect = get_run_side_effect
    harness.analyze_execute.side_effect = analysis_side_effect

    result = _execute_pipeline(max_selected_papers=1)

    assert result.historical_analysis_run is historical_run
    assert tuple((failure.paper_id, failure.error_code) for failure in result.failures) == (
        (source.paper_id, "COMPARISON_INPUT_MISSING"),
    )
    upstream_failures = harness.publication_execute.call_args.kwargs["upstream_failures"]
    assert tuple(
        (failure.paper_id, failure.paper_version_id, failure.error_code)
        for failure in upstream_failures
    ) == (
        (
            source.paper_id,
            source.paper_version_id,
            "COMPARISON_INPUT_MISSING",
        ),
    )
    harness.compare_execute.assert_not_called()


@pytest.mark.parametrize("terminal_status", [RunStatus.COMPLETE, RunStatus.PARTIAL])
def test_terminal_historical_analysis_only_admits_existing_exact_scope_targets(
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: RunStatus,
) -> None:
    now = datetime(2026, 8, 10, 5, tzinfo=UTC)
    sources = (
        _selection_candidate(
            UUID("d8fdbf73-cf9a-487f-9b6a-237e13272d55"),
            UUID("1c27b53f-e172-469e-808f-33d0495968c0"),
            "2608.00001",
            now,
        ),
        _selection_candidate(
            UUID("6eddb148-a9a6-4e7f-93fb-22cd2a70ba87"),
            UUID("a6cf8ba8-a696-4e10-b46e-fda9d2023260"),
            "2608.00002",
            now,
        ),
    )
    harness = _configure_reused_pipeline(
        monkeypatch,
        max_selected_papers=2,
        candidates=sources,
    )
    historical_version_ids = (
        UUID("efc89774-bb75-4570-9f37-6f2d6bc4ae7e"),
        UUID("79d9ffbd-1bf4-4abd-a333-46764530b6e0"),
    )
    historical_run = replace(
        _pipeline_run(
            run_id=UUID("d402977c-8087-4698-b789-ef7f848bfc66"),
            topic_id=harness.analysis.topic_id,
            operation=RunOperation.HISTORICAL_ANALYSIS,
            now=now,
            status=terminal_status,
            selected_count=2,
            selection_limit=2,
        ),
        completed_count=1,
        failed_count=1,
    )
    reusable_version_id = UUID("6b392cdd-40a8-42c4-b09b-9e328b88f149")
    reusable_analysis_id = UUID("0da186f9-f834-40c7-a0ea-a5f36b6dc0c4")
    comparison_id = UUID("b3fe29eb-5379-43b6-8698-dc16e266ac73")

    def related_detail(
        *,
        session_id: UUID,
        candidate_id: UUID,
        local_version_id: UUID | None,
        arxiv_id: str,
    ) -> SimpleNamespace:
        base_item = _related_detail((reusable_version_id,)).items[0]
        candidate = replace(
            base_item.candidate,
            id=candidate_id,
            session_id=session_id,
            local_paper_id=(UUID(int=candidate_id.int + 1000) if local_version_id else None),
            local_paper_version_id=local_version_id,
        )
        return SimpleNamespace(
            session=SimpleNamespace(id=session_id),
            items=(
                SimpleNamespace(
                    candidate=candidate,
                    external_paper=SimpleNamespace(arxiv_id=arxiv_id),
                    comparison_id=None,
                ),
            ),
        )

    related_by_source = {
        sources[0].paper_id: related_detail(
            session_id=UUID("3ab53412-3ce5-4e90-90c8-b061fa5bdb07"),
            candidate_id=UUID("298ea349-e4f9-4fd1-951f-b91138844b78"),
            local_version_id=None,
            arxiv_id="2501.00001",
        ),
        sources[1].paper_id: related_detail(
            session_id=UUID("a4de1362-9f8e-420d-a7a5-5fe225300cf5"),
            candidate_id=UUID("94532240-7550-459e-b520-0029fb5d6f93"),
            local_version_id=reusable_version_id,
            arxiv_id="2501.00002",
        ),
    }

    def search_side_effect(**kwargs: object) -> SimpleNamespace:
        detail = related_by_source[cast(UUID, kwargs["source_paper_id"])]
        return SimpleNamespace(session=detail.session, candidates=())

    def related_work_side_effect(
        paper_id: UUID,
        **_kwargs: object,
    ) -> SimpleNamespace:
        return related_by_source[paper_id]

    def update_targets_side_effect(
        session_id: UUID,
        candidates: tuple[SearchCandidate, ...],
    ) -> None:
        source_id, detail = next(
            (paper_id, value)
            for paper_id, value in related_by_source.items()
            if value.session.id == session_id
        )
        updates = {candidate.id: candidate for candidate in candidates}
        related_by_source[source_id] = SimpleNamespace(
            session=detail.session,
            items=tuple(
                SimpleNamespace(
                    candidate=updates[item.candidate.id],
                    external_paper=item.external_paper,
                    comparison_id=item.comparison_id,
                )
                for item in detail.items
            ),
        )

    def comparison_input_side_effect(
        paper_version_id: UUID,
        **_kwargs: object,
    ) -> SimpleNamespace | None:
        if paper_version_id != reusable_version_id:
            return None
        return SimpleNamespace(
            analysis_id=reusable_analysis_id,
            analysis_scope=AnalysisScope.FULL_TEXT,
        )

    original_analysis_lookup = harness.repository.get_analysis_run_for_date.side_effect

    def analysis_lookup_side_effect(
        topic_id: UUID,
        logical_date: date,
        *,
        operation: RunOperation = RunOperation.STRUCTURED_ANALYSIS,
        pipeline_execution_id: UUID | None = None,
    ) -> DailyRun | None:
        if operation is RunOperation.HISTORICAL_ANALYSIS:
            return historical_run
        assert callable(original_analysis_lookup)
        return cast(
            DailyRun | None,
            original_analysis_lookup(
                topic_id,
                logical_date,
                operation=operation,
                pipeline_execution_id=pipeline_execution_id,
            ),
        )

    original_get_run = harness.repository.get_run.side_effect

    def get_run_side_effect(run_id: UUID) -> object | None:
        if run_id == historical_run.id:
            return SimpleNamespace(
                run=historical_run,
                items=(
                    _completed_item(sources[0].paper_id, historical_version_ids[0]),
                    _failed_item(
                        sources[1].paper_id,
                        historical_version_ids[1],
                        error_code="ANALYSIS_MODEL_OUTPUT_INVALID",
                    ),
                ),
            )
        assert callable(original_get_run)
        return original_get_run(run_id)

    harness.search_execute.side_effect = search_side_effect
    harness.repository.get_related_work.side_effect = related_work_side_effect
    harness.repository.update_search_comparison_targets.side_effect = update_targets_side_effect
    harness.repository.get_comparison_paper_input.side_effect = comparison_input_side_effect
    harness.repository.get_analysis_run_for_date.side_effect = analysis_lookup_side_effect
    harness.repository.get_run.side_effect = get_run_side_effect
    harness.compare_execute.return_value = SimpleNamespace(
        comparison=SimpleNamespace(id=comparison_id)
    )

    result = _execute_pipeline(max_selected_papers=2)

    assert result.historical_analysis_run is historical_run
    assert result.comparison_count == 1
    assert result.failures == ()
    assert harness.publication_execute.call_args.kwargs["comparison_ids"] == frozenset(
        {comparison_id}
    )
    assert harness.arxiv.get_papers_by_ids.call_count == 0
    historical_call = harness.analyze_execute.call_args_list[-1]
    assert historical_call.kwargs["paper_version_ids"] == historical_version_ids
    assert tuple(
        detail.items[0].candidate.comparison_target_decision
        for detail in related_by_source.values()
    ) == (
        ComparisonTargetDecision.NOT_TARGET,
        ComparisonTargetDecision.TARGET,
    )


def _configure_reused_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    analysis_status: RunStatus = RunStatus.COMPLETE,
    max_selected_papers: int = 1,
    candidates: tuple[runtime_module.DailySelectionCandidate, ...] | None = None,
    analysis_items: tuple[SimpleNamespace, ...] | None = None,
    embeddings: object | None = None,
    execution_mode: PipelineExecutionMode = PipelineExecutionMode.NORMAL,
    pipeline_execution_id: UUID = PIPELINE_EXECUTION_ID,
) -> SimpleNamespace:
    now = datetime(2026, 8, 10, 5, tzinfo=UTC)
    topic = _pipeline_topic()
    expected_pipeline_execution_id = pipeline_execution_id
    candidates = candidates or (
        _selection_candidate(
            UUID("d8fdbf73-cf9a-487f-9b6a-237e13272d55"),
            UUID("1c27b53f-e172-469e-808f-33d0495968c0"),
            "2608.00001",
            now,
        ),
    )
    analysis_items = analysis_items or tuple(
        _completed_item(candidate.paper_id, candidate.paper_version_id) for candidate in candidates
    )
    ingestion = _pipeline_run(
        run_id=UUID("04a6195a-4267-4d72-b882-16fa95acbc12"),
        topic_id=topic.id,
        operation=RunOperation.ARXIV_INGESTION,
        now=now,
        selection_limit=max_selected_papers,
        execution_mode=execution_mode,
        pipeline_execution_id=pipeline_execution_id,
    )
    analysis = _pipeline_run(
        run_id=UUID("a3069769-e9af-43aa-9b51-2d1863ef453f"),
        topic_id=topic.id,
        operation=RunOperation.STRUCTURED_ANALYSIS,
        now=now,
        status=analysis_status,
        selected_count=len(candidates),
        selection_limit=max_selected_papers,
        execution_mode=execution_mode,
        pipeline_execution_id=pipeline_execution_id,
    )
    product = _pipeline_run(
        run_id=UUID("7b9eb955-e227-4f97-a5f8-3956552fd7da"),
        topic_id=topic.id,
        operation=RunOperation.PRODUCT_PUBLICATION,
        now=now,
        source_run_id=analysis.id,
        selected_count=len(candidates),
        selection_limit=max_selected_papers,
        execution_mode=execution_mode,
        pipeline_execution_id=pipeline_execution_id,
    )
    repository = MagicMock()

    def start_execution_stub(execution: PipelineExecution) -> PipelineExecution:
        return execution

    repository.start_pipeline_execution.side_effect = start_execution_stub
    run_state = {"ingestion": ingestion, "analysis": analysis}

    def get_ingestion_run_stub(
        _topic_id: UUID,
        _logical_date: date,
        *,
        pipeline_execution_id: UUID | None = None,
    ) -> DailyRun:
        assert pipeline_execution_id in (None, expected_pipeline_execution_id)
        return run_state["ingestion"]

    def get_analysis_run_stub(
        _topic_id: UUID,
        _logical_date: date,
        *,
        operation: RunOperation = RunOperation.STRUCTURED_ANALYSIS,
        pipeline_execution_id: UUID | None = None,
    ) -> DailyRun | None:
        assert pipeline_execution_id == expected_pipeline_execution_id
        return None if operation is RunOperation.HISTORICAL_ANALYSIS else run_state["analysis"]

    repository.get_run_for_date.side_effect = get_ingestion_run_stub
    repository.get_analysis_run_for_date.side_effect = get_analysis_run_stub
    repository.get_product_run_for_date.return_value = None
    repository.get_comparison_paper_input.return_value = SimpleNamespace(
        analysis_id=UUID("0da186f9-f834-40c7-a0ea-a5f36b6dc0c4"),
        analysis_scope=AnalysisScope.FULL_TEXT,
    )
    repository.get_analyzed_paper_version_ids.return_value = ()
    repository.get_reusable_analyzed_paper_version_ids.return_value = frozenset()
    repository.list_historical_representative_arxiv_ids.return_value = ()
    repository.list_historical_representative_version_ids.return_value = ()
    details = {
        ingestion.id: SimpleNamespace(
            run=ingestion,
            items=tuple(
                _completed_item(
                    candidate.paper_id,
                    candidate.paper_version_id,
                    stage=PaperStage.NORMALIZED,
                )
                for candidate in candidates
            ),
        ),
        analysis.id: SimpleNamespace(run=analysis, items=analysis_items),
        product.id: SimpleNamespace(run=product, items=()),
    }
    repository.get_run.side_effect = details.get
    publication_papers = tuple(
        SimpleNamespace(
            paper_id=candidate.paper_id,
            paper_version_id=candidate.paper_version_id,
            comparisons=(),
            analysis=SimpleNamespace(
                analysis=SimpleNamespace(
                    id=UUID(int=index + 1),
                    analysis_scope=AnalysisScope.FULL_TEXT,
                )
            ),
        )
        for index, candidate in enumerate(candidates)
    )
    repository.get_product_publication_input.return_value = SimpleNamespace(
        papers=publication_papers
    )
    empty_related = SimpleNamespace(
        session=SimpleNamespace(id=UUID("cdd83223-d4c2-477f-b62f-c5d1e5675889")),
        items=(),
    )
    repository.get_related_work.return_value = empty_related

    def ingest_execute_stub(topic_value: SimpleNamespace, **kwargs: object) -> DailyRun:
        run = repository.get_run_for_date(topic_value.id, kwargs["logical_date"])
        if (
            run.pipeline_execution_mode is not kwargs["pipeline_execution_mode"]
            or run.pipeline_execution_id != kwargs["pipeline_execution_id"]
        ):
            raise IngestionResumeError(
                "persisted arXiv ingestion provenance does not match the requested pipeline"
            )
        return run

    def analyze_execute_stub(topic_value: SimpleNamespace, **kwargs: object) -> DailyRun:
        run = repository.get_analysis_run_for_date(
            topic_value.id,
            kwargs["logical_date"],
            operation=kwargs["run_operation"],
            pipeline_execution_id=kwargs["pipeline_execution_id"],
        )
        assert run is not None
        if (
            run.pipeline_execution_mode is not kwargs["pipeline_execution_mode"]
            or run.pipeline_execution_id != kwargs["pipeline_execution_id"]
            or run.analysis_scope is not kwargs["analysis_scope"]
        ):
            raise AnalysisResumeError(
                "persisted analysis provenance does not match the requested pipeline"
            )
        return run

    ingest_execute = MagicMock(side_effect=ingest_execute_stub)
    ingest_constructor = MagicMock(return_value=SimpleNamespace(execute=ingest_execute))
    analyze_execute = MagicMock(side_effect=analyze_execute_stub)
    analyze_constructor = MagicMock(return_value=SimpleNamespace(execute=analyze_execute))
    historical = MagicMock(spec=HistoricalBackfillRun)
    backfill_execute = MagicMock(return_value=historical)
    backfill_constructor = MagicMock(return_value=SimpleNamespace(execute=backfill_execute))
    search_execute = MagicMock(
        return_value=SimpleNamespace(
            session=SimpleNamespace(id=UUID("cdd83223-d4c2-477f-b62f-c5d1e5675889")),
            candidates=(),
        )
    )
    search_constructor = MagicMock(return_value=SimpleNamespace(execute=search_execute))
    compare_execute = MagicMock(return_value=object())
    compare_constructor = MagicMock(return_value=SimpleNamespace(execute=compare_execute))
    publication_execute = MagicMock(return_value=product)
    publication_constructor = MagicMock(return_value=SimpleNamespace(execute=publication_execute))
    embedding_value = embeddings if embeddings is not None else _embedding_stub()
    embedding_loader = MagicMock(return_value=embedding_value)

    def load_topic_stub(_path: Path) -> SimpleNamespace:
        return topic

    def parser_stub(_scope: AnalysisScope) -> object:
        return object()

    def repository_stub(_operation: str) -> MagicMock:
        return repository

    def selection_candidates_stub(
        _repository: object,
        _detail: object,
    ) -> tuple[runtime_module.DailySelectionCandidate, ...]:
        return candidates

    monkeypatch.setattr(runtime_module, "load_topic_config", load_topic_stub)
    monkeypatch.setattr(
        runtime_module.DeepSeekSettings,
        "from_environment",
        staticmethod(lambda: SimpleNamespace(provider="deepseek", model="deepseek-v4-flash")),
    )
    monkeypatch.setattr(
        runtime_module.SemanticScholarSettings,
        "from_environment",
        staticmethod(lambda: object()),
    )
    monkeypatch.setattr(runtime_module, "_grobid_parser", parser_stub)
    monkeypatch.setattr(runtime_module, "_ready_repository", repository_stub)
    monkeypatch.setattr(runtime_module, "_specter2_embeddings", embedding_loader)
    arxiv = MagicMock()
    monkeypatch.setattr(runtime_module, "ArxivClient", MagicMock(return_value=arxiv))
    monkeypatch.setattr(runtime_module, "DeepSeekClient", MagicMock(return_value=object()))
    monkeypatch.setattr(
        runtime_module,
        "SemanticScholarClient",
        MagicMock(return_value=object()),
    )
    monkeypatch.setattr(runtime_module, "_selection_candidates", selection_candidates_stub)
    monkeypatch.setattr(runtime_module, "IngestArxiv", ingest_constructor)
    monkeypatch.setattr(runtime_module, "AnalyzePapers", analyze_constructor)
    monkeypatch.setattr(runtime_module, "HistoricalBackfill", backfill_constructor)
    monkeypatch.setattr(runtime_module, "RelatedWorkSearch", search_constructor)
    monkeypatch.setattr(runtime_module, "ComparePapers", compare_constructor)
    monkeypatch.setattr(runtime_module, "PublishProduct", publication_constructor)
    return SimpleNamespace(
        repository=repository,
        ingestion=ingestion,
        analysis=analysis,
        product=product,
        ingest_constructor=ingest_constructor,
        ingest_execute=ingest_execute,
        analyze_constructor=analyze_constructor,
        analyze_execute=analyze_execute,
        backfill_constructor=backfill_constructor,
        backfill_execute=backfill_execute,
        search_constructor=search_constructor,
        search_execute=search_execute,
        compare_constructor=compare_constructor,
        compare_execute=compare_execute,
        publication_constructor=publication_constructor,
        publication_execute=publication_execute,
        embedding_loader=embedding_loader,
        arxiv=arxiv,
        candidates=candidates,
        run_state=run_state,
    )


def _execute_pipeline(
    *,
    max_selected_papers: int,
    reprocess: bool = False,
) -> runtime_module.DailyPipelineResult:
    return execute_daily_pipeline(
        topic_config=Path("unused.yaml"),
        logical_date=date(2026, 8, 10),
        analysis_scope=AnalysisScope.FULL_TEXT,
        narrative_mode=ReportNarrativeMode.DEEPSEEK,
        max_selected_papers=max_selected_papers,
        reprocess=reprocess,
    )


def _pipeline_topic() -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID("4b7db6d4-349c-5c06-bc41-f84091580fcb"),
        slug="broad-llm-agents",
        name="Broad LLM Agents",
        description="Broad LLM-agent research.",
        categories=("cs.AI",),
        include_terms=("agent",),
        exclude_terms=(),
        overlap_hours=48,
        initial_lookback_days=7,
        max_results=500,
        representative_full_text_count=200,
    )


def _selection_candidate(
    paper_id: UUID,
    version_id: UUID,
    canonical_arxiv_id: str,
    updated_at: datetime,
) -> runtime_module.DailySelectionCandidate:
    return runtime_module.DailySelectionCandidate(
        paper_id=paper_id,
        paper_version_id=version_id,
        canonical_arxiv_id=canonical_arxiv_id,
        title="LLM agent planning",
        abstract="A language model agent with tools.",
        categories=("cs.AI",),
        updated_at=updated_at,
    )


def _completed_item(
    paper_id: UUID,
    paper_version_id: UUID,
    *,
    stage: PaperStage = PaperStage.EVIDENCE_EXTRACTED,
) -> SimpleNamespace:
    return SimpleNamespace(
        item=SimpleNamespace(
            paper_id=paper_id,
            paper_version_id=paper_version_id,
            stage=stage,
            status=RunItemStatus.COMPLETED,
        )
    )


def _failed_item(
    paper_id: UUID,
    paper_version_id: UUID,
    *,
    error_code: str,
    failed_stage: PaperStage = PaperStage.ANALYZED,
    retryable: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        item=SimpleNamespace(
            paper_id=paper_id,
            paper_version_id=paper_version_id,
            stage=PaperStage.SELECTED,
            status=RunItemStatus.FAILED,
            failed_stage=failed_stage,
            error_code=error_code,
            retryable=retryable,
            error_detail="Persisted selected-paper failure.",
        )
    )


def _related_detail(target_version_ids: tuple[UUID, ...]) -> SimpleNamespace:
    session_id = UUID("cdd83223-d4c2-477f-b62f-c5d1e5675889")
    return SimpleNamespace(
        session=SimpleNamespace(id=session_id),
        items=tuple(
            SimpleNamespace(
                candidate=SearchCandidate(
                    id=UUID(int=index + 100),
                    session_id=session_id,
                    external_paper_id=UUID(int=index + 300),
                    semantic_scholar_id=f"semantic-{index}",
                    rank=index,
                    decision=SelectionDecision.SELECTED,
                    decision_reason="Selected test candidate.",
                    local_paper_id=UUID(int=index + 200),
                    local_paper_version_id=version_id,
                    discovered_by_action_id=UUID(int=index + 400),
                    origins=(CandidateOrigin.SEARCH,),
                    relation_depth=0,
                    scores=CandidateScoreComponents(semantic_scholar=1.0, final=1.0),
                    provider="deepseek",
                    configured_model="deepseek-v4-flash",
                    model_version="deepseek-v4-flash",
                    prompt_version="selector-v1",
                    generated_at=datetime(2026, 8, 10, tzinfo=UTC),
                    verification_status=VerificationStatus.UNVERIFIED,
                    schema_version=1,
                    created_at=datetime(2026, 8, 10, tzinfo=UTC),
                    comparison_target_decision=ComparisonTargetDecision.TARGET,
                    comparison_target_reason="Persisted bounded test target.",
                ),
                external_paper=SimpleNamespace(arxiv_id=f"2501.{index:05d}"),
                comparison_id=None,
            )
            for index, version_id in enumerate(target_version_ids, start=1)
        ),
    )


def _all_versions_reusable(
    version_ids: tuple[UUID, ...],
    **_kwargs: object,
) -> frozenset[UUID]:
    return frozenset(version_ids)


def _pipeline_run(
    *,
    run_id: UUID,
    topic_id: UUID,
    operation: RunOperation,
    now: datetime,
    source_run_id: UUID | None = None,
    status: RunStatus = RunStatus.COMPLETE,
    selected_count: int = 1,
    execution_mode: PipelineExecutionMode = PipelineExecutionMode.NORMAL,
    selection_limit: int = 1,
    pipeline_execution_id: UUID = PIPELINE_EXECUTION_ID,
) -> DailyRun:
    is_ingestion = operation is RunOperation.ARXIV_INGESTION
    is_analysis = operation in (
        RunOperation.STRUCTURED_ANALYSIS,
        RunOperation.HISTORICAL_ANALYSIS,
    )
    return DailyRun(
        id=run_id,
        topic_id=topic_id,
        source_run_id=source_run_id,
        logical_date=now.date(),
        operation=operation,
        analysis_scope=AnalysisScope.FULL_TEXT if is_analysis else None,
        status=status,
        started_at=now,
        completed_at=now,
        cursor_from=now if is_ingestion else None,
        cursor_to=now if is_ingestion else None,
        discovered_count=1 if is_ingestion else 0,
        normalized_count=1 if is_ingestion else 0,
        selected_count=selected_count if not is_ingestion else 0,
        completed_count=(
            selected_count if not is_ingestion and status is not RunStatus.FAILED else 0
        ),
        failed_count=(selected_count if not is_ingestion and status is RunStatus.FAILED else 0),
        error_code="RUN_FAILED" if status is RunStatus.FAILED else None,
        error_detail="persisted child run failed" if status is RunStatus.FAILED else None,
        schema_version=1,
        created_at=now,
        pipeline_execution_mode=execution_mode,
        pipeline_selection_limit=selection_limit,
        pipeline_execution_id=pipeline_execution_id,
    )
