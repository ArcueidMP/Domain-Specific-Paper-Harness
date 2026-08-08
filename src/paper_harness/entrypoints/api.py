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
from paper_harness.application.read_models import (
    AnalysisDetail,
    PaperDetail,
    RunDetail,
    StoredTopic,
)
from paper_harness.domain.analysis import AnalysisClaim, AnalysisScope, Evidence, PaperAnalysis
from paper_harness.domain.models import DailyRun, Paper, PaperSourceIdentity, PaperVersion, RunItem
from paper_harness.domain.reports import Report, ReportFailure
from paper_harness.entrypoints.api_schemas import (
    AnalysisClaimResponse,
    ApiErrorResponse,
    EvidenceListResponse,
    EvidenceResponse,
    LiveResponse,
    ModelUsageResponse,
    PageCoordinatesResponse,
    PaperAnalysisResponse,
    PaperDetailResponse,
    PaperListResponse,
    PaperSummary,
    PaperVersionResponse,
    ReadyResponse,
    ReportFailureResponse,
    ReportResponse,
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

    @app.get(
        "/api/v1/papers/{paper_id}/analysis",
        response_model=PaperAnalysisResponse,
        operation_id="getPaperAnalysis",
        responses={
            404: {"model": ApiErrorResponse, "description": "Paper or analysis not found"},
            503: {"model": ApiErrorResponse, "description": "Analysis storage unavailable"},
        },
    )
    def _get_paper_analysis(
        paper_id: UUID,
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        paper_version_id: UUID | None = None,
        scope: AnalysisScope | None = None,
    ) -> PaperAnalysisResponse:
        if repo.get_paper(paper_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PAPER_NOT_FOUND", "message": f"paper {paper_id} was not found"},
            )
        detail = repo.get_paper_analysis(
            paper_id,
            paper_version_id=paper_version_id,
            analysis_scope=scope,
        )
        if detail is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "ANALYSIS_NOT_FOUND",
                    "message": "no matching structured analysis was found",
                },
            )
        return _analysis_detail_response(detail)

    @app.get(
        "/api/v1/papers/{paper_id}/evidence",
        response_model=EvidenceListResponse,
        operation_id="listPaperEvidence",
        responses={
            404: {"model": ApiErrorResponse, "description": "Paper or analysis not found"},
            503: {"model": ApiErrorResponse, "description": "Evidence storage unavailable"},
        },
    )
    def _list_paper_evidence(
        paper_id: UUID,
        analysis_id: UUID,
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        paper_version_id: UUID | None = None,
        scope: AnalysisScope | None = None,
    ) -> EvidenceListResponse:
        if repo.get_paper(paper_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PAPER_NOT_FOUND", "message": f"paper {paper_id} was not found"},
            )
        evidence = repo.list_paper_evidence(
            paper_id,
            analysis_id=analysis_id,
            paper_version_id=paper_version_id,
            analysis_scope=scope,
        )
        if evidence is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "ANALYSIS_NOT_FOUND",
                    "message": "no matching structured analysis was found",
                },
            )
        return EvidenceListResponse(
            items=[_evidence_response(item) for item in evidence],
            total=len(evidence),
        )

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
        analysis_scope=run.analysis_scope,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        cursor_from=run.cursor_from,
        cursor_to=run.cursor_to,
        discovered_count=run.discovered_count,
        normalized_count=run.normalized_count,
        selected_count=run.selected_count,
        completed_count=run.completed_count,
        failed_count=run.failed_count,
        error_code=run.error_code,
        error_detail=run.error_detail,
        schema_version=run.schema_version,
        created_at=run.created_at,
    )


def _item_response(item: RunItem, *, canonical_arxiv_id: str, paper_title: str) -> RunItemResponse:
    return RunItemResponse(
        id=item.id,
        run_id=item.run_id,
        paper_id=item.paper_id,
        paper_version_id=item.paper_version_id,
        canonical_arxiv_id=canonical_arxiv_id,
        paper_title=paper_title,
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
        **summary.model_dump(),
        items=[
            _item_response(
                item.item,
                canonical_arxiv_id=item.canonical_arxiv_id,
                paper_title=item.paper_title,
            )
            for item in detail.items
        ],
        report=None if detail.report is None else _report_response(detail.report),
    )


def _claim_response(claim: AnalysisClaim) -> AnalysisClaimResponse:
    return AnalysisClaimResponse(
        id=claim.id,
        analysis_id=claim.analysis_id,
        paper_id=claim.paper_id,
        paper_version_id=claim.paper_version_id,
        key=claim.key,
        claim_type=claim.claim_type,
        text=claim.text,
        provider=claim.provider,
        model_version=claim.model_version,
        prompt_version=claim.prompt_version,
        generated_at=claim.generated_at,
        source=claim.source,
        verification_status=claim.verification_status,
        schema_version=claim.schema_version,
        created_at=claim.created_at,
    )


def _analysis_response(
    analysis: PaperAnalysis,
    *,
    arxiv_version: int,
    claims: tuple[AnalysisClaim, ...],
    parser_name: str | None,
    parser_version: str | None,
) -> PaperAnalysisResponse:
    return PaperAnalysisResponse(
        id=analysis.id,
        paper_id=analysis.paper_id,
        paper_version_id=analysis.paper_version_id,
        arxiv_version=arxiv_version,
        analysis_scope=analysis.analysis_scope,
        parsed_paper_id=analysis.parsed_paper_id,
        parser_name=parser_name,
        parser_version=parser_version,
        summary=analysis.summary,
        research_problem=analysis.research_problem,
        method_summary=analysis.method_summary,
        key_contributions=list(analysis.key_contributions),
        limitations=list(analysis.limitations),
        provider=analysis.provider,
        configured_model=analysis.configured_model,
        model_version=analysis.model_version,
        prompt_version=analysis.prompt_version,
        generated_at=analysis.generated_at,
        source=analysis.source,
        verification_status=analysis.verification_status,
        usage=ModelUsageResponse(
            prompt_tokens=analysis.usage.prompt_tokens,
            completion_tokens=analysis.usage.completion_tokens,
            total_tokens=analysis.usage.total_tokens,
            call_count=analysis.usage.call_count,
            duration_ms=analysis.usage.duration_ms,
            estimated_cost_usd=analysis.usage.estimated_cost_usd,
        ),
        schema_version=analysis.schema_version,
        created_at=analysis.created_at,
        claims=[_claim_response(claim) for claim in claims],
    )


def _analysis_detail_response(detail: AnalysisDetail) -> PaperAnalysisResponse:
    return _analysis_response(
        detail.analysis,
        arxiv_version=detail.arxiv_version,
        claims=detail.claims,
        parser_name=detail.parser_name,
        parser_version=detail.parser_version,
    )


def _evidence_response(item: Evidence) -> EvidenceResponse:
    return EvidenceResponse(
        id=item.id,
        analysis_id=item.analysis_id,
        paper_id=item.paper_id,
        paper_version_id=item.paper_version_id,
        key=item.key,
        section=item.section,
        passage_id=item.passage_id,
        coordinates=[
            PageCoordinatesResponse(
                page=value.page,
                x=value.x,
                y=value.y,
                width=value.width,
                height=value.height,
            )
            for value in item.coordinates
        ],
        excerpt=item.excerpt,
        evidence_type=item.evidence_type,
        supported_claim_ids=list(item.supported_claim_ids),
        extraction_source=item.extraction_source,
        provider=item.provider,
        model_version=item.model_version,
        prompt_version=item.prompt_version,
        generated_at=item.generated_at,
        verification_status=item.verification_status,
        schema_version=item.schema_version,
        created_at=item.created_at,
    )


def _report_failure_response(failure: ReportFailure) -> ReportFailureResponse:
    return ReportFailureResponse(
        id=failure.id,
        report_id=failure.report_id,
        paper_id=failure.paper_id,
        paper_version_id=failure.paper_version_id,
        failed_stage=failure.failed_stage,
        error_code=failure.error_code,
        retryable=failure.retryable,
        error_detail=failure.error_detail,
        schema_version=failure.schema_version,
        created_at=failure.created_at,
    )


def _report_response(report: Report) -> ReportResponse:
    return ReportResponse(
        id=report.id,
        run_id=report.run_id,
        topic_id=report.topic_id,
        logical_date=report.logical_date,
        status=report.status,
        title=report.title,
        summary=report.summary,
        source=report.source,
        generated_at=report.generated_at,
        schema_version=report.schema_version,
        created_at=report.created_at,
        failures=[_report_failure_response(failure) for failure in report.failures],
    )


app = create_app()
