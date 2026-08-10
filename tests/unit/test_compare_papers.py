from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from paper_harness.application.compare_papers import ComparePapers, ComparisonInputMissingError
from paper_harness.application.read_models import SearchSessionDetail
from paper_harness.domain.analysis import (
    AnalysisScope,
    GeneratedAnalysis,
    ModelUsage,
    VerificationStatus,
)
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import (
    COMPARISON_DIMENSION_ORDER,
    CandidateOrigin,
    CandidateScoreComponents,
    CandidateSelectionRequest,
    ComparabilityStatus,
    ComparisonEvidenceInput,
    ComparisonPaperInput,
    ComparisonRequest,
    CrawlerPlanRequest,
    GeneratedCandidateSelection,
    GeneratedComparison,
    GeneratedComparisonDimension,
    GeneratedCrawlerPlan,
    GeneratedRelation,
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
from paper_harness.domain.identity import stable_comparison_id
from paper_harness.domain.reports import GeneratedReportNarrative, ReportNarrativeRequest
from paper_harness.ports.llm import LLMPort
from paper_harness.ports.repository import RepositoryPort

NOW = datetime(2026, 8, 9, 5, tzinfo=UTC)
SESSION_ID = UUID("152e6a5a-e838-45a4-899b-d95f286f280e")
SOURCE_PAPER_ID = UUID("be1064f6-0fc1-4457-9145-fb3cc765279d")
SOURCE_VERSION_ID = UUID("05f68135-e3b3-4c1d-811c-7db0d9262d73")
SOURCE_ANALYSIS_ID = UUID("288beee7-19ba-4136-b7e1-c56bbd9854d9")
TARGET_PAPER_ID = UUID("6e670c86-1478-44d8-aa4b-ae78d0475031")
TARGET_VERSION_ID = UUID("b8931432-6819-4b2f-ab65-f580292f13c8")
TARGET_ANALYSIS_ID = UUID("d18a3bd2-fe13-4535-88e8-9844e29db2ce")
SOURCE_EVIDENCE_ID = UUID("db197e9c-ab3d-4ec3-af9c-a3459cbed334")
TARGET_EVIDENCE_ID = UUID("9f0d581c-5b7d-435e-932e-c9ca1331a22b")


class _Repository:
    def __init__(
        self,
        source: ComparisonPaperInput | None,
        target: ComparisonPaperInput | None,
        *,
        selected: bool = True,
        session_status: SearchSessionStatus = SearchSessionStatus.COMPLETE,
    ) -> None:
        self.inputs = {
            SOURCE_VERSION_ID: source,
            TARGET_VERSION_ID: target,
        }
        self.persisted: object | None = None
        self.search_detail = _search_detail(selected=selected, status=session_status)

    def get_search_session(self, session_id: UUID) -> SearchSessionDetail | None:
        return self.search_detail if session_id == SESSION_ID else None

    def get_comparison_paper_input(
        self,
        paper_version_id: UUID,
        *,
        analysis_id: UUID | None = None,
    ) -> ComparisonPaperInput | None:
        value = self.inputs.get(paper_version_id)
        if value is not None and analysis_id is not None and value.analysis_id != analysis_id:
            return None
        return value

    def persist_comparison_bundle(self, bundle: object) -> None:
        self.persisted = bundle


class _LLM:
    def __init__(self, result: GeneratedComparison) -> None:
        self.result = result
        self.calls: list[ComparisonRequest] = []

    def analyze(self, request: object) -> GeneratedAnalysis:
        del request
        raise AssertionError("analysis is not part of comparison")

    def select_prior_work(
        self,
        request: CandidateSelectionRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> GeneratedCandidateSelection:
        del timeout_seconds
        del request
        raise AssertionError("selection is not part of comparison")

    def plan_scholarly_search(
        self,
        request: CrawlerPlanRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> GeneratedCrawlerPlan:
        del request, timeout_seconds
        raise AssertionError("crawler planning is not part of comparison")

    def compare_papers(self, request: ComparisonRequest) -> GeneratedComparison:
        self.calls.append(request)
        return self.result

    def generate_report(self, request: ReportNarrativeRequest) -> GeneratedReportNarrative:
        del request
        raise AssertionError("report generation is not part of comparison")


def _paper(*, paper_id: UUID, version_id: UUID, evidence_id: UUID) -> ComparisonPaperInput:
    analysis_id = SOURCE_ANALYSIS_ID if version_id == SOURCE_VERSION_ID else TARGET_ANALYSIS_ID
    return ComparisonPaperInput(
        paper_id=paper_id,
        paper_version_id=version_id,
        analysis_id=analysis_id,
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        title=f"Paper {paper_id}",
        summary="The authors report a planning method.",
        research_problem="Reliable agent planning.",
        method_summary="A bounded search method.",
        limitations=("One benchmark.",),
        evidence=(
            ComparisonEvidenceInput(
                id=evidence_id,
                analysis_id=analysis_id,
                paper_id=paper_id,
                paper_version_id=version_id,
                section="Results",
                excerpt="The authors report a result on the benchmark.",
            ),
        ),
    )


def _search_detail(*, selected: bool, status: SearchSessionStatus) -> SearchSessionDetail:
    terminal = status is not SearchSessionStatus.RUNNING
    session = SearchSession(
        id=SESSION_ID,
        topic_id=UUID("983a2b04-a10c-489f-bbb5-27d5c731b139"),
        source_paper_id=SOURCE_PAPER_ID,
        source_paper_version_id=SOURCE_VERSION_ID,
        source_analysis_id=SOURCE_ANALYSIS_ID,
        source_analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        requested_year_from=2025,
        effective_year_to=2026,
        objective="Find selected historical work.",
        status=status,
        limits=SearchLimits(),
        started_at=NOW,
        completed_at=NOW if terminal else None,
        stop_reason=(
            SearchStopReason.FAILED
            if status is SearchSessionStatus.FAILED
            else SearchStopReason.QUEUE_EXHAUSTED
            if terminal
            else None
        ),
        error_code="SEARCH_FAILED" if status is SearchSessionStatus.FAILED else None,
        error_detail="Test failure." if status is SearchSessionStatus.FAILED else None,
        provider=None,
        configured_model=None,
        model_version=None,
        prompt_version=None,
        usage=None,
        schema_version=1,
        created_at=NOW,
    )
    candidate = SearchCandidate(
        id=UUID("43f8847a-a750-45e9-934f-ce284807acbe"),
        session_id=SESSION_ID,
        external_paper_id=UUID("b929d740-f97e-46a0-80df-82dd897e56e2"),
        semantic_scholar_id="a" * 40,
        local_paper_id=TARGET_PAPER_ID,
        local_paper_version_id=TARGET_VERSION_ID,
        discovered_by_action_id=None,
        origins=(CandidateOrigin.LOCAL_LEXICAL,),
        relation_depth=0,
        scores=CandidateScoreComponents(lexical=0.8, final=0.8),
        rank=1,
        decision=SelectionDecision.SELECTED if selected else SelectionDecision.REJECTED,
        decision_reason="Selected for comparison." if selected else "Rejected by selector.",
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-04-24",
        prompt_version="m3-selector-v1",
        generated_at=NOW,
        verification_status=VerificationStatus.UNVERIFIED,
        schema_version=1,
        created_at=NOW,
    )
    return SearchSessionDetail(
        session=session,
        actions=(),
        candidates=(candidate,),
        discoveries=(),
    )


def _generated(status: ComparabilityStatus) -> GeneratedComparison:
    has_evidence = status is not ComparabilityStatus.INSUFFICIENT_EVIDENCE
    return GeneratedComparison(
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-04-24",
        prompt_version="m3-comparison-v1",
        generated_at=NOW,
        comparability_status=status,
        comparability_reason=(
            "The benchmark versions differ."
            if has_evidence
            else "The available evidence is insufficient for result comparison."
        ),
        summary="Among the retrieved candidates, the methods address the same problem.",
        dimensions=tuple(
            GeneratedComparisonDimension(
                name=name,
                source_value="Reported." if has_evidence else "Not reported.",
                target_value="Reported." if has_evidence else "Not reported.",
                assessment="Partially aligned." if has_evidence else "Insufficient evidence.",
                source_evidence_ids=(SOURCE_EVIDENCE_ID,) if has_evidence else (),
                target_evidence_ids=(TARGET_EVIDENCE_ID,) if has_evidence else (),
            )
            for name in COMPARISON_DIMENSION_ORDER
        ),
        relations=(
            (
                GeneratedRelation(
                    relation_type=PaperRelationType.SIMILAR_TO,
                    justification="Both papers address reliable planning.",
                    evidence_ids=(SOURCE_EVIDENCE_ID, TARGET_EVIDENCE_ID),
                    confidence=0.8,
                ),
            )
            if has_evidence
            else ()
        ),
        usage=ModelUsage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            call_count=1,
            duration_ms=20,
            estimated_cost_usd=Decimal("0.00001"),
        ),
    )


def _service(repository: _Repository, llm: _LLM) -> ComparePapers:
    return ComparePapers(
        repository=cast(RepositoryPort, repository),
        llm=cast(LLMPort, llm),
        clock=lambda: NOW,
    )


def test_comparison_identity_is_reproducible_and_pins_both_exact_analyses() -> None:
    arguments = (
        SESSION_ID,
        SOURCE_VERSION_ID,
        SOURCE_ANALYSIS_ID,
        TARGET_VERSION_ID,
        TARGET_ANALYSIS_ID,
        "deepseek",
        "deepseek-v4-flash",
        "DeepSeek-V4-Flash-2026-04-24",
        "m3-comparison-v1",
    )
    comparison_id = stable_comparison_id(*arguments)

    assert stable_comparison_id(*arguments) == comparison_id
    assert (
        stable_comparison_id(
            *arguments[:2], UUID("a66fb801-4c9e-487a-8830-9045cfb82bbd"), *arguments[3:]
        )
        != comparison_id
    )
    assert (
        stable_comparison_id(
            *arguments[:4], UUID("d9d5fa98-a0db-4b59-b79f-0d5d0376ac8e"), *arguments[5:]
        )
        != comparison_id
    )


@pytest.mark.parametrize(
    "status",
    [
        ComparabilityStatus.DIRECTLY_COMPARABLE,
        ComparabilityStatus.PARTIALLY_COMPARABLE,
        ComparabilityStatus.NOT_DIRECTLY_COMPARABLE,
        ComparabilityStatus.INSUFFICIENT_EVIDENCE,
    ],
)
def test_comparison_persists_fixed_dimensions_and_explicit_status(
    status: ComparabilityStatus,
) -> None:
    source = _paper(
        paper_id=SOURCE_PAPER_ID,
        version_id=SOURCE_VERSION_ID,
        evidence_id=SOURCE_EVIDENCE_ID,
    )
    target = _paper(
        paper_id=TARGET_PAPER_ID,
        version_id=TARGET_VERSION_ID,
        evidence_id=TARGET_EVIDENCE_ID,
    )
    repository = _Repository(source, target)
    llm = _LLM(_generated(status))

    bundle = _service(repository, llm).execute(
        search_session_id=SESSION_ID,
        source_paper_version_id=SOURCE_VERSION_ID,
        target_paper_version_id=TARGET_VERSION_ID,
    )

    assert bundle.comparison.comparability_status is status
    assert tuple(item.name for item in bundle.comparison.dimensions) == COMPARISON_DIMENSION_ORDER
    assert repository.persisted is bundle
    assert bundle.comparison.verification_status is VerificationStatus.UNVERIFIED
    assert bundle.comparison.source_analysis_id == SOURCE_ANALYSIS_ID
    assert bundle.comparison.target_analysis_id == TARGET_ANALYSIS_ID
    if status is ComparabilityStatus.INSUFFICIENT_EVIDENCE:
        assert bundle.relations == ()
        assert all(not item.source_evidence_ids for item in bundle.comparison.dimensions)
    else:
        assert bundle.relations[0].provenance is RelationProvenance.LLM_INFERRED
        assert bundle.relations[0].evidence_ids == (
            SOURCE_EVIDENCE_ID,
            TARGET_EVIDENCE_ID,
        )


def test_comparison_persists_direct_semantic_scholar_reference_as_metadata_cites() -> None:
    source = _paper(
        paper_id=SOURCE_PAPER_ID,
        version_id=SOURCE_VERSION_ID,
        evidence_id=SOURCE_EVIDENCE_ID,
    )
    target = _paper(
        paper_id=TARGET_PAPER_ID,
        version_id=TARGET_VERSION_ID,
        evidence_id=TARGET_EVIDENCE_ID,
    )
    repository = _Repository(source, target)
    selected = repository.search_detail.candidates[0]
    action_id = UUID("d30d1d65-0067-4c67-a632-a0221e959f44")
    action = SearchAction(
        id=action_id,
        session_id=SESSION_ID,
        step=2,
        tool=SearchTool.GET_REFERENCES,
        status=SearchActionStatus.COMPLETED,
        query=None,
        target_semantic_scholar_id="f" * 40,
        target_arxiv_id=None,
        positive_paper_ids=(),
        year_from=None,
        year_to=None,
        requested_limit=10,
        result_count=1,
        relation_depth=1,
        decision_reason="Expand the source paper's references.",
        error_code=None,
        retryable=None,
        error_detail=None,
        duration_ms=20,
        created_at=NOW,
        completed_at=NOW,
    )
    discovery = SearchCandidateDiscovery(
        id=UUID("cf834d6a-2f6f-47c7-81bb-5e4410994121"),
        candidate_id=selected.id,
        action_id=action.id,
        origin=CandidateOrigin.REFERENCES,
        relation_depth=1,
        discovered_at=NOW,
    )
    repository.search_detail = replace(
        repository.search_detail,
        actions=(action,),
        discoveries=(discovery,),
    )

    bundle = _service(
        repository,
        _LLM(_generated(ComparabilityStatus.PARTIALLY_COMPARABLE)),
    ).execute(
        search_session_id=SESSION_ID,
        source_paper_version_id=SOURCE_VERSION_ID,
        target_paper_version_id=TARGET_VERSION_ID,
    )

    metadata_relation = next(
        relation
        for relation in bundle.relations
        if relation.relation_type is PaperRelationType.CITES
    )
    assert metadata_relation.provenance is RelationProvenance.METADATA_EXPLICIT
    assert metadata_relation.evidence_ids == ()
    assert metadata_relation.provider is None
    assert metadata_relation.confidence is None


def test_comparison_rejects_missing_analyzed_target_before_model_call() -> None:
    source = _paper(
        paper_id=SOURCE_PAPER_ID,
        version_id=SOURCE_VERSION_ID,
        evidence_id=SOURCE_EVIDENCE_ID,
    )
    repository = _Repository(source, None)
    llm = _LLM(_generated(ComparabilityStatus.PARTIALLY_COMPARABLE))

    with pytest.raises(ComparisonInputMissingError, match="requires persisted"):
        _service(repository, llm).execute(
            search_session_id=SESSION_ID,
            source_paper_version_id=SOURCE_VERSION_ID,
            target_paper_version_id=TARGET_VERSION_ID,
        )

    assert llm.calls == []
    assert repository.persisted is None


def test_comparison_rejects_a_rejected_historical_candidate_before_model_call() -> None:
    source = _paper(
        paper_id=SOURCE_PAPER_ID,
        version_id=SOURCE_VERSION_ID,
        evidence_id=SOURCE_EVIDENCE_ID,
    )
    target = _paper(
        paper_id=TARGET_PAPER_ID,
        version_id=TARGET_VERSION_ID,
        evidence_id=TARGET_EVIDENCE_ID,
    )
    repository = _Repository(source, target, selected=False)
    llm = _LLM(_generated(ComparabilityStatus.PARTIALLY_COMPARABLE))

    with pytest.raises(ComparisonInputMissingError, match="selected local historical"):
        _service(repository, llm).execute(
            search_session_id=SESSION_ID,
            source_paper_version_id=SOURCE_VERSION_ID,
            target_paper_version_id=TARGET_VERSION_ID,
        )

    assert llm.calls == []


@pytest.mark.parametrize(
    "status",
    [SearchSessionStatus.RUNNING, SearchSessionStatus.FAILED],
)
def test_comparison_rejects_noncompleted_search_session(
    status: SearchSessionStatus,
) -> None:
    source = _paper(
        paper_id=SOURCE_PAPER_ID,
        version_id=SOURCE_VERSION_ID,
        evidence_id=SOURCE_EVIDENCE_ID,
    )
    target = _paper(
        paper_id=TARGET_PAPER_ID,
        version_id=TARGET_VERSION_ID,
        evidence_id=TARGET_EVIDENCE_ID,
    )
    repository = _Repository(source, target, session_status=status)
    llm = _LLM(_generated(ComparabilityStatus.PARTIALLY_COMPARABLE))

    with pytest.raises(ComparisonInputMissingError, match="completed search session"):
        _service(repository, llm).execute(
            search_session_id=SESSION_ID,
            source_paper_version_id=SOURCE_VERSION_ID,
            target_paper_version_id=TARGET_VERSION_ID,
        )

    assert llm.calls == []


def test_inferred_relation_requires_supporting_evidence() -> None:
    with pytest.raises(DomainInvariantError, match="requires unique evidence"):
        GeneratedRelation(
            relation_type=PaperRelationType.SIMILAR_TO,
            justification="The papers appear related.",
            evidence_ids=(),
            confidence=0.5,
        )


def test_improves_on_requires_direct_comparability() -> None:
    generated = _generated(ComparabilityStatus.PARTIALLY_COMPARABLE)
    relation = GeneratedRelation(
        relation_type=PaperRelationType.IMPROVES_ON,
        justification="The reported result is higher under the same setup.",
        evidence_ids=(SOURCE_EVIDENCE_ID, TARGET_EVIDENCE_ID),
        confidence=0.7,
    )

    with pytest.raises(DomainInvariantError, match="directly comparable"):
        GeneratedComparison(
            provider=generated.provider,
            configured_model=generated.configured_model,
            model_version=generated.model_version,
            prompt_version=generated.prompt_version,
            generated_at=generated.generated_at,
            comparability_status=generated.comparability_status,
            comparability_reason=generated.comparability_reason,
            summary=generated.summary,
            dimensions=generated.dimensions,
            relations=(relation,),
            usage=generated.usage,
        )


def test_generated_comparison_rejects_duplicate_relation_types() -> None:
    generated = _generated(ComparabilityStatus.PARTIALLY_COMPARABLE)

    with pytest.raises(DomainInvariantError, match="relation types must be unique"):
        GeneratedComparison(
            provider=generated.provider,
            configured_model=generated.configured_model,
            model_version=generated.model_version,
            prompt_version=generated.prompt_version,
            generated_at=generated.generated_at,
            comparability_status=generated.comparability_status,
            comparability_reason=generated.comparability_reason,
            summary=generated.summary,
            dimensions=generated.dimensions,
            relations=(generated.relations[0], generated.relations[0]),
            usage=generated.usage,
        )
