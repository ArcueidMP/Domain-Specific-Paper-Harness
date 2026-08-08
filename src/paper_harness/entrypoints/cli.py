"""Protected operator CLI; the API intentionally has no run endpoint."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from paper_harness.domain.analysis import AnalysisScope
from paper_harness.domain.errors import DuplicateDailyRunError
from paper_harness.domain.models import RunStatus
from paper_harness.entrypoints.runtime import execute_arxiv_ingestion, execute_structured_analysis
from paper_harness.ports.arxiv import ArxivPortError
from paper_harness.ports.llm import LLMPortError
from paper_harness.ports.pdf_parser import PdfParserPortError
from paper_harness.ports.repository import RepositoryError

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
