"""Shared construction for explicit operator and Daily Job entrypoints."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from paper_harness.adapters.arxiv import ArxivClient
from paper_harness.adapters.config import load_topic_config
from paper_harness.adapters.postgres import PostgresRepository, create_postgres_engine
from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.domain.models import DailyRun


def execute_arxiv_ingestion(*, topic_config: Path, logical_date: date | None) -> DailyRun:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is required for arXiv ingestion")
    topic = load_topic_config(topic_config)
    repository = PostgresRepository(create_postgres_engine(database_url))
    repository.check_ready()
    use_case = IngestArxiv(arxiv=ArxivClient(), repository=repository)
    return use_case.execute(topic, logical_date=logical_date)
