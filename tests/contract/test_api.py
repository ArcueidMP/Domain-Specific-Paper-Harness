# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid5

import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeArxiv, FakeRepository, fake_pipeline_execution_contract

from paper_harness.application.analyze_papers import build_analysis_bundle
from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.application.read_models import (
    AnalysisDetail,
    ComparisonDetail,
    ComparisonEvidenceReference,
    GraphEdgeDetail,
    GraphEdgeEvidenceReference,
    GraphEvidenceRole,
    GraphNodeDetail,
    GraphView,
    LineageDetail,
    PaperDetail,
    ProductRunDetail,
    RelatedWorkDetail,
    RelatedWorkItem,
    ReportDetail,
    RunItemDetail,
    TrendDetail,
)
from paper_harness.domain.analysis import (
    AnalysisBundle,
    AnalysisPassage,
    AnalysisRequest,
    AnalysisScope,
    ClaimType,
    EvidenceType,
    GeneratedAnalysis,
    GeneratedClaim,
    GeneratedEvidence,
    ModelUsage,
    VerificationStatus,
)
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import (
    COMPARISON_DIMENSION_ORDER,
    CandidateOrigin,
    CandidateScoreComponents,
    ComparabilityStatus,
    Comparison,
    ComparisonBundle,
    ComparisonDimension,
    ExternalPaperStub,
    PaperRelation,
    PaperRelationType,
    RelationProvenance,
    SearchAction,
    SearchActionStatus,
    SearchCandidate,
    SearchCandidateDiscovery,
    SearchLimits,
    SearchSession,
    SearchSessionStatus,
    SearchStopReason,
    SearchTool,
    SelectionDecision,
)
from paper_harness.domain.identity import (
    stable_paper_id,
    stable_paper_version_id,
    stable_pipeline_execution_id,
)
from paper_harness.domain.knowledge import (
    GraphEdge,
    GraphEntityType,
    LineagePaper,
    TrendPaperRecord,
    aggregate_trend_snapshots,
    build_lineage_snapshot,
    extract_analysis_graph,
    extract_comparison_graph,
    merge_knowledge_graph_bundles,
)
from paper_harness.domain.models import (
    DailyRun,
    Paper,
    PaperStage,
    PaperVersion,
    PipelineExecution,
    PipelineExecutionMode,
    RunItem,
    RunItemStatus,
    RunOperation,
    RunStatus,
    TopicConfig,
)
from paper_harness.domain.reports import (
    Report,
    ReportComparisonHighlight,
    ReportCounts,
    ReportEntityHighlight,
    ReportEvidenceReference,
    ReportGraphChanges,
    ReportLineageHighlight,
    ReportNarrativeMode,
    ReportPaperHighlight,
    ReportSection,
    ReportSectionKind,
    ReportType,
)
from paper_harness.entrypoints.api import create_app
from paper_harness.ports.arxiv import ArxivPaperRecord
from paper_harness.ports.repository import (
    MigrationIncompatibleError,
    RepositoryUnavailableError,
)


def _paper(record: ArxivPaperRecord) -> Paper:
    return Paper(
        id=stable_paper_id(record.canonical_arxiv_id),
        canonical_arxiv_id=record.canonical_arxiv_id,
        title=record.title,
        abstract=record.abstract,
        current_version=record.version,
        first_submitted_at=record.submitted_at,
        latest_updated_at=record.updated_at,
        primary_category=record.primary_category,
        categories=record.categories,
        authors=record.authors,
        pdf_url=record.pdf_url,
        schema_version=1,
        created_at=datetime(2026, 1, 10, 5, tzinfo=UTC),
    )


def _paper_version(record: ArxivPaperRecord, paper: Paper) -> PaperVersion:
    return PaperVersion(
        id=stable_paper_version_id(record.canonical_arxiv_id, record.version),
        paper_id=paper.id,
        canonical_arxiv_id=record.canonical_arxiv_id,
        version=record.version,
        title=record.title,
        abstract=record.abstract,
        submitted_at=record.submitted_at,
        updated_at=record.updated_at,
        primary_category=record.primary_category,
        categories=record.categories,
        authors=record.authors,
        pdf_url=record.pdf_url,
        source_url=record.source_url,
        schema_version=1,
        created_at=datetime(2026, 1, 10, 5, tzinfo=UTC),
    )


def _analysis_detail(record: ArxivPaperRecord, paper: Paper) -> AnalysisDetail:
    version = _paper_version(record, paper)
    generated_at = datetime(2026, 1, 10, 5, 1, tzinfo=UTC)
    request = AnalysisRequest(
        paper_id=paper.id,
        paper_version_id=version.id,
        canonical_arxiv_id=record.canonical_arxiv_id,
        arxiv_version=record.version,
        title=record.title,
        scope=AnalysisScope.ABSTRACT_ONLY,
        passages=(AnalysisPassage(id="abstract", section="Abstract", text=record.abstract),),
    )
    generated = GeneratedAnalysis(
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-04-24",
        prompt_version="m2-analysis-v1",
        generated_at=generated_at,
        claims=(
            GeneratedClaim(
                key="method_1",
                claim_type=ClaimType.METHOD,
                text="The paper evaluates a tool-using language model agent.",
            ),
        ),
        evidence=(
            GeneratedEvidence(
                key="evidence_1",
                claim_keys=("method_1",),
                passage_ids=("abstract",),
                evidence_type=EvidenceType.SUPPORTS,
            ),
        ),
        usage=ModelUsage(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            call_count=1,
            duration_ms=800,
            estimated_cost_usd=None,
        ),
    )
    bundle = build_analysis_bundle(request, generated, created_at=generated_at)
    return AnalysisDetail(
        analysis=bundle.analysis,
        arxiv_version=record.version,
        claims=bundle.claims,
        evidence=bundle.evidence,
    )


def _fixture_id(name: str) -> UUID:
    return uuid5(UUID(int=0), name)


def _m3_read_fixture(
    paper: Paper,
    version: PaperVersion,
) -> tuple[RelatedWorkDetail, ComparisonDetail]:
    created_at = datetime(2026, 1, 10, 5, 2, tzinfo=UTC)
    completed_at = datetime(2026, 1, 10, 5, 3, tzinfo=UTC)
    session_id = _fixture_id("search-session")
    action_id = _fixture_id("search-action")
    candidate_id = _fixture_id("candidate")
    external_paper_id = _fixture_id("external-paper")
    target_paper_id = _fixture_id("target-paper")
    target_version_id = _fixture_id("target-version")
    comparison_id = _fixture_id("comparison")
    source_evidence_id = _fixture_id("source-evidence")
    target_evidence_id = _fixture_id("target-evidence")
    session = SearchSession(
        id=session_id,
        topic_id=_fixture_id("topic"),
        source_paper_id=paper.id,
        source_paper_version_id=version.id,
        source_analysis_id=_fixture_id("source-analysis"),
        source_analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        requested_year_from=2025,
        effective_year_to=2026,
        objective="Find historical work that materially overlaps this paper.",
        status=SearchSessionStatus.COMPLETE,
        limits=SearchLimits(max_steps=8, max_queries=3, max_candidates=20),
        started_at=created_at,
        completed_at=completed_at,
        stop_reason=SearchStopReason.QUEUE_EXHAUSTED,
        error_code=None,
        error_detail=None,
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-04-24",
        prompt_version="m3-selector-v1",
        usage=ModelUsage(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            call_count=1,
            duration_ms=400,
            estimated_cost_usd=None,
        ),
        schema_version=1,
        created_at=created_at,
        crawler_queries=("reliable LLM agent evaluation",),
        crawler_use_recommendations=True,
        crawler_expand_references=True,
        crawler_expand_citations=False,
        crawler_decision_reason="Use a bounded query and reference expansion.",
        crawler_generated_at=created_at,
    )
    action = SearchAction(
        id=action_id,
        session_id=session_id,
        step=1,
        tool=SearchTool.SEARCH_PAPERS,
        status=SearchActionStatus.COMPLETED,
        query="reliable LLM agent evaluation",
        target_semantic_scholar_id=None,
        target_arxiv_id=None,
        positive_paper_ids=(),
        year_from=2025,
        year_to=2026,
        requested_limit=10,
        result_count=1,
        relation_depth=0,
        decision_reason="Initial bounded Semantic Scholar query.",
        error_code=None,
        retryable=None,
        error_detail=None,
        duration_ms=120,
        created_at=created_at,
        completed_at=completed_at,
    )
    scores = CandidateScoreComponents(
        semantic_scholar=0.8,
        lexical=0.7,
        vector=0.9,
        entity_overlap=0.6,
        citation=0.4,
        recommendation=0.2,
        final=0.78,
    )
    candidate = SearchCandidate(
        id=candidate_id,
        session_id=session_id,
        external_paper_id=external_paper_id,
        semantic_scholar_id="b" * 40,
        local_paper_id=target_paper_id,
        local_paper_version_id=target_version_id,
        discovered_by_action_id=action_id,
        origins=(CandidateOrigin.SEARCH, CandidateOrigin.LOCAL_VECTOR),
        relation_depth=0,
        scores=scores,
        rank=1,
        decision=SelectionDecision.SELECTED,
        decision_reason="High semantic overlap and matching evaluation task.",
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-04-24",
        prompt_version="m3-selector-v1",
        generated_at=completed_at,
        verification_status=VerificationStatus.UNVERIFIED,
        schema_version=1,
        created_at=created_at,
    )
    discovery = SearchCandidateDiscovery(
        id=_fixture_id("discovery"),
        candidate_id=candidate_id,
        action_id=action_id,
        origin=CandidateOrigin.SEARCH,
        relation_depth=0,
        discovered_at=created_at,
    )
    external_paper = ExternalPaperStub(
        id=external_paper_id,
        semantic_scholar_id="b" * 40,
        title="Historical Evaluation of Tool-Using Agents",
        abstract="A historical benchmark for tool-using language model agents.",
        year=2025,
        publication_date=date(2025, 9, 1),
        venue="AgentBench Workshop",
        authors=("Ada Researcher", "Grace Scientist"),
        external_ids=(("ArXiv", "2509.00001"), ("DOI", "10.1000/agent.1")),
        arxiv_id="2509.00001",
        doi="10.1000/agent.1",
        citation_count=12,
        influential_citation_count=3,
        full_text_available=True,
        source="semantic_scholar",
        schema_version=1,
        created_at=created_at,
        updated_at=completed_at,
    )
    dimensions = tuple(
        ComparisonDimension(
            id=_fixture_id(f"dimension-{name.value}"),
            comparison_id=comparison_id,
            name=name,
            position=position,
            source_value=f"Source {name.value.lower().replace('_', ' ')}",
            target_value=f"Target {name.value.lower().replace('_', ' ')}",
            assessment=f"Evidence-backed assessment for {name.value.lower()}.",
            source_evidence_ids=(source_evidence_id,),
            target_evidence_ids=(target_evidence_id,),
            schema_version=1,
            created_at=completed_at,
        )
        for position, name in enumerate(COMPARISON_DIMENSION_ORDER)
    )
    comparison = Comparison(
        id=comparison_id,
        search_session_id=session_id,
        source_paper_id=paper.id,
        source_paper_version_id=version.id,
        source_analysis_id=_fixture_id("source-analysis"),
        source_analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        target_paper_id=target_paper_id,
        target_paper_version_id=target_version_id,
        target_analysis_id=_fixture_id("target-analysis"),
        target_analysis_scope=AnalysisScope.FULL_TEXT,
        comparability_status=ComparabilityStatus.DIRECTLY_COMPARABLE,
        comparability_reason="Both papers report the same benchmark and metric.",
        summary="The papers are directly comparable within the recorded benchmark scope.",
        dimensions=dimensions,
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-04-24",
        prompt_version="m3-comparison-v1",
        generated_at=completed_at,
        source="deepseek_structured_comparison",
        verification_status=VerificationStatus.UNVERIFIED,
        usage=ModelUsage(
            prompt_tokens=500,
            completion_tokens=200,
            total_tokens=700,
            call_count=1,
            duration_ms=950,
            estimated_cost_usd=None,
        ),
        schema_version=1,
        created_at=completed_at,
    )
    relation = PaperRelation(
        id=_fixture_id("relation"),
        source_paper_id=paper.id,
        source_paper_version_id=version.id,
        target_paper_id=target_paper_id,
        target_paper_version_id=target_version_id,
        relation_type=PaperRelationType.EXTENDS,
        provenance=RelationProvenance.LLM_INFERRED,
        evidence_ids=(source_evidence_id, target_evidence_id),
        justification="The newer method extends the historical evaluation protocol.",
        provider="deepseek",
        model_version="DeepSeek-V4-Flash-2026-04-24",
        prompt_version="m3-comparison-v1",
        confidence=0.72,
        verification_status=VerificationStatus.UNVERIFIED,
        generated_at=completed_at,
        schema_version=1,
        created_at=completed_at,
    )
    bundle = ComparisonBundle(comparison=comparison, relations=(relation,))
    detail = ComparisonDetail(
        comparison=comparison,
        relations=(relation,),
        evidence=(
            ComparisonEvidenceReference(
                id=source_evidence_id,
                analysis_id=_fixture_id("source-analysis"),
                paper_id=paper.id,
                paper_version_id=version.id,
                analysis_scope=AnalysisScope.ABSTRACT_ONLY,
                section="Abstract",
                excerpt="The source evaluates tool-using agents on the shared benchmark.",
                evidence_type=EvidenceType.SUPPORTS,
                verification_status=VerificationStatus.UNVERIFIED,
            ),
            ComparisonEvidenceReference(
                id=target_evidence_id,
                analysis_id=_fixture_id("target-analysis"),
                paper_id=target_paper_id,
                paper_version_id=target_version_id,
                analysis_scope=AnalysisScope.FULL_TEXT,
                section="Results",
                excerpt="The target reports the same benchmark and metric.",
                evidence_type=EvidenceType.QUALIFIES,
                verification_status=VerificationStatus.HUMAN_VERIFIED,
            ),
        ),
    )
    related = RelatedWorkDetail(
        session=session,
        actions=(action,),
        items=(
            RelatedWorkItem(
                candidate=candidate,
                external_paper=external_paper,
                discoveries=(discovery,),
                relations=(relation,),
                comparison_id=comparison_id,
            ),
        ),
        comparisons=(bundle,),
    )
    return related, detail


def _m4_read_fixture(
    paper: Paper,
    version: PaperVersion,
    analysis_detail: AnalysisDetail,
) -> tuple[
    GraphView,
    tuple[TrendDetail, ...],
    LineageDetail,
    ProductRunDetail,
    tuple[ReportDetail, ...],
]:
    generated_at = datetime(2026, 1, 10, 6, tzinfo=UTC)
    topic_id = _fixture_id("topic")
    _, comparison_detail = _m3_read_fixture(paper, version)
    analysis_graph = extract_analysis_graph(
        topic_id,
        AnalysisBundle(
            analysis=analysis_detail.analysis,
            claims=analysis_detail.claims,
            evidence=analysis_detail.evidence,
        ),
        paper_title=paper.title,
    )
    comparison_graph = extract_comparison_graph(
        topic_id,
        ComparisonBundle(
            comparison=comparison_detail.comparison,
            relations=comparison_detail.relations,
        ),
        source_paper_title=paper.title,
        target_paper_title="Historical Evaluation of Tool-Using Agents",
    )
    graph = merge_knowledge_graph_bundles((analysis_graph.bundle, comparison_graph.bundle))
    graph_evidence_by_id = {
        item.id: item for item in (*analysis_detail.evidence, *comparison_detail.evidence)
    }

    def edge_detail(edge: GraphEdge) -> GraphEdgeDetail:
        return GraphEdgeDetail(
            edge=edge,
            evidence=tuple(
                GraphEdgeEvidenceReference(
                    edge_id=edge.id,
                    evidence_id=evidence_id,
                    paper_id=graph_evidence_by_id[evidence_id].paper_id,
                    paper_version_id=graph_evidence_by_id[evidence_id].paper_version_id,
                    role=(
                        GraphEvidenceRole.SOURCE
                        if graph_evidence_by_id[evidence_id].paper_version_id
                        == edge.source_paper_version_id
                        else GraphEvidenceRole.TARGET
                        if graph_evidence_by_id[evidence_id].paper_version_id
                        == edge.target_paper_version_id
                        else GraphEvidenceRole.RELATION
                    ),
                )
                for evidence_id in edge.evidence_ids
            ),
        )

    graph_view = GraphView(
        topic_id=topic_id,
        as_of=date(2026, 1, 10),
        nodes=tuple(
            GraphNodeDetail(
                entity=entity,
                mentions=tuple(item for item in graph.mentions if item.entity_id == entity.id),
                total_mentions=sum(item.entity_id == entity.id for item in graph.mentions),
            )
            for entity in graph.entities
        ),
        edges=tuple(edge_detail(edge) for edge in graph.edges),
        total_nodes=len(graph.entities),
        total_edges=len(graph.edges),
        total_mentions=len(graph.mentions),
        truncated=False,
    )
    target_paper_id = comparison_detail.comparison.target_paper_id
    target_version_id = comparison_detail.comparison.target_paper_version_id
    trend_papers = (
        TrendPaperRecord(
            paper_id=target_paper_id,
            paper_version_id=target_version_id,
            activity_date=date(2025, 9, 1),
            title="Historical Evaluation of Tool-Using Agents",
        ),
        TrendPaperRecord(
            paper_id=paper.id,
            paper_version_id=version.id,
            activity_date=date(2026, 1, 10),
            title=paper.title,
        ),
    )
    snapshots = aggregate_trend_snapshots(
        topic_id,
        as_of_date=date(2026, 1, 10),
        papers=trend_papers,
        entities=graph.entities,
        mentions=graph.mentions,
        edges=graph.edges,
        mention_activity_dates={
            item.id: next(
                paper.activity_date
                for paper in trend_papers
                if paper.paper_version_id == item.paper_version_id
            )
            for item in graph.mentions
        },
        edge_activity_dates={
            item.id: next(
                paper.activity_date
                for paper in trend_papers
                if paper.paper_version_id == item.source_paper_version_id
            )
            for item in graph.edges
        },
        generated_at=generated_at,
    )
    trend_details = tuple(
        TrendDetail(
            snapshot=snapshot,
            representative_papers=tuple(
                item for item in trend_papers if item.paper_id in snapshot.representative_paper_ids
            ),
            total_entities=len(snapshot.entity_counts),
            truncated=False,
        )
        for snapshot in snapshots
    )
    paper_entities = {
        entity.paper_id: entity
        for entity in graph.entities
        if entity.entity_type is GraphEntityType.PAPER
    }
    lineage = build_lineage_snapshot(
        topic_id,
        paper.id,
        as_of_date=date(2026, 1, 10),
        papers=(
            LineagePaper(
                graph_entity_id=paper_entities[target_paper_id].id,
                paper_id=target_paper_id,
                title="Historical Evaluation of Tool-Using Agents",
                publication_date=date(2025, 9, 1),
            ),
            LineagePaper(
                graph_entity_id=paper_entities[paper.id].id,
                paper_id=paper.id,
                title=paper.title,
                publication_date=date(2026, 1, 10),
            ),
        ),
        edges=graph.edges,
        generated_at=generated_at,
        max_depth=5,
        max_nodes=100,
        max_edges=100,
    )
    lineage_detail = LineageDetail(
        snapshot=lineage,
        evidence=tuple(
            reference for edge in lineage.edges for reference in edge_detail(edge).evidence
        ),
    )
    product_run_id = _fixture_id("product-run")
    product_run = DailyRun(
        id=product_run_id,
        topic_id=topic_id,
        source_run_id=_fixture_id("analysis-run"),
        logical_date=date(2026, 1, 10),
        operation=RunOperation.PRODUCT_PUBLICATION,
        analysis_scope=None,
        status=RunStatus.COMPLETE,
        started_at=generated_at,
        completed_at=generated_at,
        cursor_from=None,
        cursor_to=None,
        discovered_count=0,
        normalized_count=0,
        selected_count=1,
        completed_count=1,
        failed_count=0,
        error_code=None,
        error_detail=None,
        schema_version=1,
        created_at=generated_at,
    )
    run_item = RunItem(
        id=_fixture_id("product-run-item"),
        run_id=product_run_id,
        paper_id=paper.id,
        paper_version_id=version.id,
        stage=PaperStage.PUBLISHED,
        status=RunItemStatus.COMPLETED,
        failed_stage=None,
        error_code=None,
        retryable=None,
        error_detail=None,
        schema_version=1,
        created_at=generated_at,
        updated_at=generated_at,
    )
    evidence = (
        ReportEvidenceReference(
            id=analysis_detail.evidence[0].id,
            paper_id=paper.id,
            paper_version_id=version.id,
            section=analysis_detail.evidence[0].section,
            excerpt=analysis_detail.evidence[0].excerpt,
            evidence_type=analysis_detail.evidence[0].evidence_type.value,
            verification_status=analysis_detail.evidence[0].verification_status,
        ),
        *(
            ReportEvidenceReference(
                id=item.id,
                paper_id=item.paper_id,
                paper_version_id=item.paper_version_id,
                section=item.section,
                excerpt=item.excerpt,
                evidence_type=item.evidence_type.value,
                verification_status=item.verification_status,
            )
            for item in comparison_detail.evidence
        ),
    )

    def report_detail(
        report_type: ReportType,
        *,
        period_start: date,
        period_end: date,
        run_id: UUID | None,
    ) -> ReportDetail:
        report_id = _fixture_id(f"{report_type.value}-report")
        sections = tuple(
            ReportSection(
                id=uuid5(report_id, kind.value),
                report_id=report_id,
                kind=kind,
                narrative=f"Persisted {kind.value.lower()} section.",
                evidence_ids=(
                    (analysis_detail.evidence[0].id,)
                    if kind is ReportSectionKind.OVERVIEW
                    else (
                        tuple(item.id for item in comparison_detail.evidence)
                        if kind is ReportSectionKind.COMPARISONS
                        else ()
                    )
                ),
                schema_version=1,
                created_at=generated_at,
            )
            for kind in ReportSectionKind
        )
        method_entity = next(
            entity for entity in graph.entities if entity.entity_type is GraphEntityType.METHOD
        )
        report = Report(
            id=report_id,
            run_id=run_id,
            topic_id=topic_id,
            logical_date=period_end,
            status=RunStatus.COMPLETE,
            title=f"{report_type.value.title()} agent research report",
            summary="Evidence-linked graph, trend, comparison, and lineage summary.",
            source="m4_structured_report",
            generated_at=generated_at,
            schema_version=1,
            created_at=generated_at,
            sections=sections,
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            counts=ReportCounts(2, 1, 1, 1, 0),
            highlighted_papers=(
                ReportPaperHighlight(
                    paper_id=paper.id,
                    paper_version_id=version.id,
                    title=paper.title,
                    reason="Selected for an evidence-linked method contribution.",
                    evidence_ids=(analysis_detail.evidence[0].id,),
                ),
            ),
            major_entities=(
                ReportEntityHighlight(
                    graph_entity_id=method_entity.id,
                    entity_type=method_entity.entity_type.value,
                    label=method_entity.display_label,
                    distinct_paper_count=1,
                ),
            ),
            notable_comparisons=(
                ReportComparisonHighlight(
                    comparison_id=comparison_detail.comparison.id,
                    source_paper_id=comparison_detail.comparison.source_paper_id,
                    source_paper_version_id=(comparison_detail.comparison.source_paper_version_id),
                    target_paper_id=comparison_detail.comparison.target_paper_id,
                    target_paper_version_id=(comparison_detail.comparison.target_paper_version_id),
                    summary=comparison_detail.comparison.summary,
                    comparability_status=comparison_detail.comparison.comparability_status.value,
                    evidence_ids=tuple(item.id for item in comparison_detail.evidence),
                ),
            ),
            graph_changes=ReportGraphChanges(
                entity_count=len(graph.entities),
                edge_count=len(graph.edges),
                new_entity_count=len(graph.entities),
                inferred_edge_count=sum(
                    item.provenance is RelationProvenance.LLM_INFERRED for item in graph.edges
                ),
            ),
            trend_snapshot_ids=tuple(item.id for item in snapshots),
            lineage_highlights=(
                ReportLineageHighlight(
                    lineage_snapshot_id=lineage.id,
                    root_paper_id=paper.id,
                    summary="Lineage is scoped to the currently retrieved corpus.",
                    uncertain=True,
                ),
            ),
            evidence_ids=tuple(item.id for item in evidence),
            limitations=("Currently retrieved corpus only.",),
            missing_sections=(),
            narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
            verification_status=VerificationStatus.UNVERIFIED,
        )
        return ReportDetail(report=report, evidence=evidence)

    reports = (
        report_detail(
            ReportType.DAILY,
            period_start=date(2026, 1, 10),
            period_end=date(2026, 1, 10),
            run_id=product_run_id,
        ),
        report_detail(
            ReportType.WEEKLY,
            period_start=date(2026, 1, 5),
            period_end=date(2026, 1, 11),
            run_id=None,
        ),
        report_detail(
            ReportType.MONTHLY,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            run_id=None,
        ),
    )
    product_detail = ProductRunDetail(
        run=product_run,
        items=(
            RunItemDetail(
                item=run_item,
                canonical_arxiv_id=paper.canonical_arxiv_id,
                paper_title=paper.title,
            ),
        ),
        report=reports[0],
    )
    return graph_view, trend_details, lineage_detail, product_detail, reports


def test_m1_read_api_exposes_persisted_topics_papers_and_latest_run(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    repository = FakeRepository()
    repository.papers = (_paper(arxiv_record_v1),)
    IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)), repository=repository, clock=lambda: now
    ).execute(topic_config, logical_date=date(2026, 1, 10))
    app = create_app(repository)
    client = TestClient(app)

    assert client.get("/health/live").json() == {"status": "alive"}
    assert client.get("/health/ready").json() == {
        "status": "ready",
        "database": "ready",
        "migrations": "current",
    }
    topics = client.get("/api/v1/topics").json()
    assert topics["total"] == 1
    assert topics["items"][0]["slug"] == "broad-llm-agents"
    papers = client.get("/api/v1/papers?limit=20&offset=0").json()
    assert papers["total"] == 1
    assert papers["items"][0]["canonical_arxiv_id"] == "2601.01234"
    run = client.get("/api/v1/runs/latest").json()
    assert run["status"] == "COMPLETE"
    assert run["analysis_scope"] is None
    assert run["items"][0]["stage"] == "NORMALIZED"
    assert client.get(f"/api/v1/runs/{run['id']}").json()["id"] == run["id"]


def test_run_reads_expose_complete_parent_execution_after_empty_selection(
    topic_config: TopicConfig,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    execution_id = stable_pipeline_execution_id(
        topic_config.id,
        now.date(),
    )
    repository = FakeRepository()
    repository.upsert_topic(topic_config)
    repository.start_pipeline_execution(
        PipelineExecution(
            id=execution_id,
            topic_id=topic_config.id,
            logical_date=now.date(),
            execution_mode=PipelineExecutionMode.NORMAL,
            analysis_scope=AnalysisScope.FULL_TEXT,
            selection_limit=1,
            contract=fake_pipeline_execution_contract(),
            status=RunStatus.RUNNING,
            deadline_at=now + timedelta(hours=8),
            started_at=now,
            completed_at=None,
            error_code=None,
            error_detail=None,
            schema_version=1,
            created_at=now,
        )
    )
    ingestion = IngestArxiv(
        arxiv=FakeArxiv(),
        repository=repository,
        clock=lambda: now,
    ).execute(
        topic_config,
        logical_date=now.date(),
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=1,
        pipeline_execution_id=execution_id,
    )
    repository.complete_pipeline_execution(
        execution_id,
        status=RunStatus.COMPLETE,
        completed_at=now + timedelta(minutes=1),
    )

    client = TestClient(create_app(repository))
    listed = client.get("/api/v1/runs").json()["items"][0]
    latest = client.get("/api/v1/runs/latest").json()
    detail = client.get(f"/api/v1/runs/{ingestion.id}").json()

    for response in (listed, latest, detail):
        assert response["status"] == "COMPLETE"
        assert response["pipeline_status"] == "COMPLETE"
        assert response["pipeline_error_code"] is None
        assert response["pipeline_error_detail"] is None
        assert response["pipeline_deadline_at"] is not None


def test_readiness_reports_incompatible_migration() -> None:
    repository = FakeRepository()
    repository.ready_error = MigrationIncompatibleError("database revision is behind")
    response = TestClient(create_app(repository)).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MIGRATION_INCOMPATIBLE"


def test_m2_analysis_and_evidence_contracts_expose_scope_provenance_and_links(
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    repository = FakeRepository()
    paper = _paper(arxiv_record_v1)
    version = _paper_version(arxiv_record_v1, paper)
    repository.paper_detail = PaperDetail(
        paper=paper,
        versions=(version,),
        source_identities=(),
        topic_slugs=("broad-llm-agents",),
    )
    repository.analysis_detail = _analysis_detail(arxiv_record_v1, paper)
    app = create_app(repository)
    client = TestClient(app)

    analysis_response = client.get(
        f"/api/v1/papers/{paper.id}/analysis?paper_version_id={version.id}"
    )
    assert analysis_response.status_code == 200
    analysis = analysis_response.json()
    assert analysis["paper_version_id"] == str(version.id)
    assert analysis["arxiv_version"] == 1
    assert analysis["analysis_scope"] == "ABSTRACT_ONLY"
    assert analysis["parsed_paper_id"] is None
    assert analysis["parser_name"] is None
    assert analysis["parser_version"] is None
    assert analysis["provider"] == "deepseek"
    assert analysis["configured_model"] == "deepseek-v4-flash"
    assert analysis["prompt_version"] == "m2-analysis-v1"
    assert analysis["verification_status"] == "UNVERIFIED"
    assert analysis["claims"][0]["claim_type"] == "METHOD"

    evidence_response = client.get(
        f"/api/v1/papers/{paper.id}/evidence?analysis_id={analysis['id']}"
        f"&paper_version_id={version.id}&scope=ABSTRACT_ONLY"
    )
    assert evidence_response.status_code == 200
    evidence = evidence_response.json()
    assert evidence["total"] == 1
    assert evidence["items"][0]["section"] == "Abstract"
    assert evidence["items"][0]["extraction_source"] == "arxiv_abstract"
    assert evidence["items"][0]["supported_claim_ids"] == [analysis["claims"][0]["id"]]
    mismatched_scope = client.get(
        f"/api/v1/papers/{paper.id}/evidence?analysis_id={analysis['id']}&scope=FULL_TEXT"
    )
    assert mismatched_scope.status_code == 404
    assert mismatched_scope.json()["detail"]["code"] == "ANALYSIS_NOT_FOUND"

    openapi = app.openapi()
    for path in (
        "/api/v1/papers/{paper_id}/analysis",
        "/api/v1/papers/{paper_id}/evidence",
    ):
        responses = openapi["paths"][path]["get"]["responses"]
        for status_code in ("404", "503"):
            assert responses[status_code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ApiErrorResponse"
            }


def test_analysis_404_and_empty_evidence_are_distinct_for_an_existing_paper(
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    repository = FakeRepository()
    paper = _paper(arxiv_record_v1)
    repository.paper_detail = PaperDetail(
        paper=paper,
        versions=(_paper_version(arxiv_record_v1, paper),),
        source_identities=(),
        topic_slugs=(),
    )
    client = TestClient(create_app(repository))

    missing_analysis = client.get(f"/api/v1/papers/{paper.id}/analysis")
    assert missing_analysis.status_code == 404
    assert missing_analysis.json()["detail"]["code"] == "ANALYSIS_NOT_FOUND"
    missing_evidence = client.get(
        f"/api/v1/papers/{paper.id}/evidence?analysis_id=00000000-0000-0000-0000-000000000000"
    )
    assert missing_evidence.status_code == 404
    assert missing_evidence.json()["detail"]["code"] == "ANALYSIS_NOT_FOUND"


def test_read_api_starts_without_deepseek_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert TestClient(create_app(FakeRepository())).get("/health/live").status_code == 200


def test_unknown_paper_analysis_returns_paper_not_found() -> None:
    paper_id = UUID("e54e4c7c-e0b1-4c0b-a416-67a63b949b67")
    response = TestClient(create_app(FakeRepository())).get(f"/api/v1/papers/{paper_id}/analysis")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PAPER_NOT_FOUND"


def test_m3_related_work_and_comparison_contracts_expose_bounded_provenance(
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    repository = FakeRepository()
    paper = _paper(arxiv_record_v1)
    version = _paper_version(arxiv_record_v1, paper)
    repository.paper_detail = PaperDetail(
        paper=paper,
        versions=(version,),
        source_identities=(),
        topic_slugs=("broad-llm-agents",),
    )
    related, comparison = _m3_read_fixture(paper, version)
    repository.related_work = related
    repository.canonical_search_session_ids = frozenset((related.session.id,))
    repository.comparisons[comparison.comparison.id] = comparison
    repository.canonical_comparison_ids = frozenset((comparison.comparison.id,))
    client = TestClient(create_app(repository))

    related_response = client.get(f"/api/v1/papers/{paper.id}/related")
    assert related_response.status_code == 200
    related_body = related_response.json()
    assert related_body["paper_id"] == str(paper.id)
    assert related_body["session"]["stop_reason"] == "QUEUE_EXHAUSTED"
    assert related_body["session"]["limits"]["max_steps"] == 8
    assert related_body["session"]["crawler_queries"] == ["reliable LLM agent evaluation"]
    assert related_body["session"]["crawler_use_recommendations"] is True
    assert related_body["session"]["crawler_expand_references"] is True
    assert related_body["session"]["crawler_expand_citations"] is False
    assert (
        related_body["session"]["crawler_decision_reason"]
        == "Use a bounded query and reference expansion."
    )
    assert related_body["actions"][0]["tool"] == "search_papers"
    assert related_body["items"][0]["candidate"]["decision"] == "SELECTED"
    assert related_body["items"][0]["candidate"]["scores"] == {
        "semantic_scholar": 0.8,
        "lexical": 0.7,
        "vector": 0.9,
        "entity_overlap": 0.6,
        "citation": 0.4,
        "recommendation": 0.2,
        "final": 0.78,
    }
    assert related_body["items"][0]["paper"]["external_ids"] == {
        "ArXiv": "2509.00001",
        "DOI": "10.1000/agent.1",
    }
    assert related_body["items"][0]["comparison_id"] == str(comparison.comparison.id)
    assert related_body["comparisons"][0]["comparability_status"] == "DIRECTLY_COMPARABLE"
    assert related_body["session"]["source_analysis_id"] == str(_fixture_id("source-analysis"))
    assert related_body["session"]["requested_year_from"] == 2025
    assert related_body["session"]["effective_year_to"] == 2026

    comparison_response = client.get(f"/api/v1/comparisons/{comparison.comparison.id}")
    assert comparison_response.status_code == 200
    comparison_body = comparison_response.json()
    assert comparison_body["comparability_status"] == "DIRECTLY_COMPARABLE"
    assert comparison_body["source_analysis_id"] == str(_fixture_id("source-analysis"))
    assert comparison_body["target_analysis_id"] == str(_fixture_id("target-analysis"))
    dimensions = cast(list[dict[str, object]], comparison_body["dimensions"])
    assert len(dimensions) == len(COMPARISON_DIMENSION_ORDER)
    assert dimensions[0]["name"] == "RESEARCH_PROBLEM"
    assert dimensions[0]["source_evidence_ids"]
    assert dimensions[-1]["name"] == "RESULT_COMPARABILITY"
    assert comparison_body["relations"][0]["provenance"] == "LLM_INFERRED"
    assert comparison_body["relations"][0]["confidence"] == 0.72
    assert comparison_body["verification_status"] == "UNVERIFIED"
    assert comparison_body["prompt_version"] == "m3-comparison-v1"
    evidence = cast(list[dict[str, object]], comparison_body["evidence"])
    assert len(evidence) == 2
    evidence_by_id = {item["id"]: item for item in evidence}
    source_evidence_id = cast(list[str], dimensions[0]["source_evidence_ids"])[0]
    target_evidence_id = cast(list[str], dimensions[0]["target_evidence_ids"])[0]
    assert evidence_by_id[source_evidence_id] == {
        "id": source_evidence_id,
        "analysis_id": str(_fixture_id("source-analysis")),
        "paper_id": str(paper.id),
        "paper_version_id": str(version.id),
        "analysis_scope": "ABSTRACT_ONLY",
        "section": "Abstract",
        "excerpt": "The source evaluates tool-using agents on the shared benchmark.",
        "evidence_type": "SUPPORTS",
        "verification_status": "UNVERIFIED",
    }
    assert evidence_by_id[target_evidence_id]["analysis_scope"] == "FULL_TEXT"
    assert evidence_by_id[target_evidence_id]["section"] == "Results"
    assert evidence_by_id[target_evidence_id]["evidence_type"] == "QUALIFIES"
    assert evidence_by_id[target_evidence_id]["verification_status"] == "HUMAN_VERIFIED"


def test_smoke_only_products_stay_hidden_until_a_normal_publication(
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    repository = FakeRepository()
    repository.enforce_published_visibility = True
    paper = _paper(arxiv_record_v1)
    version = _paper_version(arxiv_record_v1, paper)
    analysis = _analysis_detail(arxiv_record_v1, paper)
    related, comparison = _m3_read_fixture(paper, version)
    repository.papers = (paper,)
    repository.paper_detail = PaperDetail(
        paper=paper,
        versions=(version,),
        source_identities=(),
        topic_slugs=("broad-llm-agents",),
    )
    repository.analysis_detail = analysis
    repository.related_work = related
    repository.comparisons[comparison.comparison.id] = comparison
    client = TestClient(create_app(repository))
    evidence_path = f"/api/v1/papers/{paper.id}/evidence?analysis_id={analysis.analysis.id}"

    assert client.get("/api/v1/papers").json()["total"] == 0
    assert client.get(f"/api/v1/papers/{paper.id}").status_code == 404
    assert client.get(f"/api/v1/papers/{paper.id}/analysis").status_code == 404
    assert client.get(evidence_path).status_code == 404
    assert client.get(f"/api/v1/papers/{paper.id}/related").status_code == 404
    assert client.get(f"/api/v1/comparisons/{comparison.comparison.id}").status_code == 404

    repository.canonically_published_version_ids = frozenset((version.id,))

    assert client.get("/api/v1/papers").json()["total"] == 1
    assert client.get(f"/api/v1/papers/{paper.id}").status_code == 200
    assert client.get(f"/api/v1/papers/{paper.id}/analysis").status_code == 200
    assert client.get(evidence_path).status_code == 200
    assert client.get(f"/api/v1/papers/{paper.id}/related").json()["session"] is None
    assert client.get(f"/api/v1/comparisons/{comparison.comparison.id}").status_code == 404
    assert (
        repository.get_related_work(
            paper.id,
            paper_version_id=version.id,
            search_session_id=related.session.id,
        )
        is related
    )

    repository.canonical_search_session_ids = frozenset((related.session.id,))
    repository.canonical_comparison_ids = frozenset((comparison.comparison.id,))

    assert client.get(f"/api/v1/papers/{paper.id}/related").json()["session"] is not None
    assert client.get(f"/api/v1/comparisons/{comparison.comparison.id}").status_code == 200


def test_canonical_paper_reads_keep_published_v1_when_smoke_ingests_v2(
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    smoke_v2 = replace(
        arxiv_record_v1,
        version=2,
        title="A Reliable LLM Agent, Smoke-Only Revision",
        updated_at=arxiv_record_v1.updated_at + timedelta(days=1),
        pdf_url="https://arxiv.org/pdf/2601.01234v2",
        source_url="https://arxiv.org/abs/2601.01234v2",
    )
    paper = _paper(smoke_v2)
    published_v1 = _paper_version(arxiv_record_v1, paper)
    smoke_only_v2 = _paper_version(smoke_v2, paper)
    analysis_v1 = _analysis_detail(arxiv_record_v1, paper)
    repository = FakeRepository()
    repository.enforce_published_visibility = True
    repository.papers = (paper,)
    repository.paper_detail = PaperDetail(
        paper=paper,
        versions=(smoke_only_v2, published_v1),
        source_identities=(),
        topic_slugs=("broad-llm-agents",),
    )
    repository.analysis_detail = analysis_v1
    repository.canonically_published_version_ids = frozenset((published_v1.id,))
    client = TestClient(create_app(repository))

    list_body = client.get("/api/v1/papers").json()
    assert list_body["total"] == 1
    assert list_body["items"][0]["current_version"] == 1
    assert list_body["items"][0]["title"] == arxiv_record_v1.title

    detail_response = client.get(f"/api/v1/papers/{paper.id}")
    assert detail_response.status_code == 200
    detail_body = detail_response.json()
    assert detail_body["current_version"] == 1
    assert detail_body["title"] == arxiv_record_v1.title
    assert [version["version"] for version in detail_body["versions"]] == [1]

    analysis_response = client.get(f"/api/v1/papers/{paper.id}/analysis")
    assert analysis_response.status_code == 200
    assert analysis_response.json()["paper_version_id"] == str(published_v1.id)
    evidence_response = client.get(
        f"/api/v1/papers/{paper.id}/evidence?analysis_id={analysis_v1.analysis.id}"
    )
    assert evidence_response.status_code == 200
    assert evidence_response.json()["items"][0]["paper_version_id"] == str(published_v1.id)


def test_comparison_detail_requires_exact_unique_evidence_with_version_ownership(
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    paper = _paper(arxiv_record_v1)
    version = _paper_version(arxiv_record_v1, paper)
    _, detail = _m3_read_fixture(paper, version)

    with pytest.raises(DomainInvariantError, match="every referenced evidence"):
        ComparisonDetail(
            comparison=detail.comparison,
            relations=detail.relations,
            evidence=detail.evidence[:1],
        )
    with pytest.raises(DomainInvariantError, match="every referenced evidence"):
        ComparisonDetail(
            comparison=detail.comparison,
            relations=detail.relations,
            evidence=detail.evidence + (detail.evidence[0],),
        )
    with pytest.raises(DomainInvariantError, match="source comparison evidence"):
        ComparisonDetail(
            comparison=detail.comparison,
            relations=detail.relations,
            evidence=(
                replace(
                    detail.evidence[0],
                    paper_version_id=detail.comparison.target_paper_version_id,
                ),
                detail.evidence[1],
            ),
        )


def test_related_work_distinguishes_missing_paper_from_no_search_session(
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    repository = FakeRepository()
    paper = _paper(arxiv_record_v1)
    repository.paper_detail = PaperDetail(
        paper=paper,
        versions=(_paper_version(arxiv_record_v1, paper),),
        source_identities=(),
        topic_slugs=(),
    )
    client = TestClient(create_app(repository))

    empty = client.get(f"/api/v1/papers/{paper.id}/related")
    assert empty.status_code == 200
    assert empty.json() == {
        "paper_id": str(paper.id),
        "related_work_status": "RELATED_WORK_UNAVAILABLE",
        "related_work_reason": "NO_RELATED_WORK_RESULT",
        "session": None,
        "actions": [],
        "items": [],
        "comparisons": [],
        "total": 0,
    }
    wrong_version = client.get(
        f"/api/v1/papers/{paper.id}/related",
        params={"paper_version_id": str(_fixture_id("unowned-paper-version"))},
    )
    assert wrong_version.status_code == 404
    assert wrong_version.json()["detail"]["code"] == "PAPER_VERSION_NOT_FOUND"

    repository.paper_detail = None
    missing = client.get(f"/api/v1/papers/{paper.id}/related")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "PAPER_NOT_FOUND"


def test_comparison_404_and_m3_read_503_are_explicit(
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    repository = FakeRepository()
    missing_id = _fixture_id("missing-comparison")
    client = TestClient(create_app(repository))
    missing = client.get(f"/api/v1/comparisons/{missing_id}")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "COMPARISON_NOT_FOUND"

    class UnavailableComparisonRepository(FakeRepository):
        def get_comparison(
            self,
            comparison_id: UUID,
            *,
            canonical_only: bool = False,
        ) -> ComparisonDetail | None:
            del comparison_id, canonical_only
            raise RepositoryUnavailableError("comparison read unavailable")

    unavailable = TestClient(create_app(UnavailableComparisonRepository())).get(
        f"/api/v1/comparisons/{missing_id}"
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "DATABASE_UNAVAILABLE"

    class UnavailableRelatedRepository(FakeRepository):
        def get_related_work(
            self,
            paper_id: UUID,
            *,
            paper_version_id: UUID | None = None,
            search_session_id: UUID | None = None,
        ) -> RelatedWorkDetail | None:
            del paper_id, paper_version_id, search_session_id
            raise RepositoryUnavailableError("related-work read unavailable")

    paper = _paper(arxiv_record_v1)
    related_repository = UnavailableRelatedRepository()
    related_repository.paper_detail = PaperDetail(
        paper=paper,
        versions=(_paper_version(arxiv_record_v1, paper),),
        source_identities=(),
        topic_slugs=(),
    )
    related_unavailable = TestClient(create_app(related_repository)).get(
        f"/api/v1/papers/{paper.id}/related"
    )
    assert related_unavailable.status_code == 503
    assert related_unavailable.json()["detail"]["code"] == "DATABASE_UNAVAILABLE"

    openapi = create_app(repository).openapi()
    for path in (
        "/api/v1/papers/{paper_id}/related",
        "/api/v1/comparisons/{comparison_id}",
    ):
        responses = openapi["paths"][path]["get"]["responses"]
        for status_code in ("404", "503"):
            assert responses[status_code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ApiErrorResponse"
            }


def test_m3_read_api_starts_without_semantic_scholar_key(
    monkeypatch: pytest.MonkeyPatch,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    repository = FakeRepository()
    paper = _paper(arxiv_record_v1)
    repository.paper_detail = PaperDetail(
        paper=paper,
        versions=(_paper_version(arxiv_record_v1, paper),),
        source_identities=(),
        topic_slugs=(),
    )
    response = TestClient(create_app(repository)).get(f"/api/v1/papers/{paper.id}/related")
    assert response.status_code == 200
    assert response.json()["session"] is None


def test_m4_graph_trend_and_lineage_contracts_are_bounded_and_provenance_aware(
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    repository = FakeRepository()
    paper = _paper(arxiv_record_v1)
    version = _paper_version(arxiv_record_v1, paper)
    analysis = _analysis_detail(arxiv_record_v1, paper)
    graph, trends, lineage, _, _ = _m4_read_fixture(paper, version, analysis)
    repository.graph_view = graph
    repository.trends = trends
    paper_entity_id = next(
        item.entity.id
        for item in graph.nodes
        if item.entity.entity_type is GraphEntityType.PAPER and item.entity.paper_id == paper.id
    )
    repository.lineages[paper.id] = lineage
    repository.lineages[paper_entity_id] = lineage
    client = TestClient(create_app(repository))

    graph_response = client.get(
        "/api/v1/graph",
        params={
            "topic": "broad-llm-agents",
            "as_of": "2026-01-10",
            "paper_id": str(paper.id),
            "entity_id": str(paper_entity_id),
            "entity_type": "PAPER",
            "relation_type": "EXTENDS",
            "provenance": "LLM_INFERRED",
            "verification_status": "UNVERIFIED",
            "max_nodes": 50,
            "max_edges": 80,
        },
    )
    assert graph_response.status_code == 200
    graph_body = graph_response.json()
    assert graph_body["as_of"] == "2026-01-10"
    assert graph_body["total_nodes"] == len(graph.nodes)
    assert graph_body["total_mentions"] == sum(item.total_mentions for item in graph.nodes)
    assert all(node["mention_count"] == len(node["mentions"]) for node in graph_body["nodes"])
    inferred = next(edge for edge in graph_body["edges"] if edge["inferred"])
    assert inferred["provenance"] == "LLM_INFERRED"
    assert inferred["relation_type"] == "EXTENDS"
    assert inferred["confidence"] == 0.72
    assert "not a probability" in inferred["confidence_meaning"]
    assert inferred["evidence_ids"]
    assert {item["role"] for item in inferred["evidence"]} == {"SOURCE", "TARGET"}
    assert {item["paper_version_id"] for item in inferred["evidence"]} == {
        str(_m3_read_fixture(paper, version)[1].comparison.source_paper_version_id),
        str(_m3_read_fixture(paper, version)[1].comparison.target_paper_version_id),
    }
    assert inferred["model_provenance"]["prompt_version"] == "m3-comparison-v1"
    assert repository.graph_read == (
        "broad-llm-agents",
        date(2026, 1, 10),
        paper.id,
        paper_entity_id,
        GraphEntityType.PAPER,
        next(
            item.edge.relation_type
            for item in graph.edges
            if item.edge.relation_type.value == "EXTENDS"
        ),
        RelationProvenance.LLM_INFERRED,
        VerificationStatus.UNVERIFIED,
        50,
        80,
    )

    trend_response = client.get("/api/v1/trends")
    assert trend_response.status_code == 200
    trend_body = trend_response.json()
    assert [item["window"] for item in trend_body["items"]] == ["7D", "30D", "90D"]
    assert all(item["data_sufficiency"] == "INSUFFICIENT" for item in trend_body["items"])
    assert [item["paper_count_change"]["growth_status"] for item in trend_body["items"]] == [
        "ZERO_DENOMINATOR",
        "ZERO_DENOMINATOR",
        "LIMITED_SAMPLE",
    ]
    assert all(
        item["paper_count_change"]["relative_change"] is None for item in trend_body["items"]
    )
    assert all(
        item["total_entities"] == len(item["entity_counts"]) and not item["truncated"]
        for item in trend_body["items"]
    )
    assert trend_body["items"][0]["representative_papers"][0]["paper_id"] == str(paper.id)
    filtered_trends = client.get(
        "/api/v1/trends",
        params=[
            ("window", "7D"),
            ("window", "90D"),
            ("entity_type", "METHOD"),
            ("max_entities", "1"),
        ],
    ).json()
    assert [item["window"] for item in filtered_trends["items"]] == ["7D", "90D"]

    lineage_response = client.get(
        f"/api/v1/lineages/{paper.id}",
        params={"max_depth": 5, "max_nodes": 100, "max_edges": 100},
    )
    assert lineage_response.status_code == 200
    lineage_body = lineage_response.json()
    assert lineage_body["root_paper_id"] == str(paper.id)
    assert lineage_body["max_edges"] == 100
    assert lineage_body["corpus_scope"] == "CURRENTLY_RETRIEVED_CORPUS"
    assert lineage_body["nodes"][0]["publication_date"] == "2025-09-01"
    assert lineage_body["nodes"][-1]["depth"] == 0
    assert lineage_body["edges"][0]["inferred"] is True
    assert {item["role"] for item in lineage_body["edges"][0]["evidence"]} == {
        "SOURCE",
        "TARGET",
    }
    assert lineage_body["limitations"]

    invalid_requests = (
        "/api/v1/graph?max_nodes=501",
        "/api/v1/graph?max_edges=1001",
        "/api/v1/graph?entity_type=UNKNOWN",
        "/api/v1/trends?window=14D",
        "/api/v1/trends?max_entities=201",
        f"/api/v1/lineages/{paper.id}?max_depth=6",
        f"/api/v1/lineages/{paper.id}?max_nodes=101",
        f"/api/v1/lineages/{paper.id}?max_edges=401",
    )
    assert all(client.get(path).status_code == 422 for path in invalid_requests)


def test_m4_daily_and_historical_report_contracts_expose_complete_structure(
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    repository = FakeRepository()
    paper = _paper(arxiv_record_v1)
    version = _paper_version(arxiv_record_v1, paper)
    analysis = _analysis_detail(arxiv_record_v1, paper)
    _, _, _, product_run, reports = _m4_read_fixture(paper, version, analysis)
    repository.product_run = product_run
    repository.reports = reports
    client = TestClient(create_app(repository))

    latest = client.get("/api/v1/daily/latest")
    assert latest.status_code == 200
    latest_body = latest.json()
    assert latest_body["run"]["operation"] == "PRODUCT_PUBLICATION"
    assert latest_body["run"]["source_run_id"] == str(_fixture_id("analysis-run"))
    assert latest_body["items"][0]["stage"] == "PUBLISHED"
    report = latest_body["report"]
    assert report["report_type"] == "DAILY"
    assert [section["kind"] for section in report["sections"]] == [
        item.value for item in ReportSectionKind
    ]
    assert report["counts"] == {
        "retrieved": 2,
        "selected": 1,
        "processed": 1,
        "completed": 1,
        "failed": 0,
    }
    assert report["highlighted_papers"][0]["evidence_ids"]
    assert report["major_entities"][0]["entity_type"] == "METHOD"
    assert report["notable_comparisons"][0]["comparability_status"] == ("DIRECTLY_COMPARABLE")
    assert report["graph_changes"]["inferred_edge_count"] >= 1
    assert len(report["trend_snapshot_ids"]) == 3
    assert report["lineage_highlights"][0]["uncertain"] is True
    assert report["evidence"][0]["excerpt"]
    assert report["narrative_mode"] == "STRUCTURED_ONLY"
    assert report["provider"] is None
    assert report["verification_status"] == "UNVERIFIED"

    history = client.get("/api/v1/reports/daily?limit=1&offset=0")
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["id"] == report["id"]
    assert client.get("/api/v1/daily/2026-01-10").status_code == 200
    assert client.get("/api/v1/reports/daily/2026-01-10").status_code == 200
    weekly = client.get("/api/v1/reports/weekly/2026-W02")
    assert weekly.status_code == 200
    assert weekly.json()["period_start"] == "2026-01-05"
    assert weekly.json()["period_end"] == "2026-01-11"
    monthly = client.get("/api/v1/reports/monthly/2026-01")
    assert monthly.status_code == 200
    assert monthly.json()["period_end"] == "2026-01-31"
    assert client.get("/api/v1/reports/weekly/2026-W54").status_code == 422
    assert client.get("/api/v1/reports/monthly/2026-13").status_code == 422
    assert client.get("/api/v1/reports/monthly/not-a-month").status_code == 422


def test_failed_product_publication_run_is_visible_without_a_report(
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    repository = FakeRepository()
    paper = _paper(arxiv_record_v1)
    version = _paper_version(arxiv_record_v1, paper)
    analysis = _analysis_detail(arxiv_record_v1, paper)
    _, _, _, product_run, _ = _m4_read_fixture(paper, version, analysis)
    failed_item = replace(
        product_run.items[0].item,
        stage=PaperStage.REPORT_GENERATED,
        status=RunItemStatus.FAILED,
        failed_stage=PaperStage.PUBLISHED,
        error_code="REPOSITORY_UNAVAILABLE",
        retryable=False,
        error_detail="The publication transaction could not commit.",
    )
    repository.product_run = ProductRunDetail(
        run=replace(
            product_run.run,
            status=RunStatus.FAILED,
            completed_count=0,
            failed_count=1,
            error_code="PUBLICATION_TRANSACTION_FAILED",
            error_detail="The atomic publication transaction failed.",
        ),
        items=(replace(product_run.items[0], item=failed_item),),
        report=None,
    )

    response = TestClient(create_app(repository)).get("/api/v1/daily/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["status"] == "FAILED"
    assert body["run"]["error_code"] == "PUBLICATION_TRANSACTION_FAILED"
    assert body["items"][0]["error_code"] == "REPOSITORY_UNAVAILABLE"
    assert body["report"] is None


def test_m4_read_routes_document_explicit_errors_and_limits() -> None:
    app = create_app(FakeRepository())
    client = TestClient(app)
    missing_id = _fixture_id("missing-m4-read")
    missing_requests = {
        "/api/v1/graph": "GRAPH_NOT_FOUND",
        f"/api/v1/lineages/{missing_id}": "LINEAGE_NOT_FOUND",
        "/api/v1/daily/latest": "PRODUCT_RUN_NOT_FOUND",
        "/api/v1/daily/2026-01-10": "PRODUCT_RUN_NOT_FOUND",
        "/api/v1/reports/daily/2026-01-10": "REPORT_NOT_FOUND",
        "/api/v1/reports/weekly/2026-W02": "REPORT_NOT_FOUND",
        "/api/v1/reports/monthly/2026-01": "REPORT_NOT_FOUND",
        f"/api/v1/runs/{missing_id}": "RUN_NOT_FOUND",
    }
    for path, code in missing_requests.items():
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == code
    assert client.get("/api/v1/trends").json() == {"items": [], "total": 0}

    openapi = app.openapi()
    for path in (
        "/api/v1/graph",
        "/api/v1/lineages/{entity_or_paper_id}",
        "/api/v1/daily/latest",
        "/api/v1/daily/{logical_date}",
        "/api/v1/reports/daily/{logical_date}",
        "/api/v1/reports/weekly/{period}",
        "/api/v1/reports/monthly/{period}",
    ):
        responses = openapi["paths"][path]["get"]["responses"]
        for status_code in ("404", "503"):
            assert responses[status_code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ApiErrorResponse"
            }
    graph_parameters = {
        item["name"]: item["schema"]
        for item in openapi["paths"]["/api/v1/graph"]["get"]["parameters"]
    }
    assert graph_parameters["max_nodes"]["maximum"] == 500
    assert graph_parameters["max_edges"]["maximum"] == 1000
    lineage_parameters = {
        item["name"]: item["schema"]
        for item in openapi["paths"]["/api/v1/lineages/{entity_or_paper_id}"]["get"]["parameters"]
    }
    assert lineage_parameters["max_edges"]["maximum"] == 400


def test_checked_in_openapi_is_generated_from_fastapi() -> None:
    expected = json.loads(Path("apps/api/openapi.json").read_text(encoding="utf-8"))
    assert create_app(FakeRepository()).openapi() == expected
