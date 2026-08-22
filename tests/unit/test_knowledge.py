from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from paper_harness.domain.analysis import (
    AnalysisBundle,
    AnalysisClaim,
    AnalysisScope,
    ClaimType,
    Evidence,
    EvidenceType,
    ModelUsage,
    PaperAnalysis,
    VerificationStatus,
)
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import RelationProvenance
from paper_harness.domain.identity import (
    stable_graph_edge_id,
    stable_graph_entity_id,
    stable_graph_entity_mention_id,
    stable_graph_paper_entity_id,
)
from paper_harness.domain.knowledge import (
    GraphEdge,
    GraphEntity,
    GraphEntityMention,
    GraphEntityType,
    GraphModelProvenance,
    GraphReferenceSet,
    GraphRelationType,
    KnowledgeGraphBundle,
    LineageCorpusScope,
    LineagePaper,
    TrendDataSufficiency,
    TrendGrowthStatus,
    TrendPaperRecord,
    TrendWindow,
    aggregate_trend_snapshots,
    build_lineage_snapshot,
    extract_analysis_graph,
    graph_entity_keys_match,
    merge_knowledge_graph_bundles,
    normalize_entity_label,
    normalized_entity_key,
)

NOW = datetime(2026, 8, 10, 5, tzinfo=UTC)
AS_OF = date(2026, 8, 10)
TOPIC_ID = UUID("d7777dad-6e1d-431e-8d92-5676d2a4aed9")


def _analysis_bundle() -> AnalysisBundle:
    analysis_id = UUID("c5762b1f-c44f-43c1-812f-02ce7cac1c6f")
    paper_id = UUID("61a1c31e-35d5-4c9a-aa11-2910c9824b7c")
    version_id = UUID("a679a95f-264b-4943-b8db-d6bc17a6a1e6")
    claims = (
        AnalysisClaim(
            id=UUID("3095c100-418e-44b3-be69-cb25617019ba"),
            analysis_id=analysis_id,
            paper_id=paper_id,
            paper_version_id=version_id,
            key="problem",
            claim_type=ClaimType.RESEARCH_PROBLEM,
            text="Tool-using agents need reliable planning.",
            provider="deepseek",
            model_version="deepseek-v4-flash-20260801",
            prompt_version="analysis-v1",
            generated_at=NOW,
            source="deepseek_structured_analysis",
            verification_status=VerificationStatus.UNVERIFIED,
            schema_version=1,
            created_at=NOW,
        ),
        AnalysisClaim(
            id=UUID("1bffbf7e-8713-44cc-ace5-41190b80875f"),
            analysis_id=analysis_id,
            paper_id=paper_id,
            paper_version_id=version_id,
            key="method",
            claim_type=ClaimType.METHOD,
            text="The paper introduces a constrained planner.",
            provider="deepseek",
            model_version="deepseek-v4-flash-20260801",
            prompt_version="analysis-v1",
            generated_at=NOW,
            source="deepseek_structured_analysis",
            verification_status=VerificationStatus.UNVERIFIED,
            schema_version=1,
            created_at=NOW,
        ),
    )
    evidence = tuple(
        Evidence(
            id=evidence_id,
            analysis_id=analysis_id,
            paper_id=paper_id,
            paper_version_id=version_id,
            key=key,
            section="Abstract",
            passage_id=f"passage-{key}",
            coordinates=(),
            excerpt=excerpt,
            evidence_type=EvidenceType.SUPPORTS,
            supported_claim_ids=(claim.id,),
            extraction_source="deepseek_grounded_extraction",
            provider="deepseek",
            model_version="deepseek-v4-flash-20260801",
            prompt_version="analysis-v1",
            generated_at=NOW,
            verification_status=VerificationStatus.UNVERIFIED,
            schema_version=1,
            created_at=NOW,
        )
        for evidence_id, key, excerpt, claim in (
            (
                UUID("6e0582fc-f123-441e-8ff5-24cd48ff41f0"),
                "problem-evidence",
                "Planning failures remain common in tool-using agents.",
                claims[0],
            ),
            (
                UUID("b1e94334-e164-4982-a5ca-00b323da5263"),
                "method-evidence",
                "We introduce a constrained planning method.",
                claims[1],
            ),
        )
    )
    return AnalysisBundle(
        analysis=PaperAnalysis(
            id=analysis_id,
            paper_id=paper_id,
            paper_version_id=version_id,
            parsed_paper_id=None,
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
            summary="A constrained planner for reliable tool use.",
            research_problem="Reliable planning for tool-using agents",
            method_summary="Constrained agent planner",
            key_contributions=("A bounded planning strategy.",),
            limitations=("Evaluation is limited to two tasks.",),
            provider="deepseek",
            configured_model="deepseek-v4-flash",
            model_version="deepseek-v4-flash-20260801",
            prompt_version="analysis-v1",
            generated_at=NOW,
            source="deepseek_structured_analysis",
            verification_status=VerificationStatus.UNVERIFIED,
            usage=ModelUsage(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                call_count=1,
                duration_ms=500,
                estimated_cost_usd=Decimal("0.001"),
            ),
            schema_version=1,
            created_at=NOW,
        ),
        claims=claims,
        evidence=evidence,
    )


def _paper_entity(topic_id: UUID, paper_id: UUID, title: str = "Paper") -> GraphEntity:
    return GraphEntity(
        id=stable_graph_paper_entity_id(topic_id, paper_id),
        topic_id=topic_id,
        entity_type=GraphEntityType.PAPER,
        paper_id=paper_id,
        canonical_label=title,
        normalized_key=f"paper:{paper_id}",
        display_label=title,
        aliases=(title,),
        provenance=RelationProvenance.METADATA_EXPLICIT,
        source="paper_metadata",
        schema_version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _named_entity(entity_type: GraphEntityType, label: str) -> GraphEntity:
    key = normalized_entity_key(label)
    return GraphEntity(
        id=stable_graph_entity_id(TOPIC_ID, entity_type.value, key),
        topic_id=TOPIC_ID,
        entity_type=entity_type,
        paper_id=None,
        canonical_label=label,
        normalized_key=key,
        display_label=label,
        aliases=(label,),
        provenance=RelationProvenance.DETERMINISTICALLY_DERIVED,
        source="canonical_entity_key_v1",
        schema_version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _mention(
    entity: GraphEntity,
    paper_id: UUID,
    paper_version_id: UUID,
    analysis_id: UUID,
) -> GraphEntityMention:
    return GraphEntityMention(
        id=stable_graph_entity_mention_id(
            entity.id,
            paper_version_id,
            analysis_id=analysis_id,
        ),
        entity_id=entity.id,
        paper_id=paper_id,
        paper_version_id=paper_version_id,
        analysis_id=analysis_id,
        comparison_id=None,
        observed_label=entity.display_label,
        provenance=RelationProvenance.DETERMINISTICALLY_DERIVED,
        evidence_ids=(),
        model_provenance=None,
        confidence=None,
        verification_status=VerificationStatus.UNVERIFIED,
        generated_at=NOW,
        schema_version=1,
        created_at=NOW,
    )


def _entity_edge(
    paper_entity: GraphEntity,
    target_entity: GraphEntity,
    paper_version_id: UUID,
    analysis_id: UUID,
    relation_type: GraphRelationType = GraphRelationType.USES_METHOD,
) -> GraphEdge:
    return GraphEdge(
        id=stable_graph_edge_id(
            paper_entity.id,
            target_entity.id,
            relation_type.value,
            paper_version_id,
            analysis_id=analysis_id,
        ),
        source_entity_id=paper_entity.id,
        target_entity_id=target_entity.id,
        relation_type=relation_type,
        source_paper_version_id=paper_version_id,
        target_paper_version_id=None,
        analysis_id=analysis_id,
        comparison_id=None,
        paper_relation_id=None,
        provenance=RelationProvenance.DETERMINISTICALLY_DERIVED,
        evidence_ids=(),
        justification="Deterministically projected from persisted structured data.",
        model_provenance=None,
        confidence=None,
        verification_status=VerificationStatus.UNVERIFIED,
        generated_at=NOW,
        schema_version=1,
        created_at=NOW,
    )


def _paper_edge(
    source_paper_id: UUID,
    target_paper_id: UUID,
    source_version_id: UUID,
    target_version_id: UUID,
    relation_type: GraphRelationType,
    *,
    inferred: bool = False,
) -> GraphEdge:
    source_entity_id = stable_graph_paper_entity_id(TOPIC_ID, source_paper_id)
    target_entity_id = stable_graph_paper_entity_id(TOPIC_ID, target_paper_id)
    comparison_id = uuid4()
    relation_id = uuid4()
    evidence_ids = (uuid4(),) if inferred else ()
    return GraphEdge(
        id=stable_graph_edge_id(
            source_entity_id,
            target_entity_id,
            relation_type.value,
            source_version_id,
            target_paper_version_id=target_version_id,
            comparison_id=comparison_id,
            paper_relation_id=relation_id,
        ),
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relation_type=relation_type,
        source_paper_version_id=source_version_id,
        target_paper_version_id=target_version_id,
        analysis_id=None,
        comparison_id=comparison_id,
        paper_relation_id=relation_id,
        provenance=(
            RelationProvenance.LLM_INFERRED if inferred else RelationProvenance.METADATA_EXPLICIT
        ),
        evidence_ids=evidence_ids,
        justification="Available evidence supports this bounded corpus relation.",
        model_provenance=(
            GraphModelProvenance(
                provider="deepseek",
                configured_model="deepseek-v4-flash",
                model_version="deepseek-v4-flash-20260801",
                prompt_version="comparison-v1",
            )
            if inferred
            else None
        ),
        confidence=0.7 if inferred else None,
        verification_status=VerificationStatus.UNVERIFIED,
        generated_at=NOW,
        schema_version=1,
        created_at=NOW,
    )


def test_entity_normalization_is_conservative_and_exact() -> None:
    assert normalize_entity_label("  Ａgent\u00a0Planning  ") == "Agent Planning"
    assert graph_entity_keys_match("Agent—Planning", "agent-planning")
    assert not graph_entity_keys_match("Agent-Planning", "Agent Planning")
    assert not graph_entity_keys_match("AgentBench.", "AgentBench")
    assert normalize_entity_label("界" * 500) == "界" * 500
    with pytest.raises(DomainInvariantError, match="persistence bound"):
        normalize_entity_label("界" * 501)


def test_graph_entity_ids_are_stable_and_topic_scoped() -> None:
    key = normalized_entity_key("Agent Planning")
    another_topic = UUID("a2af590a-59d8-4c12-8654-82301897c9fd")

    assert stable_graph_entity_id(TOPIC_ID, GraphEntityType.METHOD.value, key) == (
        stable_graph_entity_id(TOPIC_ID, GraphEntityType.METHOD.value, key)
    )
    assert stable_graph_entity_id(TOPIC_ID, GraphEntityType.METHOD.value, key) != (
        stable_graph_entity_id(another_topic, GraphEntityType.METHOD.value, key)
    )


def test_analysis_extraction_is_evidence_aware_and_idempotent() -> None:
    analysis_bundle = _analysis_bundle()

    first = extract_analysis_graph(TOPIC_ID, analysis_bundle, paper_title="Planner Paper")
    second = extract_analysis_graph(TOPIC_ID, analysis_bundle, paper_title="Planner Paper")
    merged = merge_knowledge_graph_bundles((first.bundle, second.bundle))

    assert first == second
    assert len(first.bundle.entities) == 3
    assert len(first.bundle.mentions) == 3
    assert {edge.relation_type for edge in first.bundle.edges} == {
        GraphRelationType.ADDRESSES,
        GraphRelationType.USES_METHOD,
    }
    assert all(edge.evidence_ids for edge in first.bundle.edges)
    assert all(edge.model_provenance is not None for edge in first.bundle.edges)
    assert len(merged.entities) == len(first.bundle.entities)
    assert len(merged.mentions) == len(first.bundle.mentions)
    assert len(merged.edges) == len(first.bundle.edges)
    assert set(first.omitted_entity_types) == {
        GraphEntityType.TASK,
        GraphEntityType.DATASET,
        GraphEntityType.BENCHMARK,
    }


def test_extraction_keeps_long_paper_titles_but_omits_oversized_concept_prose() -> None:
    source = _analysis_bundle()
    result = extract_analysis_graph(
        TOPIC_ID,
        replace(source, analysis=replace(source.analysis, method_summary="界" * 501)),
        paper_title="T" * 1000,
    )

    paper_entity = next(
        item for item in result.bundle.entities if item.entity_type is GraphEntityType.PAPER
    )
    assert len(paper_entity.display_label) == 1000
    assert (
        len(
            TrendPaperRecord(
                paper_id=source.analysis.paper_id,
                paper_version_id=source.analysis.paper_version_id,
                activity_date=AS_OF,
                title="T" * 1000,
            ).title
        )
        == 1000
    )
    assert GraphEntityType.METHOD in result.omitted_entity_types
    assert all(item.entity_type is not GraphEntityType.METHOD for item in result.bundle.entities)


def test_analysis_extraction_never_references_rejected_evidence() -> None:
    source = _analysis_bundle()
    rejected = replace(
        source,
        evidence=tuple(
            replace(item, verification_status=VerificationStatus.REJECTED)
            for item in source.evidence
        ),
    )

    result = extract_analysis_graph(TOPIC_ID, rejected, paper_title="Planner Paper")

    assert result.bundle.references.evidence_ids == ()
    assert {item.entity_type for item in result.bundle.entities} == {GraphEntityType.PAPER}
    assert set(result.omitted_entity_types) == {
        GraphEntityType.RESEARCH_PROBLEM,
        GraphEntityType.METHOD,
        GraphEntityType.TASK,
        GraphEntityType.DATASET,
        GraphEntityType.BENCHMARK,
    }


def test_bundle_rejects_orphan_evidence_and_self_relations() -> None:
    extracted = extract_analysis_graph(
        TOPIC_ID,
        _analysis_bundle(),
        paper_title="Planner Paper",
    ).bundle
    with pytest.raises(DomainInvariantError, match="unknown evidence"):
        KnowledgeGraphBundle(
            topic_id=extracted.topic_id,
            entities=extracted.entities,
            mentions=extracted.mentions,
            edges=extracted.edges,
            references=replace(extracted.references, evidence_ids=()),
        )

    entity_id = extracted.entities[0].id
    with pytest.raises(DomainInvariantError, match="self-relations"):
        stable_graph_edge_id(
            entity_id,
            entity_id,
            GraphRelationType.CITES.value,
            uuid4(),
            target_paper_version_id=uuid4(),
            comparison_id=uuid4(),
            paper_relation_id=uuid4(),
        )


def test_llm_inferred_edges_require_evidence_model_and_confidence() -> None:
    source_paper = _paper_entity(TOPIC_ID, uuid4())
    target_paper = _paper_entity(TOPIC_ID, uuid4())
    source_version_id = uuid4()
    target_version_id = uuid4()
    comparison_id = uuid4()
    relation_id = uuid4()

    with pytest.raises(DomainInvariantError, match="require evidence"):
        GraphEdge(
            id=stable_graph_edge_id(
                source_paper.id,
                target_paper.id,
                GraphRelationType.EXTENDS.value,
                source_version_id,
                target_paper_version_id=target_version_id,
                comparison_id=comparison_id,
                paper_relation_id=relation_id,
            ),
            source_entity_id=source_paper.id,
            target_entity_id=target_paper.id,
            relation_type=GraphRelationType.EXTENDS,
            source_paper_version_id=source_version_id,
            target_paper_version_id=target_version_id,
            analysis_id=None,
            comparison_id=comparison_id,
            paper_relation_id=relation_id,
            provenance=RelationProvenance.LLM_INFERRED,
            evidence_ids=(),
            justification="An unsupported inference.",
            model_provenance=None,
            confidence=None,
            verification_status=VerificationStatus.UNVERIFIED,
            generated_at=NOW,
            schema_version=1,
            created_at=NOW,
        )


def test_lineage_is_chronological_bounded_cycle_safe_and_honest() -> None:
    paper_ids = tuple(uuid4() for _ in range(5))
    version_ids = tuple(uuid4() for _ in range(5))
    papers = tuple(
        LineagePaper(
            graph_entity_id=stable_graph_paper_entity_id(TOPIC_ID, paper_id),
            paper_id=paper_id,
            title=f"Paper {index}",
            publication_date=(AS_OF - timedelta(days=20 - index)) if index < 4 else None,
        )
        for index, paper_id in enumerate(paper_ids)
    )
    edges = (
        _paper_edge(
            paper_ids[2],
            paper_ids[1],
            version_ids[2],
            version_ids[1],
            GraphRelationType.EXTENDS,
            inferred=True,
        ),
        _paper_edge(
            paper_ids[1],
            paper_ids[0],
            version_ids[1],
            version_ids[0],
            GraphRelationType.CITES,
        ),
        _paper_edge(
            paper_ids[0],
            paper_ids[2],
            version_ids[0],
            version_ids[2],
            GraphRelationType.COMPARES_WITH,
            inferred=True,
        ),
        _paper_edge(
            paper_ids[3],
            paper_ids[2],
            version_ids[3],
            version_ids[2],
            GraphRelationType.EXTENDS,
        ),
        _paper_edge(
            paper_ids[2],
            paper_ids[3],
            version_ids[2],
            version_ids[3],
            GraphRelationType.CITES,
        ),
        _paper_edge(
            paper_ids[1],
            paper_ids[4],
            version_ids[1],
            version_ids[4],
            GraphRelationType.CONTRADICTS,
        ),
    )

    snapshot = build_lineage_snapshot(
        TOPIC_ID,
        paper_ids[2],
        as_of_date=AS_OF,
        papers=papers,
        edges=edges,
        generated_at=NOW,
        max_depth=2,
        max_nodes=3,
    )

    assert snapshot.corpus_scope is LineageCorpusScope.CURRENTLY_RETRIEVED_CORPUS
    assert len(snapshot.nodes) == 3
    assert not snapshot.truncated
    assert {node.paper_id for node in snapshot.nodes} == set(paper_ids[:3])
    assert [node.publication_date for node in snapshot.nodes] == sorted(
        node.publication_date for node in snapshot.nodes if node.publication_date is not None
    )
    assert {edge.relation_type for edge in snapshot.edges} == {
        GraphRelationType.CITES,
        GraphRelationType.EXTENDS,
    }
    assert any(edge.provenance is RelationProvenance.LLM_INFERRED for edge in snapshot.edges)
    assert any("currently retrieved corpus" in item for item in snapshot.limitations)
    assert any("AI-inferred" in item for item in snapshot.limitations)
    assert any("later-dated" in item for item in snapshot.limitations)

    with pytest.raises(DomainInvariantError, match="unreachable"):
        replace(snapshot, edges=())

    edge_bounded = build_lineage_snapshot(
        TOPIC_ID,
        paper_ids[2],
        as_of_date=AS_OF,
        papers=papers,
        edges=edges,
        generated_at=NOW,
        max_depth=3,
        max_nodes=5,
        max_edges=1,
    )
    assert edge_bounded.truncated
    assert edge_bounded.max_edges == 1
    assert len(edge_bounded.edges) == 1
    assert len(edge_bounded.nodes) == 2


def _trend_records() -> tuple[
    tuple[TrendPaperRecord, ...],
    tuple[GraphEntity, ...],
    tuple[GraphEntityMention, ...],
    tuple[GraphEdge, ...],
    GraphEntity,
]:
    method = _named_entity(GraphEntityType.METHOD, "Constrained Planner")
    task = _named_entity(GraphEntityType.TASK, "Browser Navigation")
    papers: list[TrendPaperRecord] = []
    entities: list[GraphEntity] = [method, task]
    mentions: list[GraphEntityMention] = []
    edges: list[GraphEdge] = []
    for index in range(13):
        paper_id = uuid4()
        version_id = uuid4()
        analysis_id = uuid4()
        activity_date = (
            AS_OF - timedelta(days=index % 7)
            if index < 10
            else (AS_OF - timedelta(days=7 + index - 10))
        )
        paper_entity = _paper_entity(TOPIC_ID, paper_id, title=f"Trend Paper {index}")
        papers.append(
            TrendPaperRecord(
                paper_id=paper_id,
                paper_version_id=version_id,
                activity_date=activity_date,
                title=paper_entity.display_label,
            )
        )
        entities.append(paper_entity)
        mentions.append(_mention(method, paper_id, version_id, analysis_id))
        edges.append(_entity_edge(paper_entity, method, version_id, analysis_id))
        if index < 2:
            mentions.append(_mention(task, paper_id, version_id, analysis_id))
            edges.append(
                _entity_edge(
                    paper_entity,
                    task,
                    version_id,
                    analysis_id,
                    GraphRelationType.TARGETS_TASK,
                )
            )
    return tuple(papers), tuple(entities), tuple(mentions), tuple(edges), task


def test_trends_use_equal_preceding_windows_and_explicit_growth_statuses() -> None:
    papers, entities, mentions, edges, task = _trend_records()
    activity_dates = {item.paper_version_id: item.activity_date for item in papers}
    mention_activity_dates = {item.id: activity_dates[item.paper_version_id] for item in mentions}
    edge_activity_dates = {item.id: activity_dates[item.source_paper_version_id] for item in edges}

    snapshots = aggregate_trend_snapshots(
        TOPIC_ID,
        as_of_date=AS_OF,
        papers=papers,
        entities=entities,
        mentions=mentions,
        edges=edges,
        mention_activity_dates=mention_activity_dates,
        edge_activity_dates=edge_activity_dates,
        generated_at=NOW,
    )
    repeated = aggregate_trend_snapshots(
        TOPIC_ID,
        as_of_date=AS_OF,
        papers=papers,
        entities=reversed(entities),
        mentions=reversed(mentions),
        edges=reversed(edges),
        mention_activity_dates=mention_activity_dates,
        edge_activity_dates=edge_activity_dates,
        generated_at=NOW,
    )

    assert snapshots == repeated
    assert tuple(item.window for item in snapshots) == tuple(TrendWindow)
    seven_day = snapshots[0]
    assert seven_day.window_start == AS_OF - timedelta(days=6)
    assert seven_day.preceding_window_end == seven_day.window_start - timedelta(days=1)
    assert seven_day.preceding_window_start == seven_day.window_start - timedelta(days=7)
    assert seven_day.included_paper_count == 10
    assert seven_day.preceding_paper_count == 3
    assert seven_day.data_sufficiency is TrendDataSufficiency.SUFFICIENT
    assert seven_day.preceding_data_sufficiency is TrendDataSufficiency.LIMITED
    assert seven_day.paper_count_change.growth_status is TrendGrowthStatus.AVAILABLE
    task_count = next(item for item in seven_day.entity_counts if item.entity_id == task.id)
    assert task_count.newly_appearing
    assert task_count.change.denominator_count == 0
    assert task_count.change.relative_change is None
    assert task_count.change.growth_status is TrendGrowthStatus.ZERO_DENOMINATOR
    assert GraphRelationType.USES_METHOD in {
        item.relation_type for item in seven_day.relation_counts
    }
    assert len(seven_day.representative_paper_ids) == 5


def test_tiny_samples_do_not_emit_misleading_relative_growth() -> None:
    papers, entities, mentions, edges, _task = _trend_records()
    tiny_papers = papers[:1] + papers[10:11]
    tiny_versions = {item.paper_version_id for item in tiny_papers}
    tiny_mentions = tuple(item for item in mentions if item.paper_version_id in tiny_versions)
    tiny_edges = tuple(item for item in edges if item.source_paper_version_id in tiny_versions)
    activity_dates = {item.paper_version_id: item.activity_date for item in tiny_papers}

    seven_day = aggregate_trend_snapshots(
        TOPIC_ID,
        as_of_date=AS_OF,
        papers=tiny_papers,
        entities=entities,
        mentions=tiny_mentions,
        edges=tiny_edges,
        mention_activity_dates={
            item.id: activity_dates[item.paper_version_id] for item in tiny_mentions
        },
        edge_activity_dates={
            item.id: activity_dates[item.source_paper_version_id] for item in tiny_edges
        },
        generated_at=NOW,
        windows=(TrendWindow.SEVEN_DAYS,),
    )[0]

    assert seven_day.data_sufficiency is TrendDataSufficiency.INSUFFICIENT
    assert seven_day.paper_count_change.growth_status is TrendGrowthStatus.LIMITED_SAMPLE
    assert seven_day.paper_count_change.relative_change is None


def test_trends_use_occurrence_activity_without_reclassifying_paper_volume() -> None:
    papers, entities, mentions, edges, task = _trend_records()
    old_paper = papers[10]
    occurrence_date = AS_OF - timedelta(days=1)
    selected_mentions = tuple(
        item for item in mentions if item.paper_version_id == old_paper.paper_version_id
    )
    selected_edges = tuple(
        item for item in edges if item.source_paper_version_id == old_paper.paper_version_id
    )

    seven_day = aggregate_trend_snapshots(
        TOPIC_ID,
        as_of_date=AS_OF,
        papers=(old_paper,),
        entities=entities,
        mentions=selected_mentions,
        edges=selected_edges,
        mention_activity_dates={item.id: occurrence_date for item in selected_mentions},
        edge_activity_dates={item.id: occurrence_date for item in selected_edges},
        generated_at=NOW,
        windows=(TrendWindow.SEVEN_DAYS,),
    )[0]

    assert seven_day.included_paper_count == 0
    assert seven_day.preceding_paper_count == 1
    assert old_paper.paper_id in seven_day.representative_paper_ids
    method_count = next(
        item for item in seven_day.entity_counts if item.entity_type is GraphEntityType.METHOD
    )
    assert method_count.change.current_count == 1
    assert method_count.change.preceding_count == 0
    assert all(item.entity_id != task.id for item in seven_day.entity_counts)
    assert any(
        item.relation_type is GraphRelationType.USES_METHOD and item.change.current_count == 1
        for item in seven_day.relation_counts
    )


def test_trend_occurrence_activity_requires_known_ids_and_ignores_extras() -> None:
    papers, entities, mentions, edges, _task = _trend_records()

    with pytest.raises(DomainInvariantError, match="missing mention activity"):
        aggregate_trend_snapshots(
            TOPIC_ID,
            as_of_date=AS_OF,
            papers=papers,
            entities=entities,
            mentions=mentions,
            edges=edges,
            mention_activity_dates={},
            edge_activity_dates={item.id: AS_OF for item in edges},
            generated_at=NOW,
        )

    snapshots = aggregate_trend_snapshots(
        TOPIC_ID,
        as_of_date=AS_OF,
        papers=papers,
        entities=entities,
        mentions=mentions,
        edges=edges,
        mention_activity_dates={
            **{item.id: AS_OF for item in mentions},
            UUID(int=999): AS_OF,
        },
        edge_activity_dates={
            **{item.id: AS_OF for item in edges},
            UUID(int=1000): AS_OF,
        },
        generated_at=NOW,
    )
    assert len(snapshots) == 3


def test_bundle_deduplicates_exact_records_but_rejects_conflicts() -> None:
    extracted = extract_analysis_graph(
        TOPIC_ID,
        _analysis_bundle(),
        paper_title="Planner Paper",
    ).bundle
    duplicated = KnowledgeGraphBundle(
        topic_id=TOPIC_ID,
        entities=extracted.entities + extracted.entities,
        mentions=extracted.mentions + extracted.mentions,
        edges=extracted.edges + extracted.edges,
        references=GraphReferenceSet(
            paper_version_ids=extracted.references.paper_version_ids * 2,
            analysis_ids=extracted.references.analysis_ids * 2,
            evidence_ids=extracted.references.evidence_ids * 2,
        ),
    )
    assert duplicated == extracted

    conflicting_mention = replace(extracted.mentions[0], observed_label="Another Paper Title")
    with pytest.raises(DomainInvariantError, match="conflicting graph mention"):
        KnowledgeGraphBundle(
            topic_id=TOPIC_ID,
            entities=extracted.entities,
            mentions=extracted.mentions + (conflicting_mention,),
            edges=extracted.edges,
            references=extracted.references,
        )
