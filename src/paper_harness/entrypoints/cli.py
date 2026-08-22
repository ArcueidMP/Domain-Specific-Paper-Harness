"""Protected operator CLI; the API intentionally has no run endpoint."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from time import monotonic
from typing import Annotated
from uuid import UUID

import typer

from paper_harness.application.compare_papers import ComparisonInputMissingError
from paper_harness.application.generate_periodic_report import (
    PeriodicReportInsufficientDataError,
)
from paper_harness.application.historical_backfill import HistoricalBackfillTimeoutError
from paper_harness.application.pipeline_budget import DEFAULT_PIPELINE_TIMEOUT_SECONDS
from paper_harness.application.publish_product import (
    ProductGraphError,
    ProductInputMissingError,
    ProductReportError,
    ProductTrendError,
)
from paper_harness.application.related_work import RelatedWorkInputError
from paper_harness.application.reporting import ReportNarrativeModeConflictError
from paper_harness.domain.analysis import AnalysisScope
from paper_harness.domain.errors import DomainInvariantError, DuplicateDailyRunError
from paper_harness.domain.historical import SearchLimits, SelectionDecision
from paper_harness.domain.models import PipelineExecutionMode, RunStatus
from paper_harness.domain.reports import ReportNarrativeMode, ReportType
from paper_harness.entrypoints.runtime import (
    DailyPipelineDeadlineExceededError,
    DailyPipelineFailure,
    DailyPipelineRunFailedError,
    DailyPipelineSelectionError,
    execute_arxiv_ingestion,
    execute_daily_pipeline,
    execute_historical_backfill,
    execute_paper_comparison,
    execute_periodic_report,
    execute_product_publication,
    execute_related_work_search,
    execute_structured_analysis,
)
from paper_harness.ports.arxiv import ArxivPortError
from paper_harness.ports.llm import LLMPortError
from paper_harness.ports.pdf_parser import PdfParserPortError
from paper_harness.ports.repository import RepositoryError
from paper_harness.ports.scholarly_search import ScholarlySearchError
from paper_harness.ports.scientific_embedding import ScientificEmbeddingPortError

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

_EXHAUSTED_DEPENDENCY_BY_ERROR_CODE = {
    "ARXIV_UNAVAILABLE": "arxiv",
    "LLM_UNAVAILABLE": "deepseek",
    "PDF_PARSER_UNAVAILABLE": "grobid",
    "SCHOLARLY_SEARCH_UNAVAILABLE": "semantic_scholar",
    "SCIENTIFIC_EMBEDDING_UNAVAILABLE": "specter2",
}


def _exhausted_external_dependency(error: BaseException) -> str | None:
    if not bool(getattr(error, "retryable", False)):
        return None
    for error_type, dependency in (
        (ArxivPortError, "arxiv"),
        (LLMPortError, "deepseek"),
        (PdfParserPortError, "grobid"),
        (ScholarlySearchError, "semantic_scholar"),
        (ScientificEmbeddingPortError, "specter2"),
    ):
        if isinstance(error, error_type):
            return dependency
    return None


def _emit_external_dependency_exhaustion_events(
    *,
    failures: tuple[DailyPipelineFailure, ...] = (),
    error: BaseException | None = None,
) -> None:
    affected_item_counts: dict[tuple[str, str], int] = {}
    affected_stages: dict[tuple[str, str], set[str]] = {}
    for failure in failures:
        dependency = (
            _EXHAUSTED_DEPENDENCY_BY_ERROR_CODE.get(failure.error_code)
            if failure.retryable
            else None
        )
        if dependency is None:
            continue
        key = (dependency, failure.error_code)
        affected_item_counts[key] = affected_item_counts.get(key, 0) + 1
        affected_stages.setdefault(key, set()).add(failure.stage)

    if error is not None:
        dependency = _exhausted_external_dependency(error)
        if dependency is not None:
            error_code = str(getattr(error, "error_code", "DEPENDENCY_UNAVAILABLE"))
            affected_item_counts.setdefault((dependency, error_code), 0)
            affected_stages.setdefault((dependency, error_code), set())

    for dependency, error_code in sorted(affected_item_counts):
        payload: dict[str, object] = {
            "level": "WARNING",
            "event": "external_dependency_exhausted",
            "dependency": dependency,
            "error_code": error_code,
            "retryable": True,
        }
        affected_item_count = affected_item_counts[(dependency, error_code)]
        if affected_item_count:
            payload["affected_item_count"] = affected_item_count
            payload["stages"] = sorted(affected_stages[(dependency, error_code)])
        typer.echo(json.dumps(payload, separators=(",", ":")), err=True)


@app.callback()
def _root() -> None:
    """Operate Domain-Specific Paper Harness outside the read-only API."""


@app.command("run-pipeline")
def run_pipeline(
    topic_config: Annotated[
        Path,
        typer.Option(
            "--topic-config",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            envvar="TOPIC_CONFIG_PATH",
        ),
    ] = Path("configs/topics/broad-llm-agents.yaml"),
    logical_date: Annotated[
        str | None,
        typer.Option(
            "--logical-date",
            help="Logical run date in YYYY-MM-DD format.",
            envvar="PIPELINE_LOGICAL_DATE",
        ),
    ] = None,
    reprocess: Annotated[
        bool,
        typer.Option(
            "--reprocess",
            help="Run a fresh publishable revision for an already processed logical date.",
            envvar="PIPELINE_REPROCESS",
        ),
    ] = False,
    analysis_scope: Annotated[
        str,
        typer.Option(
            "--analysis-scope",
            help="Explicitly select full_text or abstract_only before execution.",
            envvar="ANALYSIS_MODE",
        ),
    ] = "full_text",
    narrative_mode: Annotated[
        str,
        typer.Option("--narrative-mode", help="Select deepseek or structured_only."),
    ] = "deepseek",
    max_selected_papers: Annotated[
        int,
        typer.Option("--max-selected-papers", min=1, max=200),
    ] = 10,
    backfill_max_queries: Annotated[
        int,
        typer.Option("--backfill-max-queries", min=1, max=40),
    ] = 8,
    backfill_per_query_limit: Annotated[
        int,
        typer.Option("--backfill-per-query-limit", min=1, max=500),
    ] = 100,
    backfill_timeout_seconds: Annotated[
        float,
        typer.Option("--backfill-timeout-seconds", min=1, max=7200),
    ] = 1800,
    max_search_steps: Annotated[
        int,
        typer.Option("--max-search-steps", min=1, max=100),
    ] = 12,
    max_search_queries: Annotated[
        int,
        typer.Option("--max-search-queries", min=1, max=40),
    ] = 4,
    max_search_queue_size: Annotated[
        int,
        typer.Option("--max-search-queue-size", min=1, max=2000),
    ] = 100,
    max_citation_depth: Annotated[
        int,
        typer.Option("--max-citation-depth", min=0, max=5),
    ] = 2,
    max_search_candidates: Annotated[
        int,
        typer.Option("--max-search-candidates", min=1, max=5000),
    ] = 100,
    max_selected_candidates: Annotated[
        int,
        typer.Option("--max-selected-candidates", min=1, max=100),
    ] = 5,
    search_operation_timeout_seconds: Annotated[
        float,
        typer.Option("--search-operation-timeout-seconds", min=1, max=600),
    ] = 60,
    search_overall_timeout_seconds: Annotated[
        float,
        typer.Option("--search-overall-timeout-seconds", min=1, max=3600),
    ] = 300,
    max_comparisons_per_paper: Annotated[
        int,
        typer.Option("--max-comparisons-per-paper", min=1, max=10),
    ] = 3,
    pipeline_timeout_seconds: Annotated[
        int,
        typer.Option("--pipeline-timeout-seconds", min=1, max=86_400),
    ] = DEFAULT_PIPELINE_TIMEOUT_SECONDS,
) -> None:
    """Run ingestion through atomic product publication as one bounded Daily Job."""

    command_started = monotonic()
    try:
        parsed_date = None if logical_date is None else date.fromisoformat(logical_date)
        parsed_scope = AnalysisScope(analysis_scope.strip().upper())
        parsed_narrative_mode = ReportNarrativeMode(narrative_mode.strip().upper())
        execution_mode = (
            PipelineExecutionMode.REPROCESS if reprocess else PipelineExecutionMode.NORMAL
        )
        limits = SearchLimits(
            max_steps=max_search_steps,
            max_queries=max_search_queries,
            max_queue_size=max_search_queue_size,
            max_citation_depth=max_citation_depth,
            max_candidates=max_search_candidates,
            max_selected_candidates=max_selected_candidates,
            per_operation_timeout_seconds=search_operation_timeout_seconds,
            overall_timeout_seconds=search_overall_timeout_seconds,
        )
        typer.echo(
            json.dumps(
                {
                    "level": "INFO",
                    "event": "daily_job_started",
                    "logical_date": None if parsed_date is None else parsed_date.isoformat(),
                    "execution_mode": execution_mode.value,
                    "analysis_scope": parsed_scope.value,
                    "narrative_mode": parsed_narrative_mode.value,
                    "max_selected_papers": max_selected_papers,
                    "max_search_steps": limits.max_steps,
                    "max_search_candidates": limits.max_candidates,
                },
                separators=(",", ":"),
            )
        )
        result = execute_daily_pipeline(
            topic_config=topic_config,
            logical_date=parsed_date,
            analysis_scope=parsed_scope,
            narrative_mode=parsed_narrative_mode,
            max_selected_papers=max_selected_papers,
            reprocess=reprocess,
            backfill_max_queries=backfill_max_queries,
            backfill_per_query_limit=backfill_per_query_limit,
            backfill_timeout_seconds=backfill_timeout_seconds,
            search_limits=limits,
            max_comparisons_per_paper=max_comparisons_per_paper,
            pipeline_timeout_seconds=pipeline_timeout_seconds,
        )
    except (
        ValueError,
        OSError,
        DomainInvariantError,
        DailyPipelineSelectionError,
        DailyPipelineRunFailedError,
        DailyPipelineDeadlineExceededError,
        HistoricalBackfillTimeoutError,
        RelatedWorkInputError,
        ComparisonInputMissingError,
        ProductInputMissingError,
        ProductGraphError,
        ProductTrendError,
        ProductReportError,
        ReportNarrativeModeConflictError,
        ArxivPortError,
        LLMPortError,
        PdfParserPortError,
        ScholarlySearchError,
        ScientificEmbeddingPortError,
        RepositoryError,
        DuplicateDailyRunError,
    ) as error:
        _emit_external_dependency_exhaustion_events(
            failures=(error.failures if isinstance(error, DailyPipelineRunFailedError) else ()),
            error=error,
        )
        typer.echo(
            json.dumps(
                {
                    "level": "ERROR",
                    "event": "daily_job_failed",
                    "error_code": getattr(error, "error_code", "DAILY_PIPELINE_FAILED"),
                    "retryable": bool(getattr(error, "retryable", False)),
                    "detail": str(error)[:1000],
                    "duration_ms": max(0, round((monotonic() - command_started) * 1000)),
                },
                separators=(",", ":"),
            ),
            err=True,
        )
        raise typer.Exit(code=1) from error

    _emit_external_dependency_exhaustion_events(failures=result.failures)
    level = (
        "ERROR"
        if result.status is RunStatus.FAILED
        else "WARNING"
        if result.status is RunStatus.PARTIAL
        else "INFO"
    )
    typer.echo(
        json.dumps(
            {
                "pipeline_execution_id": (
                    None
                    if result.product_run.pipeline_execution_id is None
                    else str(result.product_run.pipeline_execution_id)
                ),
                "publication_run_id": str(result.product_run.id),
                "logical_date": result.product_run.logical_date.isoformat(),
                "execution_mode": result.product_run.pipeline_execution_mode.value,
                "status": result.status.value,
                "publication_status": result.product_run.status.value,
                "level": level,
                "event": "daily_job_finished",
                "ingestion_run_id": str(result.ingestion_run.id),
                "analysis_run_id": str(result.analysis_run.id),
                "historical_analysis_run_id": (
                    None
                    if result.historical_analysis_run is None
                    else str(result.historical_analysis_run.id)
                ),
                "historical_backfill_id": str(result.historical_backfill.id),
                "evaluated_count": result.evaluated_count,
                "relevant_count": result.relevant_count,
                "selected_count": result.selected_count,
                "completed_count": result.product_run.completed_count,
                "failed_count": result.product_run.failed_count,
                "search_session_count": result.search_session_count,
                "comparison_count": result.comparison_count,
                "historical_materialized_count": result.historical_materialized_count,
                "external_call_count_lower_bound": (
                    0
                    if result.accounting is None
                    else result.accounting.external_call_count_lower_bound
                ),
                "arxiv_operation_count": (
                    0 if result.accounting is None else result.accounting.arxiv_operation_count
                ),
                "semantic_scholar_operation_count": (
                    0
                    if result.accounting is None
                    else result.accounting.semantic_scholar_operation_count
                ),
                "grobid_api_call_count": (
                    0 if result.accounting is None else result.accounting.grobid_api_call_count
                ),
                "model_api_call_count": (
                    0 if result.accounting is None else result.accounting.model_api_call_count
                ),
                "model_prompt_tokens": (
                    0 if result.accounting is None else result.accounting.prompt_tokens
                ),
                "model_completion_tokens": (
                    0 if result.accounting is None else result.accounting.completion_tokens
                ),
                "model_total_tokens": (
                    0 if result.accounting is None else result.accounting.total_tokens
                ),
                "model_duration_ms": (
                    0 if result.accounting is None else result.accounting.model_duration_ms
                ),
                "estimated_cost_usd": (
                    None
                    if result.accounting is None or result.accounting.estimated_cost_usd is None
                    else str(result.accounting.estimated_cost_usd)
                ),
                "duration_ms": result.duration_ms,
                "item_failures": [
                    {
                        "paper_id": str(failure.paper_id),
                        "stage": failure.stage,
                        "error_code": failure.error_code,
                        "retryable": failure.retryable,
                    }
                    for failure in result.failures
                ],
            },
            separators=(",", ":"),
        ),
        err=level in {"WARNING", "ERROR"},
    )
    if result.status is RunStatus.FAILED:
        raise typer.Exit(code=1)


@app.command("ingest-arxiv")
def ingest_arxiv(
    topic_config: Annotated[
        Path,
        typer.Option(
            "--topic-config",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            envvar="TOPIC_CONFIG_PATH",
        ),
    ] = Path("configs/topics/broad-llm-agents.yaml"),
    logical_date: Annotated[
        str | None,
        typer.Option("--logical-date", help="Logical run date in YYYY-MM-DD format."),
    ] = None,
) -> None:
    """Run real, bounded, version-aware arXiv ingestion."""

    try:
        parsed_logical_date = None if logical_date is None else date.fromisoformat(logical_date)
        run = execute_arxiv_ingestion(topic_config=topic_config, logical_date=parsed_logical_date)
    except (ValueError, OSError, ArxivPortError, RepositoryError, DuplicateDailyRunError) as error:
        typer.echo(
            json.dumps(
                {"level": "ERROR", "event": "arxiv_ingestion_failed", "detail": str(error)},
                separators=(",", ":"),
            ),
            err=True,
        )
        raise typer.Exit(code=1) from error
    typer.echo(
        json.dumps(
            {
                "level": "INFO",
                "event": "arxiv_ingestion_completed",
                "run_id": str(run.id),
                "status": run.status.value,
                "discovered_count": run.discovered_count,
                "normalized_count": run.normalized_count,
                "failed_count": run.failed_count,
            },
            separators=(",", ":"),
        )
    )


@app.command("publish-product")
def publish_product(
    topic_config: Annotated[
        Path,
        typer.Option(
            "--topic-config",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            envvar="TOPIC_CONFIG_PATH",
        ),
    ] = Path("configs/topics/broad-llm-agents.yaml"),
    logical_date: Annotated[
        str | None,
        typer.Option("--logical-date", help="Logical publication date in YYYY-MM-DD format."),
    ] = None,
    narrative_mode: Annotated[
        str,
        typer.Option(
            "--narrative-mode",
            help="Use deepseek or the explicit deterministic structured_only mode.",
        ),
    ] = "deepseek",
) -> None:
    """Publish the persisted M2/M3 corpus as the M4 daily product."""

    try:
        parsed_date = None if logical_date is None else date.fromisoformat(logical_date)
        parsed_mode = ReportNarrativeMode(narrative_mode.strip().upper())
        run = execute_product_publication(
            topic_config=topic_config,
            logical_date=parsed_date,
            narrative_mode=parsed_mode,
        )
    except (
        ValueError,
        OSError,
        DomainInvariantError,
        DuplicateDailyRunError,
        ProductInputMissingError,
        ProductGraphError,
        ProductTrendError,
        ProductReportError,
        ReportNarrativeModeConflictError,
        LLMPortError,
        RepositoryError,
    ) as error:
        typer.echo(
            json.dumps(
                {"level": "ERROR", "event": "product_publication_failed", "detail": str(error)},
                separators=(",", ":"),
            ),
            err=True,
        )
        raise typer.Exit(code=1) from error
    level = (
        "ERROR"
        if run.status is RunStatus.FAILED
        else "WARNING"
        if run.status is RunStatus.PARTIAL
        else "INFO"
    )
    event = (
        "product_publication_failed"
        if run.status is RunStatus.FAILED
        else "product_publication_partial"
        if run.status is RunStatus.PARTIAL
        else "product_publication_completed"
    )
    typer.echo(
        json.dumps(
            {
                "level": level,
                "event": event,
                "run_id": str(run.id),
                "status": run.status.value,
                "completed_count": run.completed_count,
                "failed_count": run.failed_count,
            },
            separators=(",", ":"),
        ),
        err=run.status in (RunStatus.PARTIAL, RunStatus.FAILED),
    )
    if run.status is RunStatus.FAILED:
        raise typer.Exit(code=1)


@app.command("generate-periodic-report")
def generate_periodic_report(
    report_type: Annotated[
        str,
        typer.Option("--report-type", help="Eligible aggregate scope: weekly or monthly."),
    ],
    period_start: Annotated[
        str,
        typer.Option("--period-start", help="Inclusive period start in YYYY-MM-DD format."),
    ],
    period_end: Annotated[
        str,
        typer.Option("--period-end", help="Inclusive period end in YYYY-MM-DD format."),
    ],
    topic_config: Annotated[
        Path,
        typer.Option(
            "--topic-config",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            envvar="TOPIC_CONFIG_PATH",
        ),
    ] = Path("configs/topics/broad-llm-agents.yaml"),
    narrative_mode: Annotated[
        str,
        typer.Option(
            "--narrative-mode",
            help="Use deepseek or the explicit deterministic structured_only mode.",
        ),
    ] = "deepseek",
) -> None:
    """Generate an eligible weekly or monthly report from persisted daily reports."""

    try:
        parsed_type = ReportType(report_type.strip().upper())
        parsed_mode = ReportNarrativeMode(narrative_mode.strip().upper())
        report = execute_periodic_report(
            topic_config=topic_config,
            report_type=parsed_type,
            period_start=date.fromisoformat(period_start),
            period_end=date.fromisoformat(period_end),
            narrative_mode=parsed_mode,
        )
    except (
        ValueError,
        OSError,
        DomainInvariantError,
        PeriodicReportInsufficientDataError,
        ReportNarrativeModeConflictError,
        LLMPortError,
        RepositoryError,
    ) as error:
        typer.echo(
            json.dumps(
                {"level": "ERROR", "event": "periodic_report_failed", "detail": str(error)},
                separators=(",", ":"),
            ),
            err=True,
        )
        raise typer.Exit(code=1) from error
    typer.echo(
        json.dumps(
            {
                "level": "INFO",
                "event": "periodic_report_completed",
                "report_id": str(report.id),
                "report_type": report.report_type.value,
                "period_start": report.period_start.isoformat() if report.period_start else None,
                "period_end": report.period_end.isoformat() if report.period_end else None,
            },
            separators=(",", ":"),
        )
    )


@app.command("analyze-papers")
def analyze_papers(
    paper_ids: Annotated[
        list[UUID],
        typer.Option(
            "--paper-id",
            help="Selected persisted paper UUID. Repeat for multiple papers.",
        ),
    ],
    topic_config: Annotated[
        Path,
        typer.Option(
            "--topic-config",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            envvar="TOPIC_CONFIG_PATH",
        ),
    ] = Path("configs/topics/broad-llm-agents.yaml"),
    analysis_scope: Annotated[
        str,
        typer.Option(
            "--analysis-scope",
            help="Explicitly select full_text or abstract_only before execution.",
            envvar="ANALYSIS_MODE",
        ),
    ] = "full_text",
    logical_date: Annotated[
        str | None,
        typer.Option("--logical-date", help="Logical run date in YYYY-MM-DD format."),
    ] = None,
) -> None:
    """Analyze selected persisted arXiv versions with strict DeepSeek output."""

    try:
        if not paper_ids:
            raise ValueError("at least one --paper-id is required")
        parsed_scope = AnalysisScope(analysis_scope.strip().upper())
        parsed_logical_date = None if logical_date is None else date.fromisoformat(logical_date)
        run = execute_structured_analysis(
            topic_config=topic_config,
            paper_ids=tuple(paper_ids),
            analysis_scope=parsed_scope,
            logical_date=parsed_logical_date,
        )
    except (
        ValueError,
        OSError,
        ArxivPortError,
        LLMPortError,
        PdfParserPortError,
        RepositoryError,
        DuplicateDailyRunError,
    ) as error:
        typer.echo(
            json.dumps(
                {"level": "ERROR", "event": "structured_analysis_failed", "detail": str(error)},
                separators=(",", ":"),
            ),
            err=True,
        )
        raise typer.Exit(code=1) from error
    level = "WARNING" if run.status is RunStatus.PARTIAL else "INFO"
    event = (
        "structured_analysis_partial"
        if run.status is RunStatus.PARTIAL
        else "structured_analysis_completed"
    )
    if run.status is RunStatus.FAILED:
        level = "ERROR"
        event = "structured_analysis_failed"
    typer.echo(
        json.dumps(
            {
                "level": level,
                "event": event,
                "run_id": str(run.id),
                "status": run.status.value,
                "selected_count": run.selected_count,
                "completed_count": run.completed_count,
                "failed_count": run.failed_count,
                "error_code": run.error_code,
            },
            separators=(",", ":"),
        ),
        err=run.status in (RunStatus.PARTIAL, RunStatus.FAILED),
    )
    if run.status is RunStatus.FAILED:
        raise typer.Exit(code=1)


@app.command("historical-backfill")
def historical_backfill(
    topic_config: Annotated[
        Path,
        typer.Option(
            "--topic-config",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            envvar="TOPIC_CONFIG_PATH",
        ),
    ] = Path("configs/topics/broad-llm-agents.yaml"),
    through: Annotated[
        str | None,
        typer.Option(
            "--through",
            help="Inclusive historical-window end date in YYYY-MM-DD format.",
        ),
    ] = None,
    max_queries: Annotated[
        int,
        typer.Option("--max-queries", min=1, max=40),
    ] = 40,
    per_query_limit: Annotated[
        int,
        typer.Option("--per-query-limit", min=1, max=500),
    ] = 500,
    overall_timeout_seconds: Annotated[
        float,
        typer.Option("--overall-timeout-seconds", min=1, max=7200),
    ] = 3600.0,
) -> None:
    """Populate the resumable six-month Semantic Scholar corpus explicitly."""

    try:
        parsed_through = date.today() if through is None else date.fromisoformat(through)
        run = execute_historical_backfill(
            topic_config=topic_config,
            through=parsed_through,
            max_queries=max_queries,
            per_query_limit=per_query_limit,
            overall_timeout_seconds=overall_timeout_seconds,
        )
    except (
        ValueError,
        OSError,
        DomainInvariantError,
        HistoricalBackfillTimeoutError,
        ScholarlySearchError,
        ScientificEmbeddingPortError,
        RepositoryError,
    ) as error:
        typer.echo(
            json.dumps(
                {"level": "ERROR", "event": "historical_backfill_failed", "detail": str(error)},
                separators=(",", ":"),
            ),
            err=True,
        )
        raise typer.Exit(code=1) from error
    typer.echo(
        json.dumps(
            {
                "level": "INFO",
                "event": "historical_backfill_completed",
                "run_id": str(run.id),
                "status": run.status.value,
                "window_from": run.window_from.isoformat(),
                "window_to": run.window_to.isoformat(),
                "discovered_count": run.discovered_count,
                "persisted_count": run.persisted_count,
                "representative_count": run.representative_count,
            },
            separators=(",", ":"),
        )
    )


@app.command("search-related")
def search_related(
    source_paper_id: Annotated[
        UUID,
        typer.Option("--paper-id", help="Persisted source-paper UUID."),
    ],
    objective: Annotated[
        str,
        typer.Option("--objective", help="Concise related-work discovery objective."),
    ],
    year_from: Annotated[int, typer.Option("--year-from", min=1900, max=3000)],
    year_to: Annotated[int, typer.Option("--year-to", min=1900, max=3000)],
    topic_config: Annotated[
        Path,
        typer.Option(
            "--topic-config",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            envvar="TOPIC_CONFIG_PATH",
        ),
    ] = Path("configs/topics/broad-llm-agents.yaml"),
    max_steps: Annotated[int, typer.Option("--max-steps", min=1, max=100)] = 24,
    max_queries: Annotated[int, typer.Option("--max-queries", min=1, max=40)] = 8,
    max_queue_size: Annotated[int, typer.Option("--max-queue-size", min=1, max=2000)] = 200,
    max_citation_depth: Annotated[int, typer.Option("--max-citation-depth", min=0, max=5)] = 2,
    max_candidates: Annotated[int, typer.Option("--max-candidates", min=1, max=5000)] = 300,
    max_selected_candidates: Annotated[
        int, typer.Option("--max-selected-candidates", min=1, max=100)
    ] = 20,
    per_operation_timeout_seconds: Annotated[
        float,
        typer.Option("--per-operation-timeout-seconds", min=1, max=600),
    ] = 60,
    overall_timeout_seconds: Annotated[
        float,
        typer.Option("--overall-timeout-seconds", min=1, max=3600),
    ] = 600,
) -> None:
    """Run one bounded PaSa-derived Crawler and Selector session."""

    try:
        if year_from > year_to:
            raise ValueError("--year-from cannot be later than --year-to")
        limits = SearchLimits(
            max_steps=max_steps,
            max_queries=max_queries,
            max_queue_size=max_queue_size,
            max_citation_depth=max_citation_depth,
            max_candidates=max_candidates,
            max_selected_candidates=max_selected_candidates,
            per_operation_timeout_seconds=per_operation_timeout_seconds,
            overall_timeout_seconds=overall_timeout_seconds,
        )
        detail = execute_related_work_search(
            topic_config=topic_config,
            source_paper_id=source_paper_id,
            objective=objective,
            year_from=year_from,
            year_to=year_to,
            limits=limits,
        )
    except (
        ValueError,
        OSError,
        DomainInvariantError,
        RelatedWorkInputError,
        ScholarlySearchError,
        ScientificEmbeddingPortError,
        LLMPortError,
        RepositoryError,
    ) as error:
        typer.echo(
            json.dumps(
                {"level": "ERROR", "event": "related_work_search_failed", "detail": str(error)},
                separators=(",", ":"),
            ),
            err=True,
        )
        raise typer.Exit(code=1) from error
    selected_count = sum(item.decision is SelectionDecision.SELECTED for item in detail.candidates)
    typer.echo(
        json.dumps(
            {
                "level": "INFO",
                "event": "related_work_search_completed",
                "session_id": str(detail.session.id),
                "status": detail.session.status.value,
                "stop_reason": (
                    None if detail.session.stop_reason is None else detail.session.stop_reason.value
                ),
                "action_count": len(detail.actions),
                "candidate_count": len(detail.candidates),
                "selected_count": selected_count,
            },
            separators=(",", ":"),
        )
    )


@app.command("compare-papers")
def compare_papers(
    search_session_id: Annotated[
        UUID,
        typer.Option("--search-session-id", help="Owning related-work search session UUID."),
    ],
    source_paper_version_id: Annotated[
        UUID,
        typer.Option("--source-paper-version-id", help="Analyzed source version UUID."),
    ],
    target_paper_version_id: Annotated[
        UUID,
        typer.Option("--target-paper-version-id", help="Analyzed historical version UUID."),
    ],
) -> None:
    """Persist one fixed-dimension, evidence-linked paper comparison."""

    try:
        bundle = execute_paper_comparison(
            search_session_id=search_session_id,
            source_paper_version_id=source_paper_version_id,
            target_paper_version_id=target_paper_version_id,
        )
    except (
        ValueError,
        OSError,
        DomainInvariantError,
        ComparisonInputMissingError,
        LLMPortError,
        RepositoryError,
    ) as error:
        typer.echo(
            json.dumps(
                {"level": "ERROR", "event": "paper_comparison_failed", "detail": str(error)},
                separators=(",", ":"),
            ),
            err=True,
        )
        raise typer.Exit(code=1) from error
    typer.echo(
        json.dumps(
            {
                "level": "INFO",
                "event": "paper_comparison_completed",
                "comparison_id": str(bundle.comparison.id),
                "comparability_status": bundle.comparison.comparability_status.value,
                "relation_count": len(bundle.relations),
            },
            separators=(",", ":"),
        )
    )
