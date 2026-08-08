"""Protected operator CLI; the API intentionally has no run endpoint."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from paper_harness.domain.errors import DuplicateDailyRunError
from paper_harness.entrypoints.runtime import execute_arxiv_ingestion
from paper_harness.ports.arxiv import ArxivPortError
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
