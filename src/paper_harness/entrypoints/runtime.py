"""Shared construction for explicit operator and Daily Job entrypoints."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

from paper_harness.adapters.arxiv import ArxivClient
from paper_harness.adapters.config import load_topic_config
from paper_harness.adapters.deepseek import DeepSeekClient, DeepSeekSettings
from paper_harness.adapters.deepseek.client import PROMPT_VERSION, REPORT_PROMPT_VERSION
from paper_harness.adapters.gcp_identity import CloudRunIdTokenProvider
from paper_harness.adapters.grobid import GROBID_PARSER_NAME, GROBID_PARSER_VERSION, GrobidClient
from paper_harness.adapters.http_retry import HttpRetryPolicy
from paper_harness.adapters.postgres import PostgresRepository, create_postgres_engine
from paper_harness.adapters.semantic_scholar import (
    SemanticScholarClient,
    SemanticScholarSettings,
)
from paper_harness.adapters.specter2 import load_specter2_encoder
from paper_harness.application.analyze_papers import AnalysisReuseContract, AnalyzePapers
from paper_harness.application.compare_papers import ComparePapers, ComparisonInputMissingError
from paper_harness.application.daily_selection import (
    DAILY_SELECTION_POLICY_VERSION,
    DailySelectionCandidate,
    select_daily_papers,
)
from paper_harness.application.generate_periodic_report import GeneratePeriodicReport
from paper_harness.application.historical_backfill import (
    HistoricalBackfill,
    HistoricalBackfillTimeoutError,
    six_month_window,
)
from paper_harness.application.ingest_arxiv import SCHEDULE_TIME_ZONE, IngestArxiv
from paper_harness.application.pipeline_accounting import (
    AccountingArxiv,
    AccountingLLM,
    AccountingPdfParser,
    AccountingScholarlySearch,
    PipelineAccounting,
    PipelineAccountingSnapshot,
)
from paper_harness.application.pipeline_budget import (
    DEFAULT_PIPELINE_TIMEOUT_SECONDS,
    pipeline_budget,
)
from paper_harness.application.product_models import ProductPaperInput
from paper_harness.application.publish_product import (
    ProductRelatedWorkUnavailableError,
    PublishProduct,
)
from paper_harness.application.read_models import (
    ProductRunDetail,
    RelatedWorkDetail,
    RelatedWorkItem,
    RunDetail,
    SearchSessionDetail,
)
from paper_harness.application.related_work import (
    RelatedWorkInputError,
    RelatedWorkSearch,
    build_related_work_objective,
)
from paper_harness.domain.analysis import AnalysisScope
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import (
    M3_COMPARISON_PROMPT_VERSION,
    M3_CRAWLER_PROMPT_VERSION,
    M3_SELECTOR_PROMPT_VERSION,
    ComparisonBundle,
    ComparisonTargetDecision,
    HistoricalBackfillRun,
    SearchCandidate,
    SearchLimits,
    SelectionDecision,
)
from paper_harness.domain.identity import stable_pipeline_execution_id
from paper_harness.domain.models import (
    DailyRun,
    PaperStage,
    PipelineExecution,
    PipelineExecutionContract,
    PipelineExecutionMode,
    RunItemStatus,
    RunOperation,
    RunStatus,
    TopicConfig,
)
from paper_harness.domain.reports import Report, ReportNarrativeMode, ReportType
from paper_harness.ports.arxiv import ArxivPort
from paper_harness.ports.llm import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMPortError,
)
from paper_harness.ports.repository import RepositoryError, RepositoryPort
from paper_harness.ports.scholarly_search import (
    ScholarlySearchAuthenticationError,
    ScholarlySearchConfigurationError,
    ScholarlySearchError,
)
from paper_harness.ports.scientific_embedding import (
    ScientificEmbeddingConfigurationError,
    ScientificEmbeddingPortError,
)

PIPELINE_ORCHESTRATION_VERSION = "daily-pipeline-v1"


class DailyPipelineRunFailedError(RuntimeError):
    retryable = False

    def __init__(
        self,
        run: DailyRun,
        *,
        failures: tuple[DailyPipelineFailure, ...] = (),
    ) -> None:
        self.error_code = run.error_code or "DAILY_PIPELINE_STAGE_FAILED"
        self.failures = failures
        super().__init__(
            f"{run.operation.value} finished FAILED for {run.logical_date}: {self.error_code}"
        )


class DailyPipelineResumeError(ValueError):
    """Raised when persisted child-run provenance cannot be reused safely."""

    error_code = "DAILY_PIPELINE_RESUME_CONFLICT"
    retryable = False


class DailyPipelineDeadlineExceededError(TimeoutError):
    error_code = "DAILY_PIPELINE_DEADLINE_EXCEEDED"
    retryable = True


@dataclass(frozen=True, slots=True)
class DailyPipelineFailure:
    paper_id: UUID | None
    stage: str
    error_code: str
    retryable: bool
    detail: str


@dataclass(frozen=True, slots=True)
class DailyPipelineResult:
    ingestion_run: DailyRun
    analysis_run: DailyRun
    historical_backfill: HistoricalBackfillRun | None
    product_run: DailyRun
    evaluated_count: int
    relevant_count: int
    selected_count: int
    search_session_count: int
    comparison_count: int
    failures: tuple[DailyPipelineFailure, ...]
    duration_ms: int
    historical_analysis_run: DailyRun | None = None
    historical_materialized_count: int = 0
    accounting: PipelineAccountingSnapshot | None = None

    def __post_init__(self) -> None:
        if (
            min(
                self.evaluated_count,
                self.relevant_count,
                self.selected_count,
                self.search_session_count,
                self.comparison_count,
                self.duration_ms,
                self.historical_materialized_count,
            )
            < 0
        ):
            raise ValueError("daily pipeline result counts cannot be negative")
        if self.selected_count > self.relevant_count or self.relevant_count > self.evaluated_count:
            raise ValueError("daily pipeline selection counts are inconsistent")

    @property
    def status(self) -> RunStatus:
        return self.product_run.status


@dataclass(frozen=True, slots=True)
class _PipelineSelection:
    selected: tuple[DailySelectionCandidate, ...]
    evaluated_count: int
    relevant_count: int

    def __post_init__(self) -> None:
        if not 0 <= len(self.selected) <= self.relevant_count <= self.evaluated_count:
            raise ValueError("persisted pipeline selection counts are inconsistent")


@dataclass(frozen=True, slots=True)
class _RelatedSource:
    paper: ProductPaperInput
    related: RelatedWorkDetail


@contextmanager
def _pipeline_execution_lifecycle(
    repository: RepositoryPort,
    execution_id: UUID,
) -> Generator[None]:
    try:
        yield
    except Exception as error:
        execution = repository.get_pipeline_execution(execution_id)
        if execution is not None and execution.status is RunStatus.RUNNING:
            repository.fail_pipeline_execution(
                execution_id,
                completed_at=datetime.now(UTC),
                error_code=str(getattr(error, "error_code", "DAILY_PIPELINE_UNEXPECTED_FAILURE"))[
                    :80
                ],
                error_detail=str(error)[:1000] or type(error).__name__,
            )
        raise
    execution = repository.get_pipeline_execution(execution_id)
    if execution is not None and execution.status is RunStatus.RUNNING:
        repository.fail_pipeline_execution(
            execution_id,
            completed_at=datetime.now(UTC),
            error_code="DAILY_PIPELINE_TERMINAL_STATE_MISSING",
            error_detail="pipeline returned without persisting a terminal execution state",
        )
        raise RepositoryError("pipeline returned without a terminal execution state")


@contextmanager
def _locked_pipeline_execution_lifecycle(
    repository: RepositoryPort,
    topic: TopicConfig,
    requested: PipelineExecution,
) -> Generator[PipelineExecution]:
    with repository.daily_pipeline_lock(requested.id):
        existing = repository.get_pipeline_execution(requested.id)
        if existing is None:
            repository.upsert_topic(topic)
            execution = repository.start_pipeline_execution(requested)
        else:
            # Stable ownership is checked before mutable failed-run inputs refresh TopicRow.
            execution = repository.start_pipeline_execution(requested)
            repository.upsert_topic(topic)
        if execution.status is RunStatus.FAILED:
            execution = repository.restart_pipeline_execution(
                execution.id,
                started_at=requested.started_at,
                deadline_at=requested.deadline_at,
                contract=requested.contract,
            )
        with _pipeline_execution_lifecycle(repository, execution.id):
            yield execution


def _require_pipeline_time(deadline: float, operation: str) -> None:
    if monotonic() >= deadline:
        raise DailyPipelineDeadlineExceededError(
            f"daily pipeline deadline expired before {operation}"
        )


def execute_arxiv_ingestion(
    *,
    topic_config: Path,
    logical_date: date | None,
    pipeline_execution_mode: PipelineExecutionMode = PipelineExecutionMode.STANDALONE,
    pipeline_selection_limit: int | None = None,
) -> DailyRun:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is required for arXiv ingestion")
    topic = load_topic_config(topic_config)
    repository = PostgresRepository(create_postgres_engine(database_url))
    repository.check_ready()
    use_case = IngestArxiv(arxiv=ArxivClient(), repository=repository)
    return use_case.execute(
        topic,
        logical_date=logical_date,
        pipeline_execution_mode=pipeline_execution_mode,
        pipeline_selection_limit=pipeline_selection_limit,
    )


def execute_structured_analysis(
    *,
    topic_config: Path,
    paper_ids: tuple[UUID, ...],
    analysis_scope: AnalysisScope,
    logical_date: date | None,
    pipeline_execution_mode: PipelineExecutionMode = PipelineExecutionMode.STANDALONE,
    pipeline_selection_limit: int | None = None,
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
        pipeline_execution_mode=pipeline_execution_mode,
        pipeline_selection_limit=pipeline_selection_limit,
    )


def execute_daily_pipeline(
    *,
    topic_config: Path,
    logical_date: date | None,
    analysis_scope: AnalysisScope,
    narrative_mode: ReportNarrativeMode,
    max_selected_papers: int,
    reprocess: bool = False,
    backfill_max_queries: int = 8,
    backfill_per_query_limit: int = 100,
    backfill_timeout_seconds: float = 1800.0,
    search_limits: SearchLimits | None = None,
    max_comparisons_per_paper: int = 3,
    pipeline_timeout_seconds: int = DEFAULT_PIPELINE_TIMEOUT_SECONDS,
) -> DailyPipelineResult:
    """Execute the complete persisted M1-M4 workflow from one protected Job command."""

    started = monotonic()
    if not 1 <= max_comparisons_per_paper <= 10:
        raise ValueError("comparison count per paper must be between one and ten")
    limits = search_limits or SearchLimits(
        max_steps=12,
        max_queries=4,
        max_queue_size=100,
        max_citation_depth=2,
        max_candidates=100,
        max_selected_candidates=5,
        per_operation_timeout_seconds=60,
        overall_timeout_seconds=300,
    )
    topic = load_topic_config(topic_config)
    if max_selected_papers > topic.representative_full_text_count:
        raise ValueError("daily pipeline paper bound exceeds the topic configuration")
    budget = pipeline_budget(
        timeout_seconds=pipeline_timeout_seconds,
        selected_papers=max_selected_papers,
        search_timeout_seconds=limits.overall_timeout_seconds,
        comparisons_per_paper=max_comparisons_per_paper,
        backfill_timeout_seconds=backfill_timeout_seconds,
    )

    # Validate every required runtime boundary before the cursor or a child run
    # can be persisted. One pipeline instance reuses these adapters and the
    # SPECTER2 model rather than reconstructing them for every selected paper.
    llm_settings = DeepSeekSettings.from_environment()
    scholarly_settings = SemanticScholarSettings.from_environment()
    raw_parser = _grobid_parser(analysis_scope)
    repository = _ready_repository("daily pipeline")
    embeddings = _specter2_embeddings()
    accounting = PipelineAccounting()
    arxiv = AccountingArxiv(ArxivClient(), accounting)
    llm = AccountingLLM(DeepSeekClient(llm_settings), accounting)
    parser = None if raw_parser is None else AccountingPdfParser(raw_parser, accounting)
    scholarly_search = AccountingScholarlySearch(
        SemanticScholarClient(
            scholarly_settings,
            retry_policy=_scholarly_retry_policy(limits.per_operation_timeout_seconds),
        ),
        accounting,
    )
    analysis_reuse_contract = AnalysisReuseContract(
        provider=llm_settings.provider,
        configured_model=llm_settings.model,
        prompt_version=PROMPT_VERSION,
        parser_name=(GROBID_PARSER_NAME if analysis_scope is AnalysisScope.FULL_TEXT else None),
        parser_version=(
            GROBID_PARSER_VERSION if analysis_scope is AnalysisScope.FULL_TEXT else None
        ),
    )
    run_date = logical_date or datetime.now(UTC).astimezone(SCHEDULE_TIME_ZONE).date()
    execution_started_at = datetime.now(UTC)
    execution_mode = PipelineExecutionMode.REPROCESS if reprocess else PipelineExecutionMode.NORMAL
    execution = PipelineExecution(
        id=(uuid4() if reprocess else stable_pipeline_execution_id(topic.id, run_date)),
        topic_id=topic.id,
        logical_date=run_date,
        execution_mode=execution_mode,
        analysis_scope=analysis_scope,
        selection_limit=max_selected_papers,
        contract=PipelineExecutionContract(
            narrative_mode=narrative_mode.value,
            llm_provider=llm_settings.provider,
            llm_configured_model=llm_settings.model,
            analysis_prompt_version=PROMPT_VERSION,
            parser_name=analysis_reuse_contract.parser_name,
            parser_version=analysis_reuse_contract.parser_version,
            backfill_max_queries=backfill_max_queries,
            backfill_per_query_limit=backfill_per_query_limit,
            backfill_timeout_seconds=backfill_timeout_seconds,
            search_max_steps=limits.max_steps,
            search_max_queries=limits.max_queries,
            search_max_queue_size=limits.max_queue_size,
            search_max_citation_depth=limits.max_citation_depth,
            search_max_candidates=limits.max_candidates,
            search_max_selected_candidates=limits.max_selected_candidates,
            search_per_operation_timeout_seconds=(limits.per_operation_timeout_seconds),
            search_overall_timeout_seconds=limits.overall_timeout_seconds,
            max_comparisons_per_paper=max_comparisons_per_paper,
            pipeline_timeout_seconds=pipeline_timeout_seconds,
            crawler_prompt_version=M3_CRAWLER_PROMPT_VERSION,
            selector_prompt_version=M3_SELECTOR_PROMPT_VERSION,
            comparison_prompt_version=M3_COMPARISON_PROMPT_VERSION,
            report_prompt_version=REPORT_PROMPT_VERSION,
            daily_selection_policy_version=DAILY_SELECTION_POLICY_VERSION,
            pipeline_orchestration_version=PIPELINE_ORCHESTRATION_VERSION,
            embedding_model_identifier=embeddings.model_identifier,
            embedding_model_revision=embeddings.model_revision,
            embedding_tokenizer_identifier=embeddings.tokenizer_identifier,
            embedding_tokenizer_revision=embeddings.tokenizer_revision,
            embedding_dimension=embeddings.dimension,
            embedding_preprocessing_contract=embeddings.preprocessing_contract,
            embedding_model_provenance=embeddings.model_provenance,
            embedding_source=embeddings.source,
            topic_categories=topic.categories,
            topic_include_terms=topic.include_terms,
            topic_exclude_terms=topic.exclude_terms,
            topic_overlap_hours=topic.overlap_hours,
            topic_initial_lookback_days=topic.initial_lookback_days,
            topic_max_results=topic.max_results,
            topic_representative_full_text_count=(topic.representative_full_text_count),
        ),
        status=RunStatus.RUNNING,
        deadline_at=execution_started_at + timedelta(seconds=budget.timeout_seconds),
        started_at=execution_started_at,
        completed_at=None,
        error_code=None,
        error_detail=None,
        schema_version=1,
        created_at=execution_started_at,
    )
    with _locked_pipeline_execution_lifecycle(repository, topic, execution) as execution:
        if execution.status in (RunStatus.COMPLETE, RunStatus.PARTIAL):
            return _terminal_pipeline_replay(
                repository,
                topic=topic,
                execution=execution,
                narrative_mode=narrative_mode,
                accounting=accounting,
                started_monotonic=started,
                selection_limit=max_selected_papers,
            )
        deadline_monotonic = started + max(
            0.0,
            (execution.deadline_at - datetime.now(UTC)).total_seconds(),
        )
        _require_pipeline_time(deadline_monotonic, "arXiv ingestion")
        ingestion_run = IngestArxiv(arxiv=arxiv, repository=repository).execute(
            topic,
            logical_date=run_date,
            pipeline_execution_mode=execution.execution_mode,
            pipeline_selection_limit=max_selected_papers,
            pipeline_execution_id=execution.id,
            resume_existing=True,
        )
        if ingestion_run.status is RunStatus.FAILED:
            raise _failed_pipeline_run_error(repository, ingestion_run, "ingestion")
        if ingestion_run.status is not RunStatus.COMPLETE:
            raise DailyPipelineResumeError(
                f"arXiv ingestion returned unsupported {ingestion_run.status.value} state"
            )
        ingestion_detail = _require_run_detail(repository, ingestion_run.id, "ingestion")
        candidates = _selection_candidates(repository, ingestion_detail)
        selection = _pipeline_selection(
            repository,
            ingestion_detail,
            topic=topic,
            candidates=candidates,
            limit=max_selected_papers,
        )
        selected_paper_version_ids = tuple(item.paper_version_id for item in selection.selected)
        repository.persist_ingestion_selection(
            ingestion_run.id,
            selected_paper_version_ids=selected_paper_version_ids,
            updated_at=datetime.now(UTC),
        )
        analysis_run = AnalyzePapers(
            arxiv=arxiv,
            parser=parser,
            llm=llm,
            repository=repository,
        ).execute(
            topic,
            paper_version_ids=selected_paper_version_ids,
            analysis_scope=analysis_scope,
            logical_date=run_date,
            pipeline_execution_mode=execution.execution_mode,
            pipeline_selection_limit=max_selected_papers,
            pipeline_execution_id=execution.id,
            run_operation=RunOperation.STRUCTURED_ANALYSIS,
            resume_existing=True,
            reuse_contract=(None if reprocess else analysis_reuse_contract),
        )
        if analysis_run.status is RunStatus.FAILED:
            raise _failed_pipeline_run_error(repository, analysis_run, "structured analysis")
        analysis_detail = _require_run_detail(
            repository,
            analysis_run.id,
            "structured analysis",
        )
        child_failures = list(_run_item_failures(analysis_detail))

        publication_source = repository.get_product_publication_input(
            topic.id,
            run_date,
            pipeline_execution_id=execution.id,
        )
        if publication_source is None:
            raise DailyPipelineResumeError(
                "structured analysis has no exact product publication input"
            )
        if not publication_source.cards:
            product_run = PublishProduct(
                repository=repository,
                llm=llm if narrative_mode is ReportNarrativeMode.DEEPSEEK else None,
            ).execute(
                topic,
                logical_date=run_date,
                narrative_mode=narrative_mode,
                comparison_ids=frozenset(),
                pipeline_execution_id=execution.id,
                upstream_failures=(),
            )
            if product_run.status is RunStatus.FAILED:
                raise _failed_pipeline_run_error(
                    repository,
                    product_run,
                    "product publication",
                )
            product_detail = _require_run_detail(
                repository,
                product_run.id,
                "product publication",
            )
            result = DailyPipelineResult(
                ingestion_run=ingestion_run,
                analysis_run=analysis_run,
                historical_backfill=None,
                product_run=product_run,
                evaluated_count=selection.evaluated_count,
                relevant_count=selection.relevant_count,
                selected_count=0,
                search_session_count=0,
                comparison_count=0,
                failures=_deduplicate_pipeline_failures(
                    child_failures + list(_run_item_failures(product_detail))
                ),
                duration_ms=max(0, round((monotonic() - started) * 1000)),
                historical_analysis_run=None,
                historical_materialized_count=0,
                accounting=accounting.snapshot(),
            )
            repository.complete_pipeline_execution(
                execution.id,
                status=result.status,
                completed_at=datetime.now(UTC),
            )
            return result

        # One stable weekly six-month snapshot avoids re-querying and re-embedding
        # the same historical corpus every day. The daily seven-day arXiv window
        # overlaps this Monday anchor, so first deployment leaves no date gap.
        historical_through = run_date - timedelta(days=run_date.weekday())
        _require_pipeline_time(deadline_monotonic, "historical backfill")
        try:
            historical_backfill = HistoricalBackfill(
                repository=repository,
                scholarly_search=scholarly_search,
                embeddings=embeddings,
            ).execute(
                topic=topic,
                through=historical_through,
                max_queries=backfill_max_queries,
                per_query_limit=backfill_per_query_limit,
                overall_timeout_seconds=backfill_timeout_seconds,
            )
        except (
            ScholarlySearchError,
            ScientificEmbeddingPortError,
            DomainInvariantError,
            HistoricalBackfillTimeoutError,
        ) as error:
            if _is_fatal_pipeline_dependency_error(error):
                raise
            window_from, window_to = six_month_window(historical_through)
            failed_backfill = repository.get_historical_backfill(
                topic.id,
                window_from,
                window_to,
            )
            if failed_backfill is None:
                raise RepositoryError(
                    "failed historical enrichment has no persisted run state"
                ) from error
            historical_backfill = failed_backfill
            child_failures.append(_pipeline_failure(None, "HISTORICAL_BACKFILL", error))

        representative_arxiv_ids = repository.list_historical_representative_arxiv_ids(
            topic.id,
            limit=topic.representative_full_text_count,
        )
        representative_version_ids = (
            repository.list_historical_representative_version_ids(
                topic.id,
                limit=topic.representative_full_text_count,
            )
            if representative_arxiv_ids
            else ()
        )
        if len(representative_version_ids) != len(representative_arxiv_ids):
            representative_records = arxiv.get_papers_by_ids(
                canonical_arxiv_ids=representative_arxiv_ids,
            )
            representative_version_ids = repository.persist_historical_arxiv_records(
                topic=topic,
                records=representative_records,
                persisted_at=datetime.now(UTC),
            )

        def run_historical_analysis(
            target_version_ids: tuple[UUID, ...],
        ) -> DailyRun | None:
            existing = repository.get_analysis_run_for_date(
                topic.id,
                run_date,
                pipeline_execution_id=execution.id,
                operation=RunOperation.HISTORICAL_ANALYSIS,
            )
            if existing is None:
                ordered = tuple(dict.fromkeys(target_version_ids + representative_version_ids))
                analyzed = repository.get_reusable_analyzed_paper_version_ids(
                    ordered,
                    analysis_scope=analysis_scope,
                    provider=analysis_reuse_contract.provider,
                    configured_model=analysis_reuse_contract.configured_model,
                    prompt_version=analysis_reuse_contract.prompt_version,
                    parser_name=analysis_reuse_contract.parser_name,
                    parser_version=analysis_reuse_contract.parser_version,
                )
                batch = tuple(version_id for version_id in ordered if version_id not in analyzed)[
                    :max_selected_papers
                ]
            else:
                existing_detail = _require_run_detail(
                    repository,
                    existing.id,
                    "historical analysis",
                )
                batch = tuple(item.item.paper_version_id for item in existing_detail.items)
            if not batch:
                return None
            historical_run = AnalyzePapers(
                arxiv=arxiv,
                parser=parser,
                llm=llm,
                repository=repository,
            ).execute(
                topic,
                paper_version_ids=batch,
                analysis_scope=analysis_scope,
                logical_date=run_date,
                pipeline_execution_mode=execution.execution_mode,
                pipeline_selection_limit=max_selected_papers,
                pipeline_execution_id=execution.id,
                run_operation=RunOperation.HISTORICAL_ANALYSIS,
                resume_existing=True,
                reuse_contract=analysis_reuse_contract,
            )
            _require_run_detail(
                repository,
                historical_run.id,
                "historical analysis",
            )
            return historical_run

        existing_product = repository.get_product_run_for_date(
            topic.id,
            run_date,
            pipeline_execution_id=execution.id,
        )
        if existing_product is not None and existing_product.status in (
            RunStatus.COMPLETE,
            RunStatus.PARTIAL,
        ):
            historical_analysis_run = (
                run_historical_analysis(()) if publication_source.papers else None
            )
            # A published product is immutable. Invoke the publisher only to
            # validate the requested narrative mode and reload that exact run;
            # do not create fresh search sessions or comparisons on replay.
            product_run = PublishProduct(
                repository=repository,
                llm=llm if narrative_mode is ReportNarrativeMode.DEEPSEEK else None,
            ).execute(
                topic,
                logical_date=run_date,
                narrative_mode=narrative_mode,
                pipeline_execution_id=execution.id,
                upstream_failures=(),
            )
            if product_run.status is RunStatus.FAILED:
                raise _failed_pipeline_run_error(
                    repository,
                    product_run,
                    "product publication",
                )
            product_detail = _require_run_detail(
                repository,
                product_run.id,
                "product publication",
            )
            result = DailyPipelineResult(
                ingestion_run=ingestion_run,
                analysis_run=analysis_run,
                historical_backfill=historical_backfill,
                product_run=product_run,
                evaluated_count=selection.evaluated_count,
                relevant_count=selection.relevant_count,
                selected_count=len(selection.selected),
                search_session_count=0,
                comparison_count=0,
                failures=_deduplicate_pipeline_failures(
                    child_failures + list(_run_item_failures(product_detail))
                ),
                duration_ms=max(0, round((monotonic() - started) * 1000)),
                historical_analysis_run=historical_analysis_run,
                historical_materialized_count=len(representative_version_ids),
                accounting=accounting.snapshot(),
            )
            repository.complete_pipeline_execution(
                execution.id,
                status=result.status,
                completed_at=datetime.now(UTC),
            )
            return result

        failures: list[DailyPipelineFailure] = list(child_failures)
        search_session_count = 0
        comparison_count = 0
        current_comparison_ids: set[UUID] = set()
        related_sources: list[_RelatedSource] = []
        for paper in publication_source.papers:
            try:
                _require_pipeline_time(deadline_monotonic, "related-work search")
                search_detail = RelatedWorkSearch(
                    repository=repository,
                    scholarly_search=scholarly_search,
                    llm=llm,
                    embeddings=embeddings,
                ).execute(
                    topic=topic,
                    source_paper_id=paper.paper_id,
                    objective=build_related_work_objective(topic),
                    year_from=max(1900, run_date.year - 10),
                    year_to=run_date.year,
                    limits=limits,
                    pipeline_execution_id=execution.id,
                    source_paper_version_id=paper.paper_version_id,
                    source_analysis_id=paper.analysis.analysis.id,
                    source_analysis_scope=paper.analysis.analysis.analysis_scope,
                )
                search_session_count += 1
            except (
                RelatedWorkInputError,
                ScholarlySearchError,
                ScientificEmbeddingPortError,
                LLMPortError,
                DomainInvariantError,
            ) as error:
                if _is_fatal_pipeline_dependency_error(error):
                    raise
                failures.append(_pipeline_failure(paper.paper_id, "PRIOR_WORK_RETRIEVED", error))
                continue
            related = repository.get_related_work(
                paper.paper_id,
                paper_version_id=paper.paper_version_id,
                search_session_id=search_detail.session.id,
            )
            if related is None:
                error = ProductRelatedWorkUnavailableError(
                    "completed related-work search has no persisted candidate projection"
                )
                failures.append(_pipeline_failure(paper.paper_id, "PRIOR_WORK_RETRIEVED", error))
                continue
            related_sources.append(_RelatedSource(paper=paper, related=related))

        existing_historical_analysis = repository.get_analysis_run_for_date(
            topic.id,
            run_date,
            pipeline_execution_id=execution.id,
            operation=RunOperation.HISTORICAL_ANALYSIS,
        )
        historical_analysis_budget = (
            0
            if existing_historical_analysis is not None
            and existing_historical_analysis.status in (RunStatus.COMPLETE, RunStatus.PARTIAL)
            else max_selected_papers
        )
        planned_sources, _remaining_analysis_budget = _assign_comparison_targets(
            repository,
            tuple(related_sources),
            analysis_scope=analysis_scope,
            max_targets_per_source=max_comparisons_per_paper,
            analysis_budget=historical_analysis_budget,
            reuse_contract=analysis_reuse_contract,
        )
        planned_sources, target_materialized_version_ids = _materialize_comparison_targets(
            repository,
            arxiv,
            topic,
            planned_sources,
        )
        target_version_ids = tuple(
            dict.fromkeys(
                item.candidate.local_paper_version_id
                for source in planned_sources
                for item in source.related.items
                if item.candidate.comparison_target_decision is ComparisonTargetDecision.TARGET
                and item.candidate.local_paper_version_id is not None
            )
        )
        historical_analysis_run = run_historical_analysis(target_version_ids)

        for source in planned_sources:
            source_comparison_ids: set[UUID] = set()
            for related_item in source.related.items:
                candidate = related_item.candidate
                if candidate.comparison_target_decision is not ComparisonTargetDecision.TARGET:
                    continue
                try:
                    if related_item.comparison_id is not None:
                        source_comparison_ids.add(related_item.comparison_id)
                        continue
                    if candidate.local_paper_version_id is None:
                        raise ComparisonInputMissingError(
                            "bounded comparison target has no local arXiv version"
                        )
                    target_input = repository.get_comparison_paper_input(
                        candidate.local_paper_version_id,
                        analysis_scope=analysis_scope,
                        provider=analysis_reuse_contract.provider,
                        configured_model=analysis_reuse_contract.configured_model,
                        prompt_version=analysis_reuse_contract.prompt_version,
                        parser_name=analysis_reuse_contract.parser_name,
                        parser_version=analysis_reuse_contract.parser_version,
                    )
                    if target_input is None:
                        raise ComparisonInputMissingError(
                            "bounded comparison target lacks exact-scope analysis"
                        )
                    _require_pipeline_time(deadline_monotonic, "paper comparison")
                    comparison = ComparePapers(repository=repository, llm=llm).execute(
                        search_session_id=source.related.session.id,
                        source_paper_version_id=source.paper.paper_version_id,
                        target_paper_version_id=candidate.local_paper_version_id,
                        target_analysis_id=target_input.analysis_id,
                    )
                except (ComparisonInputMissingError, LLMPortError, DomainInvariantError) as error:
                    if _is_fatal_pipeline_dependency_error(error):
                        raise
                    failures.append(_pipeline_failure(source.paper.paper_id, "COMPARED", error))
                    continue
                comparison_count += 1
                source_comparison_ids.add(comparison.comparison.id)
            current_comparison_ids.update(source_comparison_ids)

        product_run = PublishProduct(
            repository=repository,
            llm=llm if narrative_mode is ReportNarrativeMode.DEEPSEEK else None,
        ).execute(
            topic,
            logical_date=run_date,
            narrative_mode=narrative_mode,
            comparison_ids=frozenset(current_comparison_ids),
            pipeline_execution_id=execution.id,
            upstream_failures=(),
        )
        if product_run.status is RunStatus.FAILED:
            raise _failed_pipeline_run_error(
                repository,
                product_run,
                "product publication",
            )
        product_detail = _require_run_detail(repository, product_run.id, "product publication")
        failures.extend(_run_item_failures(product_detail))
        result = DailyPipelineResult(
            ingestion_run=ingestion_run,
            analysis_run=analysis_run,
            historical_backfill=historical_backfill,
            product_run=product_run,
            evaluated_count=selection.evaluated_count,
            relevant_count=selection.relevant_count,
            selected_count=len(selection.selected),
            search_session_count=search_session_count,
            comparison_count=comparison_count,
            failures=_deduplicate_pipeline_failures(failures),
            duration_ms=max(0, round((monotonic() - started) * 1000)),
            historical_analysis_run=historical_analysis_run,
            historical_materialized_count=len(
                set(representative_version_ids) | set(target_materialized_version_ids)
            ),
            accounting=accounting.snapshot(),
        )
        repository.complete_pipeline_execution(
            execution.id,
            status=result.status,
            completed_at=datetime.now(UTC),
        )
        return result


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


def execute_product_publication(
    *,
    topic_config: Path,
    logical_date: date | None,
    narrative_mode: ReportNarrativeMode,
) -> DailyRun:
    """Build graph/trends/lineage and atomically publish one daily report."""

    llm = (
        DeepSeekClient(DeepSeekSettings.from_environment())
        if narrative_mode is ReportNarrativeMode.DEEPSEEK
        else None
    )
    repository = _ready_repository("product publication")
    topic = load_topic_config(topic_config)
    return PublishProduct(repository=repository, llm=llm).execute(
        topic,
        narrative_mode=narrative_mode,
        logical_date=logical_date,
    )


def _terminal_pipeline_replay(
    repository: RepositoryPort,
    *,
    topic: TopicConfig,
    execution: PipelineExecution,
    narrative_mode: ReportNarrativeMode,
    accounting: PipelineAccounting,
    started_monotonic: float,
    selection_limit: int,
) -> DailyPipelineResult:
    del narrative_mode, selection_limit
    ingestion_run = repository.get_run_for_date(
        topic.id,
        execution.logical_date,
        pipeline_execution_id=execution.id,
    )
    analysis_run = repository.get_analysis_run_for_date(
        topic.id,
        execution.logical_date,
        operation=RunOperation.STRUCTURED_ANALYSIS,
        pipeline_execution_id=execution.id,
    )
    product_run = repository.get_product_run_for_date(
        topic.id,
        execution.logical_date,
        pipeline_execution_id=execution.id,
    )
    if (
        ingestion_run is None
        or ingestion_run.status is not RunStatus.COMPLETE
        or analysis_run is None
        or analysis_run.status not in (RunStatus.COMPLETE, RunStatus.PARTIAL)
        or product_run is None
        or product_run.status not in (RunStatus.COMPLETE, RunStatus.PARTIAL)
    ):
        raise DailyPipelineResumeError(
            "terminal pipeline execution has an incomplete or inconsistent child envelope"
        )
    historical_through = execution.logical_date - timedelta(days=execution.logical_date.weekday())
    window_from, window_to = six_month_window(historical_through)
    historical_backfill = repository.get_historical_backfill(
        topic.id,
        window_from,
        window_to,
    )
    if historical_backfill is None and analysis_run.selected_count > 0:
        raise DailyPipelineResumeError("terminal pipeline execution is missing its backfill")
    ingestion_detail = repository.get_run(ingestion_run.id)
    if ingestion_detail is None:
        raise DailyPipelineResumeError(
            "terminal pipeline execution is missing its ingestion detail"
        )
    product_detail = repository.get_product_run(
        logical_date=execution.logical_date,
        topic_slug=topic.slug,
        pipeline_execution_id=execution.id,
    )
    if product_detail is None:
        raise DailyPipelineResumeError("terminal pipeline execution is missing its product")
    if product_detail.report is None:
        raise DailyPipelineResumeError(
            "terminal pipeline execution is missing its persisted report"
        )
    historical_analysis_run = repository.get_analysis_run_for_date(
        topic.id,
        execution.logical_date,
        operation=RunOperation.HISTORICAL_ANALYSIS,
        pipeline_execution_id=execution.id,
    )
    analysis_detail = repository.get_run(analysis_run.id)
    if analysis_detail is None:
        raise DailyPipelineResumeError("terminal pipeline execution is missing its analysis detail")
    failures = list(_run_item_failures(ingestion_detail))
    failures.extend(_run_item_failures(analysis_detail))
    if (
        historical_analysis_run is not None
        and repository.get_run(historical_analysis_run.id) is None
    ):
        raise DailyPipelineResumeError(
            "terminal pipeline execution is missing its historical-analysis detail"
        )
    failures.extend(_run_item_failures(product_detail))
    result = DailyPipelineResult(
        ingestion_run=ingestion_run,
        analysis_run=analysis_run,
        historical_backfill=historical_backfill,
        product_run=product_run,
        evaluated_count=max(ingestion_run.normalized_count, analysis_run.selected_count),
        relevant_count=analysis_run.selected_count,
        selected_count=analysis_run.selected_count,
        search_session_count=0,
        comparison_count=0,
        failures=_deduplicate_pipeline_failures(failures),
        duration_ms=max(0, round((monotonic() - started_monotonic) * 1000)),
        historical_analysis_run=historical_analysis_run,
        historical_materialized_count=0,
        accounting=accounting.snapshot(),
    )
    return result


def _assign_comparison_targets(
    repository: RepositoryPort,
    sources: tuple[_RelatedSource, ...],
    *,
    analysis_scope: AnalysisScope,
    max_targets_per_source: int,
    analysis_budget: int,
    reuse_contract: AnalysisReuseContract,
) -> tuple[tuple[_RelatedSource, ...], int]:
    remaining_budget = analysis_budget
    budgeted_arxiv_ids: set[str] = set()
    state: list[
        tuple[
            _RelatedSource,
            tuple[SearchCandidate, ...],
            dict[UUID, RelatedWorkItem],
            tuple[SearchCandidate, ...],
            str,
        ]
    ] = []

    def needs_analysis(candidate: SearchCandidate) -> bool:
        return (
            candidate.local_paper_version_id is None
            or repository.get_comparison_paper_input(
                candidate.local_paper_version_id,
                analysis_scope=analysis_scope,
                provider=reuse_contract.provider,
                configured_model=reuse_contract.configured_model,
                prompt_version=reuse_contract.prompt_version,
                parser_name=reuse_contract.parser_name,
                parser_version=reuse_contract.parser_version,
            )
            is None
        )

    for source in sources:
        items_by_candidate = {item.candidate.id: item for item in source.related.items}
        candidates = tuple(
            sorted(
                (item.candidate for item in source.related.items),
                key=lambda candidate: (candidate.rank, str(candidate.id)),
            )
        )
        arxiv_candidates = tuple(
            candidate
            for candidate in candidates
            if items_by_candidate[candidate.id].external_paper.arxiv_id is not None
        )
        reusable_arxiv = tuple(
            candidate
            for candidate in arxiv_candidates
            if candidate.decision is not SelectionDecision.REJECTED
            and not needs_analysis(candidate)
        )
        selected_arxiv = tuple(
            candidate
            for candidate in arxiv_candidates
            if candidate.decision is SelectionDecision.SELECTED
        )
        if reusable_arxiv:
            preferred = reusable_arxiv[:max_targets_per_source]
            preference = "REUSABLE"
        elif selected_arxiv:
            preferred = selected_arxiv[:max_targets_per_source]
            preference = "SELECTED"
        else:
            preferred = arxiv_candidates[:max_targets_per_source]
            preference = "RANKED"
        state.append(
            (
                source,
                candidates,
                items_by_candidate,
                preferred,
                preference,
            )
        )

    admitted_ids: set[UUID] = set()
    budget_rejected_ids: set[UUID] = set()
    for round_index in range(max_targets_per_source):
        for _source, _candidates, items, preferred, _preference in state:
            if round_index >= len(preferred):
                continue
            candidate = preferred[round_index]
            item = items[candidate.id]
            arxiv_id = item.external_paper.arxiv_id
            if arxiv_id is None:
                continue
            if needs_analysis(candidate) and arxiv_id not in budgeted_arxiv_ids:
                if remaining_budget < 1:
                    budget_rejected_ids.add(candidate.id)
                    continue
                remaining_budget -= 1
                budgeted_arxiv_ids.add(arxiv_id)
            admitted_ids.add(candidate.id)

    planned: list[_RelatedSource] = []
    for source, candidates, items, preferred, preference in state:
        if not candidates:
            planned.append(source)
            continue
        preferred_ids = {candidate.id for candidate in preferred}
        updates: list[SearchCandidate] = []
        for candidate in candidates:
            item = items[candidate.id]
            if item.external_paper.arxiv_id is None:
                decision = ComparisonTargetDecision.INELIGIBLE
                reason = "Candidate has no arXiv-hosted full text and remains related-only."
            elif candidate.id in admitted_ids:
                decision = ComparisonTargetDecision.TARGET
                if preference == "REUSABLE":
                    reason = "Existing exact-scope analysis admitted to the bounded target set."
                elif preference == "RANKED":
                    reason = (
                        "Deterministic arXiv candidate because no model-selected candidate "
                        "was comparable."
                    )
                else:
                    reason = "Model-selected arXiv candidate admitted to the bounded target set."
            elif candidate.id in budget_rejected_ids:
                decision = ComparisonTargetDecision.NOT_TARGET
                reason = "Eligible arXiv candidate exceeds the pipeline analysis budget."
            elif candidate.id not in preferred_ids:
                decision = ComparisonTargetDecision.NOT_TARGET
                reason = (
                    "Eligible arXiv candidate falls outside the deterministic per-source bound."
                )
            else:
                decision = ComparisonTargetDecision.NOT_TARGET
                reason = "Eligible arXiv candidate was not admitted to the bounded target set."
            updates.append(
                replace(
                    candidate,
                    comparison_target_decision=decision,
                    comparison_target_reason=reason,
                )
            )
        repository.update_search_comparison_targets(
            source.related.session.id,
            tuple(updates),
        )
        reloaded = repository.get_related_work(
            source.paper.paper_id,
            paper_version_id=source.paper.paper_version_id,
            search_session_id=source.related.session.id,
        )
        if reloaded is None:
            raise DailyPipelineResumeError("persisted comparison-target plan cannot be read back")
        planned.append(replace(source, related=reloaded))
    return tuple(planned), remaining_budget


def _materialize_comparison_targets(
    repository: RepositoryPort,
    arxiv: ArxivPort,
    topic: TopicConfig,
    sources: tuple[_RelatedSource, ...],
) -> tuple[tuple[_RelatedSource, ...], tuple[UUID, ...]]:
    pending = tuple(
        (item.candidate.id, item.external_paper.arxiv_id)
        for source in sources
        for item in source.related.items
        if item.candidate.comparison_target_decision is ComparisonTargetDecision.TARGET
        and item.candidate.local_paper_version_id is None
        and item.external_paper.arxiv_id is not None
    )
    if not pending:
        return sources, ()
    requested_ids = tuple(sorted({arxiv_id for _candidate_id, arxiv_id in pending}))
    records = arxiv.get_papers_by_ids(canonical_arxiv_ids=requested_ids)
    records_by_id = {record.canonical_arxiv_id: record for record in records}
    materialized = repository.materialize_search_candidate_arxiv_records(
        topic=topic,
        candidates=tuple(
            (candidate_id, records_by_id[arxiv_id])
            for candidate_id, arxiv_id in pending
            if arxiv_id in records_by_id
        ),
        persisted_at=datetime.now(UTC),
    )
    version_ids = tuple(
        dict.fromkeys(version_id for _candidate, _paper, version_id in materialized)
    )
    reloaded_sources: list[_RelatedSource] = []
    for source in sources:
        related = repository.get_related_work(
            source.paper.paper_id,
            paper_version_id=source.paper.paper_version_id,
            search_session_id=source.related.session.id,
        )
        if related is None:
            raise DailyPipelineResumeError("materialized comparison targets cannot be read back")
        reloaded_sources.append(replace(source, related=related))
    return tuple(reloaded_sources), version_ids


def execute_periodic_report(
    *,
    topic_config: Path,
    report_type: ReportType,
    period_start: date,
    period_end: date,
    narrative_mode: ReportNarrativeMode,
) -> Report:
    """Publish one eligible weekly or monthly report from persisted daily reports."""

    llm = (
        DeepSeekClient(DeepSeekSettings.from_environment())
        if narrative_mode is ReportNarrativeMode.DEEPSEEK
        else None
    )
    repository = _ready_repository("periodic report generation")
    topic = load_topic_config(topic_config)
    return GeneratePeriodicReport(repository=repository, llm=llm).execute(
        topic,
        report_type=report_type,
        period_start=period_start,
        period_end=period_end,
        narrative_mode=narrative_mode,
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


def _require_run_detail(
    repository: RepositoryPort,
    run_id: UUID,
    stage: str,
) -> RunDetail:
    detail = repository.get_run(run_id)
    if detail is None:
        raise RepositoryError(f"persisted {stage} run could not be reloaded")
    return detail


def _failed_pipeline_run_error(
    repository: RepositoryPort,
    run: DailyRun,
    stage: str,
) -> DailyPipelineRunFailedError:
    detail = _require_run_detail(repository, run.id, stage)
    return DailyPipelineRunFailedError(run, failures=_run_item_failures(detail))


def _selection_candidates(
    repository: RepositoryPort,
    detail: RunDetail,
) -> tuple[DailySelectionCandidate, ...]:
    candidates: list[DailySelectionCandidate] = []
    for item_detail in detail.items:
        item = item_detail.item
        paper = repository.get_paper(item.paper_id)
        if paper is None:
            raise RepositoryError("ingestion selection references a missing paper")
        version = next(
            (value for value in paper.versions if value.id == item.paper_version_id),
            None,
        )
        if version is None:
            raise RepositoryError("ingestion selection references a missing paper version")
        candidates.append(
            DailySelectionCandidate(
                paper_id=item.paper_id,
                paper_version_id=item.paper_version_id,
                canonical_arxiv_id=version.canonical_arxiv_id,
                title=version.title,
                abstract=version.abstract,
                categories=version.categories,
                updated_at=version.updated_at,
            )
        )
    return tuple(candidates)


def _pipeline_selection(
    repository: RepositoryPort,
    detail: RunDetail,
    *,
    topic: TopicConfig,
    candidates: tuple[DailySelectionCandidate, ...],
    limit: int,
) -> _PipelineSelection:
    candidate_versions = {candidate.paper_version_id for candidate in candidates}
    item_versions = {item.item.paper_version_id for item in detail.items}
    if candidate_versions != item_versions:
        raise RepositoryError("ingestion selection candidates do not match persisted run items")

    decision_stages = (PaperStage.RELEVANCE_SCORED, PaperStage.SELECTED)
    if detail.items and all(item.item.stage in decision_stages for item in detail.items):
        persisted_selected = {
            item.item.paper_version_id
            for item in detail.items
            if item.item.stage is PaperStage.SELECTED
        }
        selected = tuple(
            candidate
            for candidate in candidates
            if candidate.paper_version_id in persisted_selected
        )
        return _PipelineSelection(
            selected=selected,
            evaluated_count=len(candidates),
            relevant_count=len(selected),
        )

    if any(item.item.stage is not PaperStage.NORMALIZED for item in detail.items):
        raise DailyPipelineResumeError(
            "ingestion items contain a partial or unsupported selection decision"
        )
    if detail.run.pipeline_execution_mode is PipelineExecutionMode.REPROCESS:
        baseline_version_ids = repository.get_reprocessing_baseline_paper_version_ids(
            topic.id,
            detail.run.logical_date,
        )
        baseline_candidates = tuple(
            candidate
            for candidate in candidates
            if candidate.paper_version_id in baseline_version_ids
        )
        if baseline_candidates:
            ranked = select_daily_papers(topic, baseline_candidates, limit=limit)
            return _PipelineSelection(
                selected=tuple(item.candidate for item in ranked.selected),
                evaluated_count=len(candidates),
                relevant_count=len(ranked.eligible),
            )
    published_versions = repository.get_canonically_published_paper_version_ids(
        tuple(candidate.paper_version_id for candidate in candidates),
    )
    novel_candidates = tuple(
        candidate
        for candidate in candidates
        if candidate.paper_version_id not in published_versions
    )
    ranked = select_daily_papers(topic, novel_candidates, limit=limit)
    return _PipelineSelection(
        selected=tuple(item.candidate for item in ranked.selected),
        evaluated_count=len(candidates),
        relevant_count=len(ranked.eligible),
    )


def _is_fatal_pipeline_dependency_error(error: BaseException) -> bool:
    return isinstance(
        error,
        (
            LLMAuthenticationError,
            LLMConfigurationError,
            ScholarlySearchAuthenticationError,
            ScholarlySearchConfigurationError,
            ScientificEmbeddingConfigurationError,
            RepositoryError,
        ),
    )


def _pipeline_failure(
    paper_id: UUID | None,
    stage: str,
    error: BaseException,
) -> DailyPipelineFailure:
    return DailyPipelineFailure(
        paper_id=paper_id,
        stage=stage,
        error_code=str(getattr(error, "error_code", "DAILY_PIPELINE_ITEM_FAILED")),
        retryable=bool(getattr(error, "retryable", False)),
        detail=str(error)[:500],
    )


def _run_item_failures(
    detail: RunDetail | ProductRunDetail,
) -> tuple[DailyPipelineFailure, ...]:
    failures: list[DailyPipelineFailure] = []
    for item_detail in detail.items:
        item = item_detail.item
        if item.status is not RunItemStatus.FAILED:
            continue
        failures.append(
            DailyPipelineFailure(
                paper_id=item.paper_id,
                stage=(item.failed_stage or item.stage).value,
                error_code=item.error_code or "DAILY_PIPELINE_ITEM_FAILED",
                retryable=bool(item.retryable),
                detail=(item.error_detail or "item failed without diagnostic detail")[:500],
            )
        )
    return tuple(failures)


def _deduplicate_pipeline_failures(
    failures: list[DailyPipelineFailure],
) -> tuple[DailyPipelineFailure, ...]:
    by_identity: dict[tuple[UUID | None, str, str], DailyPipelineFailure] = {}
    for failure in failures:
        by_identity[(failure.paper_id, failure.stage, failure.error_code)] = failure
    return tuple(
        by_identity[key] for key in sorted(by_identity, key=lambda value: tuple(map(str, value)))
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
