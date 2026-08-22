from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from tests.fakes import FakeRepository

from paper_harness.application.analyze_papers import build_analysis_bundle
from paper_harness.application.generate_periodic_report import (
    GeneratePeriodicReport,
    PeriodicReportInsufficientDataError,
)
from paper_harness.application.product_models import (
    ComparisonGraphInput,
    GraphCorpusInput,
    PeriodicReportInput,
    ProductFailureInput,
    ProductPaperInput,
    ProductPublicationInput,
)
from paper_harness.application.publish_product import PublishProduct
from paper_harness.application.read_models import ReportDetail, RunDetail, RunItemDetail
from paper_harness.application.report_inputs import normalize_daily_trends
from paper_harness.application.reporting import (
    ReportNarrativeModeConflictError,
    assemble_product_report,
)
from paper_harness.domain.analysis import (
    AnalysisBundle,
    AnalysisClaim,
    AnalysisPassage,
    AnalysisRequest,
    AnalysisScope,
    ClaimType,
    Evidence,
    EvidenceType,
    GeneratedAnalysis,
    GeneratedClaim,
    GeneratedEvidence,
    ModelUsage,
    PaperAnalysis,
    VerificationStatus,
)
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import (
    COMPARISON_DIMENSION_ORDER,
    ComparabilityStatus,
    Comparison,
    ComparisonBundle,
    ComparisonDimension,
    ComparisonDimensionName,
    PaperRelation,
    PaperRelationType,
    RelationProvenance,
)
from paper_harness.domain.knowledge import (
    GraphEntityType,
    KnowledgeGraphBundle,
    LineagePaper,
    TrendPaperRecord,
    TrendWindow,
    aggregate_trend_snapshots,
    extract_analysis_graph,
    extract_comparison_graph,
    merge_knowledge_graph_bundles,
)
from paper_harness.domain.models import (
    DailyRun,
    PaperStage,
    RunItem,
    RunItemStatus,
    RunOperation,
    RunStatus,
    TopicConfig,
)
from paper_harness.domain.reports import (
    GeneratedReportNarrative,
    GeneratedReportSection,
    ReportCounts,
    ReportEntityHighlight,
    ReportEvidenceReference,
    ReportFailure,
    ReportGraphChanges,
    ReportNarrativeMode,
    ReportNarrativeRequest,
    ReportPaperHighlight,
    ReportSectionKind,
    ReportType,
)
from paper_harness.ports.llm import LLMOutputError, LLMPort

NOW = datetime(2026, 8, 10, 5, tzinfo=UTC)
AS_OF = date(2026, 8, 10)
TOPIC_ID = UUID("c7a1ee38-0bc5-4f1b-9e45-0e1183042f9d")


def _id(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"m4-product-test:{label}")


def _topic() -> TopicConfig:
    return TopicConfig(
        id=TOPIC_ID,
        slug="broad-llm-agents",
        name="Broad LLM Agents",
        description="Research about broad LLM-agent systems.",
        categories=("cs.AI",),
        include_terms=("LLM agent",),
        exclude_terms=("traditional reinforcement learning",),
        overlap_hours=24,
        initial_lookback_days=2,
        max_results=100,
        representative_full_text_count=10,
    )


def test_daily_trend_input_normalizes_order_duplicates_and_missing_windows() -> None:
    snapshots = aggregate_trend_snapshots(
        TOPIC_ID,
        as_of_date=AS_OF,
        papers=(),
        entities=(),
        mentions=(),
        edges=(),
        mention_activity_dates={},
        edge_activity_dates={},
        generated_at=NOW,
    )

    normalized = normalize_daily_trends((snapshots[-1], snapshots[0], snapshots[0]))

    assert tuple(item.window for item in normalized) == (
        TrendWindow.SEVEN_DAYS,
        TrendWindow.NINETY_DAYS,
    )


def _analysis_bundle(slot: int) -> AnalysisBundle:
    paper_id = _id(f"paper:{slot}")
    paper_version_id = _id(f"paper-version:{slot}")
    analysis_id = _id(f"analysis:{slot}")
    claim = AnalysisClaim(
        id=_id(f"claim:{slot}"),
        analysis_id=analysis_id,
        paper_id=paper_id,
        paper_version_id=paper_version_id,
        key="research_problem",
        claim_type=ClaimType.RESEARCH_PROBLEM,
        text="Tool-using language-model agents need bounded, reliable planning.",
        provider="deepseek",
        model_version="DeepSeek-V4-Flash-2026-08-01",
        prompt_version="m2-analysis-v1",
        generated_at=NOW,
        source="deepseek_structured_analysis",
        verification_status=VerificationStatus.UNVERIFIED,
        schema_version=1,
        created_at=NOW,
    )
    evidence = Evidence(
        id=_id(f"source-evidence:{slot}"),
        analysis_id=analysis_id,
        paper_id=paper_id,
        paper_version_id=paper_version_id,
        key="research_problem_evidence",
        section="Abstract",
        passage_id=f"abstract-{slot}",
        coordinates=(),
        excerpt="The agent uses bounded planning to improve reliable tool use.",
        evidence_type=EvidenceType.SUPPORTS,
        supported_claim_ids=(claim.id,),
        extraction_source="deepseek_grounded_extraction",
        provider="deepseek",
        model_version="DeepSeek-V4-Flash-2026-08-01",
        prompt_version="m2-analysis-v1",
        generated_at=NOW,
        verification_status=VerificationStatus.UNVERIFIED,
        schema_version=1,
        created_at=NOW,
    )
    analysis = PaperAnalysis(
        id=analysis_id,
        paper_id=paper_id,
        paper_version_id=paper_version_id,
        parsed_paper_id=None,
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        summary="A bounded planning method improves reliable agent tool use.",
        research_problem="Reliable planning for tool-using language-model agents",
        method_summary="A bounded planning controller",
        key_contributions=("A bounded planning controller.",),
        limitations=("The evaluation covers a bounded task suite.",),
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-08-01",
        prompt_version="m2-analysis-v1",
        generated_at=NOW,
        source="deepseek_structured_analysis",
        verification_status=VerificationStatus.UNVERIFIED,
        usage=ModelUsage(100, 40, 140, 1, 300, Decimal("0.001")),
        schema_version=1,
        created_at=NOW,
    )
    return AnalysisBundle(analysis=analysis, claims=(claim,), evidence=(evidence,))


def _comparison_input(
    slot: int,
    analysis: AnalysisBundle,
    *,
    all_graph_dimensions_evidenced: bool = False,
) -> tuple[ComparisonGraphInput, ReportEvidenceReference]:
    comparison_id = _id(f"comparison:{slot}")
    target_paper_id = _id(f"target-paper:{slot}")
    target_version_id = _id(f"target-version:{slot}")
    target_analysis_id = _id(f"target-analysis:{slot}")
    source_evidence_id = analysis.evidence[0].id
    target_evidence_id = _id(f"target-evidence:{slot}")
    dimensions = tuple(
        ComparisonDimension(
            id=_id(f"dimension:{slot}:{name.value}"),
            comparison_id=comparison_id,
            name=name,
            position=position,
            source_value=(
                "Reliable planning for tool-using language-model agents"
                if name is ComparisonDimensionName.RESEARCH_PROBLEM
                else f"Source {name.value.lower().replace('_', ' ')}"
            ),
            target_value=(
                "Historical planning reliability"
                if name is ComparisonDimensionName.RESEARCH_PROBLEM
                else f"Target {name.value.lower().replace('_', ' ')}"
            ),
            assessment=f"Bounded comparison for {name.value.lower()}.",
            source_evidence_ids=(source_evidence_id,)
            if all_graph_dimensions_evidenced or name is ComparisonDimensionName.RESEARCH_PROBLEM
            else (),
            target_evidence_ids=(target_evidence_id,)
            if all_graph_dimensions_evidenced or name is ComparisonDimensionName.RESEARCH_PROBLEM
            else (),
            schema_version=1,
            created_at=NOW,
        )
        for position, name in enumerate(COMPARISON_DIMENSION_ORDER)
    )
    comparison = Comparison(
        id=comparison_id,
        search_session_id=_id(f"search-session:{slot}"),
        source_paper_id=analysis.analysis.paper_id,
        source_paper_version_id=analysis.analysis.paper_version_id,
        source_analysis_id=analysis.analysis.id,
        source_analysis_scope=analysis.analysis.analysis_scope,
        target_paper_id=target_paper_id,
        target_paper_version_id=target_version_id,
        target_analysis_id=target_analysis_id,
        target_analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        comparability_status=ComparabilityStatus.PARTIALLY_COMPARABLE,
        comparability_reason="The papers address the same problem with different evaluations.",
        summary="The persisted evidence supports a bounded partial comparison.",
        dimensions=dimensions,
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-08-01",
        prompt_version="m3-comparison-v1",
        generated_at=NOW,
        source="deepseek_structured_comparison",
        verification_status=VerificationStatus.UNVERIFIED,
        usage=ModelUsage(120, 50, 170, 1, 400, Decimal("0.002")),
        schema_version=1,
        created_at=NOW,
    )
    relation = PaperRelation(
        id=_id(f"relation:{slot}"),
        source_paper_id=analysis.analysis.paper_id,
        source_paper_version_id=analysis.analysis.paper_version_id,
        target_paper_id=target_paper_id,
        target_paper_version_id=target_version_id,
        relation_type=PaperRelationType.EXTENDS,
        provenance=RelationProvenance.LLM_INFERRED,
        evidence_ids=(source_evidence_id, target_evidence_id),
        justification="The bounded method extends the recorded historical planning setup.",
        provider="deepseek",
        model_version="DeepSeek-V4-Flash-2026-08-01",
        prompt_version="m3-comparison-v1",
        confidence=0.75,
        verification_status=VerificationStatus.UNVERIFIED,
        generated_at=NOW,
        schema_version=1,
        created_at=NOW,
    )
    return (
        ComparisonGraphInput(
            bundle=ComparisonBundle(comparison=comparison, relations=(relation,)),
            source_paper_title=f"Current Agent Paper {slot}",
            target_paper_title=f"Historical Agent Paper {slot}",
        ),
        ReportEvidenceReference(
            id=target_evidence_id,
            paper_id=target_paper_id,
            paper_version_id=target_version_id,
            section="Results",
            excerpt="The historical paper reports its planning reliability evaluation.",
            evidence_type=EvidenceType.QUALIFIES.value,
            verification_status=VerificationStatus.UNVERIFIED,
        ),
    )


def _product_paper(
    slot: int,
    *,
    with_comparison: bool = True,
    all_graph_dimensions_evidenced: bool = False,
) -> ProductPaperInput:
    analysis = _analysis_bundle(slot)
    source_evidence = analysis.evidence[0]
    report_evidence = (
        ReportEvidenceReference(
            id=source_evidence.id,
            paper_id=source_evidence.paper_id,
            paper_version_id=source_evidence.paper_version_id,
            section=source_evidence.section,
            excerpt=source_evidence.excerpt,
            evidence_type=source_evidence.evidence_type.value,
            verification_status=source_evidence.verification_status,
        ),
    )
    comparisons: tuple[ComparisonGraphInput, ...] = ()
    if with_comparison:
        comparison, target_evidence = _comparison_input(
            slot,
            analysis,
            all_graph_dimensions_evidenced=all_graph_dimensions_evidenced,
        )
        comparisons = (comparison,)
        report_evidence += (target_evidence,)
    return ProductPaperInput(
        paper_id=analysis.analysis.paper_id,
        paper_version_id=analysis.analysis.paper_version_id,
        paper_title=f"Current Agent Paper {slot}",
        analysis=analysis,
        comparisons=comparisons,
        evidence=report_evidence,
        retrieved_candidate_count=len(comparisons),
    )


def _publication_input(papers: tuple[ProductPaperInput, ...]) -> ProductPublicationInput:
    source_run_id = _id("source-run:" + ":".join(str(item.paper_version_id) for item in papers))
    source_run = DailyRun(
        id=source_run_id,
        topic_id=TOPIC_ID,
        logical_date=AS_OF,
        operation=RunOperation.STRUCTURED_ANALYSIS,
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        status=RunStatus.COMPLETE,
        started_at=NOW - timedelta(minutes=5),
        completed_at=NOW - timedelta(minutes=1),
        cursor_from=None,
        cursor_to=None,
        discovered_count=0,
        normalized_count=0,
        selected_count=len(papers),
        completed_count=len(papers),
        failed_count=0,
        error_code=None,
        error_detail=None,
        schema_version=1,
        created_at=NOW - timedelta(minutes=5),
    )
    details = tuple(
        RunItemDetail(
            item=RunItem(
                id=_id(f"source-item:{item.paper_version_id}"),
                run_id=source_run_id,
                paper_id=item.paper_id,
                paper_version_id=item.paper_version_id,
                stage=PaperStage.EVIDENCE_EXTRACTED,
                status=RunItemStatus.COMPLETED,
                failed_stage=None,
                error_code=None,
                retryable=None,
                error_detail=None,
                schema_version=1,
                created_at=NOW - timedelta(minutes=5),
                updated_at=NOW - timedelta(minutes=1),
            ),
            canonical_arxiv_id=f"2608.{slot + 1:05d}",
            paper_title=item.paper_title,
        )
        for slot, item in enumerate(papers)
    )
    return ProductPublicationInput(
        source_run=RunDetail(run=source_run, items=details),
        papers=papers,
    )


def _graph_corpus(papers: tuple[ProductPaperInput, ...]) -> GraphCorpusInput:
    bundles: list[KnowledgeGraphBundle] = []
    version_metadata: dict[UUID, tuple[UUID, str, date]] = {}
    for paper in papers:
        if not paper.comparisons:
            continue
        bundles.append(
            extract_analysis_graph(
                TOPIC_ID,
                paper.analysis,
                paper_title=paper.paper_title,
            ).bundle
        )
        version_metadata[paper.paper_version_id] = (paper.paper_id, paper.paper_title, AS_OF)
        for comparison in paper.comparisons:
            bundles.append(
                extract_comparison_graph(
                    TOPIC_ID,
                    comparison.bundle,
                    source_paper_title=comparison.source_paper_title,
                    target_paper_title=comparison.target_paper_title,
                ).bundle
            )
            target = comparison.bundle.comparison
            version_metadata[target.target_paper_version_id] = (
                target.target_paper_id,
                comparison.target_paper_title,
                AS_OF - timedelta(days=14),
            )
    merged = merge_knowledge_graph_bundles(bundles)
    paper_entities = {
        item.paper_id: item
        for item in merged.entities
        if item.entity_type is GraphEntityType.PAPER and item.paper_id is not None
    }
    return GraphCorpusInput(
        topic_id=TOPIC_ID,
        papers=tuple(
            TrendPaperRecord(
                paper_id=paper_id,
                paper_version_id=version_id,
                activity_date=activity_date,
                title=title,
            )
            for version_id, (paper_id, title, activity_date) in sorted(
                version_metadata.items(), key=lambda item: str(item[0])
            )
        ),
        lineage_papers=tuple(
            LineagePaper(
                graph_entity_id=entity.id,
                paper_id=paper_id,
                title=entity.display_label,
                publication_date=(
                    AS_OF
                    if any(
                        value[0] == paper_id and value[2] == AS_OF
                        for value in version_metadata.values()
                    )
                    else AS_OF - timedelta(days=30)
                ),
            )
            for paper_id, entity in sorted(paper_entities.items(), key=lambda item: str(item[0]))
        ),
        entities=merged.entities,
        mentions=merged.mentions,
        edges=merged.edges,
        mention_activity_dates={
            item.id: version_metadata[item.paper_version_id][2] for item in merged.mentions
        },
        edge_activity_dates={
            item.id: version_metadata[item.source_paper_version_id][2] for item in merged.edges
        },
    )


def _repository(papers: tuple[ProductPaperInput, ...]) -> FakeRepository:
    repository = FakeRepository()
    repository.product_input = _publication_input(papers)
    if any(item.comparisons for item in papers):
        repository.graph_corpus = _graph_corpus(papers)
    return repository


class _ReportLLM:
    def __init__(self, error: LLMOutputError | None = None) -> None:
        self.error = error
        self.calls: list[ReportNarrativeRequest] = []

    def generate_report(self, request: ReportNarrativeRequest) -> GeneratedReportNarrative:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        evidence_ids = (request.evidence[0].id,) if request.evidence else ()
        return GeneratedReportNarrative(
            provider="deepseek",
            configured_model="deepseek-v4-flash",
            model_version="DeepSeek-V4-Flash-2026-08-01",
            prompt_version="m4-report-v1",
            generated_at=NOW,
            summary="A grounded synthesis of the validated persisted report input.",
            sections=tuple(
                GeneratedReportSection(
                    kind=kind,
                    narrative=f"Grounded {kind.value.lower()} narrative.",
                    evidence_ids=evidence_ids if kind is ReportSectionKind.OVERVIEW else (),
                )
                for kind in ReportSectionKind
            ),
            usage=ModelUsage(80, 30, 110, 1, 250, Decimal("0.001")),
        )


def test_complete_structured_publication_persists_graph_trends_lineage_and_report() -> None:
    paper = _product_paper(1)
    repository = _repository((paper,))
    llm = _ReportLLM()

    run = PublishProduct(
        repository=repository,
        llm=cast(LLMPort, llm),
        clock=lambda: NOW,
    ).execute(_topic(), narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY, logical_date=AS_OF)

    assert run.status is RunStatus.COMPLETE
    assert (run.completed_count, run.failed_count) == (1, 0)
    assert tuple(repository.product_graphs) == (paper.paper_version_id,)
    assert tuple(item.window for item in repository.persisted_trends) == tuple(TrendWindow)
    assert len(repository.persisted_lineages) == 1
    assert repository.persisted_lineages[0].root_paper_id == paper.paper_id
    assert len(repository.persisted_lineages[0].nodes) == 2
    assert llm.calls == []
    assert repository.product_run is not None
    assert repository.product_run.report is not None
    report = repository.product_run.report.report
    assert report.status is RunStatus.COMPLETE
    assert report.narrative_mode is ReportNarrativeMode.STRUCTURED_ONLY
    assert report.source == "m4_structured_report"
    assert report.graph_changes.inferred_edge_count == 1
    assert len(report.trend_snapshot_ids) == 3


def test_daily_graph_change_counts_deduplicate_shared_stable_entities() -> None:
    papers = (_product_paper(1), _product_paper(2))
    repository = _repository(papers)

    PublishProduct(repository=repository, llm=None, clock=lambda: NOW).execute(
        _topic(),
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=AS_OF,
    )

    assert repository.product_run is not None
    assert repository.product_run.report is not None
    report = repository.product_run.report.report
    distinct_entity_ids = {
        entity.id for bundle in repository.product_graphs.values() for entity in bundle.entities
    }
    distinct_edge_ids = {
        edge.id for bundle in repository.product_graphs.values() for edge in bundle.edges
    }
    summed_entity_observations = sum(
        len(bundle.entities) for bundle in repository.product_graphs.values()
    )
    assert len(distinct_entity_ids) < summed_entity_observations
    assert report.graph_changes.entity_count == len(distinct_entity_ids)
    assert report.graph_changes.edge_count == len(distinct_edge_ids)


def test_complete_graph_categories_do_not_report_available_entities_as_missing() -> None:
    paper = _product_paper(1, all_graph_dimensions_evidenced=True)
    repository = _repository((paper,))

    PublishProduct(repository=repository, llm=None, clock=lambda: NOW).execute(
        _topic(),
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=AS_OF,
    )

    assert repository.product_run is not None
    assert repository.product_run.report is not None
    assert repository.product_run.report.report.missing_sections == ()


@pytest.mark.parametrize(
    ("first_mode", "second_mode"),
    (
        (ReportNarrativeMode.STRUCTURED_ONLY, ReportNarrativeMode.DEEPSEEK),
        (ReportNarrativeMode.DEEPSEEK, ReportNarrativeMode.STRUCTURED_ONLY),
    ),
)
def test_daily_report_idempotency_rejects_a_different_narrative_mode(
    first_mode: ReportNarrativeMode,
    second_mode: ReportNarrativeMode,
) -> None:
    repository = _repository((_product_paper(1),))
    publisher = PublishProduct(
        repository=repository,
        llm=cast(LLMPort, _ReportLLM()),
        clock=lambda: NOW,
    )
    publisher.execute(_topic(), narrative_mode=first_mode, logical_date=AS_OF)

    with pytest.raises(
        ReportNarrativeModeConflictError,
        match="requested mode",
    ):
        publisher.execute(_topic(), narrative_mode=second_mode, logical_date=AS_OF)


def test_missing_comparison_produces_partial_report_with_visible_item_failure() -> None:
    completed = _product_paper(1)
    missing_comparison = _product_paper(2, with_comparison=False)
    repository = _repository((completed, missing_comparison))

    run = PublishProduct(repository=repository, llm=None, clock=lambda: NOW).execute(
        _topic(),
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=AS_OF,
    )

    assert run.status is RunStatus.PARTIAL
    assert (run.completed_count, run.failed_count) == (1, 1)
    failed = next(item for item in repository.items if item.status is RunItemStatus.FAILED)
    assert failed.paper_version_id == missing_comparison.paper_version_id
    assert failed.failed_stage is PaperStage.COMPARED
    assert failed.error_code == "COMPARISON_MISSING"
    assert repository.product_run is not None
    assert repository.product_run.report is not None
    report = repository.product_run.report.report
    assert report.status is RunStatus.PARTIAL
    assert report.failures[0].error_code == "COMPARISON_MISSING"
    assert report.counts.completed == 1
    assert report.counts.failed == 1
    assert "missing from product publication" in report.missing_sections[0]


def test_historical_analysis_failure_is_frozen_into_partial_publication() -> None:
    completed = _product_paper(1)
    historical = _product_paper(2)
    repository = _repository((completed,))
    failure = ProductFailureInput(
        paper_id=historical.paper_id,
        paper_version_id=historical.paper_version_id,
        stage=PaperStage.PARSED,
        failed_stage=PaperStage.ANALYZED,
        error_code="ANALYSIS_MODEL_OUTPUT_INVALID",
        retryable=False,
        error_detail="Historical analysis output failed schema validation.",
    )
    publisher = PublishProduct(repository=repository, llm=None, clock=lambda: NOW)

    run = publisher.execute(
        _topic(),
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=AS_OF,
        upstream_failures=(failure,),
    )

    assert run.status is RunStatus.PARTIAL
    assert (run.selected_count, run.completed_count, run.failed_count) == (2, 1, 1)
    assert repository.product_run is not None
    assert repository.product_run.report is not None
    report = repository.product_run.report.report
    assert report.status is RunStatus.PARTIAL
    assert report.failures[0].paper_id == historical.paper_id
    assert report.failures[0].paper_version_id == historical.paper_version_id
    assert report.failures[0].failed_stage is PaperStage.ANALYZED
    assert report.failures[0].error_code == "ANALYSIS_MODEL_OUTPUT_INVALID"
    assert report.failures[0].error_detail == failure.error_detail

    replay = publisher.execute(
        _topic(),
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=AS_OF,
    )
    assert replay == run
    assert repository.product_run.report.report == report


def test_stale_persisted_comparison_cannot_satisfy_current_pipeline_publication() -> None:
    paper_with_only_stale_comparison = _product_paper(1)
    repository = _repository((paper_with_only_stale_comparison,))

    run = PublishProduct(repository=repository, llm=None, clock=lambda: NOW).execute(
        _topic(),
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=AS_OF,
        comparison_ids=frozenset(),
    )

    assert run.status is RunStatus.FAILED
    assert run.error_code == "NO_SELECTED_PAPER_COMPLETED"
    assert repository.items[0].failed_stage is PaperStage.COMPARED
    assert repository.items[0].error_code == "COMPARISON_MISSING"
    assert repository.product_graphs == {}
    assert repository.product_run is not None
    assert repository.product_run.report is None


def test_failed_product_restart_uses_current_comparison_and_preserves_upstream_failure() -> None:
    completed = _product_paper(1)
    upstream_failed = _product_paper(2)
    comparison_id = completed.comparisons[0].bundle.comparison.id
    repository = _repository((completed,))
    source = repository.product_input
    assert source is not None
    failed_source_item = RunItemDetail(
        item=RunItem(
            id=_id("source-upstream-failure"),
            run_id=source.source_run.run.id,
            paper_id=upstream_failed.paper_id,
            paper_version_id=upstream_failed.paper_version_id,
            stage=PaperStage.SELECTED,
            status=RunItemStatus.FAILED,
            failed_stage=PaperStage.ANALYZED,
            error_code="ANALYSIS_MODEL_OUTPUT_INVALID",
            retryable=False,
            error_detail="Persisted upstream analysis failure.",
            schema_version=1,
            created_at=NOW - timedelta(minutes=5),
            updated_at=NOW - timedelta(minutes=1),
        ),
        canonical_arxiv_id="2608.00002",
        paper_title=upstream_failed.paper_title,
    )
    publisher = PublishProduct(repository=repository, llm=None, clock=lambda: NOW)

    failed = publisher.execute(
        _topic(),
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=AS_OF,
        comparison_ids=frozenset(),
    )
    assert failed.status is RunStatus.FAILED
    repository.product_input = replace(
        source,
        source_run=replace(
            source.source_run,
            run=replace(
                source.source_run.run,
                status=RunStatus.PARTIAL,
                selected_count=2,
                completed_count=1,
                failed_count=1,
            ),
            items=source.source_run.items + (failed_source_item,),
        ),
    )
    retried = publisher.execute(
        _topic(),
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=AS_OF,
        comparison_ids=frozenset((comparison_id,)),
    )

    assert retried.id == failed.id
    assert retried.status is RunStatus.PARTIAL
    assert (retried.completed_count, retried.failed_count) == (1, 1)
    preserved = next(item for item in repository.items if item.paper_id == upstream_failed.paper_id)
    assert preserved.failed_stage is PaperStage.ANALYZED
    assert preserved.error_code == "ANALYSIS_MODEL_OUTPUT_INVALID"


def test_no_paper_completing_graph_construction_fails_without_a_report() -> None:
    missing_comparison = _product_paper(1, with_comparison=False)
    repository = _repository((missing_comparison,))

    run = PublishProduct(repository=repository, llm=None, clock=lambda: NOW).execute(
        _topic(),
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=AS_OF,
    )

    assert run.status is RunStatus.FAILED
    assert run.error_code == "NO_SELECTED_PAPER_COMPLETED"
    assert repository.items[0].error_code == "COMPARISON_MISSING"
    assert repository.product_run is not None
    assert repository.product_run.report is None
    assert repository.persisted_trends == ()
    assert repository.reports == ()


def test_deepseek_publication_uses_only_generated_sections_and_provenance() -> None:
    repository = _repository((_product_paper(1),))
    llm = _ReportLLM()

    run = PublishProduct(
        repository=repository,
        llm=cast(LLMPort, llm),
        clock=lambda: NOW,
    ).execute(_topic(), narrative_mode=ReportNarrativeMode.DEEPSEEK, logical_date=AS_OF)

    assert run.status is RunStatus.COMPLETE
    assert len(llm.calls) == 1
    assert repository.product_run is not None
    assert repository.product_run.report is not None
    report = repository.product_run.report.report
    assert report.narrative_mode is ReportNarrativeMode.DEEPSEEK
    assert report.source == "deepseek_chat_completions"
    assert report.provider == "deepseek"
    assert report.prompt_version == "m4-report-v1"
    assert report.sections[0].narrative == "Grounded overview narrative."


def test_unsupported_generated_claim_never_enters_daily_report_input() -> None:
    source_text = "The paper introduces a bounded planning controller."
    request = AnalysisRequest(
        paper_id=_id("grounded-report-paper"),
        paper_version_id=_id("grounded-report-version"),
        canonical_arxiv_id="2608.09999",
        arxiv_version=1,
        title="Grounded Report Paper",
        scope=AnalysisScope.ABSTRACT_ONLY,
        passages=(AnalysisPassage(id="abstract", section="Abstract", text=source_text),),
    )
    unsupported_text = "The model invents an unsupported state-of-the-art result."
    generated = GeneratedAnalysis(
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-08-01",
        prompt_version="m2-analysis-v1",
        generated_at=NOW,
        claims=(
            GeneratedClaim(
                key="method",
                claim_type=ClaimType.METHOD,
                text="The paper introduces a bounded planning controller.",
            ),
            GeneratedClaim(
                key="unsupported",
                claim_type=ClaimType.RESULT,
                text=unsupported_text,
            ),
        ),
        evidence=(
            GeneratedEvidence(
                key="method_evidence",
                claim_keys=("method",),
                passage_ids=("abstract",),
                evidence_type=EvidenceType.SUPPORTS,
            ),
            GeneratedEvidence(
                key="unsupported_evidence",
                claim_keys=("unsupported",),
                passage_ids=("missing",),
                evidence_type=EvidenceType.SUPPORTS,
            ),
        ),
        usage=ModelUsage(100, 40, 140, 1, 300, Decimal("0.001")),
    )
    bundle = build_analysis_bundle(request, generated, created_at=NOW)
    comparison, target_evidence = _comparison_input(99, bundle)
    source_evidence = tuple(
        ReportEvidenceReference(
            id=item.id,
            paper_id=item.paper_id,
            paper_version_id=item.paper_version_id,
            section=item.section,
            excerpt=item.excerpt,
            evidence_type=item.evidence_type.value,
            verification_status=item.verification_status,
        )
        for item in bundle.evidence
    )
    paper = ProductPaperInput(
        paper_id=request.paper_id,
        paper_version_id=request.paper_version_id,
        paper_title=request.title,
        analysis=bundle,
        comparisons=(comparison,),
        evidence=(*source_evidence, target_evidence),
        retrieved_candidate_count=1,
    )
    repository = _repository((paper,))
    llm = _ReportLLM()

    run = PublishProduct(
        repository=repository,
        llm=cast(LLMPort, llm),
        clock=lambda: NOW,
    ).execute(_topic(), narrative_mode=ReportNarrativeMode.DEEPSEEK, logical_date=AS_OF)

    assert run.status is RunStatus.COMPLETE
    assert len(llm.calls) == 1
    assert unsupported_text not in llm.calls[0].highlighted_papers[0].reason
    assert repository.product_run is not None
    assert repository.product_run.report is not None
    assert unsupported_text not in repository.product_run.report.report.highlighted_papers[0].reason


def test_deepseek_output_failure_fails_run_without_structured_fallback() -> None:
    repository = _repository((_product_paper(1),))
    llm = _ReportLLM(LLMOutputError("schema-invalid report output"))

    with pytest.raises(LLMOutputError, match="schema-invalid"):
        PublishProduct(
            repository=repository,
            llm=cast(LLMPort, llm),
            clock=lambda: NOW,
        ).execute(_topic(), narrative_mode=ReportNarrativeMode.DEEPSEEK, logical_date=AS_OF)

    assert len(llm.calls) == 1
    assert repository.run is not None
    assert repository.run.status is RunStatus.FAILED
    assert repository.run.error_code == "LLM_OUTPUT_INVALID"
    assert repository.reports == ()
    assert repository.product_graphs == {}
    assert repository.persisted_trends == ()
    assert repository.persisted_lineages == ()
    assert repository.product_run is not None
    assert repository.product_run.report is None


def test_failed_product_publication_restarts_the_same_run_without_staged_artifacts() -> None:
    repository = _repository((_product_paper(1),))
    llm = _ReportLLM(LLMOutputError("transient report request failed"))
    publisher = PublishProduct(
        repository=repository,
        llm=cast(LLMPort, llm),
        clock=lambda: NOW,
    )

    with pytest.raises(LLMOutputError, match="transient"):
        publisher.execute(
            _topic(),
            narrative_mode=ReportNarrativeMode.DEEPSEEK,
            logical_date=AS_OF,
        )
    assert repository.run is not None
    failed_run_id = repository.run.id
    assert repository.product_graphs == {}

    llm.error = None
    completed = publisher.execute(
        _topic(),
        narrative_mode=ReportNarrativeMode.DEEPSEEK,
        logical_date=AS_OF,
    )

    assert completed.id == failed_run_id
    assert completed.status is RunStatus.COMPLETE
    assert len(repository.reports) == 1
    assert len(repository.product_graphs) == 1


def test_deepseek_mode_requires_the_llm_before_starting_a_product_run() -> None:
    repository = _repository((_product_paper(1),))

    with pytest.raises(ValueError, match="requires the configured LLM"):
        PublishProduct(repository=repository, llm=None, clock=lambda: NOW).execute(
            _topic(),
            narrative_mode=ReportNarrativeMode.DEEPSEEK,
            logical_date=AS_OF,
        )

    assert repository.run is None


def test_product_input_rejects_comparison_evidence_marked_rejected() -> None:
    paper = _product_paper(1)
    comparison_evidence_ids = {
        evidence_id
        for item in paper.comparisons
        for dimension in item.bundle.comparison.dimensions
        for evidence_id in dimension.source_evidence_ids + dimension.target_evidence_ids
    }
    rejected_evidence = tuple(
        replace(item, verification_status=VerificationStatus.REJECTED)
        if item.id in comparison_evidence_ids
        else item
        for item in paper.evidence
    )

    with pytest.raises(DomainInvariantError, match="comparison references rejected evidence"):
        replace(paper, evidence=rejected_evidence)


def _daily_report_detail(day: date, slot: int) -> ReportDetail:
    paper_id = _id(f"periodic-paper:{slot}")
    version_id = _id(f"periodic-version:{slot}")
    evidence = ReportEvidenceReference(
        id=_id(f"periodic-evidence:{day.isoformat()}:{slot}"),
        paper_id=paper_id,
        paper_version_id=version_id,
        section="Abstract",
        excerpt="Persisted evidence supports this bounded daily highlight.",
        evidence_type=EvidenceType.SUPPORTS.value,
        verification_status=VerificationStatus.UNVERIFIED,
    )
    request = ReportNarrativeRequest(
        report_type=ReportType.DAILY,
        period_start=day,
        period_end=day,
        status=RunStatus.COMPLETE,
        counts=ReportCounts(1, 1, 1, 1, 0),
        highlighted_papers=(
            ReportPaperHighlight(
                paper_id=paper_id,
                paper_version_id=version_id,
                title=f"Periodic Paper {slot}",
                reason="The validated daily analysis is included in periodic synthesis.",
                evidence_ids=(evidence.id,),
            ),
        ),
        major_entities=(),
        notable_comparisons=(),
        graph_changes=ReportGraphChanges(2, 1, 1, 0),
        trend_summaries=(),
        lineage_highlights=(),
        failures=(),
        limitations=("The daily report covers only the persisted corpus.",),
        evidence=(evidence,),
    )
    report = assemble_product_report(
        request,
        report_id=_id(f"periodic-daily-report:{day.isoformat()}"),
        run_id=_id(f"periodic-daily-run:{day.isoformat()}"),
        topic_id=TOPIC_ID,
        logical_date=day,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        generated=None,
        trend_snapshot_ids=(),
        created_at=NOW,
    )
    return ReportDetail(report=report, evidence=(evidence,))


def _periodic_input(
    *,
    report_type: ReportType,
    period_start: date,
    period_end: date,
    daily_count: int,
    paper_count: int,
) -> PeriodicReportInput:
    return PeriodicReportInput(
        topic_id=TOPIC_ID,
        report_type=report_type,
        period_start=period_start,
        period_end=period_end,
        daily_reports=tuple(
            _daily_report_detail(period_start + timedelta(days=index), index % paper_count)
            for index in range(daily_count)
        ),
        included_paper_ids=tuple(_id(f"periodic-paper:{slot}") for slot in range(paper_count)),
        graph_changes=ReportGraphChanges(
            entity_count=paper_count + 1,
            edge_count=paper_count,
            new_entity_count=paper_count,
            inferred_edge_count=0,
        ),
        trends=(),
    )


def _partial_daily_report_with_repeated_failure(day: date, slot: int) -> ReportDetail:
    detail = _daily_report_detail(day, slot)
    failure = ReportFailure(
        id=_id(f"periodic-failure:{day.isoformat()}"),
        report_id=detail.report.id,
        paper_id=_id("periodic-repeated-failure-paper"),
        paper_version_id=_id("periodic-repeated-failure-version"),
        failed_stage=PaperStage.GRAPH_UPDATED,
        error_code="GRAPH_EXTRACTION_INVALID",
        retryable=False,
        error_detail="The bounded graph entity input was invalid.",
        schema_version=1,
        created_at=NOW,
    )
    return replace(
        detail,
        report=replace(
            detail.report,
            status=RunStatus.PARTIAL,
            counts=ReportCounts(2, 2, 2, 1, 1),
            failures=(failure,),
        ),
    )


def test_weekly_report_is_persisted_and_subsequent_generation_is_idempotent() -> None:
    period_start = date(2026, 8, 3)
    period_end = date(2026, 8, 9)
    repository = FakeRepository()
    repository.periodic_input = _periodic_input(
        report_type=ReportType.WEEKLY,
        period_start=period_start,
        period_end=period_end,
        daily_count=7,
        paper_count=3,
    )
    generator = GeneratePeriodicReport(repository=repository, llm=None, clock=lambda: NOW)

    first = generator.execute(
        _topic(),
        report_type=ReportType.WEEKLY,
        period_start=period_start,
        period_end=period_end,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
    )
    repository.periodic_input = None
    second = generator.execute(
        _topic(),
        report_type=ReportType.WEEKLY,
        period_start=period_start,
        period_end=period_end,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
    )

    assert first == second
    assert first.report_type is ReportType.WEEKLY
    assert first.run_id is None
    assert first.counts.completed == 7
    assert first.graph_changes == ReportGraphChanges(4, 3, 3, 0)
    assert first.narrative_mode is ReportNarrativeMode.STRUCTURED_ONLY
    assert tuple(section.kind for section in first.sections) == tuple(ReportSectionKind)
    assert len(repository.reports) == 1


def test_periodic_report_does_not_propagate_newly_rejected_evidence() -> None:
    period_start = date(2026, 8, 3)
    period_end = date(2026, 8, 9)
    repository = FakeRepository()
    source = _periodic_input(
        report_type=ReportType.WEEKLY,
        period_start=period_start,
        period_end=period_end,
        daily_count=7,
        paper_count=3,
    )
    rejected_id = source.daily_reports[0].evidence[0].id
    rejected_detail = replace(
        source.daily_reports[0],
        evidence=(
            replace(
                source.daily_reports[0].evidence[0],
                verification_status=VerificationStatus.REJECTED,
            ),
        ),
    )
    repository.periodic_input = replace(
        source,
        daily_reports=(rejected_detail, *source.daily_reports[1:]),
    )

    report = GeneratePeriodicReport(repository=repository, llm=None, clock=lambda: NOW).execute(
        _topic(),
        report_type=ReportType.WEEKLY,
        period_start=period_start,
        period_end=period_end,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
    )

    assert rejected_id not in report.evidence_ids
    assert all(rejected_id not in item.evidence_ids for item in report.highlighted_papers)


def test_periodic_entity_counts_use_the_latest_window_without_rolling_double_count() -> None:
    period_start = date(2026, 8, 3)
    period_end = date(2026, 8, 9)
    repository = FakeRepository()
    source = _periodic_input(
        report_type=ReportType.WEEKLY,
        period_start=period_start,
        period_end=period_end,
        daily_count=7,
        paper_count=3,
    )
    entity_id = _id("periodic-method")
    daily_reports = tuple(
        replace(
            detail,
            report=replace(
                detail.report,
                major_entities=(
                    ReportEntityHighlight(
                        graph_entity_id=entity_id,
                        entity_type=GraphEntityType.METHOD.value,
                        label="Bounded planning",
                        distinct_paper_count=index + 1,
                    ),
                ),
            ),
        )
        for index, detail in enumerate(source.daily_reports)
    )
    repository.periodic_input = replace(source, daily_reports=daily_reports)

    report = GeneratePeriodicReport(repository=repository, llm=None, clock=lambda: NOW).execute(
        _topic(),
        report_type=ReportType.WEEKLY,
        period_start=period_start,
        period_end=period_end,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
    )

    assert report.major_entities == (
        ReportEntityHighlight(
            graph_entity_id=entity_id,
            entity_type=GraphEntityType.METHOD.value,
            label="Bounded planning",
            distinct_paper_count=7,
        ),
    )


@pytest.mark.parametrize(
    ("first_mode", "second_mode"),
    (
        (ReportNarrativeMode.STRUCTURED_ONLY, ReportNarrativeMode.DEEPSEEK),
        (ReportNarrativeMode.DEEPSEEK, ReportNarrativeMode.STRUCTURED_ONLY),
    ),
)
def test_periodic_report_idempotency_rejects_a_different_narrative_mode(
    first_mode: ReportNarrativeMode,
    second_mode: ReportNarrativeMode,
) -> None:
    period_start = date(2026, 8, 3)
    period_end = date(2026, 8, 9)
    repository = FakeRepository()
    repository.periodic_input = _periodic_input(
        report_type=ReportType.WEEKLY,
        period_start=period_start,
        period_end=period_end,
        daily_count=7,
        paper_count=3,
    )
    generator = GeneratePeriodicReport(
        repository=repository,
        llm=cast(LLMPort, _ReportLLM()),
        clock=lambda: NOW,
    )
    generator.execute(
        _topic(),
        report_type=ReportType.WEEKLY,
        period_start=period_start,
        period_end=period_end,
        narrative_mode=first_mode,
    )

    with pytest.raises(ReportNarrativeModeConflictError, match="requested mode"):
        generator.execute(
            _topic(),
            report_type=ReportType.WEEKLY,
            period_start=period_start,
            period_end=period_end,
            narrative_mode=second_mode,
        )


def test_periodic_report_aggregates_repeated_failures_with_occurrence_dates() -> None:
    period_start = date(2026, 8, 3)
    period_end = date(2026, 8, 9)
    repository = FakeRepository()
    source = _periodic_input(
        report_type=ReportType.WEEKLY,
        period_start=period_start,
        period_end=period_end,
        daily_count=7,
        paper_count=3,
    )
    repository.periodic_input = replace(
        source,
        daily_reports=(
            _partial_daily_report_with_repeated_failure(period_start, 0),
            _partial_daily_report_with_repeated_failure(period_start + timedelta(days=1), 1),
            *source.daily_reports[2:],
        ),
    )

    report = GeneratePeriodicReport(repository=repository, llm=None, clock=lambda: NOW).execute(
        _topic(),
        report_type=ReportType.WEEKLY,
        period_start=period_start,
        period_end=period_end,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
    )

    assert report.counts.failed == 2
    assert len(report.failures) == 1
    assert report.failures[0].error_detail.startswith("2 daily failure occurrence(s)")
    assert period_start.isoformat() in report.failures[0].error_detail
    assert (period_start + timedelta(days=1)).isoformat() in report.failures[0].error_detail
    assert any("aggregated" in value for value in report.limitations)


@pytest.mark.parametrize(
    ("daily_count", "paper_count"),
    [(6, 10), (7, 2)],
)
def test_weekly_report_rejects_insufficient_daily_or_paper_coverage(
    daily_count: int,
    paper_count: int,
) -> None:
    repository = FakeRepository()
    repository.periodic_input = _periodic_input(
        report_type=ReportType.WEEKLY,
        period_start=date(2026, 8, 3),
        period_end=date(2026, 8, 9),
        daily_count=daily_count,
        paper_count=paper_count,
    )

    with pytest.raises(PeriodicReportInsufficientDataError, match="coverage"):
        GeneratePeriodicReport(repository=repository, llm=None, clock=lambda: NOW).execute(
            _topic(),
            report_type=ReportType.WEEKLY,
            period_start=date(2026, 8, 3),
            period_end=date(2026, 8, 9),
            narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        )

    assert repository.reports == ()


@pytest.mark.parametrize(
    ("report_type", "period_start", "period_end"),
    [
        (ReportType.DAILY, date(2026, 8, 3), date(2026, 8, 9)),
        (ReportType.WEEKLY, date(2026, 8, 4), date(2026, 8, 10)),
        (ReportType.WEEKLY, date(2026, 8, 3), date(2026, 8, 8)),
        (ReportType.MONTHLY, date(2026, 8, 2), date(2026, 8, 31)),
        (ReportType.MONTHLY, date(2026, 8, 1), date(2026, 8, 30)),
        (ReportType.MONTHLY, date(2026, 9, 1), date(2026, 8, 31)),
    ],
)
def test_periodic_report_rejects_invalid_type_or_period_bounds(
    report_type: ReportType,
    period_start: date,
    period_end: date,
) -> None:
    with pytest.raises(ValueError):
        GeneratePeriodicReport(repository=FakeRepository(), llm=None, clock=lambda: NOW).execute(
            _topic(),
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        )


def test_exact_cross_year_calendar_month_reaches_data_eligibility_check() -> None:
    with pytest.raises(PeriodicReportInsufficientDataError, match="persisted daily reports"):
        GeneratePeriodicReport(repository=FakeRepository(), llm=None, clock=lambda: NOW).execute(
            _topic(),
            report_type=ReportType.MONTHLY,
            period_start=date(2026, 12, 1),
            period_end=date(2026, 12, 31),
            narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        )


def test_periodic_deepseek_failure_does_not_persist_a_structured_fallback() -> None:
    repository = FakeRepository()
    repository.periodic_input = _periodic_input(
        report_type=ReportType.WEEKLY,
        period_start=date(2026, 8, 3),
        period_end=date(2026, 8, 9),
        daily_count=7,
        paper_count=3,
    )
    llm = _ReportLLM(LLMOutputError("periodic output is schema-invalid"))

    with pytest.raises(LLMOutputError, match="schema-invalid"):
        GeneratePeriodicReport(
            repository=repository,
            llm=cast(LLMPort, llm),
            clock=lambda: NOW,
        ).execute(
            _topic(),
            report_type=ReportType.WEEKLY,
            period_start=date(2026, 8, 3),
            period_end=date(2026, 8, 9),
            narrative_mode=ReportNarrativeMode.DEEPSEEK,
        )

    assert len(llm.calls) == 1
    assert repository.reports == ()
