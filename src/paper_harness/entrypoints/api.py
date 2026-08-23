"""Read-oriented FastAPI entrypoint for the private research product."""

# pyright: reportUnusedFunction=false

import os
from calendar import monthrange
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi import Path as ApiPath
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from paper_harness.adapters.postgres import PostgresRepository, create_postgres_engine
from paper_harness.application.read_models import (
    AnalysisAvailabilityStatus,
    AnalysisDetail,
    ComparisonAvailabilityStatus,
    ComparisonDetail,
    ComparisonEvidenceReference,
    GraphEdgeDetail,
    GraphEdgeEvidenceReference,
    GraphNodeDetail,
    GraphView,
    LineageDetail,
    PaperDetail,
    ProductRunDetail,
    RelatedWorkAvailabilityStatus,
    RelatedWorkDetail,
    ReportDetail,
    RunDetail,
    StoredTopic,
    TrendAvailabilityStatus,
    TrendDetail,
)
from paper_harness.domain.analysis import (
    AnalysisClaim,
    AnalysisScope,
    Evidence,
    PaperAnalysis,
    VerificationStatus,
)
from paper_harness.domain.historical import (
    ComparabilityStatus,
    ComparisonDimension,
    ExternalPaperStub,
    PaperRelation,
    RelationProvenance,
    SearchAction,
    SearchCandidate,
    SearchCandidateDiscovery,
    SearchSession,
    SearchSessionStatus,
)
from paper_harness.domain.knowledge import (
    GRAPH_CONFIDENCE_MEANING,
    GraphEdge,
    GraphEntityMention,
    GraphEntityType,
    GraphRelationType,
    TrendChange,
    TrendWindow,
)
from paper_harness.domain.models import (
    DailyRun,
    Paper,
    PaperSourceIdentity,
    PaperVersion,
    PipelineExecution,
    RunItem,
    RunOperation,
    RunStatus,
)
from paper_harness.domain.reports import (
    Report,
    ReportEvidenceReference,
    ReportFailure,
    ReportType,
)
from paper_harness.entrypoints.api_schemas import (
    AnalysisClaimResponse,
    ApiErrorResponse,
    CandidateDiscoveryResponse,
    CandidateScoreComponentsResponse,
    ComparisonDimensionResponse,
    ComparisonEvidenceResponse,
    ComparisonResponse,
    DailyRunEnvelopeResponse,
    EvidenceListResponse,
    EvidenceResponse,
    ExternalPaperResponse,
    GraphEdgeEvidenceResponse,
    GraphEdgeResponse,
    GraphEntityMentionResponse,
    GraphModelProvenanceResponse,
    GraphNodeResponse,
    KnowledgeGraphResponse,
    LineageNodeResponse,
    LineageResponse,
    LiveResponse,
    ModelUsageResponse,
    PageCoordinatesResponse,
    PaperAnalysisResponse,
    PaperDetailResponse,
    PaperListResponse,
    PaperRelationResponse,
    PaperSummary,
    PaperVersionResponse,
    ReadyResponse,
    RelatedComparisonSummaryResponse,
    RelatedWorkItemResponse,
    RelatedWorkResponse,
    ReportComparisonHighlightResponse,
    ReportCountsResponse,
    ReportEntityHighlightResponse,
    ReportEvidenceReferenceResponse,
    ReportFailureResponse,
    ReportGraphChangesResponse,
    ReportLineageHighlightResponse,
    ReportListResponse,
    ReportPaperHighlightResponse,
    ReportResponse,
    ReportSectionResponse,
    RunDetailResponse,
    RunItemResponse,
    RunListResponse,
    RunSummary,
    SearchActionResponse,
    SearchCandidateResponse,
    SearchLimitsResponse,
    SearchSessionResponse,
    SourceIdentityResponse,
    TopicListResponse,
    TopicSummary,
    TrendChangeResponse,
    TrendEntityCountResponse,
    TrendRelationCountResponse,
    TrendRepresentativePaperResponse,
    TrendSnapshotResponse,
    TrendsResponse,
    TrendThresholdsResponse,
)
from paper_harness.ports.repository import (
    MigrationIncompatibleError,
    RepositoryPort,
    RepositoryUnavailableError,
)


def create_app(repository: RepositoryPort | None = None) -> FastAPI:
    app = FastAPI(
        title="Domain-Specific Paper Harness API",
        description="Private read-oriented API for topic-scoped research intelligence.",
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
        papers, total = repo.list_published_papers(
            topic_slug=topic,
            limit=limit,
            offset=offset,
        )
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
        detail = repo.get_published_paper(paper_id)
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
        if repo.get_published_paper(paper_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PAPER_NOT_FOUND", "message": f"paper {paper_id} was not found"},
            )
        detail = repo.get_paper_analysis(
            paper_id,
            paper_version_id=paper_version_id,
            analysis_scope=scope,
            canonical_only=True,
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
        if repo.get_published_paper(paper_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PAPER_NOT_FOUND", "message": f"paper {paper_id} was not found"},
            )
        evidence = repo.list_paper_evidence(
            paper_id,
            analysis_id=analysis_id,
            paper_version_id=paper_version_id,
            analysis_scope=scope,
            canonical_only=True,
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

    @app.get(
        "/api/v1/papers/{paper_id}/related",
        response_model=RelatedWorkResponse,
        operation_id="getPaperRelatedWork",
        responses={
            404: {"model": ApiErrorResponse, "description": "Paper not found"},
            503: {"model": ApiErrorResponse, "description": "Related-work storage unavailable"},
        },
    )
    def _get_paper_related_work(
        paper_id: UUID,
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        paper_version_id: Annotated[UUID | None, Query()] = None,
    ) -> RelatedWorkResponse:
        paper = repo.get_published_paper(paper_id)
        if paper is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PAPER_NOT_FOUND", "message": f"paper {paper_id} was not found"},
            )
        if paper_version_id is not None and all(
            version.id != paper_version_id for version in paper.versions
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "PAPER_VERSION_NOT_FOUND",
                    "message": f"paper version {paper_version_id} was not found",
                },
            )
        detail = repo.get_related_work(
            paper_id,
            paper_version_id=paper_version_id,
        )
        if detail is None:
            return RelatedWorkResponse(
                paper_id=paper_id,
                related_work_status="RELATED_WORK_UNAVAILABLE",
                related_work_reason="NO_RELATED_WORK_RESULT",
                session=None,
                actions=[],
                items=[],
                comparisons=[],
                total=0,
            )
        return _related_work_response(paper_id, detail)

    @app.get(
        "/api/v1/comparisons/{comparison_id}",
        response_model=ComparisonResponse,
        operation_id="getComparison",
        responses={
            404: {"model": ApiErrorResponse, "description": "Comparison not found"},
            503: {"model": ApiErrorResponse, "description": "Comparison storage unavailable"},
        },
    )
    def _get_comparison(
        comparison_id: UUID,
        repo: Annotated[RepositoryPort, Depends(get_repository)],
    ) -> ComparisonResponse:
        detail = repo.get_comparison(comparison_id, canonical_only=True)
        if detail is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "COMPARISON_NOT_FOUND",
                    "message": f"comparison {comparison_id} was not found",
                },
            )
        return _comparison_response(detail)

    @app.get(
        "/api/v1/graph",
        response_model=KnowledgeGraphResponse,
        operation_id="getKnowledgeGraph",
        responses={
            404: {"model": ApiErrorResponse, "description": "Knowledge graph not found"},
            503: {"model": ApiErrorResponse, "description": "Graph storage unavailable"},
        },
    )
    def _get_graph(
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        topic: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
        as_of: Annotated[date | None, Query()] = None,
        paper_id: Annotated[UUID | None, Query()] = None,
        entity_id: Annotated[UUID | None, Query()] = None,
        entity_type: Annotated[GraphEntityType | None, Query()] = None,
        relation_type: Annotated[GraphRelationType | None, Query()] = None,
        provenance: Annotated[RelationProvenance | None, Query()] = None,
        verification_status: Annotated[VerificationStatus | None, Query()] = None,
        max_nodes: Annotated[int, Query(ge=1, le=500)] = 200,
        max_edges: Annotated[int, Query(ge=1, le=1000)] = 400,
    ) -> KnowledgeGraphResponse:
        graph = repo.get_graph(
            topic_slug=topic,
            as_of=as_of,
            paper_id=paper_id,
            entity_id=entity_id,
            entity_type=entity_type,
            relation_type=relation_type,
            provenance=provenance,
            verification_status=verification_status,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
        if graph is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "GRAPH_NOT_FOUND",
                    "message": "no matching persisted knowledge graph was found",
                },
            )
        return _graph_response(graph)

    @app.get(
        "/api/v1/trends",
        response_model=TrendsResponse,
        operation_id="listTrends",
        responses={
            503: {"model": ApiErrorResponse, "description": "Trend storage unavailable"},
        },
    )
    def _list_trends(
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        topic: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
        as_of: Annotated[date | None, Query()] = None,
        window: Annotated[list[TrendWindow] | None, Query()] = None,
        entity_type: Annotated[GraphEntityType | None, Query()] = None,
        max_entities: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> TrendsResponse:
        requested = tuple(TrendWindow) if window is None else tuple(dict.fromkeys(window))
        details = repo.list_trends(
            topic_slug=topic,
            as_of=as_of,
            windows=requested,
            entity_type=entity_type,
            max_entities=max_entities,
        )
        return TrendsResponse(
            items=[_trend_response(item) for item in details],
            total=len(details),
        )

    @app.get(
        "/api/v1/lineages/{entity_or_paper_id}",
        response_model=LineageResponse,
        operation_id="getLineage",
        responses={
            404: {"model": ApiErrorResponse, "description": "Lineage not found"},
            503: {"model": ApiErrorResponse, "description": "Lineage storage unavailable"},
        },
    )
    def _get_lineage(
        entity_or_paper_id: UUID,
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        topic: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
        max_depth: Annotated[int, Query(ge=1, le=5)] = 5,
        max_nodes: Annotated[int, Query(ge=1, le=100)] = 100,
        max_edges: Annotated[int, Query(ge=1, le=400)] = 200,
    ) -> LineageResponse:
        lineage = repo.get_lineage(
            entity_or_paper_id,
            topic_slug=topic,
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
        if lineage is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "LINEAGE_NOT_FOUND",
                    "message": f"lineage {entity_or_paper_id} was not found",
                },
            )
        return _lineage_response(lineage)

    @app.get(
        "/api/v1/daily/latest",
        response_model=DailyRunEnvelopeResponse,
        operation_id="getLatestDailyReport",
        responses={
            404: {"model": ApiErrorResponse, "description": "Product publication run not found"},
            503: {"model": ApiErrorResponse, "description": "Daily report storage unavailable"},
        },
    )
    def _get_latest_daily(
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        topic: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    ) -> DailyRunEnvelopeResponse:
        return _product_run_or_404(repo, logical_date=None, topic_slug=topic)

    @app.get(
        "/api/v1/daily/{logical_date}",
        response_model=DailyRunEnvelopeResponse,
        operation_id="getDailyReport",
        responses={
            404: {"model": ApiErrorResponse, "description": "Product publication run not found"},
            503: {"model": ApiErrorResponse, "description": "Daily report storage unavailable"},
        },
    )
    def _get_daily(
        logical_date: date,
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        topic: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    ) -> DailyRunEnvelopeResponse:
        return _product_run_or_404(repo, logical_date=logical_date, topic_slug=topic)

    @app.get(
        "/api/v1/reports/daily",
        response_model=ReportListResponse,
        operation_id="listDailyReports",
        responses={
            503: {"model": ApiErrorResponse, "description": "Report storage unavailable"},
        },
    )
    def _list_daily_reports(
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        topic: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> ReportListResponse:
        reports, total = repo.list_reports(
            report_type=ReportType.DAILY,
            topic_slug=topic,
            limit=limit,
            offset=offset,
        )
        return ReportListResponse(
            items=[_report_detail_response(item) for item in reports],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get(
        "/api/v1/reports/daily/{logical_date}",
        response_model=ReportResponse,
        operation_id="getDailyReportPublication",
        responses={
            404: {"model": ApiErrorResponse, "description": "Daily report not found"},
            503: {"model": ApiErrorResponse, "description": "Report storage unavailable"},
        },
    )
    def _get_daily_report(
        logical_date: date,
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        topic: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    ) -> ReportResponse:
        return _report_or_404(
            repo,
            report_type=ReportType.DAILY,
            period_start=logical_date,
            period_end=logical_date,
            topic_slug=topic,
        )

    @app.get(
        "/api/v1/reports/weekly/{period}",
        response_model=ReportResponse,
        operation_id="getWeeklyReport",
        responses={
            404: {"model": ApiErrorResponse, "description": "Weekly report not found"},
            503: {"model": ApiErrorResponse, "description": "Report storage unavailable"},
        },
    )
    def _get_weekly_report(
        period: Annotated[str, ApiPath(pattern=r"^\d{4}-W\d{2}$")],
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        topic: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    ) -> ReportResponse:
        period_start, period_end = _iso_week_bounds(period)
        return _report_or_404(
            repo,
            report_type=ReportType.WEEKLY,
            period_start=period_start,
            period_end=period_end,
            topic_slug=topic,
        )

    @app.get(
        "/api/v1/reports/monthly/{period}",
        response_model=ReportResponse,
        operation_id="getMonthlyReport",
        responses={
            404: {"model": ApiErrorResponse, "description": "Monthly report not found"},
            503: {"model": ApiErrorResponse, "description": "Report storage unavailable"},
        },
    )
    def _get_monthly_report(
        period: Annotated[str, ApiPath(pattern=r"^\d{4}-\d{2}$")],
        repo: Annotated[RepositoryPort, Depends(get_repository)],
        topic: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    ) -> ReportResponse:
        period_start, period_end = _month_bounds(period)
        return _report_or_404(
            repo,
            report_type=ReportType.MONTHLY,
            period_start=period_start,
            period_end=period_end,
            topic_slug=topic,
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
            items=[_run_response(run, _pipeline_execution(repo, run)) for run in runs],
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
        return _run_detail_response(detail, _pipeline_execution(repo, detail.run))

    @app.get(
        "/api/v1/runs/{run_id}",
        response_model=RunDetailResponse,
        operation_id="getRun",
        responses={
            404: {"model": ApiErrorResponse, "description": "Run not found"},
            503: {"model": ApiErrorResponse, "description": "Run storage unavailable"},
        },
    )
    def _get_run(
        run_id: UUID,
        repo: Annotated[RepositoryPort, Depends(get_repository)],
    ) -> RunDetailResponse:
        detail = repo.get_run(run_id)
        if detail is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "RUN_NOT_FOUND", "message": f"run {run_id} was not found"},
            )
        return _run_detail_response(detail, _pipeline_execution(repo, detail.run))

    static_directory = os.environ.get("PAPER_HARNESS_STATIC_DIR")
    if static_directory:
        resolved_static_directory = Path(static_directory).resolve()
        if not resolved_static_directory.is_dir():
            raise RuntimeError("PAPER_HARNESS_STATIC_DIR must reference a readable directory")
        app.mount("/", SpaStaticFiles(directory=resolved_static_directory, html=True), name="web")

    return app


def _product_run_or_404(
    repository: RepositoryPort,
    *,
    logical_date: date | None,
    topic_slug: str | None,
) -> DailyRunEnvelopeResponse:
    detail = repository.get_product_run(logical_date=logical_date, topic_slug=topic_slug)
    if detail is None:
        qualifier = "latest" if logical_date is None else logical_date.isoformat()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "PRODUCT_RUN_NOT_FOUND",
                "message": f"product publication run {qualifier} was not found",
            },
        )
    return _product_run_response(
        detail,
        _pipeline_execution(repository, detail.run),
    )


def _report_or_404(
    repository: RepositoryPort,
    *,
    report_type: ReportType,
    period_start: date,
    period_end: date,
    topic_slug: str | None,
) -> ReportResponse:
    detail = repository.get_report(
        report_type=report_type,
        period_start=period_start,
        period_end=period_end,
        topic_slug=topic_slug,
    )
    if detail is None:
        period = (
            period_start.isoformat()
            if period_start == period_end
            else f"{period_start.isoformat()} to {period_end.isoformat()}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "REPORT_NOT_FOUND",
                "message": f"{report_type.value.lower()} report for {period} was not found",
            },
        )
    return _report_detail_response(detail)


def _iso_week_bounds(period: str) -> tuple[date, date]:
    year_text, week_text = period.split("-W", maxsplit=1)
    try:
        start = date.fromisocalendar(int(year_text), int(week_text), 1)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_REPORT_PERIOD", "message": "invalid ISO week period"},
        ) from error
    return start, start + timedelta(days=6)


def _month_bounds(period: str) -> tuple[date, date]:
    year_text, month_text = period.split("-", maxsplit=1)
    year = int(year_text)
    month = int(month_text)
    try:
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_REPORT_PERIOD", "message": "invalid calendar month"},
        ) from error
    return start, end


class SpaStaticFiles(StaticFiles):
    """Serve built assets and the React entry document for client-side routes."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        if _is_service_route(path):
            # This mount is the final route in the application. Without an
            # explicit boundary, an unknown API or health path would be
            # rewritten to index.html and incorrectly reported as HTTP 200.
            raise StarletteHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Not Found",
            )
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code != status.HTTP_404_NOT_FOUND:
                raise
        else:
            if response.status_code != status.HTTP_404_NOT_FOUND:
                return response
        return await super().get_response("index.html", scope)


def _is_service_route(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/").casefold()
    return normalized in {"api", "health"} or normalized.startswith(("api/", "health/"))


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


def _pipeline_execution(
    repository: RepositoryPort,
    run: DailyRun,
) -> PipelineExecution | None:
    if run.pipeline_execution_id is None:
        return None
    return repository.get_pipeline_execution(run.pipeline_execution_id)


def _run_response(
    run: DailyRun,
    execution: PipelineExecution | None = None,
) -> RunSummary:
    return RunSummary(
        id=run.id,
        topic_id=run.topic_id,
        source_run_id=run.source_run_id,
        pipeline_execution_id=run.pipeline_execution_id,
        logical_date=run.logical_date,
        operation=run.operation,
        analysis_scope=run.analysis_scope,
        pipeline_execution_mode=run.pipeline_execution_mode,
        pipeline_selection_limit=run.pipeline_selection_limit,
        pipeline_status=None if execution is None else execution.status,
        pipeline_deadline_at=None if execution is None else execution.deadline_at,
        pipeline_completed_at=None if execution is None else execution.completed_at,
        pipeline_error_code=None if execution is None else execution.error_code,
        pipeline_error_detail=None if execution is None else execution.error_detail,
        publication_outcome=(
            None
            if run.operation is not RunOperation.PRODUCT_PUBLICATION
            or run.status not in (RunStatus.COMPLETE, RunStatus.PARTIAL)
            else "NO_UPDATE"
            if run.selected_count == 0
            else "UPDATE"
        ),
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


def _item_response(
    item: RunItem,
    *,
    canonical_arxiv_id: str,
    paper_title: str,
    paper_abstract: str | None = None,
    source_url: str | None = None,
    analysis_status: AnalysisAvailabilityStatus | None = None,
    related_work_status: RelatedWorkAvailabilityStatus | None = None,
    related_work_reason: str | None = None,
    comparison_status: ComparisonAvailabilityStatus | None = None,
    comparison_reason: str | None = None,
    trend_status: TrendAvailabilityStatus | None = None,
) -> RunItemResponse:
    return RunItemResponse(
        id=item.id,
        run_id=item.run_id,
        paper_id=item.paper_id,
        paper_version_id=item.paper_version_id,
        canonical_arxiv_id=canonical_arxiv_id,
        paper_title=paper_title,
        paper_abstract=paper_abstract,
        source_url=source_url,
        analysis_status=analysis_status,
        related_work_status=related_work_status,
        related_work_reason=related_work_reason,
        comparison_status=comparison_status,
        comparison_reason=comparison_reason,
        trend_status=trend_status,
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


def _run_detail_response(
    detail: RunDetail,
    execution: PipelineExecution | None = None,
) -> RunDetailResponse:
    summary = _run_response(detail.run, execution)
    return RunDetailResponse(
        **summary.model_dump(),
        items=[
            _item_response(
                item.item,
                canonical_arxiv_id=item.canonical_arxiv_id,
                paper_title=item.paper_title,
                paper_abstract=item.paper_abstract,
                source_url=item.source_url,
            )
            for item in detail.items
        ],
        report=None if detail.report is None else _report_response(detail.report),
    )


def _product_run_response(
    detail: ProductRunDetail,
    execution: PipelineExecution | None = None,
) -> DailyRunEnvelopeResponse:
    return DailyRunEnvelopeResponse(
        run=_run_response(detail.run, execution),
        items=[
            _item_response(
                item.item,
                canonical_arxiv_id=item.canonical_arxiv_id,
                paper_title=item.paper_title,
                paper_abstract=item.paper_abstract,
                source_url=item.source_url,
                analysis_status=item.analysis_status,
                related_work_status=item.related_work_status,
                related_work_reason=item.related_work_reason,
                comparison_status=item.comparison_status,
                comparison_reason=item.comparison_reason,
                trend_status=item.trend_status,
            )
            for item in detail.items
        ],
        report=None if detail.report is None else _report_detail_response(detail.report),
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


def _search_limits_response(session: SearchSession) -> SearchLimitsResponse:
    limits = session.limits
    return SearchLimitsResponse(
        max_steps=limits.max_steps,
        max_queries=limits.max_queries,
        max_queue_size=limits.max_queue_size,
        max_citation_depth=limits.max_citation_depth,
        max_candidates=limits.max_candidates,
        max_selected_candidates=limits.max_selected_candidates,
        per_operation_timeout_seconds=limits.per_operation_timeout_seconds,
        overall_timeout_seconds=limits.overall_timeout_seconds,
    )


def _search_session_response(session: SearchSession) -> SearchSessionResponse:
    return SearchSessionResponse(
        id=session.id,
        pipeline_execution_id=session.pipeline_execution_id,
        topic_id=session.topic_id,
        source_paper_id=session.source_paper_id,
        source_paper_version_id=session.source_paper_version_id,
        source_analysis_id=session.source_analysis_id,
        source_analysis_scope=session.source_analysis_scope,
        requested_year_from=session.requested_year_from,
        effective_year_to=session.effective_year_to,
        objective=session.objective,
        crawler_queries=(
            None if session.crawler_queries is None else list(session.crawler_queries)
        ),
        crawler_use_recommendations=session.crawler_use_recommendations,
        crawler_expand_references=session.crawler_expand_references,
        crawler_expand_citations=session.crawler_expand_citations,
        crawler_decision_reason=session.crawler_decision_reason,
        crawler_generated_at=session.crawler_generated_at,
        status=session.status,
        limits=_search_limits_response(session),
        started_at=session.started_at,
        completed_at=session.completed_at,
        stop_reason=session.stop_reason,
        error_code=session.error_code,
        error_detail=session.error_detail,
        provider=session.provider,
        configured_model=session.configured_model,
        model_version=session.model_version,
        prompt_version=session.prompt_version,
        usage=(
            None
            if session.usage is None
            else ModelUsageResponse(
                prompt_tokens=session.usage.prompt_tokens,
                completion_tokens=session.usage.completion_tokens,
                total_tokens=session.usage.total_tokens,
                call_count=session.usage.call_count,
                duration_ms=session.usage.duration_ms,
                estimated_cost_usd=session.usage.estimated_cost_usd,
            )
        ),
        schema_version=session.schema_version,
        created_at=session.created_at,
    )


def _search_action_response(action: SearchAction) -> SearchActionResponse:
    return SearchActionResponse(
        id=action.id,
        session_id=action.session_id,
        step=action.step,
        tool=action.tool,
        status=action.status,
        query=action.query,
        target_semantic_scholar_id=action.target_semantic_scholar_id,
        target_arxiv_id=action.target_arxiv_id,
        positive_paper_ids=list(action.positive_paper_ids),
        year_from=action.year_from,
        year_to=action.year_to,
        requested_limit=action.requested_limit,
        result_count=action.result_count,
        relation_depth=action.relation_depth,
        decision_reason=action.decision_reason,
        error_code=action.error_code,
        retryable=action.retryable,
        error_detail=action.error_detail,
        duration_ms=action.duration_ms,
        created_at=action.created_at,
        completed_at=action.completed_at,
        schema_version=action.schema_version,
    )


def _candidate_response(candidate: SearchCandidate) -> SearchCandidateResponse:
    return SearchCandidateResponse(
        id=candidate.id,
        session_id=candidate.session_id,
        external_paper_id=candidate.external_paper_id,
        semantic_scholar_id=candidate.semantic_scholar_id,
        local_paper_id=candidate.local_paper_id,
        local_paper_version_id=candidate.local_paper_version_id,
        discovered_by_action_id=candidate.discovered_by_action_id,
        origins=list(candidate.origins),
        relation_depth=candidate.relation_depth,
        scores=CandidateScoreComponentsResponse(
            semantic_scholar=candidate.scores.semantic_scholar,
            lexical=candidate.scores.lexical,
            vector=candidate.scores.vector,
            entity_overlap=candidate.scores.entity_overlap,
            citation=candidate.scores.citation,
            recommendation=candidate.scores.recommendation,
            final=candidate.scores.final,
        ),
        rank=candidate.rank,
        decision=candidate.decision,
        decision_reason=candidate.decision_reason,
        provider=candidate.provider,
        configured_model=candidate.configured_model,
        model_version=candidate.model_version,
        prompt_version=candidate.prompt_version,
        generated_at=candidate.generated_at,
        verification_status=candidate.verification_status,
        schema_version=candidate.schema_version,
        created_at=candidate.created_at,
    )


def _discovery_response(discovery: SearchCandidateDiscovery) -> CandidateDiscoveryResponse:
    return CandidateDiscoveryResponse(
        id=discovery.id,
        candidate_id=discovery.candidate_id,
        action_id=discovery.action_id,
        origin=discovery.origin,
        relation_depth=discovery.relation_depth,
        discovered_at=discovery.discovered_at,
    )


def _external_paper_response(paper: ExternalPaperStub) -> ExternalPaperResponse:
    return ExternalPaperResponse(
        id=paper.id,
        semantic_scholar_id=paper.semantic_scholar_id,
        title=paper.title,
        abstract=paper.abstract,
        year=paper.year,
        publication_date=paper.publication_date,
        venue=paper.venue,
        authors=list(paper.authors),
        external_ids=dict(paper.external_ids),
        arxiv_id=paper.arxiv_id,
        doi=paper.doi,
        citation_count=paper.citation_count,
        influential_citation_count=paper.influential_citation_count,
        full_text_available=paper.full_text_available,
        source="semantic_scholar",
        schema_version=paper.schema_version,
        created_at=paper.created_at,
        updated_at=paper.updated_at,
    )


def _relation_response(relation: PaperRelation) -> PaperRelationResponse:
    return PaperRelationResponse(
        id=relation.id,
        source_paper_id=relation.source_paper_id,
        source_paper_version_id=relation.source_paper_version_id,
        target_paper_id=relation.target_paper_id,
        target_paper_version_id=relation.target_paper_version_id,
        relation_type=relation.relation_type,
        provenance=relation.provenance,
        evidence_ids=list(relation.evidence_ids),
        justification=relation.justification,
        provider=relation.provider,
        model_version=relation.model_version,
        prompt_version=relation.prompt_version,
        confidence=relation.confidence,
        verification_status=relation.verification_status,
        generated_at=relation.generated_at,
        schema_version=relation.schema_version,
        created_at=relation.created_at,
    )


def _comparison_dimension_response(
    dimension: ComparisonDimension,
) -> ComparisonDimensionResponse:
    return ComparisonDimensionResponse(
        id=dimension.id,
        comparison_id=dimension.comparison_id,
        name=dimension.name,
        position=dimension.position,
        source_value=dimension.source_value,
        target_value=dimension.target_value,
        assessment=dimension.assessment,
        source_evidence_ids=list(dimension.source_evidence_ids),
        target_evidence_ids=list(dimension.target_evidence_ids),
        schema_version=dimension.schema_version,
        created_at=dimension.created_at,
    )


def _comparison_evidence_response(
    evidence: ComparisonEvidenceReference,
) -> ComparisonEvidenceResponse:
    return ComparisonEvidenceResponse(
        id=evidence.id,
        analysis_id=evidence.analysis_id,
        paper_id=evidence.paper_id,
        paper_version_id=evidence.paper_version_id,
        analysis_scope=evidence.analysis_scope,
        section=evidence.section,
        excerpt=evidence.excerpt,
        evidence_type=evidence.evidence_type,
        verification_status=evidence.verification_status,
    )


def _comparison_response(detail: ComparisonDetail) -> ComparisonResponse:
    comparison = detail.comparison
    return ComparisonResponse(
        id=comparison.id,
        search_session_id=comparison.search_session_id,
        source_paper_id=comparison.source_paper_id,
        source_paper_version_id=comparison.source_paper_version_id,
        source_analysis_id=comparison.source_analysis_id,
        source_analysis_scope=comparison.source_analysis_scope,
        target_paper_id=comparison.target_paper_id,
        target_paper_version_id=comparison.target_paper_version_id,
        target_analysis_id=comparison.target_analysis_id,
        target_analysis_scope=comparison.target_analysis_scope,
        comparability_status=comparison.comparability_status,
        comparability_reason=comparison.comparability_reason,
        summary=comparison.summary,
        dimensions=[_comparison_dimension_response(item) for item in comparison.dimensions],
        relations=[_relation_response(item) for item in detail.relations],
        evidence=[_comparison_evidence_response(item) for item in detail.evidence],
        provider=comparison.provider,
        configured_model=comparison.configured_model,
        model_version=comparison.model_version,
        prompt_version=comparison.prompt_version,
        generated_at=comparison.generated_at,
        source=comparison.source,
        verification_status=comparison.verification_status,
        usage=ModelUsageResponse(
            prompt_tokens=comparison.usage.prompt_tokens,
            completion_tokens=comparison.usage.completion_tokens,
            total_tokens=comparison.usage.total_tokens,
            call_count=comparison.usage.call_count,
            duration_ms=comparison.usage.duration_ms,
            estimated_cost_usd=comparison.usage.estimated_cost_usd,
        ),
        schema_version=comparison.schema_version,
        created_at=comparison.created_at,
    )


def _related_work_response(paper_id: UUID, detail: RelatedWorkDetail) -> RelatedWorkResponse:
    comparisons_by_id = {item.comparison.id: item.comparison for item in detail.comparisons}
    related_work_available = detail.session.status is SearchSessionStatus.COMPLETE
    return RelatedWorkResponse(
        paper_id=paper_id,
        related_work_status=("AVAILABLE" if related_work_available else "RELATED_WORK_UNAVAILABLE"),
        related_work_reason=(
            None
            if related_work_available
            else detail.session.error_code or "RELATED_WORK_UNAVAILABLE"
        ),
        session=_search_session_response(detail.session),
        actions=[_search_action_response(item) for item in detail.actions],
        items=[
            RelatedWorkItemResponse(
                candidate=_candidate_response(item.candidate),
                paper=_external_paper_response(item.external_paper),
                discoveries=[_discovery_response(value) for value in item.discoveries],
                relations=[_relation_response(value) for value in item.relations],
                comparison_id=item.comparison_id,
                comparison_status=(
                    "COMPARISON_UNAVAILABLE"
                    if item.comparison_id is None
                    else "LIMITED_COMPARABILITY"
                    if comparisons_by_id[item.comparison_id].comparability_status
                    is not ComparabilityStatus.DIRECTLY_COMPARABLE
                    else "AVAILABLE"
                ),
                comparison_reason=(
                    "NO_COMPATIBLE_HISTORICAL_ANALYSIS"
                    if item.comparison_id is None
                    else comparisons_by_id[item.comparison_id].comparability_reason
                    if comparisons_by_id[item.comparison_id].comparability_status
                    is not ComparabilityStatus.DIRECTLY_COMPARABLE
                    else None
                ),
            )
            for item in detail.items
        ],
        comparisons=[
            RelatedComparisonSummaryResponse(
                id=item.comparison.id,
                target_paper_id=item.comparison.target_paper_id,
                target_paper_version_id=item.comparison.target_paper_version_id,
                target_analysis_id=item.comparison.target_analysis_id,
                target_analysis_scope=item.comparison.target_analysis_scope,
                comparability_status=item.comparison.comparability_status,
                summary=item.comparison.summary,
                provider=item.comparison.provider,
                model_version=item.comparison.model_version,
                prompt_version=item.comparison.prompt_version,
                generated_at=item.comparison.generated_at,
                verification_status=item.comparison.verification_status,
            )
            for item in detail.comparisons
        ],
        total=len(detail.items),
    )


def _graph_model_provenance_response(
    mention_or_edge: GraphEntityMention | GraphEdge,
) -> GraphModelProvenanceResponse | None:
    provenance = mention_or_edge.model_provenance
    if provenance is None:
        return None
    return GraphModelProvenanceResponse(
        provider=provenance.provider,
        configured_model=provenance.configured_model,
        model_version=provenance.model_version,
        prompt_version=provenance.prompt_version,
    )


def _graph_mention_response(mention: GraphEntityMention) -> GraphEntityMentionResponse:
    return GraphEntityMentionResponse(
        id=mention.id,
        paper_id=mention.paper_id,
        paper_version_id=mention.paper_version_id,
        analysis_id=mention.analysis_id,
        comparison_id=mention.comparison_id,
        observed_label=mention.observed_label,
        provenance=mention.provenance,
        inferred=mention.provenance is RelationProvenance.LLM_INFERRED,
        evidence_ids=list(mention.evidence_ids),
        model_provenance=_graph_model_provenance_response(mention),
        confidence=mention.confidence,
        verification_status=mention.verification_status,
        generated_at=mention.generated_at,
        schema_version=mention.schema_version,
        created_at=mention.created_at,
    )


def _graph_node_response(detail: GraphNodeDetail) -> GraphNodeResponse:
    entity = detail.entity
    return GraphNodeResponse(
        id=entity.id,
        topic_id=entity.topic_id,
        entity_type=entity.entity_type,
        paper_id=entity.paper_id,
        canonical_label=entity.canonical_label,
        normalized_key=entity.normalized_key,
        display_label=entity.display_label,
        aliases=list(entity.aliases),
        provenance=entity.provenance,
        inferred=entity.provenance is RelationProvenance.LLM_INFERRED,
        source=entity.source,
        mention_count=detail.total_mentions,
        mentions=[_graph_mention_response(item) for item in detail.mentions],
        schema_version=entity.schema_version,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def _graph_edge_evidence_response(
    reference: GraphEdgeEvidenceReference,
) -> GraphEdgeEvidenceResponse:
    return GraphEdgeEvidenceResponse(
        evidence_id=reference.evidence_id,
        paper_id=reference.paper_id,
        paper_version_id=reference.paper_version_id,
        role=reference.role,
    )


def _graph_edge_response(detail: GraphEdgeDetail) -> GraphEdgeResponse:
    edge = detail.edge
    inferred = edge.provenance is RelationProvenance.LLM_INFERRED
    return GraphEdgeResponse(
        id=edge.id,
        source_entity_id=edge.source_entity_id,
        target_entity_id=edge.target_entity_id,
        relation_type=edge.relation_type,
        source_paper_version_id=edge.source_paper_version_id,
        target_paper_version_id=edge.target_paper_version_id,
        analysis_id=edge.analysis_id,
        comparison_id=edge.comparison_id,
        paper_relation_id=edge.paper_relation_id,
        provenance=edge.provenance,
        inferred=inferred,
        evidence_ids=list(edge.evidence_ids),
        evidence=[_graph_edge_evidence_response(item) for item in detail.evidence],
        justification=edge.justification,
        model_provenance=_graph_model_provenance_response(edge),
        confidence=edge.confidence,
        confidence_meaning=GRAPH_CONFIDENCE_MEANING if inferred else None,
        verification_status=edge.verification_status,
        generated_at=edge.generated_at,
        schema_version=edge.schema_version,
        created_at=edge.created_at,
    )


def _graph_response(graph: GraphView) -> KnowledgeGraphResponse:
    return KnowledgeGraphResponse(
        topic_id=graph.topic_id,
        as_of=graph.as_of,
        nodes=[_graph_node_response(item) for item in graph.nodes],
        edges=[_graph_edge_response(item) for item in graph.edges],
        total_nodes=graph.total_nodes,
        total_edges=graph.total_edges,
        total_mentions=graph.total_mentions,
        truncated=graph.truncated,
    )


def _trend_change_response(change: TrendChange) -> TrendChangeResponse:
    return TrendChangeResponse(
        current_count=change.current_count,
        preceding_count=change.preceding_count,
        absolute_change=change.absolute_change,
        denominator_count=change.denominator_count,
        relative_change=change.relative_change,
        growth_status=change.growth_status,
    )


def _trend_response(detail: TrendDetail) -> TrendSnapshotResponse:
    snapshot = detail.snapshot
    return TrendSnapshotResponse(
        id=snapshot.id,
        topic_id=snapshot.topic_id,
        as_of_date=snapshot.as_of_date,
        window=snapshot.window,
        window_start=snapshot.window_start,
        window_end=snapshot.window_end,
        preceding_window_start=snapshot.preceding_window_start,
        preceding_window_end=snapshot.preceding_window_end,
        included_paper_count=snapshot.included_paper_count,
        preceding_paper_count=snapshot.preceding_paper_count,
        paper_count_change=_trend_change_response(snapshot.paper_count_change),
        entity_counts=[
            TrendEntityCountResponse(
                entity_id=item.entity_id,
                entity_type=item.entity_type,
                label=item.label,
                change=_trend_change_response(item.change),
                newly_appearing=item.newly_appearing,
                recurring=item.recurring,
            )
            for item in snapshot.entity_counts
        ],
        total_entities=detail.total_entities,
        truncated=detail.truncated,
        relation_counts=[
            TrendRelationCountResponse(
                relation_type=item.relation_type,
                change=_trend_change_response(item.change),
            )
            for item in snapshot.relation_counts
        ],
        new_entity_ids=list(snapshot.new_entity_ids),
        recurring_entity_ids=list(snapshot.recurring_entity_ids),
        representative_papers=[
            TrendRepresentativePaperResponse(
                paper_id=item.paper_id,
                paper_version_id=item.paper_version_id,
                activity_date=item.activity_date,
                title=item.title,
            )
            for item in detail.representative_papers
        ],
        data_sufficiency=snapshot.data_sufficiency,
        preceding_data_sufficiency=snapshot.preceding_data_sufficiency,
        thresholds=TrendThresholdsResponse(
            limited_paper_count=snapshot.thresholds.limited_paper_count,
            sufficient_paper_count=snapshot.thresholds.sufficient_paper_count,
            minimum_growth_denominator=snapshot.thresholds.minimum_growth_denominator,
        ),
        aggregation_version=snapshot.aggregation_version,
        generated_at=snapshot.generated_at,
        schema_version=snapshot.schema_version,
    )


def _lineage_response(detail: LineageDetail) -> LineageResponse:
    snapshot = detail.snapshot
    evidence_by_edge: dict[UUID, list[GraphEdgeEvidenceReference]] = {}
    for item in detail.evidence:
        evidence_by_edge.setdefault(item.edge_id, []).append(item)
    return LineageResponse(
        id=snapshot.id,
        topic_id=snapshot.topic_id,
        root_paper_id=snapshot.root_paper_id,
        as_of_date=snapshot.as_of_date,
        nodes=[
            LineageNodeResponse(
                graph_entity_id=item.graph_entity_id,
                paper_id=item.paper_id,
                title=item.title,
                publication_date=item.publication_date,
                depth=item.depth,
            )
            for item in snapshot.nodes
        ],
        edges=[
            _graph_edge_response(
                GraphEdgeDetail(
                    edge=item,
                    evidence=tuple(evidence_by_edge.get(item.id, ())),
                )
            )
            for item in snapshot.edges
        ],
        permitted_relation_types=list(snapshot.permitted_relation_types),
        max_depth=snapshot.max_depth,
        max_nodes=snapshot.max_nodes,
        max_edges=snapshot.max_edges,
        truncated=snapshot.truncated,
        explicit_predecessor_available=snapshot.explicit_predecessor_available,
        verified_predecessor_available=snapshot.verified_predecessor_available,
        corpus_scope=snapshot.corpus_scope,
        limitations=list(snapshot.limitations),
        lineage_version=snapshot.lineage_version,
        generated_at=snapshot.generated_at,
        schema_version=snapshot.schema_version,
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


def _report_evidence_response(
    evidence: ReportEvidenceReference,
) -> ReportEvidenceReferenceResponse:
    return ReportEvidenceReferenceResponse(
        id=evidence.id,
        paper_id=evidence.paper_id,
        paper_version_id=evidence.paper_version_id,
        section=evidence.section,
        excerpt=evidence.excerpt,
        evidence_type=evidence.evidence_type,
        verification_status=evidence.verification_status,
    )


def _report_response(
    report: Report,
    *,
    evidence: tuple[ReportEvidenceReference, ...] = (),
) -> ReportResponse:
    return ReportResponse(
        id=report.id,
        run_id=report.run_id,
        topic_id=report.topic_id,
        logical_date=report.logical_date,
        status=report.status,
        publication_outcome=(
            None
            if report.report_type is not ReportType.DAILY
            else "NO_UPDATE"
            if report.counts.selected == 0
            else "UPDATE"
        ),
        title=report.title,
        summary=report.summary,
        source=report.source,
        generated_at=report.generated_at,
        schema_version=report.schema_version,
        created_at=report.created_at,
        failures=[_report_failure_response(failure) for failure in report.failures],
        sections=[
            ReportSectionResponse(
                id=section.id,
                report_id=section.report_id,
                kind=section.kind,
                narrative=section.narrative,
                evidence_ids=list(section.evidence_ids),
                schema_version=section.schema_version,
                created_at=section.created_at,
            )
            for section in report.sections
        ],
        report_type=report.report_type,
        period_start=report.period_start or report.logical_date,
        period_end=report.period_end or report.logical_date,
        counts=ReportCountsResponse(
            retrieved=report.counts.retrieved,
            selected=report.counts.selected,
            processed=report.counts.processed,
            completed=report.counts.completed,
            failed=report.counts.failed,
        ),
        highlighted_papers=[
            ReportPaperHighlightResponse(
                paper_id=item.paper_id,
                paper_version_id=item.paper_version_id,
                title=item.title,
                reason=item.reason,
                evidence_ids=list(item.evidence_ids),
            )
            for item in report.highlighted_papers
        ],
        major_entities=[
            ReportEntityHighlightResponse(
                graph_entity_id=item.graph_entity_id,
                entity_type=item.entity_type,
                label=item.label,
                distinct_paper_count=item.distinct_paper_count,
            )
            for item in report.major_entities
        ],
        notable_comparisons=[
            ReportComparisonHighlightResponse(
                comparison_id=item.comparison_id,
                summary=item.summary,
                comparability_status=item.comparability_status,
                evidence_ids=list(item.evidence_ids),
            )
            for item in report.notable_comparisons
        ],
        graph_changes=ReportGraphChangesResponse(
            entity_count=report.graph_changes.entity_count,
            edge_count=report.graph_changes.edge_count,
            new_entity_count=report.graph_changes.new_entity_count,
            inferred_edge_count=report.graph_changes.inferred_edge_count,
        ),
        trend_snapshot_ids=list(report.trend_snapshot_ids),
        lineage_highlights=[
            ReportLineageHighlightResponse(
                lineage_snapshot_id=item.lineage_snapshot_id,
                root_paper_id=item.root_paper_id,
                summary=item.summary,
                uncertain=item.uncertain,
            )
            for item in report.lineage_highlights
        ],
        evidence=[_report_evidence_response(item) for item in evidence],
        limitations=list(report.limitations),
        missing_sections=list(report.missing_sections),
        narrative_mode=report.narrative_mode,
        provider=report.provider,
        configured_model=report.configured_model,
        model_version=report.model_version,
        prompt_version=report.prompt_version,
        usage=(
            None
            if report.usage is None
            else ModelUsageResponse(
                prompt_tokens=report.usage.prompt_tokens,
                completion_tokens=report.usage.completion_tokens,
                total_tokens=report.usage.total_tokens,
                call_count=report.usage.call_count,
                duration_ms=report.usage.duration_ms,
                estimated_cost_usd=report.usage.estimated_cost_usd,
            )
        ),
        verification_status=report.verification_status,
    )


def _report_detail_response(detail: ReportDetail) -> ReportResponse:
    return _report_response(detail.report, evidence=detail.evidence)


app = create_app()
