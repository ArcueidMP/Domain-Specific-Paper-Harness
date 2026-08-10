from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from paper_harness.application.read_models import (
    AnalysisDetail,
    HistoricalRetrievalMatch,
    PaperDetail,
    SearchSessionDetail,
)
from paper_harness.application.related_work import RelatedWorkSearch, allowed_search_tools
from paper_harness.application.scholarly_mapping import external_stub_from_scholarly_paper
from paper_harness.domain.analysis import (
    AnalysisScope,
    GeneratedAnalysis,
    ModelUsage,
    PaperAnalysis,
    VerificationStatus,
)
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import (
    CandidateOrigin,
    CandidateSelectionRequest,
    ComparisonRequest,
    CrawlerPlanRequest,
    GeneratedCandidateDecision,
    GeneratedCandidateSelection,
    GeneratedComparison,
    GeneratedCrawlerPlan,
    HistoricalCorpusEntry,
    ScientificEmbedding,
    SearchAction,
    SearchActionStatus,
    SearchCandidate,
    SearchCandidateDiscovery,
    SearchLimits,
    SearchModelProvenance,
    SearchSession,
    SearchSessionStatus,
    SearchStopReason,
    SearchTool,
    SelectionDecision,
)
from paper_harness.domain.models import Paper, TopicConfig
from paper_harness.domain.reports import GeneratedReportNarrative, ReportNarrativeRequest
from paper_harness.ports.llm import LLMOutputError, LLMPort
from paper_harness.ports.repository import RepositoryPort
from paper_harness.ports.scholarly_search import (
    ScholarlyAuthor,
    ScholarlyExternalIds,
    ScholarlyPaper,
    ScholarlySearchPort,
)
from paper_harness.ports.scientific_embedding import (
    GeneratedScientificEmbedding,
    ScientificEmbeddingPort,
    ScientificPaperText,
)

NOW = datetime(2026, 8, 9, 5, tzinfo=UTC)
SOURCE_PAPER_ID = UUID("a83014ac-d4b4-482a-8d80-a6fb019e3939")
SOURCE_VERSION_ID = UUID("5f4cf773-ef83-48b0-b9a3-fb9df133b377")


class _Repository:
    def __init__(self) -> None:
        usage = _usage()
        paper = Paper(
            id=SOURCE_PAPER_ID,
            canonical_arxiv_id="2608.01234",
            title="Bounded Agent Planning",
            abstract="We study a bounded planner for LLM agents.",
            current_version=1,
            first_submitted_at=NOW,
            latest_updated_at=NOW,
            primary_category="cs.AI",
            categories=("cs.AI",),
            authors=("Ada Lovelace",),
            pdf_url="https://arxiv.org/pdf/2608.01234v1",
            schema_version=1,
            created_at=NOW,
        )
        self.paper_detail = PaperDetail(
            paper=paper,
            versions=(),
            source_identities=(),
            topic_slugs=("broad-llm-agents",),
        )
        self.analysis_detail = AnalysisDetail(
            analysis=PaperAnalysis(
                id=UUID("725cbab1-6e88-480f-bd02-c36729ee3d81"),
                paper_id=SOURCE_PAPER_ID,
                paper_version_id=SOURCE_VERSION_ID,
                parsed_paper_id=None,
                analysis_scope=AnalysisScope.ABSTRACT_ONLY,
                summary="A bounded planning agent.",
                research_problem="Long-horizon LLM-agent planning is unreliable.",
                method_summary="A bounded tree-search planner.",
                key_contributions=("Bounded search.",),
                limitations=("One benchmark.",),
                provider="deepseek",
                configured_model="deepseek-v4-flash",
                model_version="DeepSeek-V4-Flash-2026-04-24",
                prompt_version="m2-analysis-v1",
                generated_at=NOW,
                source="abstract",
                verification_status=VerificationStatus.UNVERIFIED,
                usage=usage,
                schema_version=1,
                created_at=NOW,
            ),
            arxiv_version=1,
            claims=(),
            evidence=(),
        )
        self.session: SearchSession | None = None
        self.actions: dict[UUID, SearchAction] = {}
        self.candidates: dict[UUID, SearchCandidate] = {}
        self.discoveries: dict[UUID, SearchCandidateDiscovery] = {}
        self.embeddings: tuple[ScientificEmbedding, ...] = ()
        self.lexical_matches: tuple[HistoricalRetrievalMatch, ...] = ()
        self.vector_matches: tuple[HistoricalRetrievalMatch, ...] = ()

    def get_paper(self, paper_id: UUID) -> PaperDetail | None:
        return self.paper_detail if paper_id == SOURCE_PAPER_ID else None

    def get_paper_analysis(
        self,
        paper_id: UUID,
        *,
        paper_version_id: UUID | None,
        analysis_scope: AnalysisScope | None = None,
    ) -> AnalysisDetail | None:
        del paper_version_id, analysis_scope
        return self.analysis_detail if paper_id == SOURCE_PAPER_ID else None

    def start_search_session(self, session: SearchSession) -> SearchSession:
        self.session = session
        return session

    def persist_search_crawler_plan(
        self, session_id: UUID, plan: GeneratedCrawlerPlan
    ) -> SearchSession:
        assert self.session is not None and self.session.id == session_id
        self.session = replace(
            self.session,
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
        return self.session

    def upsert_scientific_embeddings(self, embeddings: tuple[ScientificEmbedding, ...]) -> None:
        self.embeddings = embeddings

    def search_historical_lexically(
        self, topic_id: UUID, *, query: str, limit: int
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

    def persist_local_search_candidates(
        self,
        session_id: UUID,
        *,
        papers: tuple[object, ...],
        candidates: tuple[SearchCandidate, ...],
        discoveries: tuple[SearchCandidateDiscovery, ...],
    ) -> None:
        del session_id, papers
        self.candidates.update((item.id, item) for item in candidates)
        self.discoveries.update((item.id, item) for item in discoveries)

    def start_search_action(self, action: SearchAction) -> SearchAction:
        self.actions[action.id] = action
        return action

    def persist_search_action_result(
        self,
        action: SearchAction,
        *,
        papers: tuple[object, ...],
        candidates: tuple[SearchCandidate, ...],
        discoveries: tuple[SearchCandidateDiscovery, ...],
    ) -> None:
        del papers
        self.actions[action.id] = action
        self.candidates.update((item.id, item) for item in candidates)
        self.discoveries.update((item.id, item) for item in discoveries)

    def update_search_candidate_decisions(
        self, session_id: UUID, candidates: tuple[SearchCandidate, ...]
    ) -> None:
        del session_id
        self.candidates.update((item.id, item) for item in candidates)

    def complete_search_session(
        self,
        session_id: UUID,
        *,
        completed_at: datetime,
        stop_reason: SearchStopReason,
        provenance: SearchModelProvenance | None,
    ) -> SearchSession:
        assert self.session is not None and self.session.id == session_id
        self.session = replace(
            self.session,
            status=SearchSessionStatus.COMPLETE,
            completed_at=completed_at,
            stop_reason=stop_reason,
            provider=self.session.provider if provenance is None else provenance.provider,
            configured_model=(
                self.session.configured_model if provenance is None else provenance.configured_model
            ),
            model_version=(
                self.session.model_version if provenance is None else provenance.model_version
            ),
            prompt_version=(
                self.session.prompt_version if provenance is None else provenance.prompt_version
            ),
            usage=self.session.usage if provenance is None else provenance.usage,
        )
        return self.session

    def fail_search_session(
        self,
        session_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
        provenance: SearchModelProvenance | None,
    ) -> SearchSession:
        assert self.session is not None and self.session.id == session_id
        self.session = replace(
            self.session,
            status=SearchSessionStatus.FAILED,
            completed_at=completed_at,
            stop_reason=SearchStopReason.FAILED,
            error_code=error_code,
            error_detail=error_detail,
            provider=self.session.provider if provenance is None else provenance.provider,
            configured_model=(
                self.session.configured_model if provenance is None else provenance.configured_model
            ),
            model_version=(
                self.session.model_version if provenance is None else provenance.model_version
            ),
            prompt_version=(
                self.session.prompt_version if provenance is None else provenance.prompt_version
            ),
            usage=self.session.usage if provenance is None else provenance.usage,
        )
        return self.session

    def get_search_session(self, session_id: UUID) -> SearchSessionDetail | None:
        if self.session is None or self.session.id != session_id:
            return None
        return SearchSessionDetail(
            session=self.session,
            actions=tuple(sorted(self.actions.values(), key=lambda item: item.step)),
            candidates=tuple(sorted(self.candidates.values(), key=lambda item: item.rank)),
            discoveries=tuple(self.discoveries.values()),
        )


class _ScholarlySearch:
    def __init__(
        self,
        paper: ScholarlyPaper,
        *,
        source_paper: ScholarlyPaper | None = None,
    ) -> None:
        self.paper = paper
        self.source_paper = source_paper or _source_paper()
        self.calls: list[SearchTool] = []
        self.queries: list[str] = []
        self.search_limits: list[int] = []
        self.arxiv_lookups: list[str] = []
        self.timeouts: list[float | None] = []

    def search_papers(
        self,
        query: str,
        year_from: int,
        year_to: int,
        limit: int,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[ScholarlyPaper, ...]:
        del year_from, year_to
        self.queries.append(query)
        self.search_limits.append(limit)
        self.calls.append(SearchTool.SEARCH_PAPERS)
        self.timeouts.append(timeout_seconds)
        return (self.paper,)

    def get_paper(
        self, semantic_scholar_id: str, *, timeout_seconds: float | None = None
    ) -> ScholarlyPaper:
        del semantic_scholar_id
        self.calls.append(SearchTool.GET_PAPER)
        self.timeouts.append(timeout_seconds)
        return self.paper

    def get_paper_by_arxiv_id(
        self, canonical_arxiv_id: str, *, timeout_seconds: float | None = None
    ) -> ScholarlyPaper:
        self.arxiv_lookups.append(canonical_arxiv_id)
        self.calls.append(SearchTool.GET_PAPER)
        self.timeouts.append(timeout_seconds)
        return self.source_paper

    def get_references(
        self, semantic_scholar_id: str, *, timeout_seconds: float | None = None
    ) -> tuple[ScholarlyPaper, ...]:
        del semantic_scholar_id
        self.calls.append(SearchTool.GET_REFERENCES)
        self.timeouts.append(timeout_seconds)
        return (self.paper,)

    def get_citations(
        self, semantic_scholar_id: str, *, timeout_seconds: float | None = None
    ) -> tuple[ScholarlyPaper, ...]:
        del semantic_scholar_id
        self.calls.append(SearchTool.GET_CITATIONS)
        self.timeouts.append(timeout_seconds)
        return (self.paper,)

    def get_recommendations(
        self,
        positive_paper_ids: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[ScholarlyPaper, ...]:
        del positive_paper_ids
        self.calls.append(SearchTool.GET_RECOMMENDATIONS)
        self.timeouts.append(timeout_seconds)
        return (self.paper,)


class _SourceReferenceSearch(_ScholarlySearch):
    def __init__(self, source: ScholarlyPaper, target: ScholarlyPaper) -> None:
        super().__init__(target, source_paper=source)
        self.target = target

    def get_references(
        self, semantic_scholar_id: str, *, timeout_seconds: float | None = None
    ) -> tuple[ScholarlyPaper, ...]:
        assert semantic_scholar_id in {
            self.source_paper.semantic_scholar_id,
            self.paper.semantic_scholar_id,
        }
        self.calls.append(SearchTool.GET_REFERENCES)
        self.timeouts.append(timeout_seconds)
        return (self.target,)

    def get_citations(
        self, semantic_scholar_id: str, *, timeout_seconds: float | None = None
    ) -> tuple[ScholarlyPaper, ...]:
        del semantic_scholar_id
        self.calls.append(SearchTool.GET_CITATIONS)
        self.timeouts.append(timeout_seconds)
        return ()


class _Embeddings:
    model_identifier = "allenai/specter2_base"
    model_revision = "base-revision"
    tokenizer_identifier = "allenai/specter2_base"
    tokenizer_revision = "tokenizer-revision"
    dimension = 768
    preprocessing_contract = "title + separator + abstract; cls; max_length=512"
    model_provenance = "huggingface:allenai/specter2_base@base-revision"
    source = "specter2_base_title_abstract_cls"

    def encode(
        self, papers: tuple[ScientificPaperText, ...]
    ) -> tuple[GeneratedScientificEmbedding, ...]:
        return tuple(
            GeneratedScientificEmbedding(key=paper.key, vector=(0.01,) * 768) for paper in papers
        )


class _AdvancingEmbeddings(_Embeddings):
    def __init__(self, elapsed: list[float], *, elapsed_after: float = 2.0) -> None:
        self._elapsed = elapsed
        self._elapsed_after = elapsed_after

    def encode(
        self, papers: tuple[ScientificPaperText, ...]
    ) -> tuple[GeneratedScientificEmbedding, ...]:
        result = super().encode(papers)
        self._elapsed[0] = self._elapsed_after
        return result


class _LLM:
    def __init__(self, *, error: LLMOutputError | None = None) -> None:
        self.error = error
        self.requests: list[CandidateSelectionRequest] = []
        self.plan_requests: list[CrawlerPlanRequest] = []
        self.plan_timeouts: list[float | None] = []
        self.selection_timeouts: list[float | None] = []

    def analyze(self, request: object) -> GeneratedAnalysis:
        del request
        raise AssertionError("analysis is not part of related-work selection")

    def plan_scholarly_search(
        self,
        request: CrawlerPlanRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> GeneratedCrawlerPlan:
        self.plan_timeouts.append(timeout_seconds)
        self.plan_requests.append(request)
        queries = (
            request.objective,
            f"{request.source_research_problem} {request.source_method}",
            request.source_title,
            *request.topic_include_terms,
        )[: request.max_queries]
        return GeneratedCrawlerPlan(
            provider="deepseek",
            configured_model="deepseek-v4-flash",
            model_version="DeepSeek-V4-Flash-2026-04-24",
            prompt_version="m3-crawler-v1",
            generated_at=NOW,
            queries=tuple(dict.fromkeys(queries)),
            use_recommendations=True,
            expand_references=True,
            expand_citations=True,
            decision_reason="Use bounded search and citation expansion.",
            usage=_usage(),
        )

    def select_prior_work(
        self,
        request: CandidateSelectionRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> GeneratedCandidateSelection:
        self.selection_timeouts.append(timeout_seconds)
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return GeneratedCandidateSelection(
            provider="deepseek",
            configured_model="deepseek-v4-flash",
            model_version="DeepSeek-V4-Flash-2026-04-24",
            prompt_version="m3-selector-v1",
            generated_at=NOW,
            decisions=tuple(
                GeneratedCandidateDecision(
                    semantic_scholar_id=item.semantic_scholar_id,
                    decision=(
                        SelectionDecision.SELECTED
                        if index < request.max_selected_candidates
                        else SelectionDecision.REJECTED
                    ),
                    reason="Methodologically relevant." if index == 0 else "Lower relevance.",
                )
                for index, item in enumerate(request.candidates)
            ),
            usage=_usage(),
        )

    def compare_papers(self, request: ComparisonRequest) -> GeneratedComparison:
        del request
        raise AssertionError("comparison is a separate application use case")

    def generate_report(self, request: ReportNarrativeRequest) -> GeneratedReportNarrative:
        del request
        raise AssertionError("report generation is a separate application use case")


def _usage() -> ModelUsage:
    return ModelUsage(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        call_count=1,
        duration_ms=10,
        estimated_cost_usd=Decimal("0.000001"),
    )


def _paper() -> ScholarlyPaper:
    paper_id = "a" * 40
    return ScholarlyPaper(
        semantic_scholar_id=paper_id,
        corpus_id=1,
        external_ids=ScholarlyExternalIds(
            arxiv_id="2507.01234",
            doi=None,
            values=(("ArXiv", "2507.01234"),),
        ),
        url=f"https://www.semanticscholar.org/paper/{paper_id}",
        title="Historical Bounded Planner",
        abstract="A methodologically related bounded planning agent.",
        venue="ACL",
        year=2025,
        publication_date=date(2025, 7, 1),
        authors=(ScholarlyAuthor(author_id="1", name="Alan Turing"),),
        citation_count=5,
        influential_citation_count=1,
        reference_count=4,
    )


def _source_paper() -> ScholarlyPaper:
    paper_id = "f" * 40
    return replace(
        _paper(),
        semantic_scholar_id=paper_id,
        corpus_id=2,
        external_ids=ScholarlyExternalIds(
            arxiv_id="2608.01234",
            doi=None,
            values=(("ArXiv", "2608.01234"),),
        ),
        url=f"https://www.semanticscholar.org/paper/{paper_id}",
        title="Bounded Agent Planning",
    )


def _local_match(identity: str, *, score: float, year: int = 2025) -> HistoricalRetrievalMatch:
    scholarly = replace(
        _paper(),
        semantic_scholar_id=identity * 40,
        corpus_id=ord(identity),
        external_ids=ScholarlyExternalIds(
            arxiv_id=None,
            doi=None,
            values=(),
        ),
        url=f"https://www.semanticscholar.org/paper/{identity * 40}",
        title=f"Historical planning paper {identity}",
        year=year,
        publication_date=date(year, 7, 1),
    )
    stub = external_stub_from_scholarly_paper(scholarly, observed_at=NOW)
    return HistoricalRetrievalMatch(
        external_paper=stub,
        corpus_entry=HistoricalCorpusEntry(
            id=stub.id,
            topic_id=UUID("4b7db6d4-349c-5c06-bc41-f84091580fcb"),
            external_paper_id=stub.id,
            local_paper_id=None,
            local_paper_version_id=None,
            representative_rank=None,
            first_seen_at=NOW,
            last_seen_at=NOW,
            schema_version=1,
        ),
        score=score,
    )


def _service(
    repository: _Repository,
    scholarly: _ScholarlySearch,
    llm: _LLM,
    *,
    embeddings: ScientificEmbeddingPort | None = None,
    monotonic: Callable[[], float] | None = None,
) -> RelatedWorkSearch:
    return RelatedWorkSearch(
        repository=cast(RepositoryPort, repository),
        scholarly_search=cast(ScholarlySearchPort, scholarly),
        llm=cast(LLMPort, llm),
        embeddings=embeddings or cast(ScientificEmbeddingPort, _Embeddings()),
        clock=lambda: NOW,
        monotonic=monotonic or (lambda: 0.0),
    )


def _execute(
    service: RelatedWorkSearch,
    topic_config: TopicConfig,
    *,
    limits: SearchLimits,
) -> SearchSessionDetail:
    return service.execute(
        topic=topic_config,
        source_paper_id=SOURCE_PAPER_ID,
        objective="Find methodologically relevant prior work.",
        year_from=2025,
        year_to=2026,
        limits=limits,
    )


def test_crawler_tool_allowlist_is_exact() -> None:
    assert allowed_search_tools() == {
        SearchTool.SEARCH_PAPERS,
        SearchTool.GET_PAPER,
        SearchTool.GET_REFERENCES,
        SearchTool.GET_CITATIONS,
        SearchTool.GET_RECOMMENDATIONS,
        SearchTool.READ_ARXIV_PAPER,
    }


def test_max_steps_has_a_deterministic_stop_reason(topic_config: TopicConfig) -> None:
    repository = _Repository()
    scholarly = _ScholarlySearch(_paper())

    detail = _execute(
        _service(repository, scholarly, _LLM()),
        topic_config,
        limits=SearchLimits(max_steps=1, max_queries=8, max_selected_candidates=2),
    )

    assert detail.session.stop_reason is SearchStopReason.MAX_STEPS
    assert scholarly.calls == [SearchTool.GET_PAPER]
    assert scholarly.arxiv_lookups == ["2608.01234"]
    assert detail.candidates == ()
    assert detail.actions[0].target_semantic_scholar_id == "ARXIV:2608.01234"
    assert detail.actions[0].result_count == 1


def test_max_queries_stops_before_expansion(topic_config: TopicConfig) -> None:
    repository = _Repository()
    scholarly = _ScholarlySearch(_paper())

    detail = _execute(
        _service(repository, scholarly, _LLM()),
        topic_config,
        limits=SearchLimits(max_steps=10, max_queries=1, max_selected_candidates=2),
    )

    assert detail.session.stop_reason is SearchStopReason.MAX_QUERIES
    assert scholarly.calls == [SearchTool.GET_PAPER, SearchTool.SEARCH_PAPERS]


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        (
            SearchLimits(
                max_steps=10,
                max_queries=8,
                max_queue_size=1,
                max_candidates=10,
                max_selected_candidates=1,
            ),
            SearchStopReason.MAX_QUEUE_SIZE,
        ),
        (
            SearchLimits(
                max_steps=10,
                max_queries=8,
                max_queue_size=10,
                max_candidates=1,
                max_selected_candidates=1,
            ),
            SearchStopReason.MAX_CANDIDATES,
        ),
    ],
)
def test_candidate_and_queue_bounds_stop_before_another_action(
    topic_config: TopicConfig,
    limits: SearchLimits,
    expected: SearchStopReason,
) -> None:
    repository = _Repository()
    scholarly = _ScholarlySearch(_paper())

    detail = _execute(_service(repository, scholarly, _LLM()), topic_config, limits=limits)

    assert detail.session.stop_reason is expected
    assert scholarly.calls == [SearchTool.GET_PAPER, SearchTool.SEARCH_PAPERS]


def test_selected_candidate_limit_is_visible_as_stop_reason(topic_config: TopicConfig) -> None:
    repository = _Repository()
    scholarly = _ScholarlySearch(_paper())

    detail = _execute(
        _service(repository, scholarly, _LLM()),
        topic_config,
        limits=SearchLimits(
            max_steps=100,
            max_queries=40,
            max_citation_depth=0,
            max_selected_candidates=1,
        ),
    )

    assert detail.session.stop_reason is SearchStopReason.MAX_SELECTED_CANDIDATES
    assert detail.candidates[0].decision is SelectionDecision.SELECTED


def test_duplicate_candidate_keeps_multi_action_provenance(topic_config: TopicConfig) -> None:
    repository = _Repository()
    scholarly = _ScholarlySearch(_paper())

    detail = _execute(
        _service(repository, scholarly, _LLM()),
        topic_config,
        limits=SearchLimits(
            max_steps=10,
            max_queries=8,
            max_citation_depth=1,
            max_selected_candidates=2,
        ),
    )

    assert len(detail.candidates) == 1
    assert set(detail.candidates[0].origins) == {
        CandidateOrigin.SEARCH,
        CandidateOrigin.REFERENCES,
        CandidateOrigin.CITATIONS,
        CandidateOrigin.RECOMMENDATIONS,
    }
    assert {item.origin.value for item in detail.discoveries} == {
        "SEARCH",
        "REFERENCES",
        "CITATIONS",
        "RECOMMENDATIONS",
    }
    assert len(detail.discoveries) == 9
    assert len({item.id for item in detail.discoveries}) == len(detail.discoveries)


def test_crawler_uses_the_explicit_objective_as_its_first_query(
    topic_config: TopicConfig,
) -> None:
    repository = _Repository()
    scholarly = _ScholarlySearch(_paper())

    _execute(
        _service(repository, scholarly, _LLM()),
        topic_config,
        limits=SearchLimits(max_steps=2, max_queries=8, max_selected_candidates=2),
    )

    assert scholarly.queries == ["Find methodologically relevant prior work."]


def test_each_remote_search_action_has_a_provider_compatible_result_bound(
    topic_config: TopicConfig,
) -> None:
    repository = _Repository()
    scholarly = _ScholarlySearch(_paper())

    _execute(
        _service(repository, scholarly, _LLM()),
        topic_config,
        limits=SearchLimits(
            max_steps=2,
            max_queries=8,
            max_queue_size=2000,
            max_candidates=300,
            max_selected_candidates=2,
        ),
    )

    assert scholarly.search_limits == [300]


def test_effective_selector_candidate_bound_is_validated_before_execution() -> None:
    with pytest.raises(DomainInvariantError, match="effective candidate bound"):
        SearchLimits(
            max_queue_size=301,
            max_candidates=301,
            max_selected_candidates=20,
        )


def test_source_paper_is_not_added_as_its_own_related_candidate(
    topic_config: TopicConfig,
) -> None:
    repository = _Repository()
    source_result = replace(
        _paper(),
        external_ids=ScholarlyExternalIds(
            arxiv_id="2608.01234",
            doi=None,
            values=(("ArXiv", "2608.01234"),),
        ),
    )

    detail = _execute(
        _service(
            repository,
            _ScholarlySearch(source_result, source_paper=source_result),
            _LLM(),
        ),
        topic_config,
        limits=SearchLimits(max_steps=2, max_queries=8, max_selected_candidates=2),
    )

    assert detail.candidates == ()
    assert detail.actions[0].tool is SearchTool.GET_PAPER
    assert detail.actions[0].target_semantic_scholar_id == "ARXIV:2608.01234"
    assert detail.actions[0].result_count == 1


def test_explicit_source_mapping_is_expanded_when_search_does_not_return_source(
    topic_config: TopicConfig,
) -> None:
    repository = _Repository()
    source_result = replace(
        _paper(),
        semantic_scholar_id="f" * 40,
        url=f"https://www.semanticscholar.org/paper/{'f' * 40}",
        external_ids=ScholarlyExternalIds(
            arxiv_id="2608.01234",
            doi=None,
            values=(("ArXiv", "2608.01234"),),
        ),
    )
    target = _paper()
    scholarly = _SourceReferenceSearch(source_result, target)

    detail = _execute(
        _service(repository, scholarly, _LLM()),
        topic_config,
        limits=SearchLimits(
            max_steps=10,
            max_queries=8,
            max_citation_depth=1,
            max_selected_candidates=2,
        ),
    )

    assert all(
        candidate.semantic_scholar_id != source_result.semantic_scholar_id
        for candidate in detail.candidates
    )
    target_candidate = next(
        candidate
        for candidate in detail.candidates
        if candidate.semantic_scholar_id == target.semantic_scholar_id
    )
    reference_action = next(
        action for action in detail.actions if action.tool is SearchTool.GET_REFERENCES
    )
    assert reference_action.target_semantic_scholar_id == source_result.semantic_scholar_id
    assert detail.actions[0].tool is SearchTool.GET_PAPER
    assert detail.actions[0].target_semantic_scholar_id == "ARXIV:2608.01234"
    assert any(
        discovery.candidate_id == target_candidate.id
        and discovery.action_id == reference_action.id
        and discovery.origin is CandidateOrigin.REFERENCES
        and discovery.relation_depth == 1
        for discovery in detail.discoveries
    )


def test_remote_expansion_filters_candidates_to_the_prior_work_year_window(
    topic_config: TopicConfig,
) -> None:
    repository = _Repository()
    future = replace(
        _paper(),
        year=2027,
        publication_date=date(2027, 1, 1),
    )
    scholarly = _SourceReferenceSearch(_source_paper(), future)

    detail = _service(repository, scholarly, _LLM()).execute(
        topic=topic_config,
        source_paper_id=SOURCE_PAPER_ID,
        objective="Find methodologically relevant prior work.",
        year_from=2025,
        year_to=2028,
        limits=SearchLimits(
            max_steps=10,
            max_queries=8,
            max_citation_depth=1,
            max_selected_candidates=2,
        ),
    )

    assert detail.candidates == ()
    assert detail.session.source_analysis_id == repository.analysis_detail.analysis.id
    assert detail.session.source_analysis_scope is AnalysisScope.ABSTRACT_ONLY
    assert detail.session.requested_year_from == 2025
    assert detail.session.effective_year_to == 2026
    reference_action = next(
        action for action in detail.actions if action.tool is SearchTool.GET_REFERENCES
    )
    assert reference_action.year_from == 2025
    assert reference_action.year_to == 2026
    assert reference_action.result_count == 0


def test_local_hybrid_union_is_truncated_to_the_effective_candidate_bound(
    topic_config: TopicConfig,
) -> None:
    repository = _Repository()
    repository.lexical_matches = (
        _local_match("a", score=0.95),
        _local_match("b", score=0.85),
    )
    repository.vector_matches = (
        _local_match("c", score=0.9),
        _local_match("d", score=0.8),
    )
    scholarly = _ScholarlySearch(_paper())

    detail = _execute(
        _service(repository, scholarly, _LLM()),
        topic_config,
        limits=SearchLimits(
            max_steps=10,
            max_queries=2,
            max_queue_size=2,
            max_candidates=2,
            max_selected_candidates=1,
        ),
    )

    assert len(detail.candidates) == 2
    assert detail.session.stop_reason is SearchStopReason.MAX_CANDIDATES
    assert scholarly.calls == []


def test_local_retrieval_filters_candidates_outside_the_prior_work_year_window(
    topic_config: TopicConfig,
) -> None:
    repository = _Repository()
    repository.lexical_matches = (_local_match("a", score=0.95, year=2024),)

    detail = _execute(
        _service(repository, _ScholarlySearch(_paper()), _LLM()),
        topic_config,
        limits=SearchLimits(max_steps=1, max_queries=1, max_selected_candidates=1),
    )

    assert detail.candidates == ()
    assert repository.candidates == {}


def test_overall_timeout_stops_after_local_embedding_without_calling_deepseek(
    topic_config: TopicConfig,
) -> None:
    elapsed = [0.0]
    repository = _Repository()
    scholarly = _ScholarlySearch(_paper())
    llm = _LLM()

    detail = _execute(
        _service(
            repository,
            scholarly,
            llm,
            embeddings=cast(ScientificEmbeddingPort, _AdvancingEmbeddings(elapsed)),
            monotonic=lambda: elapsed[0],
        ),
        topic_config,
        limits=SearchLimits(
            max_steps=2,
            max_queries=1,
            max_selected_candidates=1,
            per_operation_timeout_seconds=1,
            overall_timeout_seconds=1,
        ),
    )

    assert detail.session.stop_reason is SearchStopReason.OVERALL_TIMEOUT
    assert llm.plan_requests == []
    assert llm.requests == []
    assert scholarly.calls == []
    assert scholarly.timeouts == []


def test_scholarly_action_receives_the_smaller_remaining_overall_budget(
    topic_config: TopicConfig,
) -> None:
    elapsed = [0.0]
    repository = _Repository()
    scholarly = _ScholarlySearch(_paper())

    detail = _execute(
        _service(
            repository,
            scholarly,
            _LLM(),
            embeddings=cast(
                ScientificEmbeddingPort,
                _AdvancingEmbeddings(elapsed, elapsed_after=8.0),
            ),
            monotonic=lambda: elapsed[0],
        ),
        topic_config,
        limits=SearchLimits(
            max_steps=1,
            max_queries=1,
            max_selected_candidates=1,
            per_operation_timeout_seconds=7,
            overall_timeout_seconds=10,
        ),
    )

    assert detail.session.stop_reason is SearchStopReason.MAX_STEPS
    assert scholarly.calls == [SearchTool.GET_PAPER]
    assert scholarly.timeouts == [2.0]


def test_scholarly_action_with_less_than_one_second_remaining_is_overall_timeout(
    topic_config: TopicConfig,
) -> None:
    elapsed = [0.0]
    repository = _Repository()
    scholarly = _ScholarlySearch(_paper())

    detail = _execute(
        _service(
            repository,
            scholarly,
            _LLM(),
            embeddings=cast(
                ScientificEmbeddingPort,
                _AdvancingEmbeddings(elapsed, elapsed_after=9.5),
            ),
            monotonic=lambda: elapsed[0],
        ),
        topic_config,
        limits=SearchLimits(
            max_steps=1,
            max_queries=1,
            max_selected_candidates=1,
            per_operation_timeout_seconds=7,
            overall_timeout_seconds=10,
        ),
    )

    assert detail.session.status is SearchSessionStatus.COMPLETE
    assert detail.session.stop_reason is SearchStopReason.OVERALL_TIMEOUT
    assert scholarly.calls == []
    assert scholarly.timeouts == []
    assert len(detail.actions) == 1
    assert detail.actions[0].status is SearchActionStatus.FAILED
    assert detail.actions[0].error_code == "RELATED_WORK_OVERALL_TIMEOUT"


def test_deepseek_calls_receive_the_per_operation_timeout_when_budget_is_larger(
    topic_config: TopicConfig,
) -> None:
    llm = _LLM()

    _execute(
        _service(_Repository(), _ScholarlySearch(_paper()), llm),
        topic_config,
        limits=SearchLimits(
            max_steps=2,
            max_queries=1,
            max_selected_candidates=1,
            per_operation_timeout_seconds=7,
            overall_timeout_seconds=10,
        ),
    )

    assert llm.plan_timeouts == [7]
    assert llm.selection_timeouts == [7]


def test_deepseek_calls_receive_the_smaller_remaining_overall_budget(
    topic_config: TopicConfig,
) -> None:
    elapsed = [0.0]
    llm = _LLM()

    _execute(
        _service(
            _Repository(),
            _ScholarlySearch(_paper()),
            llm,
            embeddings=cast(
                ScientificEmbeddingPort,
                _AdvancingEmbeddings(elapsed, elapsed_after=8.0),
            ),
            monotonic=lambda: elapsed[0],
        ),
        topic_config,
        limits=SearchLimits(
            max_steps=2,
            max_queries=1,
            max_selected_candidates=1,
            per_operation_timeout_seconds=7,
            overall_timeout_seconds=10,
        ),
    )

    assert llm.plan_timeouts == [2.0]
    assert llm.selection_timeouts == [2.0]


def test_search_session_aggregates_crawler_and_selector_usage(
    topic_config: TopicConfig,
) -> None:
    detail = _execute(
        _service(_Repository(), _ScholarlySearch(_paper()), _LLM()),
        topic_config,
        limits=SearchLimits(max_steps=2, max_queries=1, max_selected_candidates=1),
    )

    assert detail.session.prompt_version == "m3-crawler-v1+m3-selector-v1"
    assert detail.session.usage is not None
    assert detail.session.usage.call_count == 2
    assert detail.session.usage.total_tokens == 30
    assert detail.session.crawler_queries == ("Find methodologically relevant prior work.",)
    assert detail.session.crawler_use_recommendations is True
    assert detail.session.crawler_expand_references is True
    assert detail.session.crawler_expand_citations is True
    assert detail.session.crawler_decision_reason == "Use bounded search and citation expansion."
    assert detail.session.crawler_generated_at == NOW


def test_zero_citation_depth_never_expands_relations(topic_config: TopicConfig) -> None:
    repository = _Repository()
    scholarly = _ScholarlySearch(_paper())

    _execute(
        _service(repository, scholarly, _LLM()),
        topic_config,
        limits=SearchLimits(
            max_steps=10,
            max_queries=8,
            max_citation_depth=0,
            max_selected_candidates=2,
        ),
    )

    assert SearchTool.GET_REFERENCES not in scholarly.calls
    assert SearchTool.GET_CITATIONS not in scholarly.calls


def test_selector_failure_persists_failed_session(topic_config: TopicConfig) -> None:
    repository = _Repository()
    scholarly = _ScholarlySearch(_paper())
    llm = _LLM(error=LLMOutputError("invalid selector JSON"))

    with pytest.raises(LLMOutputError, match="invalid selector JSON"):
        _execute(
            _service(repository, scholarly, llm),
            topic_config,
            limits=SearchLimits(max_steps=2, max_queries=8, max_selected_candidates=2),
        )

    assert repository.session is not None
    assert repository.session.status is SearchSessionStatus.FAILED
    assert repository.session.stop_reason is SearchStopReason.FAILED
    assert repository.session.error_code == "LLM_OUTPUT_INVALID"
    assert repository.session.provider == "deepseek"
    assert repository.session.prompt_version == "m3-crawler-v1"
    assert repository.session.usage is not None
    assert repository.session.usage.call_count == 1
    assert repository.session.crawler_queries is not None
    assert repository.session.crawler_queries[0] == ("Find methodologically relevant prior work.")
    assert len(repository.session.crawler_queries) == 5
    assert (
        repository.session.crawler_decision_reason == "Use bounded search and citation expansion."
    )
