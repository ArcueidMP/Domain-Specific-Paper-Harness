# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid5

import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeArxiv, FakeRepository

from paper_harness.application.analyze_papers import build_analysis_bundle
from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.application.read_models import (
    AnalysisDetail,
    ComparisonDetail,
    ComparisonEvidenceReference,
    PaperDetail,
    RelatedWorkDetail,
    RelatedWorkItem,
)
from paper_harness.domain.analysis import (
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
from paper_harness.domain.identity import stable_paper_id, stable_paper_version_id
from paper_harness.domain.models import Paper, PaperVersion, TopicConfig
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
        summary="The paper evaluates a tool-using language model agent.",
        research_problem="Tool-using agents require reliable evaluation.",
        method_summary="The authors evaluate a tool-using agent.",
        key_contributions=("A focused agent evaluation.",),
        limitations=("The abstract does not describe every benchmark.",),
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
                passage_id="abstract",
                excerpt="evaluate a tool-using language model agent",
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
    repository.comparisons[comparison.comparison.id] = comparison
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
        def get_comparison(self, comparison_id: UUID) -> ComparisonDetail | None:
            del comparison_id
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
        ) -> RelatedWorkDetail | None:
            del paper_id, paper_version_id
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


def test_checked_in_openapi_is_generated_from_fastapi() -> None:
    expected = json.loads(Path("apps/api/openapi.json").read_text(encoding="utf-8"))
    assert create_app(FakeRepository()).openapi() == expected
