"""Read-oriented FastAPI entrypoint for the M1 product surface."""

# pyright: reportUnusedFunction=false

import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from paper_harness.adapters.postgres import PostgresRepository, create_postgres_engine
from paper_harness.application.read_models import PaperDetail, RunDetail, StoredTopic
from paper_harness.domain.models import DailyRun, Paper, PaperSourceIdentity, PaperVersion, RunItem
from paper_harness.entrypoints.api_schemas import (
    LiveResponse,
    PaperDetailResponse,
    PaperListResponse,
    PaperSummary,
    PaperVersionResponse,
    ReadyResponse,
    RunDetailResponse,
    RunItemResponse,
    RunListResponse,
    RunSummary,
    SourceIdentityResponse,
    TopicListResponse,
    TopicSummary,
)
from paper_harness.ports.repository import (
    MigrationIncompatibleError,
    RepositoryPort,
    RepositoryUnavailableError,
)


def create_app(repository: RepositoryPort | None = None) -> FastAPI:
    app = FastAPI(
        title="Domain-Specific Paper Harness API",
        description="Private read-oriented API for broad LLM-agent research intelligence.",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    get_repository = _repository_dependency(repository)

    @app.exception_handler(RepositoryUnavailableError)
    async def _database_unavailable(
        _request: Request, error: RepositoryUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": {"code": "DATABASE_UNAVAILABLE", "message": str(error)}},
        )

    @app.exception_handler(MigrationIncompatibleError)
    async def _migration_incompatible(
        _request: Request, error: MigrationIncompatibleError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": {"code": "MIGRATION_INCOMPATIBLE", "message": str(error)}},
        )

    @app.get("/health/live", response_model=LiveResponse, operation_id="getLiveness")
    def _liveness() -> LiveResponse:
        return LiveResponse()

    @app.get(
        "/health/ready",
        response_model=ReadyResponse,
        operation_id="getReadiness",
        responses={503: {"description": "Database unavailable or migration incompatible"}},
    )
    def _readiness(repo: Annotated[RepositoryPort, Depends(get_repository)]) -> ReadyResponse:
        repo.check_ready()
        return ReadyResponse()

    @app.get("/api/v1/topics", response_model=TopicListResponse, operation_id="listTopics")
    def _list_topics(
        repo: Annotated[RepositoryPort, Depends(get_repository)],
    ) -> TopicListResponse:
        topics = repo.list_topics()
        return TopicListResponse(
            items=[_topic_response(topic) for topic in topics], total=len(topics)
        )

    @app.get("/api/v1/papers", response_model=PaperListResponse, operation_id="listPapers")
    def _list_papers(
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        topic: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> PaperListResponse:
        papers, total = repo.list_papers(topic_slug=topic, limit=limit, offset=offset)
        return PaperListResponse(
            items=[_paper_response(paper) for paper in papers],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get(
        "/api/v1/papers/{paper_id}",
        response_model=PaperDetailResponse,
        operation_id="getPaper",
    )
    def _get_paper(
        paper_id: UUID, repo: Annotated[RepositoryPort, Depends(get_repository)]
    ) -> PaperDetailResponse:
        detail = repo.get_paper(paper_id)
        if detail is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PAPER_NOT_FOUND", "message": f"paper {paper_id} was not found"},
            )
        return _paper_detail_response(detail)

    @app.get("/api/v1/runs", response_model=RunListResponse, operation_id="listRuns")
    def _list_runs(
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        topic: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> RunListResponse:
        runs, total = repo.list_runs(topic_slug=topic, limit=limit, offset=offset)
        return RunListResponse(
            items=[_run_response(run) for run in runs],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get(
        "/api/v1/runs/latest",
        response_model=RunDetailResponse,
        operation_id="getLatestRun",
    )
    def _latest_run(
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        topic: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    ) -> RunDetailResponse:
        detail = repo.get_latest_run(topic_slug=topic)
        if detail is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "RUN_NOT_FOUND", "message": "no matching daily run was found"},
            )
        return _run_detail_response(detail)

    static_directory = os.environ.get("PAPER_HARNESS_STATIC_DIR")
    if static_directory:
        resolved_static_directory = Path(static_directory).resolve()
        if not resolved_static_directory.is_dir():
            raise RuntimeError("PAPER_HARNESS_STATIC_DIR must reference a readable directory")
        app.mount("/", SpaStaticFiles(directory=resolved_static_directory, html=True), name="web")

    return app


class SpaStaticFiles(StaticFiles):
    """Serve built assets and the React entry document for client-side routes."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code != status.HTTP_404_NOT_FOUND:
                raise
        else:
            if response.status_code != status.HTTP_404_NOT_FOUND:
                return response
        return await super().get_response("index.html", scope)


def _repository_dependency(
    injected: RepositoryPort | None,
) -> Callable[[], RepositoryPort]:
    runtime_repository = injected

    def get_repository() -> RepositoryPort:
        nonlocal runtime_repository
        if runtime_repository is not None:
            return runtime_repository
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RepositoryUnavailableError("DATABASE_URL is required for persistence")
        runtime_repository = PostgresRepository(create_postgres_engine(database_url))
        return runtime_repository

    return get_repository


def _topic_response(topic: StoredTopic) -> TopicSummary:
    return TopicSummary(
        id=topic.config.id,
        slug=topic.config.slug,
        name=topic.config.name,
        description=topic.config.description,
        schema_version=topic.config.schema_version,
        created_at=topic.created_at,
    )


def _paper_response(paper: Paper) -> PaperSummary:
    return PaperSummary(
        id=paper.id,
        canonical_arxiv_id=paper.canonical_arxiv_id,
        title=paper.title,
        abstract=paper.abstract,
        current_version=paper.current_version,
        first_submitted_at=paper.first_submitted_at,
        latest_updated_at=paper.latest_updated_at,
        primary_category=paper.primary_category,
        categories=list(paper.categories),
        authors=list(paper.authors),
        pdf_url=paper.pdf_url,
        schema_version=paper.schema_version,
        created_at=paper.created_at,
    )


def _version_response(version: PaperVersion) -> PaperVersionResponse:
    return PaperVersionResponse(
        id=version.id,
        paper_id=version.paper_id,
        canonical_arxiv_id=version.canonical_arxiv_id,
        version=version.version,
        title=version.title,
        abstract=version.abstract,
        submitted_at=version.submitted_at,
        updated_at=version.updated_at,
        primary_category=version.primary_category,
        categories=list(version.categories),
        authors=list(version.authors),
        pdf_url=version.pdf_url,
        source_url=version.source_url,
        schema_version=version.schema_version,
        created_at=version.created_at,
    )


def _identity_response(identity: PaperSourceIdentity) -> SourceIdentityResponse:
    return SourceIdentityResponse(
        id=identity.id,
        paper_id=identity.paper_id,
        paper_version_id=identity.paper_version_id,
        source="arxiv",
        external_id=identity.external_id,
        source_version=identity.source_version,
        source_url=identity.source_url,
        schema_version=identity.schema_version,
        created_at=identity.created_at,
    )


def _paper_detail_response(detail: PaperDetail) -> PaperDetailResponse:
    summary = _paper_response(detail.paper)
    return PaperDetailResponse(
        **summary.model_dump(),
        versions=[_version_response(version) for version in detail.versions],
        source_identities=[_identity_response(identity) for identity in detail.source_identities],
        topic_slugs=list(detail.topic_slugs),
    )


def _run_response(run: DailyRun) -> RunSummary:
    return RunSummary(
        id=run.id,
        topic_id=run.topic_id,
        logical_date=run.logical_date,
        operation=run.operation,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        cursor_from=run.cursor_from,
        cursor_to=run.cursor_to,
        discovered_count=run.discovered_count,
        normalized_count=run.normalized_count,
        failed_count=run.failed_count,
        error_code=run.error_code,
        error_detail=run.error_detail,
        schema_version=run.schema_version,
        created_at=run.created_at,
    )


def _item_response(item: RunItem) -> RunItemResponse:
    return RunItemResponse(
        id=item.id,
        run_id=item.run_id,
        paper_id=item.paper_id,
        paper_version_id=item.paper_version_id,
        stage=item.stage,
        status=item.status,
        failed_stage=item.failed_stage,
        error_code=item.error_code,
        retryable=item.retryable,
        error_detail=item.error_detail,
        schema_version=item.schema_version,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _run_detail_response(detail: RunDetail) -> RunDetailResponse:
    summary = _run_response(detail.run)
    return RunDetailResponse(
        **summary.model_dump(), items=[_item_response(item) for item in detail.items]
    )


app = create_app()
