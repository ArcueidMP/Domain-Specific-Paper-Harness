"""M4 batch graph, trend, lineage, report, and atomic publication pipeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import NoReturn
from uuid import UUID

from paper_harness.application.ingest_arxiv import SCHEDULE_TIME_ZONE
from paper_harness.application.product_models import GraphWriteResult, ProductFailureInput
from paper_harness.application.report_inputs import build_daily_report_plan
from paper_harness.application.reporting import (
    assemble_product_report,
    require_matching_narrative_mode,
)
from paper_harness.domain.errors import DomainInvariantError, DuplicateDailyRunError
from paper_harness.domain.identity import stable_report_id
from paper_harness.domain.knowledge import (
    GraphEntityType,
    aggregate_trend_snapshots,
    build_lineage_snapshot,
    extract_analysis_graph,
    extract_comparison_graph,
    merge_knowledge_graph_bundles,
    namespace_knowledge_graph_bundle,
    namespace_lineage_snapshot,
    namespace_trend_snapshot,
)
from paper_harness.domain.models import (
    DailyRun,
    PaperStage,
    RunItemStatus,
    RunStatus,
    TopicConfig,
)
from paper_harness.domain.reports import (
    GeneratedReportNarrative,
    ReportNarrativeMode,
    ReportNarrativeRequest,
)
from paper_harness.ports.llm import LLMPort, LLMPortError
from paper_harness.ports.repository import RepositoryError, RepositoryPort


class ProductInputMissingError(RuntimeError):
    error_code = "PRODUCT_INPUT_MISSING"
    retryable = False


class ProductComparisonMissingError(RuntimeError):
    error_code = "COMPARISON_MISSING"
    retryable = False


class ProductGraphError(RuntimeError):
    error_code = "GRAPH_EXTRACTION_INVALID"
    retryable = False


class ProductTrendError(RuntimeError):
    error_code = "TREND_AGGREGATION_INVALID"
    retryable = False


class ProductReportError(RuntimeError):
    error_code = "REPORT_INPUT_INVALID"
    retryable = False


class PublishProduct:
    """Consume persisted M2/M3 records; never retrieve scholarly data online."""

    def __init__(
        self,
        *,
        repository: RepositoryPort,
        llm: LLMPort | None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._llm = llm
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        topic: TopicConfig,
        *,
        narrative_mode: ReportNarrativeMode,
        logical_date: date | None = None,
        comparison_ids: frozenset[UUID] | None = None,
        pipeline_execution_id: UUID | None = None,
        upstream_failures: tuple[ProductFailureInput, ...] = (),
    ) -> DailyRun:
        if narrative_mode is ReportNarrativeMode.DEEPSEEK and self._llm is None:
            raise ValueError("DeepSeek narrative mode requires the configured LLM adapter")
        started_at = self._aware_now()
        run_date = logical_date or started_at.astimezone(SCHEDULE_TIME_ZONE).date()
        with self._repository.daily_run_lock(topic.id, run_date):
            existing = self._repository.get_product_run_for_date(
                topic.id,
                run_date,
                pipeline_execution_id=pipeline_execution_id,
            )
            if existing is not None:
                if existing.status in (RunStatus.COMPLETE, RunStatus.PARTIAL):
                    detail = self._repository.get_product_run(
                        logical_date=run_date,
                        topic_slug=topic.slug,
                        pipeline_execution_id=pipeline_execution_id,
                    )
                    if detail is None or detail.report is None:
                        raise RepositoryError(
                            "publishable product run is missing its persisted report"
                        )
                    require_matching_narrative_mode(
                        detail.report.report.narrative_mode,
                        narrative_mode,
                    )
                    return existing
                if existing.status is not RunStatus.FAILED:
                    raise DuplicateDailyRunError(
                        f"product publication already exists for topic {topic.slug!r} on {run_date}"
                    )
            source = self._repository.get_product_publication_input(
                topic.id,
                run_date,
                pipeline_execution_id=pipeline_execution_id,
            )
            if source is None:
                raise ProductInputMissingError(
                    "product publication requires a persisted complete or partial analysis run"
                )
            if comparison_ids is not None:
                source = replace(
                    source,
                    papers=tuple(
                        replace(
                            paper,
                            comparisons=tuple(
                                comparison
                                for comparison in paper.comparisons
                                if comparison.bundle.comparison.id in comparison_ids
                            ),
                        )
                        for paper in source.papers
                    ),
                )
            run = (
                self._repository.start_product_run(
                    topic_id=topic.id,
                    logical_date=run_date,
                    source=source,
                    upstream_failures=upstream_failures,
                    started_at=started_at,
                    pipeline_execution_id=pipeline_execution_id,
                )
                if existing is None
                else self._repository.restart_product_run(
                    existing.id,
                    source=source,
                    upstream_failures=upstream_failures,
                    started_at=started_at,
                )
            )
            graph_results: dict[UUID, GraphWriteResult] = {}
            materialized_types: set[GraphEntityType] = set()
            upstream_failed_versions = {failure.paper_version_id for failure in upstream_failures}
            for paper in source.papers:
                if paper.paper_version_id in upstream_failed_versions:
                    continue
                if not paper.comparisons:
                    self._fail_item(
                        run.id,
                        paper.paper_version_id,
                        failed_stage=PaperStage.COMPARED,
                        error=ProductComparisonMissingError(
                            "no persisted M3 comparison is available for this source paper"
                        ),
                    )
                    continue
                try:
                    self._repository.advance_product_item(
                        run_id=run.id,
                        paper_version_id=paper.paper_version_id,
                        expected_stage=PaperStage.EVIDENCE_EXTRACTED,
                        next_stage=PaperStage.COMPARED,
                        updated_at=self._aware_now(),
                    )
                except RepositoryError as error:
                    self._fail_run_then_raise(
                        run.id,
                        failed_stage=PaperStage.COMPARED,
                        error=error,
                    )
                try:
                    analysis_graph = extract_analysis_graph(
                        topic.id,
                        paper.analysis,
                        paper_title=paper.paper_title,
                    )
                    comparison_graphs = tuple(
                        extract_comparison_graph(
                            topic.id,
                            item.bundle,
                            source_paper_title=item.source_paper_title,
                            target_paper_title=item.target_paper_title,
                        )
                        for item in paper.comparisons
                    )
                    bundle = merge_knowledge_graph_bundles(
                        (analysis_graph.bundle, *(item.bundle for item in comparison_graphs))
                    )
                    if pipeline_execution_id is not None:
                        bundle = namespace_knowledge_graph_bundle(
                            bundle,
                            pipeline_execution_id,
                        )
                    result = self._repository.persist_product_graph(
                        run_id=run.id,
                        paper_version_id=paper.paper_version_id,
                        bundle=bundle,
                        expected_stage=PaperStage.COMPARED,
                        updated_at=self._aware_now(),
                    )
                except DomainInvariantError as error:
                    self._fail_item(
                        run.id,
                        paper.paper_version_id,
                        failed_stage=PaperStage.GRAPH_UPDATED,
                        error=ProductGraphError(_concise_detail(error)),
                    )
                    continue
                except RepositoryError as error:
                    self._fail_run_then_raise(
                        run.id,
                        failed_stage=PaperStage.GRAPH_UPDATED,
                        error=error,
                    )
                else:
                    graph_results[paper.paper_version_id] = result
                    materialized_types.update(
                        entity.entity_type
                        for entity in bundle.entities
                        if entity.entity_type is not GraphEntityType.PAPER
                    )

            if not graph_results:
                return self._repository.fail_product_run(
                    run.id,
                    completed_at=self._aware_now(),
                    failed_stage=PaperStage.GRAPH_UPDATED,
                    error_code="NO_SELECTED_PAPER_COMPLETED",
                    retryable=False,
                    error_detail="No selected paper completed graph construction.",
                )
            try:
                corpus = self._repository.get_graph_corpus(
                    topic.id,
                    as_of_date=run_date,
                    current_publication_run_id=run.id,
                )
                trends = aggregate_trend_snapshots(
                    topic.id,
                    as_of_date=run_date,
                    papers=corpus.papers,
                    entities=corpus.entities,
                    mentions=corpus.mentions,
                    edges=corpus.edges,
                    mention_activity_dates=corpus.mention_activity_dates,
                    edge_activity_dates=corpus.edge_activity_dates,
                    generated_at=started_at,
                )
                if pipeline_execution_id is not None:
                    trends = tuple(
                        namespace_trend_snapshot(item, pipeline_execution_id) for item in trends
                    )
                completed_paper_ids = {
                    paper.paper_id
                    for paper in source.papers
                    if paper.paper_version_id in graph_results
                }
                lineages = tuple(
                    build_lineage_snapshot(
                        topic.id,
                        paper_id,
                        as_of_date=run_date,
                        papers=corpus.lineage_papers,
                        edges=corpus.edges,
                        generated_at=started_at,
                    )
                    for paper_id in sorted(completed_paper_ids, key=str)
                )
                if pipeline_execution_id is not None:
                    lineages = tuple(
                        namespace_lineage_snapshot(item, pipeline_execution_id) for item in lineages
                    )
            except DomainInvariantError as error:
                self._fail_run_then_raise(
                    run.id,
                    failed_stage=PaperStage.TREND_SNAPSHOTS_GENERATED,
                    error=ProductTrendError(_concise_detail(error)),
                )
            except RepositoryError as error:
                self._fail_run_then_raise(
                    run.id,
                    failed_stage=PaperStage.TREND_SNAPSHOTS_GENERATED,
                    error=error,
                )
            try:
                run_detail = self._repository.persist_product_aggregates(
                    run_id=run.id,
                    trends=trends,
                    lineages=lineages,
                    updated_at=self._aware_now(),
                )
            except RepositoryError as error:
                self._fail_run_then_raise(
                    run.id,
                    failed_stage=PaperStage.TREND_SNAPSHOTS_GENERATED,
                    error=error,
                )
            try:
                plan = build_daily_report_plan(
                    run_detail,
                    source,
                    report_id=stable_report_id(run.id),
                    graph_results=tuple(
                        graph_results[item.item.paper_version_id]
                        for item in run_detail.items
                        if item.item.paper_version_id in graph_results
                        and item.item.status is RunItemStatus.IN_PROGRESS
                    ),
                    trends=trends,
                    lineages=lineages,
                    omitted_entity_types=tuple(
                        sorted(
                            set(GraphEntityType) - {GraphEntityType.PAPER} - materialized_types,
                            key=lambda item: item.value,
                        )
                    ),
                )
                generated = (
                    None
                    if narrative_mode is ReportNarrativeMode.STRUCTURED_ONLY
                    else self._generate_report(plan.request)
                )
                report = assemble_product_report(
                    plan.request,
                    report_id=stable_report_id(run.id),
                    run_id=run.id,
                    topic_id=topic.id,
                    logical_date=run_date,
                    narrative_mode=narrative_mode,
                    generated=generated,
                    trend_snapshot_ids=plan.trend_snapshot_ids,
                    created_at=self._aware_now(),
                )
            except LLMPortError as error:
                self._fail_run_then_raise(
                    run.id,
                    failed_stage=PaperStage.REPORT_GENERATED,
                    error=error,
                )
            except DomainInvariantError as error:
                self._fail_run_then_raise(
                    run.id,
                    failed_stage=PaperStage.REPORT_GENERATED,
                    error=ProductReportError(_concise_detail(error)),
                )
            try:
                return self._repository.finalize_product_publication(
                    run_id=run.id,
                    report=report,
                    completed_at=self._aware_now(),
                )
            except RepositoryError as error:
                self._fail_run_then_raise(
                    run.id,
                    failed_stage=PaperStage.PUBLISHED,
                    error=error,
                )

    def _generate_report(self, request: ReportNarrativeRequest) -> GeneratedReportNarrative:
        if self._llm is None:
            raise AssertionError("DeepSeek mode was validated before starting the run")
        return self._llm.generate_report(request)

    def _fail_item(
        self,
        run_id: UUID,
        paper_version_id: UUID,
        *,
        failed_stage: PaperStage,
        error: Exception,
    ) -> None:
        try:
            self._repository.fail_product_item(
                run_id=run_id,
                paper_version_id=paper_version_id,
                failed_stage=failed_stage,
                error_code=str(getattr(error, "error_code", "DOMAIN_INVARIANT_VIOLATION")),
                retryable=bool(getattr(error, "retryable", False)),
                error_detail=_concise_detail(error),
                updated_at=self._aware_now(),
            )
        except RepositoryError as write_error:
            self._fail_run_then_raise(
                run_id,
                failed_stage=failed_stage,
                error=write_error,
            )

    def _fail_run_then_raise(
        self,
        run_id: UUID,
        *,
        failed_stage: PaperStage,
        error: Exception,
    ) -> NoReturn:
        try:
            self._repository.fail_product_run(
                run_id,
                completed_at=self._aware_now(),
                failed_stage=failed_stage,
                error_code=str(getattr(error, "error_code", "DOMAIN_INVARIANT_VIOLATION")),
                retryable=bool(getattr(error, "retryable", False)),
                error_detail=_concise_detail(error),
            )
        except RepositoryError as transition_error:
            raise transition_error from error
        raise error

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("product publication clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def _concise_detail(error: Exception) -> str:
    detail = " ".join(str(error).split())
    return (detail or type(error).__name__)[:1000]


__all__ = [
    "ProductComparisonMissingError",
    "ProductGraphError",
    "ProductInputMissingError",
    "ProductReportError",
    "ProductTrendError",
    "PublishProduct",
]
