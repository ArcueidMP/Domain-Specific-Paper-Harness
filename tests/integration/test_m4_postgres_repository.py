# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false

"""Focused PostgreSQL integration coverage for the normalized M4 product boundary."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid5

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session
from tests.fakes import FakeArxiv, fake_pipeline_execution_contract
from tests.integration.test_m3_postgres_repository import (
    GroundedAnalysisLLM,
    RevisedGroundedAnalysisLLM,
    _external_stub,
    _ingest,
    _search_session,
    _second_arxiv_record,
)

from paper_harness.adapters.postgres import PostgresRepository
from paper_harness.adapters.postgres.models import (
    GraphEntityMentionRow,
    PaperAnalysisRow,
    ReportRow,
)
from paper_harness.application.analyze_papers import AnalyzePapers
from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.application.product_models import ProductFailureInput
from paper_harness.application.publish_product import PublishProduct
from paper_harness.application.read_models import GraphView
from paper_harness.domain.analysis import AnalysisScope, ModelUsage, VerificationStatus
from paper_harness.domain.historical import (
    COMPARISON_DIMENSION_ORDER,
    CandidateOrigin,
    CandidateScoreComponents,
    ComparabilityStatus,
    Comparison,
    ComparisonBundle,
    ComparisonDimension,
    ComparisonTargetDecision,
    PaperRelation,
    PaperRelationType,
    RelationProvenance,
    SearchCandidate,
    SearchCandidateDiscovery,
    SearchModelProvenance,
    SearchStopReason,
    SelectionDecision,
)
from paper_harness.domain.identity import (
    stable_analysis_id,
    stable_candidate_discovery_id,
    stable_comparison_dimension_id,
    stable_comparison_id,
    stable_graph_entity_mention_id,
    stable_paper_id,
    stable_paper_relation_id,
    stable_paper_version_id,
    stable_search_candidate_id,
)
from paper_harness.domain.knowledge import (
    GraphEntity,
    GraphEntityType,
    GraphReferenceSet,
    GraphRelationType,
    KnowledgeGraphBundle,
    TrendWindow,
    extract_analysis_graph,
    extract_comparison_graph,
    merge_knowledge_graph_bundles,
)
from paper_harness.domain.models import (
    DailyRun,
    PaperStage,
    PipelineExecution,
    PipelineExecutionMode,
    RunItemStatus,
    RunStatus,
    TopicConfig,
)
from paper_harness.domain.reports import (
    GeneratedReportNarrative,
    Report,
    ReportNarrativeMode,
    ReportNarrativeRequest,
    ReportType,
)
from paper_harness.entrypoints.api import create_app
from paper_harness.ports.arxiv import ArxivPaperRecord
from paper_harness.ports.llm import AnalysisRequest, GeneratedAnalysis, LLMOutputError
from paper_harness.ports.repository import RepositoryError, RepositoryIntegrityError

pytestmark = pytest.mark.integration

NOW = datetime(2026, 1, 10, 5, tzinfo=UTC)


def _persist_comparison(
    repository: PostgresRepository,
    topic: TopicConfig,
    source_record: ArxivPaperRecord,
    target_record: ArxivPaperRecord,
    *,
    now: datetime,
    session_salt: str | None = None,
    pipeline_execution_id: UUID | None = None,
) -> ComparisonBundle:
    source_paper_id = stable_paper_id(source_record.canonical_arxiv_id)
    source_version_id = stable_paper_version_id(
        source_record.canonical_arxiv_id, source_record.version
    )
    target_paper_id = stable_paper_id(target_record.canonical_arxiv_id)
    target_version_id = stable_paper_version_id(target_record.canonical_arxiv_id, 1)
    source_analysis = repository.get_paper_analysis(
        source_paper_id,
        paper_version_id=source_version_id,
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
    )
    target_analysis = repository.get_paper_analysis(
        target_paper_id,
        paper_version_id=target_version_id,
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
    )
    assert source_analysis is not None
    assert target_analysis is not None
    source_evidence_id = source_analysis.evidence[0].id
    target_evidence_id = target_analysis.evidence[0].id
    session = replace(
        _search_session(
            uuid5(
                UUID("2b69b281-5251-4bdb-8d03-3cf4dd57cb8d"),
                (
                    str(source_version_id)
                    if session_salt is None
                    else f"{source_version_id}:{session_salt}"
                ),
            ),
            topic_id=topic.id,
            source_paper_id=source_paper_id,
            source_paper_version_id=source_version_id,
            started_at=now,
            objective="Compare the source with persisted historical work.",
        ),
        source_analysis_id=source_analysis.analysis.id,
        pipeline_execution_id=pipeline_execution_id,
    )
    repository.start_search_session(session)
    external_target = _external_stub(
        target_record,
        semantic_scholar_id="d" * 40,
        now=now,
    )
    candidate = SearchCandidate(
        id=stable_search_candidate_id(session.id, external_target.semantic_scholar_id),
        session_id=session.id,
        external_paper_id=external_target.id,
        semantic_scholar_id=external_target.semantic_scholar_id,
        local_paper_id=None,
        local_paper_version_id=None,
        discovered_by_action_id=None,
        origins=(CandidateOrigin.LOCAL_LEXICAL,),
        relation_depth=0,
        scores=CandidateScoreComponents(lexical=0.8, final=0.8),
        rank=1,
        decision=SelectionDecision.SELECTED,
        decision_reason="Selected as grounded local historical work.",
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-04-24",
        prompt_version="m3-selector-v1",
        generated_at=now,
        verification_status=VerificationStatus.UNVERIFIED,
        schema_version=1,
        created_at=now,
    )
    if pipeline_execution_id is not None:
        candidate = replace(
            candidate,
            comparison_target_decision=ComparisonTargetDecision.TARGET,
            comparison_target_reason="Selected within the owning pipeline execution.",
        )
    discovery = SearchCandidateDiscovery(
        id=stable_candidate_discovery_id(
            candidate.id,
            CandidateOrigin.LOCAL_LEXICAL.value,
            None,
            0,
        ),
        candidate_id=candidate.id,
        action_id=None,
        origin=CandidateOrigin.LOCAL_LEXICAL,
        relation_depth=0,
        discovered_at=now,
    )
    repository.persist_local_search_candidates(
        session.id,
        papers=(external_target,),
        candidates=(candidate,),
        discoveries=(discovery,),
    )
    repository.complete_search_session(
        session.id,
        completed_at=now + timedelta(seconds=1),
        stop_reason=SearchStopReason.QUEUE_EXHAUSTED,
        provenance=SearchModelProvenance(
            provider="deepseek",
            configured_model="deepseek-v4-flash",
            model_version="DeepSeek-V4-Flash-2026-04-24",
            prompt_version="m3-crawler-v1+m3-selector-v1",
            usage=ModelUsage(
                prompt_tokens=40,
                completion_tokens=10,
                total_tokens=50,
                call_count=1,
                duration_ms=100,
                estimated_cost_usd=None,
            ),
        ),
    )
    comparison_id = stable_comparison_id(
        session.id,
        source_version_id,
        source_analysis.analysis.id,
        target_version_id,
        target_analysis.analysis.id,
        "deepseek",
        "deepseek-v4-flash",
        "DeepSeek-V4-Flash-2026-04-24",
        "m3-comparison-v1",
    )
    dimensions = tuple(
        ComparisonDimension(
            id=stable_comparison_dimension_id(comparison_id, name.value),
            comparison_id=comparison_id,
            name=name,
            position=position,
            source_value=f"Source {name.value.lower()}.",
            target_value=f"Target {name.value.lower()}.",
            assessment=f"Evidence-bounded assessment for {name.value.lower()}.",
            source_evidence_ids=((source_evidence_id,) if position == 0 else ()),
            target_evidence_ids=((target_evidence_id,) if position == 0 else ()),
            schema_version=1,
            created_at=now + timedelta(minutes=1),
        )
        for position, name in enumerate(COMPARISON_DIMENSION_ORDER)
    )
    comparison = Comparison(
        id=comparison_id,
        search_session_id=session.id,
        source_paper_id=source_paper_id,
        source_paper_version_id=source_version_id,
        source_analysis_id=source_analysis.analysis.id,
        source_analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        target_paper_id=target_paper_id,
        target_paper_version_id=target_version_id,
        target_analysis_id=target_analysis.analysis.id,
        target_analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        comparability_status=ComparabilityStatus.PARTIALLY_COMPARABLE,
        comparability_reason="The persisted abstracts support a scoped comparison.",
        summary="Both papers evaluate bounded LLM-agent workflows.",
        dimensions=dimensions,
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-04-24",
        prompt_version="m3-comparison-v1",
        generated_at=now + timedelta(minutes=1),
        source="deepseek_comparison",
        verification_status=VerificationStatus.UNVERIFIED,
        usage=ModelUsage(
            prompt_tokens=80,
            completion_tokens=20,
            total_tokens=100,
            call_count=1,
            duration_ms=200,
            estimated_cost_usd=None,
        ),
        schema_version=1,
        created_at=now + timedelta(minutes=1),
    )
    relation = PaperRelation(
        id=stable_paper_relation_id(
            comparison.id,
            source_version_id,
            target_version_id,
            PaperRelationType.SIMILAR_TO.value,
            RelationProvenance.LLM_INFERRED.value,
            comparison.model_version,
            comparison.prompt_version,
        ),
        source_paper_id=source_paper_id,
        source_paper_version_id=source_version_id,
        target_paper_id=target_paper_id,
        target_paper_version_id=target_version_id,
        relation_type=PaperRelationType.SIMILAR_TO,
        provenance=RelationProvenance.LLM_INFERRED,
        evidence_ids=tuple(sorted((source_evidence_id, target_evidence_id), key=str)),
        justification="Both papers evaluate bounded tool-using agent workflows.",
        provider="deepseek",
        model_version=comparison.model_version,
        prompt_version=comparison.prompt_version,
        confidence=0.75,
        verification_status=VerificationStatus.UNVERIFIED,
        generated_at=comparison.generated_at,
        schema_version=1,
        created_at=comparison.created_at,
    )
    extends_relation = replace(
        relation,
        id=stable_paper_relation_id(
            comparison.id,
            source_version_id,
            target_version_id,
            PaperRelationType.EXTENDS.value,
            RelationProvenance.LLM_INFERRED.value,
            comparison.model_version,
            comparison.prompt_version,
        ),
        relation_type=PaperRelationType.EXTENDS,
        justification="Available evidence suggests the source extends the target workflow.",
    )
    bundle = ComparisonBundle(comparison=comparison, relations=(relation, extends_relation))
    repository.persist_comparison_bundle(bundle)
    return bundle


def _prepare_complete_source(
    repository: PostgresRepository,
    topic: TopicConfig,
    source_record: ArxivPaperRecord,
) -> tuple[ArxivPaperRecord, date]:
    target_record = _second_arxiv_record(source_record)
    _ingest(repository, topic, (target_record,), now=NOW)
    source_time = NOW + timedelta(days=1)
    IngestArxiv(
        arxiv=FakeArxiv((source_record,)), repository=repository, clock=lambda: source_time
    ).execute(topic, logical_date=source_time.date())
    AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=GroundedAnalysisLLM(),
        repository=repository,
        clock=lambda: source_time + timedelta(minutes=2),
    ).execute(
        topic,
        paper_ids=(stable_paper_id(source_record.canonical_arxiv_id),),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=source_time.date(),
    )
    _persist_comparison(
        repository,
        topic,
        source_record,
        target_record,
        now=source_time + timedelta(minutes=3),
    )
    return target_record, source_time.date()


def _prepare_revised_source(
    repository: PostgresRepository,
    topic: TopicConfig,
    source_record: ArxivPaperRecord,
) -> tuple[ArxivPaperRecord, date]:
    target_record = _second_arxiv_record(source_record)
    revised_time = NOW + timedelta(days=2)
    revised = replace(
        source_record,
        version=2,
        title="An Unpublished Revised Agent Title",
        updated_at=revised_time,
        pdf_url=f"https://arxiv.org/pdf/{source_record.canonical_arxiv_id}v2",
        source_url=f"https://arxiv.org/abs/{source_record.canonical_arxiv_id}v2",
    )
    IngestArxiv(
        arxiv=FakeArxiv((revised,)),
        repository=repository,
        clock=lambda: revised_time,
    ).execute(topic, logical_date=revised_time.date())
    AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=GroundedAnalysisLLM(),
        repository=repository,
        clock=lambda: revised_time + timedelta(minutes=2),
    ).execute(
        topic,
        paper_ids=(stable_paper_id(source_record.canonical_arxiv_id),),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=revised_time.date(),
    )
    _persist_comparison(
        repository,
        topic,
        revised,
        target_record,
        now=revised_time + timedelta(minutes=3),
    )
    return revised, revised_time.date()


class FailingReportLLM(GroundedAnalysisLLM):
    def generate_report(self, request: ReportNarrativeRequest) -> GeneratedReportNarrative:
        del request
        raise LLMOutputError("DeepSeek report output failed schema validation")


class FutureGroundedAnalysisLLM(RevisedGroundedAnalysisLLM):
    """Create an analysis that is strictly newer than the original source run."""

    def analyze(self, request: AnalysisRequest) -> GeneratedAnalysis:
        return replace(
            super().analyze(request),
            model_version="DeepSeek-V4-Flash-2026-06-01",
            generated_at=NOW + timedelta(days=5, minutes=2),
        )


def _prepare_future_reanalysis(
    repository: PostgresRepository,
    topic: TopicConfig,
    source_record: ArxivPaperRecord,
    target_record: ArxivPaperRecord,
    *,
    logical_date: date,
    session_salt: str,
) -> ComparisonBundle:
    future_time = datetime.combine(logical_date, NOW.timetz())
    AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=FutureGroundedAnalysisLLM(),
        repository=repository,
        clock=lambda: future_time + timedelta(minutes=2),
    ).execute(
        topic,
        paper_ids=(stable_paper_id(source_record.canonical_arxiv_id),),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=logical_date,
    )
    return _persist_comparison(
        repository,
        topic,
        source_record,
        target_record,
        now=future_time + timedelta(minutes=3),
        session_salt=session_salt,
    )


def test_complete_product_publication_round_trips_graph_trends_lineage_and_report(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    target_record, logical_date = _prepare_complete_source(
        postgres_repository, topic_config, arxiv_record_v1
    )
    product_time = NOW + timedelta(days=1, minutes=10)
    run = PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: product_time,
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
    )
    assert run.status is RunStatus.COMPLETE
    assert run.completed_count == 1
    detail = postgres_repository.get_product_run(
        logical_date=logical_date, topic_slug=topic_config.slug
    )
    assert detail is not None
    assert detail.report is not None
    assert detail.report.report.report_type is ReportType.DAILY
    assert detail.items[0].item.stage is PaperStage.PUBLISHED
    assert detail.items[0].item.status is RunItemStatus.COMPLETED

    graph = postgres_repository.get_graph(
        topic_slug=topic_config.slug,
        as_of=logical_date,
        paper_id=None,
        entity_type=None,
        relation_type=GraphRelationType.SIMILAR_TO,
        provenance=RelationProvenance.LLM_INFERRED,
        verification_status=VerificationStatus.UNVERIFIED,
        max_nodes=200,
        max_edges=400,
    )
    assert graph is not None
    assert len(graph.edges) == 1
    assert graph.edges[0].edge.evidence_ids
    assert graph.edges[0].evidence
    evidence_by_role = {item.role.value: item for item in graph.edges[0].evidence}
    assert evidence_by_role["SOURCE"].paper_id == stable_paper_id(
        arxiv_record_v1.canonical_arxiv_id
    )
    assert evidence_by_role["TARGET"].paper_id == stable_paper_id(target_record.canonical_arxiv_id)
    assert evidence_by_role["TARGET"].paper_version_id == stable_paper_version_id(
        target_record.canonical_arxiv_id, 1
    )

    for requested_relation, requested_provenance, requested_verification in (
        (GraphRelationType.SIMILAR_TO, None, None),
        (None, RelationProvenance.LLM_INFERRED, None),
        (None, None, VerificationStatus.UNVERIFIED),
    ):
        filtered = postgres_repository.get_graph(
            topic_slug=topic_config.slug,
            as_of=logical_date,
            paper_id=None,
            entity_type=None,
            relation_type=requested_relation,
            provenance=requested_provenance,
            verification_status=requested_verification,
            max_nodes=200,
            max_edges=400,
        )
        assert filtered is not None
        assert filtered.edges
        assert all(
            item.edge.source_entity_id in {node.entity.id for node in filtered.nodes}
            and item.edge.target_entity_id in {node.entity.id for node in filtered.nodes}
            for item in filtered.edges
        )
    bounded = postgres_repository.get_graph(
        topic_slug=topic_config.slug,
        as_of=logical_date,
        paper_id=None,
        entity_type=None,
        relation_type=None,
        provenance=None,
        verification_status=None,
        max_nodes=1,
        max_edges=1,
    )
    assert bounded is not None
    assert len(bounded.nodes) == 1
    assert bounded.truncated

    trends = postgres_repository.list_trends(
        topic_slug=topic_config.slug,
        as_of=logical_date,
        windows=tuple(TrendWindow),
    )
    assert len(trends) == 3
    assert {item.snapshot.included_paper_count for item in trends} == {2}
    bounded_trends = postgres_repository.list_trends(
        topic_slug=topic_config.slug,
        as_of=logical_date,
        windows=tuple(TrendWindow),
        entity_type=None,
        max_entities=1,
    )
    assert all(len(item.snapshot.entity_counts) <= 1 for item in bounded_trends)
    assert any(item.total_entities > 1 and item.truncated for item in bounded_trends)
    method_trends = postgres_repository.list_trends(
        topic_slug=topic_config.slug,
        as_of=logical_date,
        windows=(TrendWindow.SEVEN_DAYS,),
        entity_type=GraphEntityType.METHOD,
        max_entities=50,
    )
    assert all(
        metric.entity_type is GraphEntityType.METHOD
        for metric in method_trends[0].snapshot.entity_counts
    )
    source_paper_id = stable_paper_id(arxiv_record_v1.canonical_arxiv_id)
    lineage = postgres_repository.get_lineage(
        source_paper_id,
        topic_slug=topic_config.slug,
        max_depth=1,
        max_nodes=1,
        max_edges=1,
    )
    assert lineage is not None
    assert len(lineage.snapshot.nodes) == 1
    assert len(lineage.snapshot.edges) <= 1
    assert lineage.snapshot.max_edges == 1
    connected_lineage = postgres_repository.get_lineage(
        source_paper_id,
        topic_slug=topic_config.slug,
        max_depth=1,
        max_nodes=2,
        max_edges=1,
    )
    assert connected_lineage is not None
    assert len(connected_lineage.snapshot.nodes) == 2
    assert len(connected_lineage.snapshot.edges) == 1
    lineage_edge = connected_lineage.snapshot.edges[0]
    depths = {item.graph_entity_id: item.depth for item in connected_lineage.snapshot.nodes}
    assert depths[lineage_edge.source_entity_id] == 0
    assert depths[lineage_edge.target_entity_id] == 1
    assert lineage_edge.relation_type is GraphRelationType.EXTENDS
    lineage_target_evidence = tuple(
        item for item in connected_lineage.evidence if item.role.value == "TARGET"
    )
    assert lineage_target_evidence
    assert all(
        item.paper_id == stable_paper_id(target_record.canonical_arxiv_id)
        for item in lineage_target_evidence
    )

    reports, total = postgres_repository.list_reports(
        report_type=ReportType.DAILY,
        topic_slug=topic_config.slug,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert reports[0] == detail.report
    client = TestClient(create_app(postgres_repository))
    assert client.get(f"/api/v1/daily/{logical_date}?topic={topic_config.slug}").status_code == 200
    assert client.get(f"/api/v1/graph?topic={topic_config.slug}").status_code == 200
    assert client.get(f"/api/v1/trends?topic={topic_config.slug}").status_code == 200
    for name, value in (
        ("relation_type", GraphRelationType.SIMILAR_TO.value),
        ("provenance", RelationProvenance.LLM_INFERRED.value),
        ("verification_status", VerificationStatus.UNVERIFIED.value),
    ):
        response = client.get(
            "/api/v1/graph",
            params={"topic": topic_config.slug, name: value},
        )
        assert response.status_code == 200
        assert response.json()["edges"]
    assert (
        client.get(
            f"/api/v1/lineages/{source_paper_id}?topic={topic_config.slug}"
            "&max_depth=1&max_nodes=1&max_edges=1"
        ).status_code
        == 200
    )
    repeated = PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: product_time + timedelta(minutes=1),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
    )
    assert repeated.id == run.id
    assert (
        postgres_repository.list_reports(
            report_type=ReportType.DAILY,
            topic_slug=topic_config.slug,
            limit=10,
            offset=0,
        )[1]
        == 1
    )


def test_reprocess_publishes_the_latest_same_date_revision_without_deleting_history(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    target_record, logical_date = _prepare_complete_source(
        postgres_repository,
        topic_config,
        arxiv_record_v1,
    )
    first = PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=1, minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
    )
    execution_id = UUID("3b301c07-9aa3-4a3d-b13a-e7b3ba4db146")
    reprocess_started = NOW + timedelta(days=1, hours=1)
    postgres_repository.start_pipeline_execution(
        PipelineExecution(
            id=execution_id,
            topic_id=topic_config.id,
            logical_date=logical_date,
            execution_mode=PipelineExecutionMode.REPROCESS,
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
            selection_limit=1,
            contract=fake_pipeline_execution_contract(),
            status=RunStatus.RUNNING,
            deadline_at=reprocess_started + timedelta(hours=8),
            started_at=reprocess_started,
            completed_at=None,
            error_code=None,
            error_detail=None,
            schema_version=1,
            created_at=reprocess_started,
        )
    )
    cursor_before_reprocess = postgres_repository.get_ingestion_cursor(topic_config.id)
    ingestion_run = IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=postgres_repository,
        clock=lambda: reprocess_started,
    ).execute(
        topic_config,
        logical_date=logical_date,
        pipeline_execution_mode=PipelineExecutionMode.REPROCESS,
        pipeline_selection_limit=1,
        pipeline_execution_id=execution_id,
        resume_existing=True,
    )
    assert ingestion_run.pipeline_execution_id == execution_id
    assert postgres_repository.get_ingestion_cursor(topic_config.id) == cursor_before_reprocess

    class ReprocessAnalysisLLM(GroundedAnalysisLLM):
        def analyze(self, request: AnalysisRequest) -> GeneratedAnalysis:
            return replace(
                super().analyze(request),
                generated_at=reprocess_started + timedelta(minutes=2),
            )

    analysis_run = AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=ReprocessAnalysisLLM(),
        repository=postgres_repository,
        clock=lambda: reprocess_started + timedelta(minutes=2),
    ).execute(
        topic_config,
        paper_ids=(stable_paper_id(arxiv_record_v1.canonical_arxiv_id),),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=logical_date,
        pipeline_execution_mode=PipelineExecutionMode.REPROCESS,
        pipeline_selection_limit=1,
        pipeline_execution_id=execution_id,
        resume_existing=True,
        reuse_contract=None,
    )
    _persist_comparison(
        postgres_repository,
        topic_config,
        arxiv_record_v1,
        target_record,
        now=reprocess_started + timedelta(minutes=3),
        session_salt="reprocess",
        pipeline_execution_id=execution_id,
    )
    revised = PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: reprocess_started + timedelta(minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
        pipeline_execution_id=execution_id,
    )
    postgres_repository.complete_pipeline_execution(
        execution_id,
        status=revised.status,
        completed_at=reprocess_started + timedelta(minutes=11),
    )

    assert analysis_run.pipeline_execution_id == execution_id
    assert revised.id != first.id
    detail = postgres_repository.get_product_run(
        logical_date=logical_date,
        topic_slug=topic_config.slug,
    )
    assert detail is not None
    assert detail.run.id == revised.id
    reports, total = postgres_repository.list_reports(
        report_type=ReportType.DAILY,
        topic_slug=topic_config.slug,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert reports[0].report.run_id == revised.id

    source_paper_id = stable_paper_id(arxiv_record_v1.canonical_arxiv_id)
    with Session(postgres_engine) as session:
        source_analyses = tuple(
            session.scalars(
                select(PaperAnalysisRow)
                .where(PaperAnalysisRow.paper_id == source_paper_id)
                .order_by(PaperAnalysisRow.generated_at, PaperAnalysisRow.id)
            )
        )
        persisted_reports = tuple(
            session.scalars(
                select(ReportRow).where(
                    ReportRow.topic_id == topic_config.id,
                    ReportRow.report_type == ReportType.DAILY.value,
                    ReportRow.logical_date == logical_date,
                )
            )
        )
    assert {row.revision_id for row in source_analyses} == {None, execution_id}
    assert len(persisted_reports) == 2

    graph = postgres_repository.get_graph(
        topic_slug=topic_config.slug,
        as_of=logical_date,
        paper_id=None,
        entity_type=None,
        relation_type=GraphRelationType.SIMILAR_TO,
        provenance=RelationProvenance.LLM_INFERRED,
        verification_status=VerificationStatus.UNVERIFIED,
        max_nodes=200,
        max_edges=400,
    )
    assert graph is not None
    assert len(graph.edges) == 1


def test_historical_analysis_failure_persists_exact_partial_daily_report(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    target_record, logical_date = _prepare_complete_source(
        postgres_repository,
        topic_config,
        arxiv_record_v1,
    )
    failure = ProductFailureInput(
        paper_id=stable_paper_id(target_record.canonical_arxiv_id),
        paper_version_id=stable_paper_version_id(target_record.canonical_arxiv_id, 1),
        stage=PaperStage.PARSED,
        failed_stage=PaperStage.ANALYZED,
        error_code="ANALYSIS_MODEL_OUTPUT_INVALID",
        retryable=False,
        error_detail="Historical analysis output failed schema validation.",
    )

    run = PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=1, minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
        upstream_failures=(failure,),
    )

    assert run.status is RunStatus.PARTIAL
    assert (run.selected_count, run.completed_count, run.failed_count) == (2, 1, 1)
    detail = postgres_repository.get_product_run(
        logical_date=logical_date,
        topic_slug=topic_config.slug,
    )
    assert detail is not None
    assert detail.report is not None
    failed_item = next(
        item.item for item in detail.items if item.item.status is RunItemStatus.FAILED
    )
    assert failed_item.paper_id == failure.paper_id
    assert failed_item.paper_version_id == failure.paper_version_id
    assert failed_item.stage is failure.stage
    assert failed_item.failed_stage is failure.failed_stage
    assert failed_item.error_code == failure.error_code
    assert failed_item.retryable is failure.retryable
    assert failed_item.error_detail == failure.error_detail
    assert detail.report.report.status is RunStatus.PARTIAL
    assert detail.report.report.failures[0].paper_version_id == failure.paper_version_id
    assert detail.report.report.failures[0].failed_stage is failure.failed_stage
    assert detail.report.report.failures[0].error_code == failure.error_code
    assert detail.report.report.failures[0].error_detail == failure.error_detail


def test_graph_batch_rejects_orphan_evidence_and_failed_run_publishes_no_report(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _, logical_date = _prepare_complete_source(postgres_repository, topic_config, arxiv_record_v1)
    source = postgres_repository.get_product_publication_input(topic_config.id, logical_date)
    assert source is not None
    paper = source.papers[0]
    started_at = NOW + timedelta(days=1, minutes=10)
    run = postgres_repository.start_product_run(
        topic_id=topic_config.id,
        logical_date=logical_date,
        source=source,
        started_at=started_at,
    )
    postgres_repository.advance_product_item(
        run_id=run.id,
        paper_version_id=paper.paper_version_id,
        expected_stage=PaperStage.EVIDENCE_EXTRACTED,
        next_stage=PaperStage.COMPARED,
        updated_at=started_at,
    )
    analysis_graph = extract_analysis_graph(
        topic_config.id, paper.analysis, paper_title=paper.paper_title
    )
    comparison_graph = extract_comparison_graph(
        topic_config.id,
        paper.comparisons[0].bundle,
        source_paper_title=paper.comparisons[0].source_paper_title,
        target_paper_title=paper.comparisons[0].target_paper_title,
    )
    valid = merge_knowledge_graph_bundles((analysis_graph.bundle, comparison_graph.bundle))
    mention_index = next(
        index for index, mention in enumerate(valid.mentions) if mention.evidence_ids
    )
    orphan_id = UUID("f4f2ad24-1330-4462-845d-c261e295ba45")
    mentions = list(valid.mentions)
    mentions[mention_index] = replace(mentions[mention_index], evidence_ids=(orphan_id,))
    invalid = KnowledgeGraphBundle(
        topic_id=valid.topic_id,
        entities=valid.entities,
        mentions=tuple(mentions),
        edges=valid.edges,
        references=GraphReferenceSet(
            paper_version_ids=valid.references.paper_version_ids,
            analysis_ids=valid.references.analysis_ids,
            comparison_ids=valid.references.comparison_ids,
            paper_relation_ids=valid.references.paper_relation_ids,
            evidence_ids=tuple((*valid.references.evidence_ids, orphan_id)),
        ),
    )
    with pytest.raises(RepositoryError, match="missing persisted records"):
        postgres_repository.persist_product_graph(
            run_id=run.id,
            paper_version_id=paper.paper_version_id,
            bundle=invalid,
            expected_stage=PaperStage.COMPARED,
            updated_at=started_at,
        )
    assert (
        postgres_repository.get_graph(
            topic_slug=topic_config.slug,
            as_of=logical_date,
            paper_id=None,
            entity_type=None,
            relation_type=None,
            provenance=None,
            verification_status=None,
            max_nodes=200,
            max_edges=400,
        )
        is None
    )
    for _ in range(2):
        postgres_repository.fail_product_item(
            run_id=run.id,
            paper_version_id=paper.paper_version_id,
            failed_stage=PaperStage.GRAPH_UPDATED,
            error_code="GRAPH_REFERENCE_INVALID",
            retryable=False,
            error_detail="The graph references missing persisted evidence.",
            updated_at=started_at + timedelta(seconds=30),
        )
    failed_item_run = postgres_repository.get_run(run.id)
    assert failed_item_run is not None
    assert failed_item_run.run.failed_count == 1
    assert sum(item.item.status is RunItemStatus.FAILED for item in failed_item_run.items) == 1
    failed = postgres_repository.fail_product_run(
        run.id,
        completed_at=started_at + timedelta(minutes=1),
        failed_stage=PaperStage.GRAPH_UPDATED,
        error_code="GRAPH_REFERENCE_INVALID",
        retryable=False,
        error_detail="The graph references missing persisted evidence.",
    )
    assert failed.status is RunStatus.FAILED
    detail = postgres_repository.get_product_run(
        logical_date=logical_date, topic_slug=topic_config.slug
    )
    assert detail is not None
    assert detail.report is None
    assert (
        postgres_repository.list_reports(
            report_type=ReportType.DAILY,
            topic_slug=topic_config.slug,
            limit=10,
            offset=0,
        )[1]
        == 0
    )


@pytest.mark.parametrize("owner_kind", ("analysis", "comparison"))
def test_graph_batch_rejects_same_version_evidence_from_the_wrong_owner(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
    owner_kind: str,
) -> None:
    _, logical_date = _prepare_complete_source(postgres_repository, topic_config, arxiv_record_v1)
    source = postgres_repository.get_product_publication_input(topic_config.id, logical_date)
    assert source is not None
    paper = source.papers[0]
    AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=RevisedGroundedAnalysisLLM(),
        repository=postgres_repository,
        clock=lambda: NOW + timedelta(days=2, minutes=2),
    ).execute(
        topic_config,
        paper_ids=(paper.paper_id,),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=logical_date + timedelta(days=1),
    )
    revised = postgres_repository.get_paper_analysis(
        paper.paper_id,
        paper_version_id=paper.paper_version_id,
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
    )
    assert revised is not None
    wrong_evidence_id = revised.evidence[0].id
    assert wrong_evidence_id not in {item.id for item in paper.evidence}
    started_at = NOW + timedelta(days=2, minutes=10)
    run = postgres_repository.start_product_run(
        topic_id=topic_config.id,
        logical_date=logical_date,
        source=source,
        started_at=started_at,
    )
    postgres_repository.advance_product_item(
        run_id=run.id,
        paper_version_id=paper.paper_version_id,
        expected_stage=PaperStage.EVIDENCE_EXTRACTED,
        next_stage=PaperStage.COMPARED,
        updated_at=started_at,
    )
    valid = merge_knowledge_graph_bundles(
        (
            extract_analysis_graph(
                topic_config.id,
                paper.analysis,
                paper_title=paper.paper_title,
            ).bundle,
            extract_comparison_graph(
                topic_config.id,
                paper.comparisons[0].bundle,
                source_paper_title=paper.comparisons[0].source_paper_title,
                target_paper_title=paper.comparisons[0].target_paper_title,
            ).bundle,
        )
    )
    mentions = list(valid.mentions)
    edges = list(valid.edges)
    if owner_kind == "analysis":
        index = next(
            index
            for index, mention in enumerate(mentions)
            if mention.analysis_id is not None and mention.evidence_ids
        )
        mentions[index] = replace(mentions[index], evidence_ids=(wrong_evidence_id,))
        expected_error = "wrong analysis owner"
    else:
        index = next(
            index for index, edge in enumerate(edges) if edge.paper_relation_id is not None
        )
        edges[index] = replace(edges[index], evidence_ids=(wrong_evidence_id,))
        expected_error = "outside its persisted comparison owner"
    invalid = KnowledgeGraphBundle(
        topic_id=valid.topic_id,
        entities=valid.entities,
        mentions=tuple(mentions),
        edges=tuple(edges),
        references=GraphReferenceSet(
            paper_version_ids=valid.references.paper_version_ids,
            analysis_ids=valid.references.analysis_ids,
            comparison_ids=valid.references.comparison_ids,
            paper_relation_ids=valid.references.paper_relation_ids,
            evidence_ids=tuple((*valid.references.evidence_ids, wrong_evidence_id)),
        ),
    )
    with pytest.raises(RepositoryError, match=expected_error):
        postgres_repository.persist_product_graph(
            run_id=run.id,
            paper_version_id=paper.paper_version_id,
            bundle=invalid,
            expected_stage=PaperStage.COMPARED,
            updated_at=started_at,
        )
    run_detail = postgres_repository.get_run(run.id)
    assert run_detail is not None
    assert run_detail.items[0].item.stage is PaperStage.COMPARED


def test_graph_data_error_is_mapped_without_leaving_partial_rows(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _, logical_date = _prepare_complete_source(postgres_repository, topic_config, arxiv_record_v1)
    source = postgres_repository.get_product_publication_input(topic_config.id, logical_date)
    assert source is not None
    paper = source.papers[0]
    started_at = NOW + timedelta(days=1, minutes=10)
    run = postgres_repository.start_product_run(
        topic_id=topic_config.id,
        logical_date=logical_date,
        source=source,
        started_at=started_at,
    )
    postgres_repository.advance_product_item(
        run_id=run.id,
        paper_version_id=paper.paper_version_id,
        expected_stage=PaperStage.EVIDENCE_EXTRACTED,
        next_stage=PaperStage.COMPARED,
        updated_at=started_at,
    )
    bundle = merge_knowledge_graph_bundles(
        (
            extract_analysis_graph(
                topic_config.id,
                paper.analysis,
                paper_title=paper.paper_title,
            ).bundle,
            extract_comparison_graph(
                topic_config.id,
                paper.comparisons[0].bundle,
                source_paper_title=paper.comparisons[0].source_paper_title,
                target_paper_title=paper.comparisons[0].target_paper_title,
            ).bundle,
        )
    )
    object.__setattr__(bundle.entities[0], "normalized_key", "x" * 501)
    with pytest.raises(RepositoryIntegrityError, match="ownership constraints"):
        postgres_repository.persist_product_graph(
            run_id=run.id,
            paper_version_id=paper.paper_version_id,
            bundle=bundle,
            expected_stage=PaperStage.COMPARED,
            updated_at=started_at,
        )
    run_detail = postgres_repository.get_run(run.id)
    assert run_detail is not None
    assert run_detail.items[0].item.stage is PaperStage.COMPARED
    assert (
        postgres_repository.get_graph(
            topic_slug=topic_config.slug,
            as_of=logical_date,
            paper_id=None,
            entity_type=None,
            relation_type=None,
            provenance=None,
            verification_status=None,
            max_nodes=200,
            max_edges=400,
        )
        is None
    )


def test_report_generation_failure_removes_staged_artifacts(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _, logical_date = _prepare_complete_source(postgres_repository, topic_config, arxiv_record_v1)
    with pytest.raises(LLMOutputError, match="schema validation"):
        PublishProduct(
            repository=postgres_repository,
            llm=FailingReportLLM(),
            clock=lambda: NOW + timedelta(days=1, minutes=10),
        ).execute(
            topic_config,
            narrative_mode=ReportNarrativeMode.DEEPSEEK,
            logical_date=logical_date,
        )
    failed = postgres_repository.get_product_run(
        logical_date=logical_date,
        topic_slug=topic_config.slug,
    )
    assert failed is not None
    assert failed.run.status is RunStatus.FAILED
    assert failed.report is None
    assert (
        postgres_repository.get_graph(
            topic_slug=topic_config.slug,
            as_of=logical_date,
            paper_id=None,
            entity_type=None,
            relation_type=None,
            provenance=None,
            verification_status=None,
            max_nodes=200,
            max_edges=400,
        )
        is None
    )
    assert (
        postgres_repository.list_trends(
            topic_slug=topic_config.slug,
            as_of=logical_date,
            windows=tuple(TrendWindow),
        )
        == ()
    )
    assert (
        postgres_repository.get_lineage(
            stable_paper_id(arxiv_record_v1.canonical_arxiv_id),
            topic_slug=topic_config.slug,
            max_depth=5,
            max_nodes=100,
            max_edges=200,
        )
        is None
    )
    corpus = postgres_repository.get_graph_corpus(topic_config.id, as_of_date=logical_date)
    assert corpus.entities == ()
    assert corpus.mentions == ()
    assert corpus.edges == ()


def test_graph_as_of_uses_publication_logical_date_not_utc_generation_date(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _, logical_date = _prepare_complete_source(postgres_repository, topic_config, arxiv_record_v1)
    PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW,
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
    )

    def get_graph(as_of: date) -> GraphView | None:
        return postgres_repository.get_graph(
            topic_slug=topic_config.slug,
            as_of=as_of,
            paper_id=None,
            entity_type=None,
            relation_type=None,
            provenance=None,
            verification_status=None,
            max_nodes=200,
            max_edges=400,
        )

    assert get_graph(logical_date - timedelta(days=1)) is None
    assert get_graph(logical_date) is not None


def test_product_input_and_graph_preserve_exact_version_titles(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    target_record, logical_date = _prepare_complete_source(
        postgres_repository, topic_config, arxiv_record_v1
    )
    future_time = NOW + timedelta(days=2)
    source_v2 = replace(
        arxiv_record_v1,
        version=2,
        title="A Mutable Current Source Title",
        updated_at=future_time,
        pdf_url=f"https://arxiv.org/pdf/{arxiv_record_v1.canonical_arxiv_id}v2",
        source_url=f"https://arxiv.org/abs/{arxiv_record_v1.canonical_arxiv_id}v2",
    )
    target_v2 = replace(
        target_record,
        version=2,
        title="A Mutable Current Target Title",
        updated_at=future_time,
        pdf_url=f"https://arxiv.org/pdf/{target_record.canonical_arxiv_id}v2",
        source_url=f"https://arxiv.org/abs/{target_record.canonical_arxiv_id}v2",
    )
    IngestArxiv(
        arxiv=FakeArxiv((source_v2, target_v2)),
        repository=postgres_repository,
        clock=lambda: future_time,
    ).execute(topic_config, logical_date=future_time.date())

    publication_input = postgres_repository.get_product_publication_input(
        topic_config.id, logical_date
    )
    assert publication_input is not None
    assert publication_input.papers[0].paper_title == arxiv_record_v1.title
    assert publication_input.papers[0].comparisons[0].target_paper_title == target_record.title
    PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: future_time + timedelta(minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
    )
    graph = postgres_repository.get_graph(
        topic_slug=topic_config.slug,
        as_of=logical_date,
        paper_id=None,
        entity_id=None,
        entity_type=None,
        relation_type=None,
        provenance=None,
        verification_status=None,
        max_nodes=200,
        max_edges=400,
    )
    assert graph is not None
    paper_labels = {
        node.entity.paper_id: node.entity.display_label
        for node in graph.nodes
        if node.entity.paper_id is not None
    }
    assert paper_labels[stable_paper_id(arxiv_record_v1.canonical_arxiv_id)] == (
        arxiv_record_v1.title
    )
    assert paper_labels[stable_paper_id(target_record.canonical_arxiv_id)] == target_record.title


def test_published_mentions_derive_aliases_at_each_as_of_boundary(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _, first_date = _prepare_complete_source(postgres_repository, topic_config, arxiv_record_v1)
    PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=1, minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=first_date,
    )
    revised, revised_date = _prepare_revised_source(
        postgres_repository, topic_config, arxiv_record_v1
    )
    PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=2, minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=revised_date,
    )

    def source_entity(as_of: date) -> GraphEntity:
        graph = postgres_repository.get_graph(
            topic_slug=topic_config.slug,
            as_of=as_of,
            paper_id=stable_paper_id(arxiv_record_v1.canonical_arxiv_id),
            entity_id=None,
            entity_type=None,
            relation_type=None,
            provenance=None,
            verification_status=None,
            max_nodes=200,
            max_edges=400,
        )
        assert graph is not None
        return next(
            node.entity
            for node in graph.nodes
            if node.entity.paper_id == stable_paper_id(arxiv_record_v1.canonical_arxiv_id)
        )

    first = source_entity(first_date)
    latest = source_entity(revised_date)
    assert first.display_label == arxiv_record_v1.title
    assert first.aliases == (arxiv_record_v1.title,)
    assert latest.display_label == revised.title
    assert latest.aliases == (arxiv_record_v1.title, revised.title)


def test_repeated_publication_reuses_exact_graph_occurrences(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _, first_date = _prepare_complete_source(postgres_repository, topic_config, arxiv_record_v1)
    first = PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=1, minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=first_date,
    )
    second_date = first_date + timedelta(days=1)
    AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=GroundedAnalysisLLM(),
        repository=postgres_repository,
        clock=lambda: NOW + timedelta(days=2, minutes=2),
    ).execute(
        topic_config,
        paper_ids=(stable_paper_id(arxiv_record_v1.canonical_arxiv_id),),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=second_date,
    )
    second = PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=2, minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=second_date,
    )
    assert first.status is RunStatus.COMPLETE
    assert second.status is RunStatus.COMPLETE
    assert second.id != first.id
    assert (
        postgres_repository.list_reports(
            report_type=ReportType.DAILY,
            topic_slug=topic_config.slug,
            limit=10,
            offset=0,
        )[1]
        == 2
    )


def test_first_product_input_excludes_analysis_created_after_source_completion(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    target_record, logical_date = _prepare_complete_source(
        postgres_repository, topic_config, arxiv_record_v1
    )
    original = postgres_repository.get_product_publication_input(topic_config.id, logical_date)
    assert original is not None
    original_analysis_id = original.papers[0].analysis.analysis.id
    original_comparison_ids = {item.bundle.comparison.id for item in original.papers[0].comparisons}
    future_bundle = _prepare_future_reanalysis(
        postgres_repository,
        topic_config,
        arxiv_record_v1,
        target_record,
        logical_date=logical_date + timedelta(days=4),
        session_salt="future-cutoff",
    )
    selected = postgres_repository.get_product_publication_input(topic_config.id, logical_date)
    assert selected is not None
    assert selected.papers[0].analysis.analysis.id == original_analysis_id
    assert {
        item.bundle.comparison.id for item in selected.papers[0].comparisons
    } == original_comparison_ids
    assert future_bundle.comparison.id not in original_comparison_ids


def test_default_related_work_uses_the_exact_published_session_not_a_later_failure(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _, logical_date = _prepare_complete_source(
        postgres_repository,
        topic_config,
        arxiv_record_v1,
    )
    source = postgres_repository.get_product_publication_input(
        topic_config.id,
        logical_date,
    )
    assert source is not None
    source_paper = source.papers[0]
    published_session_id = source_paper.comparisons[0].bundle.comparison.search_session_id
    published = PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=1, minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
    )
    assert published.status is RunStatus.COMPLETE
    later = replace(
        _search_session(
            UUID("0c2ba527-7d3c-45b5-8834-2195dce189c3"),
            topic_id=topic_config.id,
            source_paper_id=source_paper.paper_id,
            source_paper_version_id=source_paper.paper_version_id,
            started_at=NOW + timedelta(days=2),
            objective="A later search that was never published.",
        ),
        source_analysis_id=source_paper.analysis.analysis.id,
    )
    postgres_repository.start_search_session(later)
    postgres_repository.fail_search_session(
        later.id,
        completed_at=NOW + timedelta(days=2, seconds=1),
        error_code="SCHOLARLY_SEARCH_FAILED",
        error_detail="Later uncommitted session failed.",
        provenance=None,
    )

    canonical = postgres_repository.get_related_work(source_paper.paper_id)
    explicit_later = postgres_repository.get_related_work(
        source_paper.paper_id,
        search_session_id=later.id,
    )
    assert canonical is not None
    assert canonical.session.id == published_session_id
    assert explicit_later is not None
    assert explicit_later.session.id == later.id


def test_failed_product_retry_reuses_transactional_input_snapshot(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    target_record, logical_date = _prepare_complete_source(
        postgres_repository, topic_config, arxiv_record_v1
    )
    source = postgres_repository.get_product_publication_input(topic_config.id, logical_date)
    assert source is not None
    run = postgres_repository.start_product_run(
        topic_id=topic_config.id,
        logical_date=logical_date,
        source=source,
        started_at=NOW + timedelta(days=1, minutes=10),
    )
    failed = postgres_repository.fail_product_run(
        run.id,
        completed_at=NOW + timedelta(days=1, minutes=11),
        failed_stage=PaperStage.EVIDENCE_EXTRACTED,
        error_code="REPORT_GENERATION_FAILED",
        retryable=False,
        error_detail="Intentional snapshot regression failure.",
    )
    assert failed.status is RunStatus.FAILED
    _prepare_future_reanalysis(
        postgres_repository,
        topic_config,
        arxiv_record_v1,
        target_record,
        logical_date=logical_date + timedelta(days=4),
        session_salt="after-failure",
    )

    retry_source = postgres_repository.get_product_publication_input(topic_config.id, logical_date)
    assert retry_source is not None
    assert retry_source.papers[0].analysis.analysis.id == source.papers[0].analysis.analysis.id
    assert {item.bundle.comparison.id for item in retry_source.papers[0].comparisons} == {
        item.bundle.comparison.id for item in source.papers[0].comparisons
    }
    restarted = postgres_repository.restart_product_run(
        run.id,
        source=retry_source,
        started_at=NOW + timedelta(days=5, minutes=10),
    )
    assert restarted.id == run.id
    assert restarted.status is RunStatus.RUNNING


def test_failed_product_restart_uses_current_comparison_and_analysis_failure(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    target_record, logical_date = _prepare_complete_source(
        postgres_repository,
        topic_config,
        arxiv_record_v1,
    )
    source = postgres_repository.get_product_publication_input(
        topic_config.id,
        logical_date,
    )
    assert source is not None
    valid_comparison_id = source.papers[0].comparisons[0].bundle.comparison.id
    target_paper_id = stable_paper_id(target_record.canonical_arxiv_id)
    target_version_id = stable_paper_version_id(target_record.canonical_arxiv_id, 1)
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE daily_runs SET status = 'PARTIAL', selected_count = 2, "
                "completed_count = 1, failed_count = 1 WHERE id = :run_id"
            ),
            {"run_id": source.source_run.run.id},
        )
        connection.execute(
            text(
                "INSERT INTO run_items (id, run_id, paper_id, paper_version_id, stage, "
                "status, failed_stage, error_code, retryable, error_detail, schema_version, "
                "created_at, updated_at) VALUES (:id, :run_id, :paper_id, :version_id, "
                "'SELECTED', 'FAILED', 'ANALYZED', 'ANALYSIS_MODEL_OUTPUT_INVALID', "
                "false, 'Persisted upstream analysis failure.', 1, :now, :now)"
            ),
            {
                "id": uuid5(source.source_run.run.id, f"failed:{target_version_id}"),
                "run_id": source.source_run.run.id,
                "paper_id": target_paper_id,
                "version_id": target_version_id,
                "now": NOW + timedelta(days=1, minutes=4),
            },
        )
    publisher = PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=1, minutes=10),
    )
    failed = publisher.execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
        comparison_ids=frozenset(),
    )
    assert failed.status is RunStatus.FAILED
    with postgres_engine.connect() as connection:
        paper_input_before = connection.execute(
            text(
                "SELECT topic_id, source_run_id, paper_id, paper_version_id, analysis_id, "
                "analysis_scope, created_at FROM product_run_paper_inputs "
                "WHERE run_id = :run_id"
            ),
            {"run_id": failed.id},
        ).one()
        assert (
            connection.execute(
                text(
                    "SELECT comparison_id FROM product_run_comparison_inputs WHERE run_id = :run_id"
                ),
                {"run_id": failed.id},
            ).all()
            == []
        )

    retried = publisher.execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
        comparison_ids=frozenset((valid_comparison_id,)),
    )

    assert retried.id == failed.id
    assert retried.status is RunStatus.PARTIAL
    assert (retried.completed_count, retried.failed_count) == (1, 1)
    with postgres_engine.connect() as connection:
        paper_input_after = connection.execute(
            text(
                "SELECT topic_id, source_run_id, paper_id, paper_version_id, analysis_id, "
                "analysis_scope, created_at FROM product_run_paper_inputs "
                "WHERE run_id = :run_id"
            ),
            {"run_id": retried.id},
        ).one()
        comparison_ids = {
            row.comparison_id
            for row in connection.execute(
                text(
                    "SELECT comparison_id FROM product_run_comparison_inputs WHERE run_id = :run_id"
                ),
                {"run_id": retried.id},
            )
        }
    assert paper_input_after == paper_input_before
    assert comparison_ids == {valid_comparison_id}
    detail = postgres_repository.get_run(retried.id)
    assert detail is not None
    preserved = next(
        item.item for item in detail.items if item.item.paper_version_id == target_version_id
    )
    assert preserved.failed_stage is PaperStage.ANALYZED
    assert preserved.error_code == "ANALYSIS_MODEL_OUTPUT_INVALID"


def test_long_version_title_publishes_without_graph_key_truncation(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    long_title = "A Long Agent Title " + ("界" * 600)
    long_record = replace(arxiv_record_v1, title=long_title)
    _, logical_date = _prepare_complete_source(postgres_repository, topic_config, long_record)
    published = PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=1, minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
    )
    assert published.status is RunStatus.COMPLETE
    corpus = postgres_repository.get_graph_corpus(topic_config.id, as_of_date=logical_date)
    source_paper_id = stable_paper_id(long_record.canonical_arxiv_id)
    assert next(item.title for item in corpus.papers if item.paper_id == source_paper_id) == (
        long_title
    )
    graph = postgres_repository.get_graph(
        topic_slug=topic_config.slug,
        as_of=logical_date,
        paper_id=source_paper_id,
        entity_id=None,
        entity_type=None,
        relation_type=None,
        provenance=None,
        verification_status=None,
        max_nodes=200,
        max_edges=400,
    )
    assert graph is not None
    assert (
        next(
            node.entity.display_label
            for node in graph.nodes
            if node.entity.paper_id == source_paper_id
        )
        == long_title
    )


def test_reanalysis_occurrences_use_owning_product_activity_date(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    target_record, first_date = _prepare_complete_source(
        postgres_repository, topic_config, arxiv_record_v1
    )
    PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=1, minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=first_date,
    )
    later_date = first_date + timedelta(days=19)
    later_comparison = _prepare_future_reanalysis(
        postgres_repository,
        topic_config,
        arxiv_record_v1,
        target_record,
        logical_date=later_date,
        session_salt="day-20",
    )
    later_input = postgres_repository.get_product_publication_input(topic_config.id, later_date)
    assert later_input is not None
    later_analysis_id = later_input.papers[0].analysis.analysis.id
    PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: datetime.combine(later_date, NOW.timetz()) + timedelta(minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=later_date,
    )
    corpus = postgres_repository.get_graph_corpus(topic_config.id, as_of_date=later_date)
    assert {
        corpus.mention_activity_dates[item.id]
        for item in corpus.mentions
        if item.analysis_id == later_analysis_id
    } == {later_date}
    assert {
        corpus.edge_activity_dates[item.id]
        for item in corpus.edges
        if item.comparison_id == later_comparison.comparison.id
    } == {later_date}
    seven_day = postgres_repository.list_trends(
        topic_slug=topic_config.slug,
        as_of=later_date,
        windows=(TrendWindow.SEVEN_DAYS,),
        entity_type=None,
        max_entities=50,
    )
    assert len(seven_day) == 1
    assert any(item.change.current_count > 0 for item in seven_day[0].snapshot.entity_counts)


def test_graph_entity_mentions_are_sql_bounded_and_report_exact_totals(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _, logical_date = _prepare_complete_source(postgres_repository, topic_config, arxiv_record_v1)
    PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=1, minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
    )
    paper_id = stable_paper_id(arxiv_record_v1.canonical_arxiv_id)
    initial = postgres_repository.get_graph(
        topic_slug=topic_config.slug,
        as_of=logical_date,
        paper_id=paper_id,
        entity_id=None,
        entity_type=None,
        relation_type=None,
        provenance=None,
        verification_status=None,
        max_nodes=200,
        max_edges=400,
    )
    assert initial is not None
    paper_node = next(node for node in initial.nodes if node.entity.paper_id == paper_id)
    with Session(postgres_engine) as session, session.begin():
        template = session.scalars(
            select(GraphEntityMentionRow)
            .where(
                GraphEntityMentionRow.entity_id == paper_node.entity.id,
                GraphEntityMentionRow.provenance == RelationProvenance.METADATA_EXPLICIT.value,
                GraphEntityMentionRow.analysis_id.is_not(None),
            )
            .order_by(GraphEntityMentionRow.id)
            .limit(1)
        ).one()
        template_analysis = session.get(PaperAnalysisRow, template.analysis_id)
        assert template_analysis is not None
        analysis_ids: list[UUID] = []
        for index in range(105):
            model_version = f"bounded-mention-model-{index:03d}"
            analysis_id = stable_analysis_id(
                template.paper_version_id,
                template_analysis.analysis_scope,
                template_analysis.parsed_paper_id,
                template_analysis.provider,
                template_analysis.configured_model,
                model_version,
                template_analysis.prompt_version,
            )
            analysis_ids.append(analysis_id)
            session.add(
                PaperAnalysisRow(
                    id=analysis_id,
                    paper_id=template_analysis.paper_id,
                    paper_version_id=template_analysis.paper_version_id,
                    parsed_paper_id=template_analysis.parsed_paper_id,
                    analysis_scope=template_analysis.analysis_scope,
                    summary=template_analysis.summary,
                    research_problem=template_analysis.research_problem,
                    method_summary=template_analysis.method_summary,
                    key_contributions=template_analysis.key_contributions,
                    limitations=template_analysis.limitations,
                    provider=template_analysis.provider,
                    configured_model=template_analysis.configured_model,
                    model_version=model_version,
                    prompt_version=template_analysis.prompt_version,
                    generated_at=template_analysis.generated_at + timedelta(seconds=index + 1),
                    source=template_analysis.source,
                    verification_status=template_analysis.verification_status,
                    prompt_tokens=template_analysis.prompt_tokens,
                    completion_tokens=template_analysis.completion_tokens,
                    total_tokens=template_analysis.total_tokens,
                    call_count=template_analysis.call_count,
                    duration_ms=template_analysis.duration_ms,
                    estimated_cost_usd=template_analysis.estimated_cost_usd,
                    schema_version=1,
                    created_at=template_analysis.created_at + timedelta(seconds=index + 1),
                )
            )
        session.flush()
        for index, analysis_id in enumerate(analysis_ids):
            session.add(
                GraphEntityMentionRow(
                    id=stable_graph_entity_mention_id(
                        template.entity_id,
                        template.paper_version_id,
                        analysis_id=analysis_id,
                    ),
                    publication_run_id=template.publication_run_id,
                    topic_id=template.topic_id,
                    entity_id=template.entity_id,
                    paper_id=template.paper_id,
                    paper_version_id=template.paper_version_id,
                    analysis_id=analysis_id,
                    comparison_id=None,
                    observed_label=f"{template.observed_label} alias {index:03d}",
                    provenance=template.provenance,
                    provider=None,
                    configured_model=None,
                    model_version=None,
                    prompt_version=None,
                    confidence=None,
                    verification_status=template.verification_status,
                    generated_at=template.generated_at + timedelta(seconds=index + 1),
                    schema_version=1,
                    created_at=template.created_at + timedelta(seconds=index + 1),
                )
            )
    bounded = postgres_repository.get_graph(
        topic_slug=topic_config.slug,
        as_of=logical_date,
        paper_id=None,
        entity_id=paper_node.entity.id,
        entity_type=None,
        relation_type=None,
        provenance=None,
        verification_status=None,
        max_nodes=1,
        max_edges=1,
    )
    assert bounded is not None
    assert bounded.nodes[0].entity.id == paper_node.entity.id
    assert len(bounded.nodes[0].mentions) == 100
    assert bounded.nodes[0].total_mentions == len(paper_node.mentions) + 105
    assert bounded.total_mentions == bounded.nodes[0].total_mentions
    assert bounded.truncated


def test_finalize_failure_preserves_published_history_and_failed_run_can_restart(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, first_date = _prepare_complete_source(postgres_repository, topic_config, arxiv_record_v1)
    first_run = PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=1, minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=first_date,
    )
    before_graph = postgres_repository.get_graph(
        topic_slug=topic_config.slug,
        as_of=None,
        paper_id=None,
        entity_type=None,
        relation_type=None,
        provenance=None,
        verification_status=None,
        max_nodes=200,
        max_edges=400,
    )
    assert before_graph is not None
    before_corpus = postgres_repository.get_graph_corpus(topic_config.id, as_of_date=first_date)
    _, revised_date = _prepare_revised_source(postgres_repository, topic_config, arxiv_record_v1)
    original_finalize = postgres_repository.finalize_product_publication
    missing_trend_id = UUID("972710e3-3fcb-4f04-b6b4-29ae98735722")

    def failing_finalize(*, run_id: UUID, report: Report, completed_at: datetime) -> DailyRun:
        invalid_report = replace(
            report,
            trend_snapshot_ids=(missing_trend_id, *report.trend_snapshot_ids[1:]),
        )
        return original_finalize(
            run_id=run_id,
            report=invalid_report,
            completed_at=completed_at,
        )

    monkeypatch.setattr(
        postgres_repository,
        "finalize_product_publication",
        failing_finalize,
    )
    with pytest.raises(RepositoryError, match="trend-snapshot ownership"):
        PublishProduct(
            repository=postgres_repository,
            llm=None,
            clock=lambda: NOW + timedelta(days=2, minutes=10),
        ).execute(
            topic_config,
            narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
            logical_date=revised_date,
        )
    failed = postgres_repository.get_product_run(
        logical_date=revised_date,
        topic_slug=topic_config.slug,
    )
    assert failed is not None
    assert failed.run.status is RunStatus.FAILED
    assert failed.report is None
    assert (
        postgres_repository.get_graph(
            topic_slug=topic_config.slug,
            as_of=None,
            paper_id=None,
            entity_type=None,
            relation_type=None,
            provenance=None,
            verification_status=None,
            max_nodes=200,
            max_edges=400,
        )
        == before_graph
    )
    assert (
        postgres_repository.list_trends(
            topic_slug=topic_config.slug,
            as_of=revised_date,
            windows=tuple(TrendWindow),
        )
        == ()
    )
    lineage = postgres_repository.get_lineage(
        stable_paper_id(arxiv_record_v1.canonical_arxiv_id),
        topic_slug=topic_config.slug,
        max_depth=5,
        max_nodes=100,
        max_edges=200,
    )
    assert lineage is not None
    assert lineage.snapshot.as_of_date == first_date
    after_failed_corpus = postgres_repository.get_graph_corpus(
        topic_config.id, as_of_date=revised_date
    )
    assert {item.id for item in after_failed_corpus.entities} == {
        item.id for item in before_corpus.entities
    }
    assert {item.id for item in after_failed_corpus.mentions} == {
        item.id for item in before_corpus.mentions
    }
    assert {item.id for item in after_failed_corpus.edges} == {
        item.id for item in before_corpus.edges
    }
    assert (
        postgres_repository.list_reports(
            report_type=ReportType.DAILY,
            topic_slug=topic_config.slug,
            limit=10,
            offset=0,
        )[1]
        == 1
    )
    monkeypatch.setattr(
        postgres_repository,
        "finalize_product_publication",
        original_finalize,
    )
    restarted = PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=2, minutes=20),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=revised_date,
    )
    assert restarted.id == failed.run.id
    assert restarted.id != first_run.id
    assert restarted.status is RunStatus.COMPLETE


@pytest.mark.parametrize("invalid_context", ("paper", "comparison"))
def test_report_evidence_owner_failure_rolls_back_daily_publication(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
    monkeypatch: pytest.MonkeyPatch,
    invalid_context: str,
) -> None:
    target_record, logical_date = _prepare_complete_source(
        postgres_repository, topic_config, arxiv_record_v1
    )
    unrelated_comparison_evidence_id: UUID | None = None
    if invalid_context == "comparison":
        target_paper_id = stable_paper_id(target_record.canonical_arxiv_id)
        target_version_id = stable_paper_version_id(target_record.canonical_arxiv_id, 1)
        AnalyzePapers(
            arxiv=FakeArxiv(),
            parser=None,
            llm=RevisedGroundedAnalysisLLM(),
            repository=postgres_repository,
            clock=lambda: NOW + timedelta(days=2, minutes=2),
        ).execute(
            topic_config,
            paper_ids=(target_paper_id,),
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
            logical_date=logical_date + timedelta(days=1),
        )
        revised_target = postgres_repository.get_paper_analysis(
            target_paper_id,
            paper_version_id=target_version_id,
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        )
        assert revised_target is not None
        unrelated_comparison_evidence_id = revised_target.evidence[0].id
    original_finalize = postgres_repository.finalize_product_publication

    def invalid_finalize(*, run_id: UUID, report: Report, completed_at: datetime) -> DailyRun:
        if invalid_context == "paper":
            paper_highlight = report.highlighted_papers[0]
            target_evidence_id = next(
                evidence_id
                for evidence_id in report.notable_comparisons[0].evidence_ids
                if evidence_id not in paper_highlight.evidence_ids
            )
            invalid_report = report
            object.__setattr__(paper_highlight, "evidence_ids", (target_evidence_id,))
        else:
            assert unrelated_comparison_evidence_id is not None
            comparison_highlight = report.notable_comparisons[0]
            invalid_report = report
            object.__setattr__(
                invalid_report,
                "evidence_ids",
                tuple(dict.fromkeys((*report.evidence_ids, unrelated_comparison_evidence_id))),
            )
            object.__setattr__(
                comparison_highlight,
                "evidence_ids",
                (unrelated_comparison_evidence_id,),
            )
        return original_finalize(
            run_id=run_id,
            report=invalid_report,
            completed_at=completed_at,
        )

    monkeypatch.setattr(
        postgres_repository,
        "finalize_product_publication",
        invalid_finalize,
    )
    expected = (
        "paper-highlight evidence has the wrong paper-version owner"
        if invalid_context == "paper"
        else "comparison-highlight evidence is outside its persisted comparison"
    )
    with pytest.raises(RepositoryError, match=expected):
        PublishProduct(
            repository=postgres_repository,
            llm=None,
            clock=lambda: NOW + timedelta(days=2, minutes=10),
        ).execute(
            topic_config,
            narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
            logical_date=logical_date,
        )
    failed = postgres_repository.get_product_run(
        logical_date=logical_date,
        topic_slug=topic_config.slug,
    )
    assert failed is not None
    assert failed.run.status is RunStatus.FAILED
    assert failed.report is None
    assert (
        postgres_repository.list_reports(
            report_type=ReportType.DAILY,
            topic_slug=topic_config.slug,
            limit=10,
            offset=0,
        )[1]
        == 0
    )


def test_periodic_report_inherits_evidence_owner_validation(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _, logical_date = _prepare_complete_source(postgres_repository, topic_config, arxiv_record_v1)
    PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=1, minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
    )
    detail = postgres_repository.get_product_run(
        logical_date=logical_date,
        topic_slug=topic_config.slug,
    )
    assert detail is not None
    assert detail.report is not None
    daily = detail.report.report
    periodic_id = UUID("257c0345-012f-48f6-8835-60a060d3ed05")
    paper_highlight = daily.highlighted_papers[0]
    target_evidence_id = next(
        evidence_id
        for evidence_id in daily.notable_comparisons[0].evidence_ids
        if evidence_id not in paper_highlight.evidence_ids
    )
    invalid_periodic = replace(
        daily,
        id=periodic_id,
        run_id=None,
        report_type=ReportType.WEEKLY,
        period_start=logical_date - timedelta(days=6),
        period_end=logical_date,
        sections=tuple(replace(item, report_id=periodic_id) for item in daily.sections),
    )
    object.__setattr__(
        invalid_periodic.highlighted_papers[0],
        "evidence_ids",
        (target_evidence_id,),
    )
    with pytest.raises(
        RepositoryError,
        match="paper-highlight evidence has the wrong paper-version owner",
    ):
        postgres_repository.persist_periodic_report(invalid_periodic)
    assert (
        postgres_repository.get_report(
            report_type=ReportType.WEEKLY,
            period_start=logical_date - timedelta(days=6),
            period_end=logical_date,
            topic_slug=topic_config.slug,
        )
        is None
    )
