"""Protected operator CLI; the API intentionally has no run endpoint."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from paper_harness.application.compare_papers import ComparisonInputMissingError
from paper_harness.application.historical_backfill import HistoricalBackfillTimeoutError
from paper_harness.application.related_work import RelatedWorkInputError
from paper_harness.domain.analysis import AnalysisScope
from paper_harness.domain.errors import DomainInvariantError, DuplicateDailyRunError
from paper_harness.domain.historical import SearchLimits, SelectionDecision
from paper_harness.domain.models import RunStatus
from paper_harness.entrypoints.runtime import (
    execute_arxiv_ingestion,
    execute_historical_backfill,
    execute_paper_comparison,
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


@app.callback()
def _root() -> None:
    """Operate Domain-Specific Paper Harness outside the read-only API."""


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
