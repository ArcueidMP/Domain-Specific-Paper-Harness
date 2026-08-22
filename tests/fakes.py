"""Deterministic test doubles kept outside production wiring."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID, uuid4, uuid5

from paper_harness.application.product_models import (
    GraphCorpusInput,
    GraphWriteResult,
    PeriodicReportInput,
    ProductFailureInput,
    ProductPublicationInput,
)
from paper_harness.application.read_models import (
    AnalysisDetail,
    AnalysisTarget,
    ComparisonDetail,
    GraphView,
    HistoricalRetrievalMatch,
    LineageDetail,
    PaperDetail,
    ProductRunDetail,
    PublicationArtifactSummary,
    RelatedWorkDetail,
    ReportDetail,
    RunDetail,
    RunItemDetail,
    SearchSessionDetail,
    StoredTopic,
    TrendDetail,
)
from paper_harness.domain.analysis import (
    AnalysisBundle,
    AnalysisScope,
    Evidence,
    ParsedPaper,
    VerificationStatus,
)
from paper_harness.domain.errors import DuplicateDailyRunError
from paper_harness.domain.historical import (
    BackfillStatus,
    ComparisonBundle,
    ComparisonPaperInput,
    ComparisonTargetDecision,
    ExternalPaperStub,
    GeneratedCrawlerPlan,
    HistoricalBackfillRun,
    HistoricalCorpusEntry,
    RelationProvenance,
    ScientificEmbedding,
    SearchAction,
    SearchCandidate,
    SearchCandidateDiscovery,
    SearchModelProvenance,
    SearchSession,
    SearchSessionStatus,
    SearchStopReason,
)
from paper_harness.domain.identity import stable_paper_id, stable_paper_version_id
from paper_harness.domain.knowledge import (
    GraphEntityType,
    GraphRelationType,
    KnowledgeGraphBundle,
    LineageSnapshot,
    TrendSnapshot,
    TrendWindow,
)
from paper_harness.domain.models import (
    DailyRun,
    IngestionCursor,
    Paper,
    PaperStage,
    PipelineExecution,
    PipelineExecutionContract,
    PipelineExecutionMode,
    RunItem,
    RunItemStatus,
    RunOperation,
    RunStatus,
    TopicConfig,
)
from paper_harness.domain.reports import Report, ReportEvidenceReference, ReportType
from paper_harness.ports.arxiv import ArxivPaperRecord, ArxivPdf, ArxivPortError
from paper_harness.ports.repository import RepositoryError


def fake_pipeline_execution_contract() -> PipelineExecutionContract:
    return PipelineExecutionContract(
        narrative_mode="STRUCTURED_ONLY",
        llm_provider="deepseek",
        llm_configured_model="deepseek-v4-flash",
        analysis_prompt_version="m2-analysis-v1",
        parser_name="grobid",
        parser_version="0.8.2",
        backfill_max_queries=8,
        backfill_per_query_limit=100,
        backfill_timeout_seconds=1800.0,
        search_max_steps=12,
        search_max_queries=4,
        search_max_queue_size=100,
        search_max_citation_depth=2,
        search_max_candidates=100,
        search_max_selected_candidates=5,
        search_per_operation_timeout_seconds=60.0,
        search_overall_timeout_seconds=300.0,
        max_comparisons_per_paper=3,
        pipeline_timeout_seconds=28800,
        crawler_prompt_version="m3-crawler-v1",
        selector_prompt_version="m3-selector-v1",
        comparison_prompt_version="m3-comparison-v1",
        report_prompt_version="m4-report-v1",
        daily_selection_policy_version="daily-selection-v1",
        pipeline_orchestration_version="daily-pipeline-v1",
        embedding_model_identifier="allenai/specter2_base",
        embedding_model_revision="3447645e1def9117997203454fa4495937bfbd83",
        embedding_tokenizer_identifier="allenai/specter2_base",
        embedding_tokenizer_revision="3447645e1def9117997203454fa4495937bfbd83",
        embedding_dimension=768,
        embedding_preprocessing_contract="title [SEP] abstract; max_length=512",
        embedding_model_provenance=(
            "huggingface:allenai/specter2_base@3447645e1def9117997203454fa4495937bfbd83"
        ),
        embedding_source="specter2_base_title_abstract_cls",
        topic_categories=("cs.AI",),
        topic_include_terms=("agent",),
        topic_exclude_terms=("chemical agent",),
        topic_overlap_hours=48,
        topic_initial_lookback_days=7,
        topic_max_results=500,
        topic_representative_full_text_count=100,
    )


class FakeArxiv:
    def __init__(
        self,
        records: tuple[ArxivPaperRecord, ...] = (),
        error: ArxivPortError | None = None,
        pdf_content: bytes = b"%PDF-1.7\nfixture",
        pdf_error: ArxivPortError | None = None,
    ) -> None:
        self.records = records
        self.error = error
        self.pdf_content = pdf_content
        self.pdf_error = pdf_error
        self.calls: list[tuple[str, datetime, datetime, int]] = []
        self.id_calls: list[tuple[str, ...]] = []
        self.pdf_calls: list[tuple[str, int, str]] = []

    def search(
        self,
        *,
        query: str,
        updated_from: datetime,
        updated_until: datetime,
        max_results: int,
    ) -> tuple[ArxivPaperRecord, ...]:
        self.calls.append((query, updated_from, updated_until, max_results))
        if self.error is not None:
            raise self.error
        return self.records

    def get_papers_by_ids(
        self,
        *,
        canonical_arxiv_ids: tuple[str, ...],
    ) -> tuple[ArxivPaperRecord, ...]:
        self.id_calls.append(canonical_arxiv_ids)
        if self.error is not None:
            raise self.error
        by_id = {record.canonical_arxiv_id: record for record in self.records}
        return tuple(by_id[paper_id] for paper_id in canonical_arxiv_ids)

    def download_pdf(
        self,
        *,
        canonical_arxiv_id: str,
        version: int,
        pdf_url: str,
    ) -> ArxivPdf:
        self.pdf_calls.append((canonical_arxiv_id, version, pdf_url))
        if self.pdf_error is not None:
            raise self.pdf_error
        return ArxivPdf(
            canonical_arxiv_id=canonical_arxiv_id,
            version=version,
            source_url=pdf_url,
            content=self.pdf_content,
        )


class FakeRepository:
    def __init__(self) -> None:
        self.topic: StoredTopic | None = None
        self.cursor: IngestionCursor | None = None
        self.run: DailyRun | None = None
        self.items: tuple[RunItem, ...] = ()
        self.papers: tuple[Paper, ...] = ()
        self.paper_detail: PaperDetail | None = None
        self.analysis_detail: AnalysisDetail | None = None
        self.analysis_targets: tuple[AnalysisTarget, ...] = ()
        self.parsed_papers: dict[UUID, ParsedPaper] = {}
        self.ready_error: Exception | None = None
        self.analysis_persist_error: RepositoryError | None = None
        self.finalize_error: RepositoryError | None = None
        self.historical_backfill: HistoricalBackfillRun | None = None
        self.historical_representative_arxiv_ids: tuple[str, ...] = ()
        self.historical_representative_version_ids: tuple[UUID, ...] = ()
        self.persisted_historical_arxiv_records: tuple[ArxivPaperRecord, ...] = ()
        self.search_detail: SearchSessionDetail | None = None
        self.external_papers: dict[UUID, ExternalPaperStub] = {}
        self.embeddings: tuple[ScientificEmbedding, ...] = ()
        self.lexical_matches: tuple[HistoricalRetrievalMatch, ...] = ()
        self.vector_matches: tuple[HistoricalRetrievalMatch, ...] = ()
        self.comparison_inputs: dict[UUID | tuple[UUID, UUID], ComparisonPaperInput] = {}
        self.comparisons: dict[UUID, ComparisonDetail] = {}
        self.canonical_comparison_ids: frozenset[UUID] = frozenset()
        self.canonical_search_session_ids: frozenset[UUID] = frozenset()
        self.persisted_comparisons: dict[UUID, ComparisonBundle] = {}
        self.related_work: RelatedWorkDetail | None = None
        self.graph_view: GraphView | None = None
        self.trends: tuple[TrendDetail, ...] = ()
        self.lineages: dict[UUID, LineageDetail] = {}
        self.product_run: ProductRunDetail | None = None
        self.publication_artifact_summary: PublicationArtifactSummary | None = None
        self.product_input: ProductPublicationInput | None = None
        self.product_comparison_snapshot_ids: frozenset[UUID] | None = None
        self.graph_corpus: GraphCorpusInput | None = None
        self.product_graphs: dict[UUID, KnowledgeGraphBundle] = {}
        self.persisted_trends: tuple[TrendSnapshot, ...] = ()
        self.persisted_lineages: tuple[LineageSnapshot, ...] = ()
        self.periodic_input: PeriodicReportInput | None = None
        self.reports: tuple[ReportDetail, ...] = ()
        self.graph_read: (
            tuple[
                str | None,
                date | None,
                UUID | None,
                UUID | None,
                GraphEntityType | None,
                GraphRelationType | None,
                RelationProvenance | None,
                VerificationStatus | None,
                int,
                int,
            ]
            | None
        ) = None
        self.locked = False
        self.pipeline_locked = False
        self.pipeline_executions: dict[UUID, PipelineExecution] = {}
        self.canonically_published_version_ids: frozenset[UUID] = frozenset()
        self.enforce_published_visibility = False

    @contextmanager
    def daily_pipeline_lock(self, execution_id: UUID) -> Generator[None]:
        if self.pipeline_locked:
            raise DuplicateDailyRunError(f"another daily pipeline holds {execution_id}")
        self.pipeline_locked = True
        try:
            yield
        finally:
            self.pipeline_locked = False

    def get_pipeline_execution(self, execution_id: UUID) -> PipelineExecution | None:
        return self.pipeline_executions.get(execution_id)

    def start_pipeline_execution(self, execution: PipelineExecution) -> PipelineExecution:
        existing = self.pipeline_executions.setdefault(execution.id, execution)
        stable_owner = ("topic_id", "logical_date", "execution_mode")
        if any(getattr(existing, name) != getattr(execution, name) for name in stable_owner):
            raise RepositoryError("pipeline execution stable owner changed")
        if existing.status in (RunStatus.RUNNING, RunStatus.FAILED):
            existing = replace(
                existing,
                analysis_scope=execution.analysis_scope,
                selection_limit=execution.selection_limit,
                contract=execution.contract,
            )
            self.pipeline_executions[execution.id] = existing
        return existing

    def complete_pipeline_execution(
        self,
        execution_id: UUID,
        *,
        status: RunStatus,
        completed_at: datetime,
    ) -> PipelineExecution:
        execution = self.pipeline_executions[execution_id]
        if execution.status is RunStatus.RUNNING:
            execution = replace(execution, status=status, completed_at=completed_at)
            self.pipeline_executions[execution_id] = execution
        if execution.status is not status:
            raise RepositoryError("pipeline execution is already terminal")
        return execution

    def restart_pipeline_execution(
        self,
        execution_id: UUID,
        *,
        started_at: datetime,
        deadline_at: datetime,
        contract: PipelineExecutionContract,
    ) -> PipelineExecution:
        execution = self.pipeline_executions[execution_id]
        if execution.status is not RunStatus.FAILED:
            raise RepositoryError("only a failed pipeline execution may restart")
        execution = replace(
            execution,
            status=RunStatus.RUNNING,
            contract=contract,
            started_at=started_at,
            deadline_at=deadline_at,
            completed_at=None,
            error_code=None,
            error_detail=None,
        )
        self.pipeline_executions[execution_id] = execution
        return execution

    def fail_pipeline_execution(
        self,
        execution_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> PipelineExecution:
        normalized_code = error_code.strip()[:80]
        normalized_detail = error_detail.strip()[:1000]
        if not normalized_code or not normalized_detail:
            raise RepositoryError("pipeline execution failure requires a stable code and detail")
        execution = self.pipeline_executions[execution_id]
        if execution.status is RunStatus.RUNNING:
            execution = replace(
                execution,
                status=RunStatus.FAILED,
                completed_at=completed_at,
                error_code=normalized_code,
                error_detail=normalized_detail,
            )
            self.pipeline_executions[execution_id] = execution
        if execution.status is not RunStatus.FAILED:
            raise RepositoryError("pipeline execution is already terminal")
        return execution

    @contextmanager
    def daily_run_lock(self, topic_id: UUID, logical_date: date) -> Generator[None]:
        del topic_id, logical_date
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def upsert_topic(self, topic: TopicConfig) -> StoredTopic:
        created_at = datetime(2026, 1, 1, tzinfo=UTC)
        self.topic = StoredTopic(config=topic, created_at=created_at)
        return self.topic

    def get_ingestion_cursor(self, topic_id: UUID) -> IngestionCursor | None:
        del topic_id
        return self.cursor

    def get_run_for_date(
        self,
        topic_id: UUID,
        logical_date: date,
        *,
        pipeline_execution_id: UUID | None = None,
    ) -> DailyRun | None:
        if self.run is None or (self.run.topic_id, self.run.logical_date) != (
            topic_id,
            logical_date,
        ):
            return None
        return self.run if self.run.pipeline_execution_id == pipeline_execution_id else None

    def start_ingestion_run(
        self,
        *,
        topic_id: UUID,
        logical_date: date,
        started_at: datetime,
        cursor_from: datetime,
        cursor_to: datetime,
        pipeline_execution_mode: PipelineExecutionMode = PipelineExecutionMode.STANDALONE,
        pipeline_selection_limit: int | None = None,
        pipeline_execution_id: UUID | None = None,
    ) -> DailyRun:
        self.run = DailyRun(
            id=uuid4(),
            topic_id=topic_id,
            logical_date=logical_date,
            operation=RunOperation.ARXIV_INGESTION,
            analysis_scope=None,
            status=RunStatus.RUNNING,
            started_at=started_at,
            completed_at=None,
            cursor_from=cursor_from,
            cursor_to=cursor_to,
            discovered_count=0,
            normalized_count=0,
            selected_count=0,
            completed_count=0,
            failed_count=0,
            error_code=None,
            error_detail=None,
            schema_version=1,
            created_at=started_at,
            pipeline_execution_mode=pipeline_execution_mode,
            pipeline_selection_limit=pipeline_selection_limit,
            pipeline_execution_id=pipeline_execution_id,
        )
        return self.run

    def restart_ingestion_run(
        self,
        run_id: UUID,
        *,
        started_at: datetime,
        cursor_from: datetime,
        cursor_to: datetime,
        pipeline_selection_limit: int | None,
    ) -> DailyRun:
        if self.run is None or self.run.id != run_id:
            raise RepositoryError("ingestion run is missing")
        self.run = replace(
            self.run,
            status=RunStatus.RUNNING,
            started_at=started_at,
            completed_at=None,
            cursor_from=cursor_from,
            cursor_to=cursor_to,
            pipeline_selection_limit=pipeline_selection_limit,
            discovered_count=0,
            normalized_count=0,
            error_code=None,
            error_detail=None,
        )
        self.items = ()
        return self.run

    def persist_ingestion_selection(
        self,
        run_id: UUID,
        *,
        selected_paper_version_ids: tuple[UUID, ...],
        updated_at: datetime,
    ) -> None:
        if self.run is None or self.run.id != run_id:
            raise AssertionError("run was not started")
        selected = set(selected_paper_version_ids)
        self.items = tuple(
            replace(
                item,
                stage=(
                    PaperStage.SELECTED
                    if item.paper_version_id in selected
                    else PaperStage.RELEVANCE_SCORED
                ),
                updated_at=updated_at,
            )
            for item in self.items
        )

    def persist_arxiv_batch_and_complete(
        self,
        *,
        topic: TopicConfig,
        run_id: UUID,
        records: tuple[ArxivPaperRecord, ...],
        watermark: datetime,
        advance_shared_cursor: bool,
        persisted_at: datetime,
        completed_at: datetime,
    ) -> DailyRun:
        del topic
        self.items = tuple(
            RunItem(
                id=uuid5(run_id, f"{record.canonical_arxiv_id}:v{record.version}"),
                run_id=run_id,
                paper_id=uuid5(run_id, record.canonical_arxiv_id),
                paper_version_id=uuid5(
                    run_id, f"version:{record.canonical_arxiv_id}:v{record.version}"
                ),
                stage=PaperStage.NORMALIZED,
                status=RunItemStatus.COMPLETED,
                failed_stage=None,
                error_code=None,
                retryable=None,
                error_detail=None,
                schema_version=1,
                created_at=persisted_at,
                updated_at=persisted_at,
            )
            for record in records
        )
        if self.run is None or self.run.id != run_id:
            raise AssertionError("run was not started")
        expected_cursor_policy = self.run.pipeline_execution_mode is not PipelineExecutionMode.SMOKE
        if advance_shared_cursor is not expected_cursor_policy:
            raise AssertionError("cursor policy does not match the persisted execution mode")
        if advance_shared_cursor:
            self.cursor = IngestionCursor(
                topic_id=self.run.topic_id,
                watermark=watermark,
                schema_version=1,
                created_at=persisted_at,
                updated_at=persisted_at,
            )
        self.run = replace(
            self.run,
            status=RunStatus.COMPLETE,
            completed_at=completed_at,
            discovered_count=len(records),
            normalized_count=len(self.items),
        )
        return self.run

    def fail_ingestion_run(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> DailyRun:
        if self.run is None or self.run.id != run_id:
            raise AssertionError("run was not started")
        self.run = replace(
            self.run,
            status=RunStatus.FAILED,
            completed_at=completed_at,
            error_code=error_code,
            error_detail=error_detail,
        )
        return self.run

    def check_ready(self) -> None:
        if self.ready_error is not None:
            raise self.ready_error

    def list_topics(self) -> tuple[StoredTopic, ...]:
        return () if self.topic is None else (self.topic,)

    def list_papers(
        self, *, topic_slug: str | None, limit: int, offset: int
    ) -> tuple[tuple[Paper, ...], int]:
        del topic_slug
        return self.papers[offset : offset + limit], len(self.papers)

    def list_published_papers(
        self, *, topic_slug: str | None, limit: int, offset: int
    ) -> tuple[tuple[Paper, ...], int]:
        if not self.enforce_published_visibility:
            return self.list_papers(topic_slug=topic_slug, limit=limit, offset=offset)
        visible: list[Paper] = []
        for paper in self.papers:
            if self.paper_detail is not None and self.paper_detail.paper.id == paper.id:
                projected = self.get_published_paper(paper.id)
                if projected is not None:
                    visible.append(projected.paper)
                continue
            if (
                stable_paper_version_id(paper.canonical_arxiv_id, paper.current_version)
                in self.canonically_published_version_ids
            ):
                visible.append(paper)
        visible_papers = tuple(visible)
        return visible_papers[offset : offset + limit], len(visible_papers)

    def get_paper(self, paper_id: UUID) -> PaperDetail | None:
        del paper_id
        return self.paper_detail

    def get_published_paper(self, paper_id: UUID) -> PaperDetail | None:
        detail = self.get_paper(paper_id)
        if detail is None or not self.enforce_published_visibility:
            return detail
        visible_versions = tuple(
            value for value in detail.versions if value.id in self.canonically_published_version_ids
        )
        if not visible_versions:
            return None
        visible_version = max(visible_versions, key=lambda value: value.version)
        visible_paper = replace(
            detail.paper,
            title=visible_version.title,
            abstract=visible_version.abstract,
            current_version=visible_version.version,
            latest_updated_at=visible_version.updated_at,
            primary_category=visible_version.primary_category,
            categories=visible_version.categories,
            authors=visible_version.authors,
            pdf_url=visible_version.pdf_url,
        )
        return replace(detail, paper=visible_paper, versions=visible_versions)

    def list_runs(
        self, *, topic_slug: str | None, limit: int, offset: int
    ) -> tuple[tuple[DailyRun, ...], int]:
        del topic_slug, limit, offset
        return (() if self.run is None else (self.run,)), int(self.run is not None)

    def get_latest_run(self, *, topic_slug: str | None) -> RunDetail | None:
        del topic_slug
        return (
            None
            if self.run is None or self.run.pipeline_execution_mode is PipelineExecutionMode.SMOKE
            else RunDetail(
                run=self.run,
                items=tuple(
                    RunItemDetail(
                        item=item,
                        canonical_arxiv_id="2601.01234",
                        paper_title="A Reliable LLM Agent",
                    )
                    for item in self.items
                ),
            )
        )

    def get_run(self, run_id: UUID) -> RunDetail | None:
        detail = self.get_latest_run(topic_slug=None)
        return detail if detail is not None and detail.run.id == run_id else None

    def get_graph(
        self,
        *,
        topic_slug: str | None,
        as_of: date | None,
        paper_id: UUID | None,
        entity_id: UUID | None,
        entity_type: GraphEntityType | None,
        relation_type: GraphRelationType | None,
        provenance: RelationProvenance | None,
        verification_status: VerificationStatus | None,
        max_nodes: int,
        max_edges: int,
    ) -> GraphView | None:
        self.graph_read = (
            topic_slug,
            as_of,
            paper_id,
            entity_id,
            entity_type,
            relation_type,
            provenance,
            verification_status,
            max_nodes,
            max_edges,
        )
        return self.graph_view

    def list_trends(
        self,
        *,
        topic_slug: str | None,
        as_of: date | None,
        windows: tuple[TrendWindow, ...],
        entity_type: GraphEntityType | None,
        max_entities: int,
    ) -> tuple[TrendDetail, ...]:
        del topic_slug, as_of
        requested = set(windows)
        values: list[TrendDetail] = []
        for item in self.trends:
            if item.snapshot.window not in requested:
                continue
            matching = tuple(
                value
                for value in item.snapshot.entity_counts
                if entity_type is None or value.entity_type is entity_type
            )
            selected = matching[:max_entities]
            selected_ids = {value.entity_id for value in selected}
            values.append(
                TrendDetail(
                    snapshot=replace(
                        item.snapshot,
                        entity_counts=selected,
                        new_entity_ids=tuple(
                            value for value in item.snapshot.new_entity_ids if value in selected_ids
                        ),
                        recurring_entity_ids=tuple(
                            value
                            for value in item.snapshot.recurring_entity_ids
                            if value in selected_ids
                        ),
                    ),
                    representative_papers=item.representative_papers,
                    total_entities=len(matching),
                    truncated=len(matching) > len(selected),
                )
            )
        return tuple(values)

    def get_lineage(
        self,
        entity_or_paper_id: UUID,
        *,
        topic_slug: str | None,
        max_depth: int,
        max_nodes: int,
        max_edges: int,
    ) -> LineageDetail | None:
        del topic_slug
        detail = self.lineages.get(entity_or_paper_id)
        snapshot = None if detail is None else detail.snapshot
        if (
            snapshot is None
            or snapshot.max_depth > max_depth
            or len(snapshot.nodes) > max_nodes
            or len(snapshot.edges) > max_edges
        ):
            return None
        return detail

    def get_product_run(
        self,
        *,
        logical_date: date | None,
        topic_slug: str | None,
        pipeline_execution_id: UUID | None = None,
    ) -> ProductRunDetail | None:
        del topic_slug
        if self.product_run is None:
            return None
        if logical_date is not None and self.product_run.run.logical_date != logical_date:
            return None
        if (
            pipeline_execution_id is not None
            and self.product_run.run.pipeline_execution_id != pipeline_execution_id
        ):
            return None
        if (
            pipeline_execution_id is None
            and self.product_run.run.pipeline_execution_mode is PipelineExecutionMode.SMOKE
        ):
            return None
        return self.product_run

    def get_publication_artifact_summary(
        self,
        *,
        publication_run_id: UUID,
        pipeline_execution_id: UUID,
    ) -> PublicationArtifactSummary | None:
        summary = self.publication_artifact_summary
        if summary is None:
            return None
        if (
            summary.publication_run_id != publication_run_id
            or summary.pipeline_execution_id != pipeline_execution_id
        ):
            return None
        return summary

    def list_reports(
        self,
        *,
        report_type: ReportType,
        topic_slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[ReportDetail, ...], int]:
        del topic_slug
        matching = tuple(item for item in self.reports if item.report.report_type is report_type)
        return matching[offset : offset + limit], len(matching)

    def get_report(
        self,
        *,
        report_type: ReportType,
        period_start: date,
        period_end: date,
        topic_slug: str | None,
    ) -> ReportDetail | None:
        del topic_slug
        return next(
            (
                item
                for item in self.reports
                if item.report.report_type is report_type
                and (item.report.period_start or item.report.logical_date) == period_start
                and (item.report.period_end or item.report.logical_date) == period_end
            ),
            None,
        )

    def get_product_run_for_date(
        self,
        topic_id: UUID,
        logical_date: date,
        *,
        pipeline_execution_id: UUID | None = None,
    ) -> DailyRun | None:
        run = None if self.product_run is None else self.product_run.run
        if run is None or (run.topic_id, run.logical_date) != (topic_id, logical_date):
            return None
        return run if run.pipeline_execution_id == pipeline_execution_id else None

    def get_product_publication_input(
        self,
        topic_id: UUID,
        logical_date: date,
        *,
        pipeline_execution_id: UUID | None = None,
    ) -> ProductPublicationInput | None:
        value = self.product_input
        if value is None or (
            value.source_run.run.topic_id,
            value.source_run.run.logical_date,
        ) != (topic_id, logical_date):
            return None
        if value.source_run.run.pipeline_execution_id != pipeline_execution_id:
            return None
        if (
            self.product_run is not None
            and self.product_run.run.status in (RunStatus.COMPLETE, RunStatus.PARTIAL)
            and self.product_comparison_snapshot_ids is not None
        ):
            value = replace(
                value,
                papers=tuple(
                    replace(
                        paper,
                        comparisons=tuple(
                            comparison
                            for comparison in paper.comparisons
                            if comparison.bundle.comparison.id
                            in self.product_comparison_snapshot_ids
                        ),
                    )
                    for paper in value.papers
                ),
            )
        return value

    def start_product_run(
        self,
        *,
        topic_id: UUID,
        logical_date: date,
        source: ProductPublicationInput,
        upstream_failures: tuple[ProductFailureInput, ...] = (),
        started_at: datetime,
        pipeline_execution_id: UUID | None = None,
    ) -> DailyRun:
        failures_by_version = {failure.paper_version_id: failure for failure in upstream_failures}
        if len(failures_by_version) != len(upstream_failures):
            raise RepositoryError("product upstream failures must identify unique paper versions")
        source_items = {
            detail.item.paper_version_id: detail.item for detail in source.source_run.items
        }
        for version_id, failure in failures_by_version.items():
            source_item = source_items.get(version_id)
            if (
                source_item is not None
                and source_item.status is RunItemStatus.FAILED
                and (
                    source_item.paper_id != failure.paper_id
                    or source_item.stage is not failure.stage
                    or source_item.failed_stage is not failure.failed_stage
                    or source_item.error_code != failure.error_code
                    or source_item.retryable is not failure.retryable
                    or source_item.error_detail != failure.error_detail
                )
            ):
                raise RepositoryError("product upstream failure conflicts with its source run item")
        selected_versions = set(source_items) | set(failures_by_version)
        self.run = DailyRun(
            id=uuid4(),
            topic_id=topic_id,
            logical_date=logical_date,
            operation=RunOperation.PRODUCT_PUBLICATION,
            analysis_scope=None,
            status=RunStatus.RUNNING,
            started_at=started_at,
            completed_at=None,
            cursor_from=None,
            cursor_to=None,
            discovered_count=0,
            normalized_count=0,
            selected_count=len(selected_versions),
            completed_count=0,
            failed_count=sum(
                item.status is RunItemStatus.FAILED or version_id in failures_by_version
                for version_id, item in source_items.items()
            )
            + len(set(failures_by_version) - set(source_items)),
            error_code=None,
            error_detail=None,
            schema_version=1,
            created_at=started_at,
            source_run_id=source.source_run.run.id,
            pipeline_execution_mode=source.source_run.run.pipeline_execution_mode,
            pipeline_selection_limit=source.source_run.run.pipeline_selection_limit,
            pipeline_execution_id=pipeline_execution_id,
        )
        product_items = [
            RunItem(
                id=uuid5(self.run.id, str(detail.item.paper_version_id)),
                run_id=self.run.id,
                paper_id=detail.item.paper_id,
                paper_version_id=detail.item.paper_version_id,
                stage=(
                    failures_by_version[detail.item.paper_version_id].stage
                    if detail.item.paper_version_id in failures_by_version
                    else (
                        PaperStage.EVIDENCE_EXTRACTED
                        if detail.item.status is RunItemStatus.COMPLETED
                        else detail.item.stage
                    )
                ),
                status=(
                    RunItemStatus.FAILED
                    if detail.item.status is RunItemStatus.FAILED
                    or detail.item.paper_version_id in failures_by_version
                    else RunItemStatus.IN_PROGRESS
                ),
                failed_stage=(
                    failures_by_version[detail.item.paper_version_id].failed_stage
                    if detail.item.paper_version_id in failures_by_version
                    else detail.item.failed_stage
                ),
                error_code=(
                    failures_by_version[detail.item.paper_version_id].error_code
                    if detail.item.paper_version_id in failures_by_version
                    else detail.item.error_code
                ),
                retryable=(
                    failures_by_version[detail.item.paper_version_id].retryable
                    if detail.item.paper_version_id in failures_by_version
                    else detail.item.retryable
                ),
                error_detail=(
                    failures_by_version[detail.item.paper_version_id].error_detail
                    if detail.item.paper_version_id in failures_by_version
                    else detail.item.error_detail
                ),
                schema_version=1,
                created_at=started_at,
                updated_at=started_at,
            )
            for detail in source.source_run.items
        ]
        product_items.extend(
            RunItem(
                id=uuid5(self.run.id, str(failure.paper_version_id)),
                run_id=self.run.id,
                paper_id=failure.paper_id,
                paper_version_id=failure.paper_version_id,
                stage=failure.stage,
                status=RunItemStatus.FAILED,
                failed_stage=failure.failed_stage,
                error_code=failure.error_code,
                retryable=failure.retryable,
                error_detail=failure.error_detail,
                schema_version=1,
                created_at=started_at,
                updated_at=started_at,
            )
            for failure in upstream_failures
            if failure.paper_version_id not in source_items
        )
        self.items = tuple(product_items)
        self.product_run = ProductRunDetail(
            run=self.run,
            items=self._run_item_details(),
            report=None,
        )
        self.product_comparison_snapshot_ids = frozenset(
            comparison.bundle.comparison.id
            for paper in source.papers
            for comparison in paper.comparisons
        )
        return self.run

    def restart_product_run(
        self,
        run_id: UUID,
        *,
        source: ProductPublicationInput,
        upstream_failures: tuple[ProductFailureInput, ...] = (),
        started_at: datetime,
    ) -> DailyRun:
        if (
            self.run is None
            or self.run.id != run_id
            or self.run.status is not RunStatus.FAILED
            or self.run.source_run_id != source.source_run.run.id
        ):
            raise RepositoryError("only the matching failed product run may restart")
        source_items = {item.item.paper_version_id: item.item for item in source.source_run.items}
        failures_by_version = {failure.paper_version_id: failure for failure in upstream_failures}
        if len(failures_by_version) != len(upstream_failures):
            raise RepositoryError("product upstream failures must identify unique paper versions")
        for version_id, failure in failures_by_version.items():
            source_item = source_items.get(version_id)
            if (
                source_item is not None
                and source_item.status is RunItemStatus.FAILED
                and (
                    source_item.paper_id != failure.paper_id
                    or source_item.stage is not failure.stage
                    or source_item.failed_stage is not failure.failed_stage
                    or source_item.error_code != failure.error_code
                    or source_item.retryable is not failure.retryable
                    or source_item.error_detail != failure.error_detail
                )
            ):
                raise RepositoryError("product upstream failure conflicts with its source run item")
        existing_by_version = {item.paper_version_id: item for item in self.items}
        restarted_items: list[RunItem] = []
        for detail in source.source_run.items:
            source_item = detail.item
            version_id = source_item.paper_version_id
            failure = failures_by_version.get(version_id)
            existing = existing_by_version.get(version_id)
            if source_item.status is RunItemStatus.FAILED:
                stage = source_item.stage
                status = RunItemStatus.FAILED
                failed_stage = source_item.failed_stage
                error_code = source_item.error_code
                retryable = source_item.retryable
                error_detail = source_item.error_detail
            elif failure is not None:
                stage = failure.stage
                status = RunItemStatus.FAILED
                failed_stage = failure.failed_stage
                error_code = failure.error_code
                retryable = failure.retryable
                error_detail = failure.error_detail
            else:
                stage = PaperStage.EVIDENCE_EXTRACTED
                status = RunItemStatus.IN_PROGRESS
                failed_stage = None
                error_code = None
                retryable = None
                error_detail = None
            restarted_items.append(
                RunItem(
                    id=(existing.id if existing is not None else uuid5(run_id, str(version_id))),
                    run_id=run_id,
                    paper_id=source_item.paper_id,
                    paper_version_id=version_id,
                    stage=stage,
                    status=status,
                    failed_stage=failed_stage,
                    error_code=error_code,
                    retryable=retryable,
                    error_detail=error_detail,
                    schema_version=1,
                    created_at=existing.created_at if existing is not None else started_at,
                    updated_at=started_at,
                )
            )
        restarted_items.extend(
            RunItem(
                id=(
                    existing.id
                    if (existing := existing_by_version.get(failure.paper_version_id)) is not None
                    else uuid5(run_id, str(failure.paper_version_id))
                ),
                run_id=run_id,
                paper_id=failure.paper_id,
                paper_version_id=failure.paper_version_id,
                stage=failure.stage,
                status=RunItemStatus.FAILED,
                failed_stage=failure.failed_stage,
                error_code=failure.error_code,
                retryable=failure.retryable,
                error_detail=failure.error_detail,
                schema_version=1,
                created_at=existing.created_at if existing is not None else started_at,
                updated_at=started_at,
            )
            for failure in upstream_failures
            if failure.paper_version_id not in source_items
        )
        self.items = tuple(restarted_items)
        self.product_graphs.clear()
        self.persisted_trends = ()
        self.persisted_lineages = ()
        self.product_comparison_snapshot_ids = frozenset(
            comparison.bundle.comparison.id
            for paper in source.papers
            for comparison in paper.comparisons
        )
        self.run = replace(
            self.run,
            status=RunStatus.RUNNING,
            started_at=started_at,
            completed_at=None,
            selected_count=len(self.items),
            completed_count=0,
            failed_count=sum(item.status is RunItemStatus.FAILED for item in self.items),
            error_code=None,
            error_detail=None,
            pipeline_selection_limit=source.source_run.run.pipeline_selection_limit,
        )
        self.product_run = ProductRunDetail(
            run=self.run,
            items=self._run_item_details(),
            report=None,
        )
        return self.run

    def advance_product_item(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        expected_stage: PaperStage,
        next_stage: PaperStage,
        updated_at: datetime,
    ) -> None:
        if self.run is None or self.run.id != run_id:
            raise RepositoryError("product run was not started")
        self.items = tuple(
            replace(item, stage=next_stage, updated_at=updated_at)
            if item.paper_version_id == paper_version_id
            and item.status is RunItemStatus.IN_PROGRESS
            and item.stage is expected_stage
            else item
            for item in self.items
        )

    def persist_product_graph(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        bundle: KnowledgeGraphBundle,
        expected_stage: PaperStage,
        updated_at: datetime,
    ) -> GraphWriteResult:
        existing_entity_ids = {
            entity.id
            for persisted_bundle in self.product_graphs.values()
            for entity in persisted_bundle.entities
        }
        self.product_graphs[paper_version_id] = bundle
        self.advance_product_item(
            run_id=run_id,
            paper_version_id=paper_version_id,
            expected_stage=expected_stage,
            next_stage=PaperStage.GRAPH_UPDATED,
            updated_at=updated_at,
        )
        return GraphWriteResult(
            entity_ids=tuple(sorted((item.id for item in bundle.entities), key=str)),
            edge_ids=tuple(sorted((item.id for item in bundle.edges), key=str)),
            new_entity_ids=tuple(
                sorted(
                    (item.id for item in bundle.entities if item.id not in existing_entity_ids),
                    key=str,
                )
            ),
            inferred_edge_ids=tuple(
                sorted(
                    (
                        item.id
                        for item in bundle.edges
                        if item.provenance is RelationProvenance.LLM_INFERRED
                    ),
                    key=str,
                )
            ),
        )

    def fail_product_item(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        failed_stage: PaperStage,
        error_code: str,
        retryable: bool,
        error_detail: str,
        updated_at: datetime,
    ) -> None:
        if self.run is None or self.run.id != run_id:
            raise RepositoryError("product run was not started")
        self.items = tuple(
            replace(
                item,
                status=RunItemStatus.FAILED,
                failed_stage=failed_stage,
                error_code=error_code,
                retryable=retryable,
                error_detail=error_detail,
                updated_at=updated_at,
            )
            if item.paper_version_id == paper_version_id
            and item.status is RunItemStatus.IN_PROGRESS
            else item
            for item in self.items
        )

    def get_graph_corpus(
        self,
        topic_id: UUID,
        *,
        as_of_date: date,
        current_publication_run_id: UUID | None = None,
    ) -> GraphCorpusInput:
        del as_of_date, current_publication_run_id
        if self.graph_corpus is None or self.graph_corpus.topic_id != topic_id:
            raise RepositoryError("graph corpus fixture is missing")
        return self.graph_corpus

    def persist_product_aggregates(
        self,
        *,
        run_id: UUID,
        trends: tuple[TrendSnapshot, ...],
        lineages: tuple[LineageSnapshot, ...],
        updated_at: datetime,
    ) -> RunDetail:
        self.persisted_trends = trends
        self.persisted_lineages = lineages
        self.items = tuple(
            replace(
                item,
                stage=PaperStage.TREND_SNAPSHOTS_GENERATED,
                updated_at=updated_at,
            )
            if item.status is RunItemStatus.IN_PROGRESS and item.stage is PaperStage.GRAPH_UPDATED
            else item
            for item in self.items
        )
        if self.run is None or self.run.id != run_id:
            raise RepositoryError("product run was not started")
        return RunDetail(run=self.run, items=self._run_item_details())

    def finalize_product_publication(
        self,
        *,
        run_id: UUID,
        report: Report,
        completed_at: datetime,
    ) -> DailyRun:
        if self.finalize_error is not None:
            raise self.finalize_error
        if self.run is None or self.run.id != run_id:
            raise RepositoryError("product run was not started")
        self.items = tuple(
            replace(
                item,
                stage=PaperStage.PUBLISHED,
                status=RunItemStatus.COMPLETED,
                updated_at=completed_at,
            )
            if item.status is RunItemStatus.IN_PROGRESS
            and item.stage is PaperStage.TREND_SNAPSHOTS_GENERATED
            else item
            for item in self.items
        )
        completed = sum(item.status is RunItemStatus.COMPLETED for item in self.items)
        failed = sum(item.status is RunItemStatus.FAILED for item in self.items)
        self.run = replace(
            self.run,
            status=RunStatus.PARTIAL if failed else RunStatus.COMPLETE,
            completed_at=completed_at,
            completed_count=completed,
            failed_count=failed,
        )
        evidence = self._report_evidence(report)
        detail = ReportDetail(report=report, evidence=evidence)
        self.reports += (detail,)
        self.product_run = ProductRunDetail(
            run=self.run,
            items=self._run_item_details(),
            report=detail,
        )
        return self.run

    def fail_product_run(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        failed_stage: PaperStage,
        error_code: str,
        retryable: bool,
        error_detail: str,
    ) -> DailyRun:
        if self.run is None or self.run.id != run_id:
            raise RepositoryError("product run was not started")
        self.product_graphs.clear()
        self.persisted_trends = ()
        self.persisted_lineages = ()
        self.items = tuple(
            replace(
                item,
                status=RunItemStatus.FAILED,
                failed_stage=failed_stage,
                error_code=error_code,
                retryable=retryable,
                error_detail=error_detail,
                updated_at=completed_at,
            )
            if item.status is RunItemStatus.IN_PROGRESS
            else item
            for item in self.items
        )
        self.run = replace(
            self.run,
            status=RunStatus.FAILED,
            completed_at=completed_at,
            completed_count=sum(item.status is RunItemStatus.COMPLETED for item in self.items),
            failed_count=sum(item.status is RunItemStatus.FAILED for item in self.items),
            error_code=error_code,
            error_detail=error_detail,
        )
        self.product_run = ProductRunDetail(
            run=self.run,
            items=self._run_item_details(),
            report=None,
        )
        return self.run

    def get_periodic_report_input(
        self,
        topic_id: UUID,
        *,
        report_type: ReportType,
        period_start: date,
        period_end: date,
    ) -> PeriodicReportInput | None:
        value = self.periodic_input
        if value is None or (
            value.topic_id,
            value.report_type,
            value.period_start,
            value.period_end,
        ) != (topic_id, report_type, period_start, period_end):
            return None
        return value

    def persist_periodic_report(self, report: Report) -> Report:
        existing = next((item.report for item in self.reports if item.report.id == report.id), None)
        if existing is not None:
            return existing
        detail = ReportDetail(report=report, evidence=self._report_evidence(report))
        self.reports += (detail,)
        return report

    def _run_item_details(self) -> tuple[RunItemDetail, ...]:
        titles = {
            item.paper_version_id: item.paper_title
            for item in (() if self.product_input is None else self.product_input.papers)
        }
        return tuple(
            RunItemDetail(
                item=item,
                canonical_arxiv_id="2601.01234",
                paper_title=titles.get(item.paper_version_id, "A Reliable LLM Agent"),
            )
            for item in self.items
        )

    def _report_evidence(self, report: Report) -> tuple[ReportEvidenceReference, ...]:
        evidence_by_id = {
            evidence.id: evidence
            for source in (() if self.product_input is None else self.product_input.papers)
            for evidence in source.evidence
        }
        if self.periodic_input is not None:
            evidence_by_id.update(
                {
                    evidence.id: evidence
                    for detail in self.periodic_input.daily_reports
                    for evidence in detail.evidence
                }
            )
        return tuple(evidence_by_id[item] for item in report.evidence_ids)

    def get_analysis_targets(
        self, topic_id: UUID, paper_ids: tuple[UUID, ...]
    ) -> tuple[AnalysisTarget, ...]:
        del topic_id
        return tuple(
            target
            for paper_id in paper_ids
            for target in self.analysis_targets
            if target.paper.id == paper_id
        )

    def get_analysis_targets_by_version_ids(
        self,
        topic_id: UUID,
        paper_version_ids: tuple[UUID, ...],
    ) -> tuple[AnalysisTarget, ...]:
        del topic_id
        return tuple(
            target
            for version_id in paper_version_ids
            for target in self.analysis_targets
            if target.version.id == version_id
        )

    def get_analyzed_paper_version_ids(
        self,
        paper_version_ids: tuple[UUID, ...],
        *,
        analysis_scope: AnalysisScope,
    ) -> frozenset[UUID]:
        detail = self.analysis_detail
        if detail is None or detail.analysis.analysis_scope is not analysis_scope:
            return frozenset()
        return frozenset(
            version_id
            for version_id in paper_version_ids
            if version_id == detail.analysis.paper_version_id
        )

    def get_reusable_analyzed_paper_version_ids(
        self,
        paper_version_ids: tuple[UUID, ...],
        *,
        analysis_scope: AnalysisScope,
        provider: str,
        configured_model: str,
        prompt_version: str,
        parser_name: str | None,
        parser_version: str | None,
    ) -> frozenset[UUID]:
        detail = self.analysis_detail
        if detail is None:
            return frozenset()
        analysis = detail.analysis
        parser_matches = (
            (parser_name is None and parser_version is None)
            if analysis_scope is AnalysisScope.ABSTRACT_ONLY
            else (detail.parser_name == parser_name and detail.parser_version == parser_version)
        )
        return (
            frozenset({analysis.paper_version_id})
            if analysis.paper_version_id in paper_version_ids
            and analysis.analysis_scope is analysis_scope
            and analysis.provider == provider
            and analysis.configured_model == configured_model
            and analysis.prompt_version == prompt_version
            and parser_matches
            else frozenset()
        )

    def get_canonically_published_paper_version_ids(
        self,
        paper_version_ids: tuple[UUID, ...],
    ) -> frozenset[UUID]:
        return frozenset(paper_version_ids).intersection(self.canonically_published_version_ids)

    def attach_existing_analysis_to_run(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        analysis_scope: AnalysisScope,
        provider: str,
        configured_model: str,
        prompt_version: str,
        parser_name: str | None,
        parser_version: str | None,
        updated_at: datetime,
    ) -> bool:
        detail = self.analysis_detail
        if (
            self.run is None
            or self.run.id != run_id
            or detail is None
            or detail.analysis.paper_version_id != paper_version_id
            or detail.analysis.analysis_scope is not analysis_scope
            or detail.analysis.provider != provider
            or detail.analysis.configured_model != configured_model
            or detail.analysis.prompt_version != prompt_version
            or (
                analysis_scope is AnalysisScope.FULL_TEXT
                and (detail.parser_name != parser_name or detail.parser_version != parser_version)
            )
        ):
            return False
        attached = False
        values: list[RunItem] = []
        for item in self.items:
            if item.paper_version_id == paper_version_id:
                values.append(
                    replace(
                        item,
                        stage=PaperStage.EVIDENCE_EXTRACTED,
                        status=RunItemStatus.COMPLETED,
                        failed_stage=None,
                        error_code=None,
                        retryable=None,
                        error_detail=None,
                        updated_at=updated_at,
                    )
                )
                attached = True
            else:
                values.append(item)
        self.items = tuple(values)
        return attached

    def get_analysis_run_for_date(
        self,
        topic_id: UUID,
        logical_date: date,
        *,
        pipeline_execution_id: UUID | None = None,
        operation: RunOperation = RunOperation.STRUCTURED_ANALYSIS,
    ) -> DailyRun | None:
        del topic_id, logical_date
        if (
            self.run is not None
            and self.run.operation is operation
            and self.run.pipeline_execution_id == pipeline_execution_id
        ):
            return self.run
        return None

    def start_analysis_run(
        self,
        *,
        topic_id: UUID,
        logical_date: date,
        analysis_scope: AnalysisScope,
        started_at: datetime,
        targets: tuple[AnalysisTarget, ...],
        pipeline_execution_mode: PipelineExecutionMode = PipelineExecutionMode.STANDALONE,
        pipeline_selection_limit: int | None = None,
        pipeline_execution_id: UUID | None = None,
        operation: RunOperation = RunOperation.STRUCTURED_ANALYSIS,
    ) -> DailyRun:
        self.run = DailyRun(
            id=uuid4(),
            topic_id=topic_id,
            logical_date=logical_date,
            operation=operation,
            analysis_scope=analysis_scope,
            status=RunStatus.RUNNING,
            started_at=started_at,
            completed_at=None,
            cursor_from=None,
            cursor_to=None,
            discovered_count=0,
            normalized_count=0,
            selected_count=len(targets),
            completed_count=0,
            failed_count=0,
            error_code=None,
            error_detail=None,
            schema_version=1,
            created_at=started_at,
            pipeline_execution_mode=pipeline_execution_mode,
            pipeline_selection_limit=pipeline_selection_limit,
            pipeline_execution_id=pipeline_execution_id,
        )
        self.items = tuple(
            RunItem(
                id=uuid5(self.run.id, str(target.version.id)),
                run_id=self.run.id,
                paper_id=target.paper.id,
                paper_version_id=target.version.id,
                stage=PaperStage.SELECTED,
                status=RunItemStatus.IN_PROGRESS,
                failed_stage=None,
                error_code=None,
                retryable=None,
                error_detail=None,
                schema_version=1,
                created_at=started_at,
                updated_at=started_at,
            )
            for target in targets
        )
        return self.run

    def restart_analysis_run(
        self,
        run_id: UUID,
        *,
        targets: tuple[AnalysisTarget, ...],
        started_at: datetime,
        pipeline_selection_limit: int | None,
    ) -> DailyRun:
        if (
            self.run is None
            or self.run.id != run_id
            or self.run.status not in (RunStatus.RUNNING, RunStatus.FAILED)
        ):
            raise RepositoryError("analysis run is missing")
        existing_by_version = {item.paper_version_id: item for item in self.items}
        restarted_items: list[RunItem] = []
        for target in targets:
            existing = existing_by_version.get(target.version.id)
            if existing is not None and existing.status is RunItemStatus.COMPLETED:
                restarted_items.append(existing)
                continue
            restarted_items.append(
                RunItem(
                    id=uuid5(self.run.id, str(target.version.id)),
                    run_id=self.run.id,
                    paper_id=target.paper.id,
                    paper_version_id=target.version.id,
                    stage=PaperStage.SELECTED,
                    status=RunItemStatus.IN_PROGRESS,
                    failed_stage=None,
                    error_code=None,
                    retryable=None,
                    error_detail=None,
                    schema_version=1,
                    created_at=existing.created_at if existing is not None else started_at,
                    updated_at=started_at,
                )
            )
        self.items = tuple(restarted_items)
        self.run = replace(
            self.run,
            status=RunStatus.RUNNING,
            started_at=started_at,
            completed_at=None,
            pipeline_selection_limit=(
                pipeline_selection_limit
                if pipeline_selection_limit is not None
                else self.run.pipeline_selection_limit
            ),
            selected_count=len(self.items),
            completed_count=sum(item.status is RunItemStatus.COMPLETED for item in self.items),
            failed_count=0,
            error_code=None,
            error_detail=None,
        )
        return self.run

    def advance_analysis_item(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        expected_stage: PaperStage,
        next_stage: PaperStage,
        updated_at: datetime,
    ) -> None:
        del run_id
        self.items = tuple(
            replace(item, stage=next_stage, updated_at=updated_at)
            if item.paper_version_id == paper_version_id and item.stage is expected_stage
            else item
            for item in self.items
        )

    def persist_parsed_paper(
        self,
        *,
        run_id: UUID,
        parsed_paper: ParsedPaper,
        expected_stage: PaperStage,
        updated_at: datetime,
    ) -> ParsedPaper:
        canonical = self.parsed_papers.setdefault(parsed_paper.id, parsed_paper)
        if (
            canonical.paper_id != parsed_paper.paper_id
            or canonical.paper_version_id != parsed_paper.paper_version_id
            or canonical.parser_name != parsed_paper.parser_name
            or canonical.parser_version != parsed_paper.parser_version
        ):
            raise RepositoryError("stable parsed-paper identity conflicts with stored data")
        self.advance_analysis_item(
            run_id=run_id,
            paper_version_id=parsed_paper.paper_version_id,
            expected_stage=expected_stage,
            next_stage=PaperStage.PARSED,
            updated_at=updated_at,
        )
        return canonical

    def persist_analysis_bundle(
        self,
        *,
        run_id: UUID,
        bundle: AnalysisBundle,
        expected_stage: PaperStage,
        updated_at: datetime,
    ) -> None:
        del run_id, expected_stage
        if self.analysis_persist_error is not None:
            raise self.analysis_persist_error
        parsed = (
            None
            if bundle.analysis.parsed_paper_id is None
            else self.parsed_papers[bundle.analysis.parsed_paper_id]
        )
        self.analysis_detail = AnalysisDetail(
            analysis=bundle.analysis,
            arxiv_version=1,
            claims=bundle.claims,
            evidence=bundle.evidence,
            parser_name=None if parsed is None else parsed.parser_name,
            parser_version=None if parsed is None else parsed.parser_version,
        )
        self.items = tuple(
            replace(
                item,
                stage=PaperStage.EVIDENCE_EXTRACTED,
                status=RunItemStatus.COMPLETED,
                updated_at=updated_at,
            )
            if item.paper_version_id == bundle.analysis.paper_version_id
            else item
            for item in self.items
        )

    def fail_analysis_item(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        failed_stage: PaperStage,
        error_code: str,
        retryable: bool,
        error_detail: str,
        updated_at: datetime,
    ) -> None:
        del run_id
        self.items = tuple(
            replace(
                item,
                status=RunItemStatus.FAILED,
                failed_stage=failed_stage,
                error_code=error_code,
                retryable=retryable,
                error_detail=error_detail,
                updated_at=updated_at,
            )
            if item.paper_version_id == paper_version_id
            else item
            for item in self.items
        )

    def finalize_analysis_run(self, run_id: UUID, *, completed_at: datetime) -> DailyRun:
        if self.finalize_error is not None:
            raise self.finalize_error
        if self.run is None or self.run.id != run_id:
            raise AssertionError("run was not started")
        completed = sum(item.status is RunItemStatus.COMPLETED for item in self.items)
        failed = sum(item.status is RunItemStatus.FAILED for item in self.items)
        status = (
            RunStatus.COMPLETE
            if failed == 0
            else (RunStatus.PARTIAL if completed else RunStatus.FAILED)
        )
        self.run = replace(
            self.run,
            status=status,
            completed_at=completed_at,
            completed_count=completed,
            failed_count=failed,
            error_code=None if completed else "NO_SELECTED_PAPER_COMPLETED",
            error_detail=None if completed else "No selected paper completed evidence extraction.",
        )
        return self.run

    def fail_analysis_run(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        failed_stage: PaperStage,
        error_code: str,
        retryable: bool,
        error_detail: str,
    ) -> DailyRun:
        if self.run is None or self.run.id != run_id:
            raise AssertionError("run was not started")
        self.items = tuple(
            replace(
                item,
                status=RunItemStatus.FAILED,
                failed_stage=failed_stage,
                error_code=error_code,
                retryable=retryable,
                error_detail=error_detail,
                updated_at=completed_at,
            )
            if item.status is RunItemStatus.IN_PROGRESS
            else item
            for item in self.items
        )
        completed_count = sum(item.status is RunItemStatus.COMPLETED for item in self.items)
        failed_count = sum(item.status is RunItemStatus.FAILED for item in self.items)
        self.run = replace(
            self.run,
            status=RunStatus.FAILED,
            completed_at=completed_at,
            completed_count=completed_count,
            error_code=error_code,
            error_detail=error_detail,
            failed_count=failed_count,
        )
        return self.run

    def get_paper_analysis(
        self,
        paper_id: UUID,
        *,
        paper_version_id: UUID | None,
        analysis_scope: AnalysisScope | None = None,
        canonical_only: bool = False,
    ) -> AnalysisDetail | None:
        if self.analysis_detail is None:
            return None
        if (
            canonical_only
            and self.enforce_published_visibility
            and self.analysis_detail.analysis.paper_version_id
            not in self.canonically_published_version_ids
        ):
            return None
        if self.analysis_detail.analysis.paper_id != paper_id:
            return None
        if (
            paper_version_id is not None
            and self.analysis_detail.analysis.paper_version_id != paper_version_id
        ):
            return None
        if (
            analysis_scope is not None
            and self.analysis_detail.analysis.analysis_scope is not analysis_scope
        ):
            return None
        return self.analysis_detail

    def list_paper_evidence(
        self,
        paper_id: UUID,
        *,
        analysis_id: UUID,
        paper_version_id: UUID | None,
        analysis_scope: AnalysisScope | None = None,
        canonical_only: bool = False,
    ) -> tuple[Evidence, ...] | None:
        detail = self.get_paper_analysis(
            paper_id,
            paper_version_id=paper_version_id,
            analysis_scope=analysis_scope,
            canonical_only=canonical_only,
        )
        if detail is None or detail.analysis.id != analysis_id:
            return None
        return detail.evidence

    def start_historical_backfill(self, run: HistoricalBackfillRun) -> HistoricalBackfillRun:
        self.historical_backfill = run
        return run

    def get_historical_backfill(
        self, topic_id: UUID, window_from: date, window_to: date
    ) -> HistoricalBackfillRun | None:
        run = self.historical_backfill
        if run is None or (run.topic_id, run.window_from, run.window_to) != (
            topic_id,
            window_from,
            window_to,
        ):
            return None
        return run

    def persist_historical_backfill_page(
        self,
        run_id: UUID,
        *,
        expected_query_index: int,
        next_query_index: int,
        papers: tuple[ExternalPaperStub, ...],
        entries: tuple[HistoricalCorpusEntry, ...],
        embeddings: tuple[ScientificEmbedding, ...],
        discovered_count: int,
        persisted_count: int,
        persisted_at: datetime,
    ) -> HistoricalBackfillRun:
        del papers, entries, persisted_at
        run = self.historical_backfill
        if run is None or run.id != run_id or run.next_query_index != expected_query_index:
            raise RepositoryError("historical backfill cursor mismatch")
        self.embeddings += embeddings
        self.historical_backfill = replace(
            run,
            next_query_index=next_query_index,
            discovered_count=discovered_count,
            persisted_count=persisted_count,
        )
        return self.historical_backfill

    def finalize_historical_backfill(
        self,
        run_id: UUID,
        *,
        representatives: tuple[tuple[UUID, int], ...],
        completed_at: datetime,
    ) -> HistoricalBackfillRun:
        run = self.historical_backfill
        if run is None or run.id != run_id:
            raise RepositoryError("historical backfill was not started")
        self.historical_backfill = replace(
            run,
            status=BackfillStatus.COMPLETE,
            representative_count=len(representatives),
            completed_at=completed_at,
        )
        return self.historical_backfill

    def list_historical_representative_arxiv_ids(
        self,
        topic_id: UUID,
        *,
        limit: int,
    ) -> tuple[str, ...]:
        del topic_id
        return self.historical_representative_arxiv_ids[:limit]

    def list_historical_representative_version_ids(
        self,
        topic_id: UUID,
        *,
        limit: int,
    ) -> tuple[UUID, ...]:
        del topic_id
        return self.historical_representative_version_ids[:limit]

    def persist_historical_arxiv_records(
        self,
        *,
        topic: TopicConfig,
        records: tuple[ArxivPaperRecord, ...],
        persisted_at: datetime,
    ) -> tuple[UUID, ...]:
        del topic, persisted_at
        self.persisted_historical_arxiv_records = records
        rank_by_id = {
            canonical_arxiv_id: rank
            for rank, canonical_arxiv_id in enumerate(
                self.historical_representative_arxiv_ids,
                start=1,
            )
        }
        ordered = sorted(records, key=lambda record: rank_by_id[record.canonical_arxiv_id])
        version_ids = tuple(
            stable_paper_version_id(record.canonical_arxiv_id, record.version) for record in ordered
        )
        self.historical_representative_version_ids = version_ids
        return version_ids

    def materialize_search_candidate_arxiv_records(
        self,
        *,
        topic: TopicConfig,
        candidates: tuple[tuple[UUID, ArxivPaperRecord], ...],
        persisted_at: datetime,
    ) -> tuple[tuple[UUID, UUID, UUID], ...]:
        del topic, persisted_at
        detail = self.search_detail
        if detail is None or detail.session.status is not SearchSessionStatus.COMPLETE:
            raise RepositoryError("search session is not complete")
        by_id = {candidate.id: candidate for candidate in detail.candidates}
        materialized: list[tuple[UUID, UUID, UUID]] = []
        updates: list[SearchCandidate] = []
        for candidate_id, record in candidates:
            candidate = by_id.get(candidate_id)
            external = (
                None if candidate is None else self.external_papers.get(candidate.external_paper_id)
            )
            if (
                candidate is None
                or candidate.comparison_target_decision is not ComparisonTargetDecision.TARGET
                or external is None
                or external.arxiv_id != record.canonical_arxiv_id
            ):
                raise RepositoryError("search-candidate materialization provenance conflicts")
            paper_id = stable_paper_id(record.canonical_arxiv_id)
            version_id = stable_paper_version_id(
                record.canonical_arxiv_id,
                record.version,
            )
            updates.append(
                replace(
                    candidate,
                    local_paper_id=paper_id,
                    local_paper_version_id=version_id,
                )
            )
            materialized.append((candidate_id, paper_id, version_id))
        self.search_detail = replace(
            detail,
            candidates=_merge_candidates(detail.candidates, tuple(updates)),
        )
        return tuple(materialized)

    def fail_historical_backfill(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> HistoricalBackfillRun:
        run = self.historical_backfill
        if run is None or run.id != run_id:
            raise RepositoryError("historical backfill was not started")
        self.historical_backfill = replace(
            run,
            status=BackfillStatus.FAILED,
            completed_at=completed_at,
            error_code=error_code,
            error_detail=error_detail,
        )
        return self.historical_backfill

    def start_search_session(self, session: SearchSession) -> SearchSession:
        self.search_detail = SearchSessionDetail(
            session=session, actions=(), candidates=(), discoveries=()
        )
        return session

    def get_search_session(self, session_id: UUID) -> SearchSessionDetail | None:
        if self.search_detail is None or self.search_detail.session.id != session_id:
            return None
        return self.search_detail

    def restart_search_session(self, session_id: UUID, *, restarted_at: datetime) -> SearchSession:
        detail = self.search_detail
        if (
            detail is None
            or detail.session.id != session_id
            or detail.session.status
            not in (SearchSessionStatus.RUNNING, SearchSessionStatus.FAILED)
        ):
            raise RepositoryError("only an incomplete search session may restart")
        if any(
            bundle.comparison.search_session_id == session_id
            for bundle in self.persisted_comparisons.values()
        ) or any(
            comparison.comparison.search_session_id == session_id
            for comparison in self.comparisons.values()
        ):
            raise RepositoryError("a referenced search session cannot restart")
        restarted = replace(
            detail.session,
            status=SearchSessionStatus.RUNNING,
            started_at=restarted_at,
            completed_at=None,
            stop_reason=None,
            error_code=None,
            error_detail=None,
            provider=None,
            configured_model=None,
            model_version=None,
            prompt_version=None,
            usage=None,
            crawler_queries=None,
            crawler_use_recommendations=None,
            crawler_expand_references=None,
            crawler_expand_citations=None,
            crawler_decision_reason=None,
            crawler_generated_at=None,
        )
        self.search_detail = SearchSessionDetail(
            session=restarted,
            actions=(),
            candidates=(),
            discoveries=(),
        )
        return restarted

    def start_search_action(self, action: SearchAction) -> SearchAction:
        if self.search_detail is None or self.search_detail.session.id != action.session_id:
            raise RepositoryError("search session was not started")
        self.search_detail = replace(
            self.search_detail, actions=self.search_detail.actions + (action,)
        )
        return action

    def persist_search_crawler_plan(
        self, session_id: UUID, plan: GeneratedCrawlerPlan
    ) -> SearchSession:
        detail = self.search_detail
        if detail is None or detail.session.id != session_id:
            raise RepositoryError("search session was not started")
        session = replace(
            detail.session,
            provider=plan.provider,
            configured_model=plan.configured_model,
            model_version=plan.model_version,
            prompt_version=plan.prompt_version,
            usage=plan.usage,
            crawler_queries=plan.queries,
            crawler_use_recommendations=plan.use_recommendations,
            crawler_expand_references=plan.expand_references,
            crawler_expand_citations=plan.expand_citations,
            crawler_decision_reason=plan.decision_reason,
            crawler_generated_at=plan.generated_at,
        )
        self.search_detail = replace(detail, session=session)
        return session

    def persist_search_action_result(
        self,
        action: SearchAction,
        *,
        papers: tuple[ExternalPaperStub, ...],
        candidates: tuple[SearchCandidate, ...],
        discoveries: tuple[SearchCandidateDiscovery, ...],
    ) -> None:
        if self.search_detail is None:
            raise RepositoryError("search session was not started")
        self.external_papers.update((paper.id, paper) for paper in papers)
        actions = tuple(
            action if existing.id == action.id else existing
            for existing in self.search_detail.actions
        )
        self.search_detail = replace(
            self.search_detail,
            actions=actions,
            candidates=_merge_candidates(self.search_detail.candidates, candidates),
            discoveries=self.search_detail.discoveries + discoveries,
        )

    def persist_local_search_candidates(
        self,
        session_id: UUID,
        *,
        papers: tuple[ExternalPaperStub, ...],
        candidates: tuple[SearchCandidate, ...],
        discoveries: tuple[SearchCandidateDiscovery, ...],
    ) -> None:
        if self.search_detail is None or self.search_detail.session.id != session_id:
            raise RepositoryError("search session was not started")
        self.external_papers.update((paper.id, paper) for paper in papers)
        self.search_detail = replace(
            self.search_detail,
            candidates=_merge_candidates(self.search_detail.candidates, candidates),
            discoveries=self.search_detail.discoveries + discoveries,
        )

    def update_search_candidate_decisions(
        self,
        session_id: UUID,
        candidates: tuple[SearchCandidate, ...],
    ) -> None:
        if self.search_detail is None or self.search_detail.session.id != session_id:
            raise RepositoryError("search session was not started")
        self.search_detail = replace(
            self.search_detail,
            candidates=_merge_candidates(self.search_detail.candidates, candidates),
        )

    def update_search_comparison_targets(
        self,
        session_id: UUID,
        candidates: tuple[SearchCandidate, ...],
    ) -> None:
        detail = self.search_detail
        if (
            detail is None
            or detail.session.id != session_id
            or detail.session.status is not SearchSessionStatus.COMPLETE
        ):
            raise RepositoryError("comparison targets require a completed search session")
        if {item.id for item in candidates} != {item.id for item in detail.candidates}:
            raise RepositoryError("comparison targets must cover every search candidate")
        if any(
            item.comparison_target_decision is None or item.comparison_target_reason is None
            for item in candidates
        ):
            raise RepositoryError("comparison target decision cannot remain pending")
        self.search_detail = replace(detail, candidates=tuple(candidates))

    def complete_search_session(
        self,
        session_id: UUID,
        *,
        completed_at: datetime,
        stop_reason: SearchStopReason,
        provenance: SearchModelProvenance | None,
    ) -> SearchSession:
        if self.search_detail is None or self.search_detail.session.id != session_id:
            raise RepositoryError("search session was not started")
        current = self.search_detail.session
        completed = replace(
            current,
            status=SearchSessionStatus.COMPLETE,
            completed_at=completed_at,
            stop_reason=stop_reason,
            provider=current.provider if provenance is None else provenance.provider,
            configured_model=(
                current.configured_model if provenance is None else provenance.configured_model
            ),
            model_version=(
                current.model_version if provenance is None else provenance.model_version
            ),
            prompt_version=(
                current.prompt_version if provenance is None else provenance.prompt_version
            ),
            usage=current.usage if provenance is None else provenance.usage,
        )
        self.search_detail = replace(self.search_detail, session=completed)
        return completed

    def fail_search_session(
        self,
        session_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
        provenance: SearchModelProvenance | None,
    ) -> SearchSession:
        if self.search_detail is None or self.search_detail.session.id != session_id:
            raise RepositoryError("search session was not started")
        current = self.search_detail.session
        failed = replace(
            current,
            status=SearchSessionStatus.FAILED,
            completed_at=completed_at,
            stop_reason=SearchStopReason.FAILED,
            error_code=error_code,
            error_detail=error_detail,
            provider=current.provider if provenance is None else provenance.provider,
            configured_model=(
                current.configured_model if provenance is None else provenance.configured_model
            ),
            model_version=(
                current.model_version if provenance is None else provenance.model_version
            ),
            prompt_version=(
                current.prompt_version if provenance is None else provenance.prompt_version
            ),
            usage=current.usage if provenance is None else provenance.usage,
        )
        self.search_detail = replace(self.search_detail, session=failed)
        return failed

    def upsert_scientific_embeddings(self, embeddings: tuple[ScientificEmbedding, ...]) -> None:
        self.embeddings += embeddings

    def search_historical_lexically(
        self,
        topic_id: UUID,
        *,
        query: str,
        limit: int,
    ) -> tuple[HistoricalRetrievalMatch, ...]:
        del topic_id, query
        return self.lexical_matches[:limit]

    def search_historical_by_vector(
        self,
        topic_id: UUID,
        *,
        vector: tuple[float, ...],
        model_identifier: str,
        model_revision: str,
        tokenizer_identifier: str,
        tokenizer_revision: str,
        dimension: int,
        preprocessing_contract: str,
        model_provenance: str,
        source: str,
        limit: int,
    ) -> tuple[HistoricalRetrievalMatch, ...]:
        del (
            topic_id,
            vector,
            model_identifier,
            model_revision,
            tokenizer_identifier,
            tokenizer_revision,
            dimension,
            preprocessing_contract,
            model_provenance,
            source,
        )
        return self.vector_matches[:limit]

    def get_comparison_paper_input(
        self,
        paper_version_id: UUID,
        *,
        analysis_id: UUID | None = None,
        analysis_scope: AnalysisScope | None = None,
        provider: str | None = None,
        configured_model: str | None = None,
        prompt_version: str | None = None,
        parser_name: str | None = None,
        parser_version: str | None = None,
    ) -> ComparisonPaperInput | None:
        value = (
            self.comparison_inputs.get((paper_version_id, analysis_id))
            if analysis_id is not None
            else self.comparison_inputs.get(paper_version_id)
        )
        if value is None and analysis_id is not None:
            value = self.comparison_inputs.get(paper_version_id)
        if value is not None and analysis_id is not None and value.analysis_id != analysis_id:
            return None
        if (
            value is not None
            and analysis_scope is not None
            and value.analysis_scope is not analysis_scope
        ):
            return None
        del parser_name, parser_version
        if value is not None and any(
            expected is not None and actual != expected
            for expected, actual in (
                (provider, getattr(value, "provider", None)),
                (configured_model, getattr(value, "configured_model", None)),
                (prompt_version, getattr(value, "prompt_version", None)),
            )
        ):
            return None
        return value

    def persist_comparison_bundle(self, bundle: ComparisonBundle) -> None:
        self.persisted_comparisons[bundle.comparison.id] = bundle

    def get_comparison(
        self,
        comparison_id: UUID,
        *,
        canonical_only: bool = False,
    ) -> ComparisonDetail | None:
        if canonical_only and comparison_id not in self.canonical_comparison_ids:
            return None
        return self.comparisons.get(comparison_id)

    def get_related_work(
        self,
        paper_id: UUID,
        *,
        paper_version_id: UUID | None = None,
        search_session_id: UUID | None = None,
    ) -> RelatedWorkDetail | None:
        if self.related_work is None or self.related_work.session.source_paper_id != paper_id:
            return None
        if (
            paper_version_id is not None
            and self.related_work.session.source_paper_version_id != paper_version_id
        ):
            return None
        if search_session_id is not None and self.related_work.session.id != search_session_id:
            return None
        if (
            search_session_id is None
            and self.related_work.session.id not in self.canonical_search_session_ids
        ):
            return None
        return self.related_work


def _merge_candidates(
    existing: tuple[SearchCandidate, ...],
    updates: tuple[SearchCandidate, ...],
) -> tuple[SearchCandidate, ...]:
    by_id = {item.id: item for item in existing}
    by_id.update((item.id, item) for item in updates)
    return tuple(sorted(by_id.values(), key=lambda item: (item.rank, str(item.id))))
