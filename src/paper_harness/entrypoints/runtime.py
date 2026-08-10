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
from paper_harness.adapters.http_retry import HttpRetryPolicy
from paper_harness.adapters.postgres import PostgresRepository, create_postgres_engine
from paper_harness.adapters.semantic_scholar import (
    SemanticScholarClient,
    SemanticScholarSettings,
)
from paper_harness.adapters.specter2 import load_specter2_encoder
from paper_harness.application.analyze_papers import AnalyzePapers
from paper_harness.application.compare_papers import ComparePapers
from paper_harness.application.historical_backfill import HistoricalBackfill
from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.application.read_models import SearchSessionDetail
from paper_harness.application.related_work import RelatedWorkSearch
from paper_harness.domain.analysis import AnalysisScope
from paper_harness.domain.historical import (
    ComparisonBundle,
    HistoricalBackfillRun,
    SearchLimits,
)
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


def execute_historical_backfill(
    *,
    topic_config: Path,
    through: date,
    max_queries: int = 40,
    per_query_limit: int = 500,
    overall_timeout_seconds: float = 3600.0,
) -> HistoricalBackfillRun:
    """Run the explicit six-month operation; never invoked by API startup."""

    scholarly_settings = SemanticScholarSettings.from_environment()
    embeddings = _specter2_embeddings()
    repository = _ready_repository("historical backfill")
    topic = load_topic_config(topic_config)
    return HistoricalBackfill(
        repository=repository,
        scholarly_search=SemanticScholarClient(scholarly_settings),
        embeddings=embeddings,
    ).execute(
        topic=topic,
        through=through,
        max_queries=max_queries,
        per_query_limit=per_query_limit,
        overall_timeout_seconds=overall_timeout_seconds,
    )


def execute_related_work_search(
    *,
    topic_config: Path,
    source_paper_id: UUID,
    objective: str,
    year_from: int,
    year_to: int,
    limits: SearchLimits,
) -> SearchSessionDetail:
    """Run one bounded PaSa-derived search session through approved tools."""

    scholarly_settings = SemanticScholarSettings.from_environment()
    llm_settings = DeepSeekSettings.from_environment()
    embeddings = _specter2_embeddings()
    repository = _ready_repository("related-work search")
    topic = load_topic_config(topic_config)
    return RelatedWorkSearch(
        repository=repository,
        scholarly_search=SemanticScholarClient(
            scholarly_settings,
            retry_policy=_scholarly_retry_policy(limits.per_operation_timeout_seconds),
        ),
        llm=DeepSeekClient(llm_settings),
        embeddings=embeddings,
    ).execute(
        topic=topic,
        source_paper_id=source_paper_id,
        objective=objective,
        year_from=year_from,
        year_to=year_to,
        limits=limits,
    )


def execute_paper_comparison(
    *,
    search_session_id: UUID,
    source_paper_version_id: UUID,
    target_paper_version_id: UUID,
) -> ComparisonBundle:
    """Generate and atomically persist one evidence-linked comparison."""

    llm_settings = DeepSeekSettings.from_environment()
    repository = _ready_repository("paper comparison")
    return ComparePapers(
        repository=repository,
        llm=DeepSeekClient(llm_settings),
    ).execute(
        search_session_id=search_session_id,
        source_paper_version_id=source_paper_version_id,
        target_paper_version_id=target_paper_version_id,
    )


def _specter2_embeddings():
    model_path = os.environ.get("SPECTER2_MODEL_PATH", "").strip()
    return load_specter2_encoder() if not model_path else load_specter2_encoder(model_path)


def _ready_repository(operation: str) -> PostgresRepository:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError(f"DATABASE_URL is required for {operation}")
    repository = PostgresRepository(create_postgres_engine(database_url))
    repository.check_ready()
    return repository


def _scholarly_retry_policy(operation_timeout_seconds: float) -> HttpRetryPolicy:
    return HttpRetryPolicy(
        max_retries=2,
        request_timeout_seconds=min(30, operation_timeout_seconds),
        total_timeout_seconds=operation_timeout_seconds,
        backoff_seconds=1,
        max_retry_after_seconds=min(30, max(1, operation_timeout_seconds)),
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
