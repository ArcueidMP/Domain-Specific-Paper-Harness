"""PostgreSQL persistence boundary used by M1 application use cases."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from paper_harness.application.product_models import (
    GraphCorpusInput,
    GraphWriteResult,
    PeriodicReportInput,
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
    RelatedWorkDetail,
    ReportDetail,
    RunDetail,
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
from paper_harness.domain.historical import (
    ComparisonBundle,
    ComparisonPaperInput,
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
    SearchStopReason,
)
from paper_harness.domain.knowledge import (
    GraphEntityType,
    GraphRelationType,
    KnowledgeGraphBundle,
    LineageSnapshot,
    TrendSnapshot,
    TrendWindow,
)
from paper_harness.domain.models import DailyRun, IngestionCursor, Paper, PaperStage, TopicConfig
from paper_harness.domain.reports import Report, ReportType
from paper_harness.ports.arxiv import ArxivPaperRecord


class RepositoryError(RuntimeError):
    """Base persistence-boundary failure."""

    error_code = "REPOSITORY_FAILURE"
    retryable = False


class RepositoryUnavailableError(RepositoryError):
    """The configured PostgreSQL database is unavailable."""

    error_code = "REPOSITORY_UNAVAILABLE"
    retryable = True


class RepositoryIntegrityError(RepositoryError):
    """PostgreSQL rejected a validated write without exposing SQL parameters."""

    error_code = "PERSISTENCE_INTEGRITY_FAILED"


class MigrationIncompatibleError(RepositoryError):
    """The database migration revision differs from the application head."""

    error_code = "MIGRATION_INCOMPATIBLE"


class RepositoryPort(Protocol):
    def daily_run_lock(
        self, topic_id: UUID, logical_date: date
    ) -> AbstractContextManager[None]: ...

    def upsert_topic(self, topic: TopicConfig) -> StoredTopic: ...

    def get_ingestion_cursor(self, topic_id: UUID) -> IngestionCursor | None: ...

    def get_run_for_date(self, topic_id: UUID, logical_date: date) -> DailyRun | None: ...

    def start_ingestion_run(
        self,
        *,
        topic_id: UUID,
        logical_date: date,
        started_at: datetime,
        cursor_from: datetime,
        cursor_to: datetime,
    ) -> DailyRun: ...

    def persist_arxiv_batch_and_complete(
        self,
        *,
        topic: TopicConfig,
        run_id: UUID,
        records: tuple[ArxivPaperRecord, ...],
        watermark: datetime,
        persisted_at: datetime,
        completed_at: datetime,
    ) -> DailyRun: ...

    def fail_ingestion_run(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> DailyRun: ...

    def check_ready(self) -> None: ...

    def list_topics(self) -> tuple[StoredTopic, ...]: ...

    def list_papers(
        self, *, topic_slug: str | None, limit: int, offset: int
    ) -> tuple[tuple[Paper, ...], int]: ...

    def get_paper(self, paper_id: UUID) -> PaperDetail | None: ...

    def list_runs(
        self, *, topic_slug: str | None, limit: int, offset: int
    ) -> tuple[tuple[DailyRun, ...], int]: ...

    def get_latest_run(self, *, topic_slug: str | None) -> RunDetail | None: ...

    def get_run(self, run_id: UUID) -> RunDetail | None: ...

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
    ) -> GraphView | None: ...

    def list_trends(
        self,
        *,
        topic_slug: str | None,
        as_of: date | None,
        windows: tuple[TrendWindow, ...],
        entity_type: GraphEntityType | None,
        max_entities: int,
    ) -> tuple[TrendDetail, ...]: ...

    def get_lineage(
        self,
        entity_or_paper_id: UUID,
        *,
        topic_slug: str | None,
        max_depth: int,
        max_nodes: int,
        max_edges: int,
    ) -> LineageDetail | None: ...

    def get_product_run(
        self,
        *,
        logical_date: date | None,
        topic_slug: str | None,
    ) -> ProductRunDetail | None: ...

    def list_reports(
        self,
        *,
        report_type: ReportType,
        topic_slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[ReportDetail, ...], int]: ...

    def get_report(
        self,
        *,
        report_type: ReportType,
        period_start: date,
        period_end: date,
        topic_slug: str | None,
    ) -> ReportDetail | None: ...

    def get_product_run_for_date(self, topic_id: UUID, logical_date: date) -> DailyRun | None: ...

    def get_product_publication_input(
        self, topic_id: UUID, logical_date: date
    ) -> ProductPublicationInput | None: ...

    def start_product_run(
        self,
        *,
        topic_id: UUID,
        logical_date: date,
        source: ProductPublicationInput,
        started_at: datetime,
    ) -> DailyRun: ...

    def restart_product_run(
        self,
        run_id: UUID,
        *,
        source: ProductPublicationInput,
        started_at: datetime,
    ) -> DailyRun: ...

    def advance_product_item(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        expected_stage: PaperStage,
        next_stage: PaperStage,
        updated_at: datetime,
    ) -> None: ...

    def persist_product_graph(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        bundle: KnowledgeGraphBundle,
        expected_stage: PaperStage,
        updated_at: datetime,
    ) -> GraphWriteResult: ...

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
    ) -> None: ...

    def get_graph_corpus(self, topic_id: UUID, *, as_of_date: date) -> GraphCorpusInput: ...

    def persist_product_aggregates(
        self,
        *,
        run_id: UUID,
        trends: tuple[TrendSnapshot, ...],
        lineages: tuple[LineageSnapshot, ...],
        updated_at: datetime,
    ) -> RunDetail: ...

    def finalize_product_publication(
        self,
        *,
        run_id: UUID,
        report: Report,
        completed_at: datetime,
    ) -> DailyRun: ...

    def fail_product_run(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        failed_stage: PaperStage,
        error_code: str,
        retryable: bool,
        error_detail: str,
    ) -> DailyRun: ...

    def get_periodic_report_input(
        self,
        topic_id: UUID,
        *,
        report_type: ReportType,
        period_start: date,
        period_end: date,
    ) -> PeriodicReportInput | None: ...

    def persist_periodic_report(self, report: Report) -> Report: ...

    def get_analysis_targets(
        self, topic_id: UUID, paper_ids: tuple[UUID, ...]
    ) -> tuple[AnalysisTarget, ...]: ...

    def get_analysis_run_for_date(self, topic_id: UUID, logical_date: date) -> DailyRun | None: ...

    def start_analysis_run(
        self,
        *,
        topic_id: UUID,
        logical_date: date,
        analysis_scope: AnalysisScope,
        started_at: datetime,
        targets: tuple[AnalysisTarget, ...],
    ) -> DailyRun: ...

    def advance_analysis_item(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        expected_stage: PaperStage,
        next_stage: PaperStage,
        updated_at: datetime,
    ) -> None: ...

    def persist_parsed_paper(
        self,
        *,
        run_id: UUID,
        parsed_paper: ParsedPaper,
        expected_stage: PaperStage,
        updated_at: datetime,
    ) -> ParsedPaper: ...

    def persist_analysis_bundle(
        self,
        *,
        run_id: UUID,
        bundle: AnalysisBundle,
        expected_stage: PaperStage,
        updated_at: datetime,
    ) -> None: ...

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
    ) -> None: ...

    def finalize_analysis_run(self, run_id: UUID, *, completed_at: datetime) -> DailyRun: ...

    def fail_analysis_run(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        failed_stage: PaperStage,
        error_code: str,
        retryable: bool,
        error_detail: str,
    ) -> DailyRun: ...

    def get_paper_analysis(
        self,
        paper_id: UUID,
        *,
        paper_version_id: UUID | None,
        analysis_scope: AnalysisScope | None = None,
    ) -> AnalysisDetail | None: ...

    def list_paper_evidence(
        self,
        paper_id: UUID,
        *,
        analysis_id: UUID,
        paper_version_id: UUID | None,
        analysis_scope: AnalysisScope | None = None,
    ) -> tuple[Evidence, ...] | None: ...

    def start_historical_backfill(self, run: HistoricalBackfillRun) -> HistoricalBackfillRun: ...

    def get_historical_backfill(
        self, topic_id: UUID, window_from: date, window_to: date
    ) -> HistoricalBackfillRun | None: ...

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
    ) -> HistoricalBackfillRun: ...

    def finalize_historical_backfill(
        self,
        run_id: UUID,
        *,
        representatives: tuple[tuple[UUID, int], ...],
        completed_at: datetime,
    ) -> HistoricalBackfillRun: ...

    def fail_historical_backfill(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> HistoricalBackfillRun: ...

    def start_search_session(self, session: SearchSession) -> SearchSession: ...

    def get_search_session(self, session_id: UUID) -> SearchSessionDetail | None: ...

    def start_search_action(self, action: SearchAction) -> SearchAction: ...

    def persist_search_crawler_plan(
        self, session_id: UUID, plan: GeneratedCrawlerPlan
    ) -> SearchSession: ...

    def persist_search_action_result(
        self,
        action: SearchAction,
        *,
        papers: tuple[ExternalPaperStub, ...],
        candidates: tuple[SearchCandidate, ...],
        discoveries: tuple[SearchCandidateDiscovery, ...],
    ) -> None: ...

    def persist_local_search_candidates(
        self,
        session_id: UUID,
        *,
        papers: tuple[ExternalPaperStub, ...],
        candidates: tuple[SearchCandidate, ...],
        discoveries: tuple[SearchCandidateDiscovery, ...],
    ) -> None: ...

    def update_search_candidate_decisions(
        self,
        session_id: UUID,
        candidates: tuple[SearchCandidate, ...],
    ) -> None: ...

    def complete_search_session(
        self,
        session_id: UUID,
        *,
        completed_at: datetime,
        stop_reason: SearchStopReason,
        provenance: SearchModelProvenance | None,
    ) -> SearchSession: ...

    def fail_search_session(
        self,
        session_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
        provenance: SearchModelProvenance | None,
    ) -> SearchSession: ...

    def upsert_scientific_embeddings(self, embeddings: tuple[ScientificEmbedding, ...]) -> None: ...

    def search_historical_lexically(
        self,
        topic_id: UUID,
        *,
        query: str,
        limit: int,
    ) -> tuple[HistoricalRetrievalMatch, ...]: ...

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
    ) -> tuple[HistoricalRetrievalMatch, ...]: ...

    def get_comparison_paper_input(
        self,
        paper_version_id: UUID,
        *,
        analysis_id: UUID | None = None,
    ) -> ComparisonPaperInput | None: ...

    def persist_comparison_bundle(self, bundle: ComparisonBundle) -> None: ...

    def get_comparison(self, comparison_id: UUID) -> ComparisonDetail | None: ...

    def get_related_work(
        self,
        paper_id: UUID,
        *,
        paper_version_id: UUID | None = None,
    ) -> RelatedWorkDetail | None: ...
