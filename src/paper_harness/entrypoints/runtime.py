"""Shared construction for explicit operator and Daily Job entrypoints."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from uuid import UUID

from paper_harness.adapters.arxiv import ArxivClient
from paper_harness.adapters.config import load_topic_config
from paper_harness.adapters.deepseek import DeepSeekClient, DeepSeekSettings
from paper_harness.adapters.gcp_identity import CloudRunIdTokenProvider
from paper_harness.adapters.grobid import GrobidClient
from paper_harness.adapters.postgres import PostgresRepository, create_postgres_engine
from paper_harness.application.analyze_papers import AnalyzePapers
from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.domain.analysis import AnalysisScope
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


def execute_structured_analysis(
    *,
    topic_config: Path,
    paper_ids: tuple[UUID, ...],
    analysis_scope: AnalysisScope,
    logical_date: date | None,
) -> DailyRun:
    # Operation-scoped dependency validation deliberately happens before any
    # database or external work. FastAPI never constructs these dependencies.
    llm_settings = DeepSeekSettings.from_environment()
    parser = _grobid_parser(analysis_scope)
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is required for structured analysis")
    topic = load_topic_config(topic_config)
    repository = PostgresRepository(create_postgres_engine(database_url))
    repository.check_ready()
    use_case = AnalyzePapers(
        arxiv=ArxivClient(),
        parser=parser,
        llm=DeepSeekClient(llm_settings),
        repository=repository,
    )
    return use_case.execute(
        topic,
        paper_ids=paper_ids,
        analysis_scope=analysis_scope,
        logical_date=logical_date,
    )


def _grobid_parser(analysis_scope: AnalysisScope) -> GrobidClient | None:
    if analysis_scope is AnalysisScope.ABSTRACT_ONLY:
        return None
    grobid_url = os.environ.get("GROBID_URL", "").strip()
    if not grobid_url:
        raise ValueError("GROBID_URL is required for full-text analysis")
    app_environment = os.environ.get("APP_ENV", "development").strip().lower()
    if app_environment not in {"development", "test", "production"}:
        raise ValueError("APP_ENV must be development, test, or production")
    auth_mode = os.environ.get("GROBID_AUTH_MODE", "none").strip().lower()
    if auth_mode == "none":
        if app_environment == "production":
            raise ValueError("production GROBID requires GROBID_AUTH_MODE=google_identity")
        token_provider = None
    elif auth_mode == "google_identity":
        audience = os.environ.get("GROBID_AUDIENCE", "").strip()
        if not audience:
            raise ValueError("GROBID_AUDIENCE is required for Google identity authentication")
        if audience.rstrip("/") != grobid_url.rstrip("/"):
            raise ValueError("GROBID_AUDIENCE must exactly match GROBID_URL")
        token_provider = CloudRunIdTokenProvider(audience)
    else:
        raise ValueError("GROBID_AUTH_MODE must be none or google_identity")
    if app_environment == "production" and not grobid_url.startswith("https://"):
        raise ValueError("production GROBID_URL must use HTTPS")
    return GrobidClient(grobid_url, bearer_token_provider=token_provider)
