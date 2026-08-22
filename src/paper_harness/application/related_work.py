"""Bounded PaSa-derived Crawler and Selector for historical related work."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from paper_harness.application.historical_ranking import (
    RankingSignals,
    combine_ranking_signals,
    lexical_similarity,
)
from paper_harness.application.read_models import HistoricalRetrievalMatch, SearchSessionDetail
from paper_harness.application.scholarly_mapping import external_stub_from_scholarly_paper
from paper_harness.domain.analysis import AnalysisScope, ModelUsage, VerificationStatus
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import (
    M3_CRAWLER_PROMPT_VERSION,
    M3_SELECTOR_PROMPT_VERSION,
    CandidateOrigin,
    CandidateScoreComponents,
    CandidateSelectionInput,
    CandidateSelectionRequest,
    CrawlerPlanRequest,
    ExternalPaperStub,
    GeneratedCandidateSelection,
    GeneratedCrawlerPlan,
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
from paper_harness.domain.identity import (
    stable_candidate_discovery_id,
    stable_embedding_id,
    stable_search_action_id,
    stable_search_candidate_id,
    stable_search_session_id,
)
from paper_harness.domain.models import TopicConfig
from paper_harness.ports.llm import LLMPort, LLMPortError
from paper_harness.ports.repository import RepositoryPort
from paper_harness.ports.scholarly_search import (
    ScholarlyPaper,
    ScholarlySearchError,
    ScholarlySearchPort,
    ScholarlySearchResponseError,
    ScholarlySearchUnavailableError,
)
from paper_harness.ports.scientific_embedding import (
    ScientificEmbeddingOutputError,
    ScientificEmbeddingPort,
    ScientificEmbeddingPortError,
    ScientificPaperText,
)

MAX_SEARCH_ACTION_RESULTS = 500
MAX_RELATION_ACTION_RESULTS = 100


def _search_limits_identity(limits: SearchLimits) -> str:
    return ":".join(
        (
            str(limits.max_steps),
            str(limits.max_queries),
            str(limits.max_queue_size),
            str(limits.max_citation_depth),
            str(limits.max_candidates),
            str(limits.max_selected_candidates),
            format(limits.per_operation_timeout_seconds, ".12g"),
            format(limits.overall_timeout_seconds, ".12g"),
        )
    )


class RelatedWorkInputError(RuntimeError):
    error_code = "RELATED_WORK_INPUT_INVALID"
    retryable = False


class _OverallSearchTimeout(RuntimeError):
    error_code = "RELATED_WORK_OVERALL_TIMEOUT"
    retryable = True


@dataclass(slots=True)
class _CandidateState:
    stub: ExternalPaperStub
    origins: set[CandidateOrigin] = field(default_factory=lambda: set[CandidateOrigin]())
    discoveries: list[SearchCandidateDiscovery] = field(
        default_factory=lambda: list[SearchCandidateDiscovery]()
    )
    first_action_id: UUID | None = None
    relation_depth: int = 0
    semantic_rank: int | None = None
    semantic_result_count: int = 0
    lexical_score: float = 0.0
    cosine_similarity: float | None = None
    citation_related: bool = False
    recommendation_related: bool = False
    local_paper_id: UUID | None = None
    local_paper_version_id: UUID | None = None

    def scores(self) -> CandidateScoreComponents:
        return combine_ranking_signals(
            RankingSignals(
                semantic_scholar_rank=self.semantic_rank,
                semantic_scholar_result_count=self.semantic_result_count,
                lexical_score=self.lexical_score,
                cosine_similarity=self.cosine_similarity,
                citation_related=self.citation_related,
                recommendation_related=self.recommendation_related,
            )
        )


class RelatedWorkSearch:
    """First-party bounded realization of PaSa's Crawler/Selector split."""

    def __init__(
        self,
        *,
        repository: RepositoryPort,
        scholarly_search: ScholarlySearchPort,
        llm: LLMPort,
        embeddings: ScientificEmbeddingPort,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repository = repository
        self._scholarly_search = scholarly_search
        self._llm = llm
        self._embeddings = embeddings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic

    def execute(
        self,
        *,
        topic: TopicConfig,
        source_paper_id: UUID,
        objective: str,
        year_from: int,
        year_to: int,
        limits: SearchLimits,
        pipeline_execution_id: UUID | None = None,
        source_paper_version_id: UUID | None = None,
        source_analysis_id: UUID | None = None,
        source_analysis_scope: AnalysisScope | None = None,
    ) -> SearchSessionDetail:
        if not 1000 <= year_from <= year_to <= 9999:
            raise RelatedWorkInputError("related-work year range is invalid")
        exact_provenance = (
            source_paper_version_id,
            source_analysis_id,
            source_analysis_scope,
        )
        if any(value is not None for value in exact_provenance) and any(
            value is None for value in exact_provenance
        ):
            raise RelatedWorkInputError(
                "exact related-work analysis provenance must be supplied together"
            )
        paper_detail = self._repository.get_paper(source_paper_id)
        if paper_detail is None:
            raise RelatedWorkInputError("source paper does not exist")
        analysis_detail = self._repository.get_paper_analysis(
            source_paper_id,
            paper_version_id=source_paper_version_id,
            analysis_scope=source_analysis_scope,
        )
        if analysis_detail is None:
            raise RelatedWorkInputError(
                "related-work search requires a persisted source-paper analysis"
            )
        if source_analysis_id is not None and (
            analysis_detail.analysis.id != source_analysis_id
            or analysis_detail.analysis.paper_version_id != source_paper_version_id
            or analysis_detail.analysis.analysis_scope is not source_analysis_scope
        ):
            raise RelatedWorkInputError(
                "related-work source analysis does not match the requested exact provenance"
            )
        source_version_id = analysis_detail.analysis.paper_version_id
        source_version = next(
            (version for version in paper_detail.versions if version.id == source_version_id),
            None,
        )
        if source_version is None:
            raise RelatedWorkInputError(
                "related-work source analysis references an unavailable paper version"
            )
        prior_year_to = min(year_to, source_version.submitted_at.year)
        if year_from > prior_year_to:
            raise RelatedWorkInputError(
                "related-work year range begins after the source paper was submitted"
            )
        started_at = self._aware_now()
        deadline = self._monotonic() + limits.overall_timeout_seconds
        session_id = (
            uuid4()
            if pipeline_execution_id is None
            else stable_search_session_id(
                source_version_id,
                objective,
                f"{pipeline_execution_id}:{year_from}:{prior_year_to}:"
                f"{_search_limits_identity(limits)}",
                f"{M3_CRAWLER_PROMPT_VERSION}+{M3_SELECTOR_PROMPT_VERSION}",
            )
        )
        existing_detail = self._repository.get_search_session(session_id)
        if existing_detail is not None:
            existing = existing_detail.session
            if (
                existing.pipeline_execution_id != pipeline_execution_id
                or existing.topic_id != topic.id
                or existing.source_paper_id != source_paper_id
                or existing.source_paper_version_id != source_version_id
                or existing.source_analysis_id != analysis_detail.analysis.id
                or existing.source_analysis_scope is not analysis_detail.analysis.analysis_scope
                or existing.requested_year_from != year_from
                or existing.effective_year_to != prior_year_to
                or existing.objective != objective
                or existing.limits != limits
            ):
                raise RelatedWorkInputError(
                    "stable related-work session conflicts with persisted provenance"
                )
            if existing.status is SearchSessionStatus.COMPLETE:
                return existing_detail
            session = self._repository.restart_search_session(
                session_id,
                restarted_at=started_at,
            )
        else:
            session = self._repository.start_search_session(
                SearchSession(
                    id=session_id,
                    pipeline_execution_id=pipeline_execution_id,
                    topic_id=topic.id,
                    source_paper_id=source_paper_id,
                    source_paper_version_id=source_version_id,
                    source_analysis_id=analysis_detail.analysis.id,
                    source_analysis_scope=analysis_detail.analysis.analysis_scope,
                    requested_year_from=year_from,
                    effective_year_to=prior_year_to,
                    objective=objective,
                    status=SearchSessionStatus.RUNNING,
                    limits=limits,
                    started_at=started_at,
                    completed_at=None,
                    stop_reason=None,
                    error_code=None,
                    error_detail=None,
                    provider=None,
                    configured_model=None,
                    model_version=None,
                    prompt_version=None,
                    usage=None,
                    schema_version=1,
                    created_at=started_at,
                )
            )
        candidates: dict[str, _CandidateState] = {}
        source_semantic_scholar_ids: set[str] = set()
        step = 0
        query_count = 0
        stop_reason = SearchStopReason.QUEUE_EXHAUSTED
        source_text = " ".join(
            (
                source_version.title,
                analysis_detail.analysis.research_problem,
                analysis_detail.analysis.method_summary,
            )
        )
        crawler_plan: GeneratedCrawlerPlan | None = None
        try:
            local_timed_out = self._add_local_candidates(
                session=session,
                topic=topic,
                paper_title=source_version.title,
                paper_abstract=source_version.abstract,
                source_text=source_text,
                candidates=candidates,
                limit=min(limits.max_queue_size, limits.max_candidates),
                year_from=year_from,
                year_to=prior_year_to,
                deadline=deadline,
            )
            if local_timed_out:
                stop_reason = SearchStopReason.OVERALL_TIMEOUT
            if stop_reason is SearchStopReason.QUEUE_EXHAUSTED:
                if len(candidates) >= limits.max_candidates:
                    stop_reason = SearchStopReason.MAX_CANDIDATES
                elif len(candidates) >= limits.max_queue_size:
                    stop_reason = SearchStopReason.MAX_QUEUE_SIZE
                elif step >= limits.max_steps:
                    stop_reason = SearchStopReason.MAX_STEPS
                else:
                    step += 1
                    source_records = self._run_action(
                        session_id=session.id,
                        step=step,
                        tool=SearchTool.GET_PAPER,
                        query=None,
                        target_id=(f"ARXIV:{source_version.canonical_arxiv_id}"),
                        positive_ids=(),
                        year_from=None,
                        year_to=None,
                        relation_depth=0,
                        requested_limit=1,
                        invoke=lambda timeout_seconds: self._resolve_source_paper(
                            source_version.canonical_arxiv_id,
                            timeout_seconds=timeout_seconds,
                        ),
                        candidates=candidates,
                        source_text=source_text,
                        origin=CandidateOrigin.SEARCH,
                        excluded_arxiv_id=source_version.canonical_arxiv_id,
                        excluded_semantic_scholar_ids=frozenset(),
                        deadline=deadline,
                        per_operation_timeout=limits.per_operation_timeout_seconds,
                    )
                    source_semantic_scholar_ids.update(
                        record.semantic_scholar_id for record in source_records
                    )
                    if step >= limits.max_steps:
                        stop_reason = SearchStopReason.MAX_STEPS
            if stop_reason is SearchStopReason.QUEUE_EXHAUSTED:
                remaining = deadline - self._monotonic()
                if remaining < 1:
                    stop_reason = SearchStopReason.OVERALL_TIMEOUT
                else:
                    try:
                        crawler_plan = self._llm.plan_scholarly_search(
                            CrawlerPlanRequest(
                                objective=objective,
                                source_title=source_version.title,
                                source_research_problem=(analysis_detail.analysis.research_problem),
                                source_method=analysis_detail.analysis.method_summary,
                                topic_name=topic.name,
                                topic_description=topic.description,
                                topic_include_terms=topic.include_terms,
                                topic_exclude_terms=topic.exclude_terms,
                                year_from=year_from,
                                year_to=prior_year_to,
                                max_queries=limits.max_queries,
                            ),
                            timeout_seconds=min(
                                limits.per_operation_timeout_seconds,
                                remaining,
                            ),
                        )
                    except LLMPortError:
                        if self._monotonic() >= deadline:
                            raise _OverallSearchTimeout(
                                "DeepSeek crawler exhausted the overall search timeout"
                            ) from None
                        raise
                    crawler_plan = replace(
                        crawler_plan,
                        queries=tuple(dict.fromkeys(crawler_plan.queries))[: limits.max_queries],
                    )
                    session = self._repository.persist_search_crawler_plan(session.id, crawler_plan)
            queries = () if crawler_plan is None else crawler_plan.queries
            for query in queries:
                if self._monotonic() >= deadline:
                    stop_reason = SearchStopReason.OVERALL_TIMEOUT
                    break
                if step >= limits.max_steps:
                    stop_reason = SearchStopReason.MAX_STEPS
                    break
                if query_count >= limits.max_queries:
                    stop_reason = SearchStopReason.MAX_QUERIES
                    break
                if len(candidates) >= limits.max_candidates:
                    stop_reason = SearchStopReason.MAX_CANDIDATES
                    break
                if len(candidates) >= limits.max_queue_size:
                    stop_reason = SearchStopReason.MAX_QUEUE_SIZE
                    break
                step += 1
                query_count += 1
                requested_limit = min(
                    MAX_SEARCH_ACTION_RESULTS,
                    limits.max_candidates - len(candidates),
                    limits.max_queue_size - len(candidates),
                )
                records = self._run_action(
                    session_id=session.id,
                    step=step,
                    tool=SearchTool.SEARCH_PAPERS,
                    query=query,
                    target_id=None,
                    positive_ids=(),
                    year_from=year_from,
                    year_to=prior_year_to,
                    relation_depth=0,
                    requested_limit=requested_limit,
                    invoke=lambda timeout_seconds, query=query, requested_limit=requested_limit: (
                        self._scholarly_search.search_papers(
                            query,
                            year_from,
                            prior_year_to,
                            requested_limit,
                            timeout_seconds=timeout_seconds,
                        )
                    ),
                    candidates=candidates,
                    source_text=source_text,
                    origin=CandidateOrigin.SEARCH,
                    excluded_arxiv_id=source_version.canonical_arxiv_id,
                    excluded_semantic_scholar_ids=frozenset(source_semantic_scholar_ids),
                    deadline=deadline,
                    per_operation_timeout=limits.per_operation_timeout_seconds,
                )
                source_semantic_scholar_ids.update(
                    record.semantic_scholar_id
                    for record in records
                    if record.external_ids.arxiv_id == source_version.canonical_arxiv_id
                )
                if not records:
                    continue

            if stop_reason is SearchStopReason.QUEUE_EXHAUSTED:
                if query_count >= limits.max_queries and len(queries) >= limits.max_queries:
                    stop_reason = SearchStopReason.MAX_QUERIES
                elif len(candidates) >= limits.max_candidates:
                    stop_reason = SearchStopReason.MAX_CANDIDATES
                elif len(candidates) >= limits.max_queue_size:
                    stop_reason = SearchStopReason.MAX_QUEUE_SIZE

            if (
                stop_reason is SearchStopReason.QUEUE_EXHAUSTED
                and candidates
                and crawler_plan is not None
                and crawler_plan.use_recommendations
                and step < limits.max_steps
                and self._monotonic() < deadline
            ):
                positive_ids = tuple(
                    state.stub.semantic_scholar_id for state in _ranked_states(candidates)[:5]
                )
                step += 1
                self._run_action(
                    session_id=session.id,
                    step=step,
                    tool=SearchTool.GET_RECOMMENDATIONS,
                    query=None,
                    target_id=None,
                    positive_ids=positive_ids,
                    year_from=year_from,
                    year_to=prior_year_to,
                    relation_depth=0,
                    requested_limit=min(
                        MAX_RELATION_ACTION_RESULTS,
                        limits.max_candidates - len(candidates),
                        limits.max_queue_size - len(candidates),
                    ),
                    invoke=lambda timeout_seconds: self._scholarly_search.get_recommendations(
                        positive_ids,
                        timeout_seconds=timeout_seconds,
                    ),
                    candidates=candidates,
                    source_text=source_text,
                    origin=CandidateOrigin.RECOMMENDATIONS,
                    excluded_arxiv_id=source_version.canonical_arxiv_id,
                    excluded_semantic_scholar_ids=frozenset(source_semantic_scholar_ids),
                    deadline=deadline,
                    per_operation_timeout=limits.per_operation_timeout_seconds,
                )

            expansion_queue: list[tuple[str, int]] = []
            if stop_reason is SearchStopReason.QUEUE_EXHAUSTED:
                expansion_roots = [
                    (paper_id, 0) for paper_id in sorted(source_semantic_scholar_ids)
                ]
                expansion_roots.extend(
                    (state.stub.semantic_scholar_id, state.relation_depth)
                    for state in _ranked_states(candidates)
                    if state.stub.semantic_scholar_id not in source_semantic_scholar_ids
                )
                expansion_queue = expansion_roots[: limits.max_queue_size]
            expanded: set[tuple[str, SearchTool]] = set()
            while expansion_queue:
                if self._monotonic() >= deadline:
                    stop_reason = SearchStopReason.OVERALL_TIMEOUT
                    break
                if step >= limits.max_steps:
                    stop_reason = SearchStopReason.MAX_STEPS
                    break
                if len(candidates) >= limits.max_candidates:
                    stop_reason = SearchStopReason.MAX_CANDIDATES
                    break
                if len(candidates) >= limits.max_queue_size:
                    stop_reason = SearchStopReason.MAX_QUEUE_SIZE
                    break
                paper_id, depth = expansion_queue.pop(0)
                if depth >= limits.max_citation_depth:
                    continue
                expansion_tools: list[
                    tuple[
                        SearchTool,
                        CandidateOrigin,
                        Callable[[float], tuple[ScholarlyPaper, ...]],
                    ]
                ] = []
                if crawler_plan is not None and crawler_plan.expand_references:
                    expansion_tools.append(
                        (
                            SearchTool.GET_REFERENCES,
                            CandidateOrigin.REFERENCES,
                            lambda timeout_seconds, paper_id=paper_id: (
                                self._scholarly_search.get_references(
                                    paper_id,
                                    timeout_seconds=timeout_seconds,
                                )
                            ),
                        )
                    )
                if crawler_plan is not None and crawler_plan.expand_citations:
                    expansion_tools.append(
                        (
                            SearchTool.GET_CITATIONS,
                            CandidateOrigin.CITATIONS,
                            lambda timeout_seconds, paper_id=paper_id: (
                                self._scholarly_search.get_citations(
                                    paper_id,
                                    timeout_seconds=timeout_seconds,
                                )
                            ),
                        )
                    )
                for tool, origin, invoke in expansion_tools:
                    if self._monotonic() >= deadline:
                        stop_reason = SearchStopReason.OVERALL_TIMEOUT
                        break
                    if step >= limits.max_steps or len(candidates) >= limits.max_candidates:
                        break
                    if (paper_id, tool) in expanded:
                        continue
                    expanded.add((paper_id, tool))
                    before_ids = set(candidates)
                    step += 1
                    self._run_action(
                        session_id=session.id,
                        step=step,
                        tool=tool,
                        query=None,
                        target_id=paper_id,
                        positive_ids=(),
                        year_from=year_from,
                        year_to=prior_year_to,
                        relation_depth=depth + 1,
                        requested_limit=min(
                            MAX_RELATION_ACTION_RESULTS,
                            limits.max_candidates - len(candidates),
                            limits.max_queue_size - len(candidates),
                        ),
                        invoke=invoke,
                        candidates=candidates,
                        source_text=source_text,
                        origin=origin,
                        excluded_arxiv_id=source_version.canonical_arxiv_id,
                        excluded_semantic_scholar_ids=frozenset(source_semantic_scholar_ids),
                        deadline=deadline,
                        per_operation_timeout=limits.per_operation_timeout_seconds,
                    )
                    expansion_queue.extend(
                        (candidate_id, depth + 1)
                        for candidate_id in sorted(set(candidates) - before_ids)
                    )

            selection: GeneratedCandidateSelection | None = None
            terminal_candidates: tuple[SearchCandidate, ...] = ()
            remaining = deadline - self._monotonic()
            if (
                candidates
                and stop_reason is not SearchStopReason.OVERALL_TIMEOUT
                and remaining >= 1
            ):
                terminal_candidates, selection = self._select_candidates(
                    objective=objective,
                    source_title=source_version.title,
                    source_problem=analysis_detail.analysis.research_problem,
                    source_method=analysis_detail.analysis.method_summary,
                    candidates=candidates,
                    limit=limits.max_selected_candidates,
                    session_id=session.id,
                    deadline=deadline,
                    per_operation_timeout=limits.per_operation_timeout_seconds,
                )
            elif candidates and remaining < 1:
                stop_reason = SearchStopReason.OVERALL_TIMEOUT
            if terminal_candidates:
                self._repository.update_search_candidate_decisions(session.id, terminal_candidates)
            selected_count = sum(
                item.decision is SelectionDecision.SELECTED for item in terminal_candidates
            )
            if (
                stop_reason is SearchStopReason.QUEUE_EXHAUSTED
                and selected_count >= limits.max_selected_candidates
            ):
                stop_reason = SearchStopReason.MAX_SELECTED_CANDIDATES
            completed = self._repository.complete_search_session(
                session.id,
                completed_at=self._aware_now(),
                stop_reason=stop_reason,
                provenance=_search_model_provenance(crawler_plan, selection),
            )
            detail = self._repository.get_search_session(completed.id)
            if detail is None:
                raise RelatedWorkInputError("completed search session cannot be read back")
            return detail
        except _OverallSearchTimeout:
            completed = self._repository.complete_search_session(
                session.id,
                completed_at=self._aware_now(),
                stop_reason=SearchStopReason.OVERALL_TIMEOUT,
                provenance=_search_model_provenance(crawler_plan, None),
            )
            detail = self._repository.get_search_session(completed.id)
            if detail is None:
                raise RelatedWorkInputError(
                    "timed-out search session cannot be read back"
                ) from None
            return detail
        except (
            ScholarlySearchError,
            ScientificEmbeddingPortError,
            LLMPortError,
            DomainInvariantError,
            RelatedWorkInputError,
        ) as error:
            self._repository.fail_search_session(
                session.id,
                completed_at=self._aware_now(),
                error_code=getattr(error, "error_code", "RELATED_WORK_SEARCH_INVALID"),
                error_detail=_concise_detail(error),
                provenance=_search_model_provenance(crawler_plan, None),
            )
            raise

    def _resolve_source_paper(
        self,
        canonical_arxiv_id: str,
        *,
        timeout_seconds: float,
    ) -> tuple[ScholarlyPaper, ...]:
        paper = self._scholarly_search.get_paper_by_arxiv_id(
            canonical_arxiv_id,
            timeout_seconds=timeout_seconds,
        )
        if paper.external_ids.arxiv_id != canonical_arxiv_id:
            raise ScholarlySearchResponseError(
                "Semantic Scholar source-paper mapping did not match the requested arXiv identity"
            )
        return (paper,)

    def _add_local_candidates(
        self,
        *,
        session: SearchSession,
        topic: TopicConfig,
        paper_title: str,
        paper_abstract: str,
        source_text: str,
        candidates: dict[str, _CandidateState],
        limit: int,
        year_from: int,
        year_to: int,
        deadline: float,
    ) -> bool:
        if self._monotonic() >= deadline:
            return True
        generated = self._embeddings.encode(
            (
                ScientificPaperText(
                    key=str(session.source_paper_version_id),
                    title=paper_title,
                    abstract=paper_abstract,
                ),
            )
        )
        if len(generated) != 1 or generated[0].key != str(session.source_paper_version_id):
            raise ScientificEmbeddingOutputError(
                "SPECTER2 did not return the source-paper embedding"
            )
        if self._monotonic() >= deadline:
            return True
        source_vector = generated[0].vector
        if len(source_vector) != self._embeddings.dimension:
            raise ScientificEmbeddingOutputError(
                "SPECTER2 source-paper vector has the wrong dimension"
            )
        generated_at = self._aware_now()
        self._repository.upsert_scientific_embeddings(
            (
                ScientificEmbedding(
                    id=stable_embedding_id(
                        session.source_paper_version_id,
                        model_identifier=self._embeddings.model_identifier,
                        model_revision=self._embeddings.model_revision,
                        tokenizer_identifier=self._embeddings.tokenizer_identifier,
                        tokenizer_revision=self._embeddings.tokenizer_revision,
                        dimension=self._embeddings.dimension,
                        preprocessing_contract=self._embeddings.preprocessing_contract,
                        model_provenance=self._embeddings.model_provenance,
                        source=self._embeddings.source,
                    ),
                    paper_version_id=session.source_paper_version_id,
                    external_paper_id=None,
                    model_identifier=self._embeddings.model_identifier,
                    model_revision=self._embeddings.model_revision,
                    tokenizer_identifier=self._embeddings.tokenizer_identifier,
                    tokenizer_revision=self._embeddings.tokenizer_revision,
                    dimension=self._embeddings.dimension,
                    preprocessing_contract=self._embeddings.preprocessing_contract,
                    model_provenance=self._embeddings.model_provenance,
                    vector=source_vector,
                    generated_at=generated_at,
                    source=self._embeddings.source,
                    schema_version=1,
                    created_at=generated_at,
                ),
            )
        )
        if self._monotonic() >= deadline:
            return True
        lexical = self._repository.search_historical_lexically(
            topic.id, query=source_text[:500], limit=limit
        )
        if self._monotonic() >= deadline:
            return True
        vector = self._repository.search_historical_by_vector(
            topic.id,
            vector=source_vector,
            model_identifier=self._embeddings.model_identifier,
            model_revision=self._embeddings.model_revision,
            tokenizer_identifier=self._embeddings.tokenizer_identifier,
            tokenizer_revision=self._embeddings.tokenizer_revision,
            dimension=self._embeddings.dimension,
            preprocessing_contract=self._embeddings.preprocessing_contract,
            model_provenance=self._embeddings.model_provenance,
            source=self._embeddings.source,
            limit=limit,
        )
        if self._monotonic() >= deadline:
            return True
        by_id: dict[
            str, tuple[ExternalPaperStub, float, float | None, HistoricalRetrievalMatch]
        ] = {}
        for match in lexical:
            by_id[match.external_paper.semantic_scholar_id] = (
                match.external_paper,
                match.score,
                None,
                match,
            )
        for match in vector:
            existing = by_id.get(match.external_paper.semantic_scholar_id)
            by_id[match.external_paper.semantic_scholar_id] = (
                match.external_paper,
                0.0 if existing is None else existing[1],
                match.score * 2 - 1,
                match,
            )
        ranked_local = sorted(
            (
                item
                for item in by_id.values()
                if item[3].corpus_entry.local_paper_id != session.source_paper_id
                and _within_year_window(item[0], year_from=year_from, year_to=year_to)
            ),
            key=lambda item: (
                -combine_ranking_signals(
                    RankingSignals(
                        lexical_score=item[1],
                        cosine_similarity=item[2],
                    )
                ).final,
                item[0].semantic_scholar_id,
            ),
        )[:limit]
        persisted: list[SearchCandidate] = []
        discoveries: list[SearchCandidateDiscovery] = []
        papers: list[ExternalPaperStub] = []
        for stub, lexical_score, cosine, match in ranked_local:
            state = candidates.setdefault(stub.semantic_scholar_id, _CandidateState(stub=stub))
            state.local_paper_id = match.corpus_entry.local_paper_id
            state.local_paper_version_id = match.corpus_entry.local_paper_version_id
            state.lexical_score = max(state.lexical_score, lexical_score)
            state.cosine_similarity = (
                cosine
                if state.cosine_similarity is None
                else max(state.cosine_similarity, cosine if cosine is not None else -1.0)
            )
            for origin in (
                CandidateOrigin.LOCAL_LEXICAL if lexical_score else None,
                CandidateOrigin.LOCAL_VECTOR if cosine is not None else None,
            ):
                if origin is None or origin in state.origins:
                    continue
                discovery = self._discovery(
                    session.id,
                    stub.semantic_scholar_id,
                    origin=origin,
                    action_id=None,
                    depth=0,
                )
                state.origins.add(origin)
                state.discoveries.append(discovery)
                discoveries.append(discovery)
            papers.append(stub)
            persisted.append(self._pending_candidate(session.id, state, rank=1))
        if persisted:
            self._repository.persist_local_search_candidates(
                session.id,
                papers=tuple(papers),
                candidates=tuple(persisted),
                discoveries=tuple(discoveries),
            )
        return self._monotonic() >= deadline

    def _run_action(
        self,
        *,
        session_id: UUID,
        step: int,
        tool: SearchTool,
        query: str | None,
        target_id: str | None,
        positive_ids: tuple[str, ...],
        year_from: int | None,
        year_to: int | None,
        relation_depth: int,
        requested_limit: int,
        invoke: Callable[[float], tuple[ScholarlyPaper, ...]],
        candidates: dict[str, _CandidateState],
        source_text: str,
        origin: CandidateOrigin,
        excluded_arxiv_id: str,
        excluded_semantic_scholar_ids: frozenset[str],
        deadline: float,
        per_operation_timeout: float,
    ) -> tuple[ScholarlyPaper, ...]:
        if requested_limit < 1:
            return ()
        action_id = stable_search_action_id(session_id, step)
        started_at = self._aware_now()
        running = SearchAction(
            id=action_id,
            session_id=session_id,
            step=step,
            tool=tool,
            status=SearchActionStatus.RUNNING,
            query=query,
            target_semantic_scholar_id=target_id,
            target_arxiv_id=None,
            positive_paper_ids=positive_ids,
            year_from=year_from,
            year_to=year_to,
            requested_limit=requested_limit,
            result_count=0,
            relation_depth=relation_depth,
            decision_reason=_action_reason(tool),
            error_code=None,
            retryable=None,
            error_detail=None,
            duration_ms=0,
            created_at=started_at,
            completed_at=None,
            schema_version=1,
        )
        self._repository.start_search_action(running)
        started = self._monotonic()
        try:
            remaining = deadline - self._monotonic()
            if remaining < 1:
                raise _OverallSearchTimeout(
                    "scholarly search operation exhausted the overall search timeout"
                )
            operation_timeout = min(per_operation_timeout, remaining)
            records = invoke(operation_timeout)
            duration = self._monotonic() - started
            if self._monotonic() >= deadline:
                raise _OverallSearchTimeout(
                    "scholarly search operation exhausted the overall search timeout"
                )
            if duration > operation_timeout:
                raise ScholarlySearchUnavailableError(
                    "scholarly search operation exceeded its configured timeout"
                )
            bounded = records[:requested_limit]
            observed_at = self._aware_now()
            mapped_stubs = tuple(
                external_stub_from_scholarly_paper(record, observed_at=observed_at)
                for record in bounded
            )
            stubs = tuple(
                {
                    stub.semantic_scholar_id: stub
                    for stub in mapped_stubs
                    if _within_year_window(stub, year_from=year_from, year_to=year_to)
                }.values()
            )
        except (
            ScholarlySearchError,
            ScientificEmbeddingPortError,
            DomainInvariantError,
            _OverallSearchTimeout,
        ) as error:
            failed = replace(
                running,
                status=SearchActionStatus.FAILED,
                error_code=getattr(error, "error_code", "SCHOLARLY_SEARCH_RESPONSE_INVALID"),
                retryable=getattr(error, "retryable", False),
                error_detail=_concise_detail(error),
                duration_ms=_bounded_duration_ms(self._monotonic() - started),
                completed_at=self._aware_now(),
            )
            self._repository.persist_search_action_result(
                failed,
                papers=(),
                candidates=(),
                discoveries=(),
            )
            raise
        discoveries: list[SearchCandidateDiscovery] = []
        action_candidates: list[SearchCandidate] = []
        retained_stubs = tuple(
            stub
            for stub in stubs
            if stub.arxiv_id != excluded_arxiv_id
            and stub.semantic_scholar_id not in excluded_semantic_scholar_ids
        )
        for position, stub in enumerate(retained_stubs, start=1):
            state = candidates.get(stub.semantic_scholar_id)
            if state is None:
                state = _CandidateState(stub=stub, relation_depth=relation_depth)
                candidates[stub.semantic_scholar_id] = state
            state.origins.add(origin)
            state.first_action_id = state.first_action_id or action_id
            state.relation_depth = min(state.relation_depth, relation_depth)
            state.lexical_score = max(
                state.lexical_score,
                lexical_similarity(source_text, f"{stub.title} {stub.abstract or ''}"),
            )
            if origin is CandidateOrigin.SEARCH and (
                state.semantic_rank is None or position < state.semantic_rank
            ):
                state.semantic_rank = position
                state.semantic_result_count = max(1, len(stubs))
            state.citation_related = state.citation_related or origin in {
                CandidateOrigin.REFERENCES,
                CandidateOrigin.CITATIONS,
            }
            state.recommendation_related = (
                state.recommendation_related or origin is CandidateOrigin.RECOMMENDATIONS
            )
            discovery = self._discovery(
                session_id,
                stub.semantic_scholar_id,
                origin=origin,
                action_id=action_id,
                depth=relation_depth,
            )
            if discovery.id not in {item.id for item in state.discoveries}:
                state.discoveries.append(discovery)
                discoveries.append(discovery)
            action_candidates.append(self._pending_candidate(session_id, state, rank=position))
        completed = replace(
            running,
            status=SearchActionStatus.COMPLETED,
            result_count=len(stubs),
            duration_ms=_bounded_duration_ms(self._monotonic() - started),
            completed_at=observed_at,
        )
        self._repository.persist_search_action_result(
            completed,
            papers=stubs,
            candidates=tuple(action_candidates),
            discoveries=tuple(discoveries),
        )
        return bounded

    def _select_candidates(
        self,
        *,
        objective: str,
        source_title: str,
        source_problem: str,
        source_method: str,
        candidates: dict[str, _CandidateState],
        limit: int,
        session_id: UUID,
        deadline: float,
        per_operation_timeout: float,
    ) -> tuple[tuple[SearchCandidate, ...], GeneratedCandidateSelection | None]:
        ranked = _ranked_states(candidates)
        if not ranked:
            return (), None
        remaining = deadline - self._monotonic()
        if remaining < 1:
            raise _OverallSearchTimeout("DeepSeek selector exhausted the overall search timeout")
        try:
            generated = self._llm.select_prior_work(
                CandidateSelectionRequest(
                    objective=objective,
                    source_title=source_title,
                    source_research_problem=source_problem,
                    source_method=source_method,
                    candidates=tuple(
                        CandidateSelectionInput(
                            semantic_scholar_id=state.stub.semantic_scholar_id,
                            title=state.stub.title,
                            abstract=state.stub.abstract,
                            year=state.stub.year,
                            venue=state.stub.venue,
                            scores=state.scores(),
                        )
                        for state in ranked
                    ),
                    max_selected_candidates=limit,
                ),
                timeout_seconds=min(per_operation_timeout, remaining),
            )
        except LLMPortError:
            if self._monotonic() >= deadline:
                raise _OverallSearchTimeout(
                    "DeepSeek selector exhausted the overall search timeout"
                ) from None
            raise
        decisions = {item.semantic_scholar_id: item for item in generated.decisions}
        selected_order = tuple(
            state.stub.semantic_scholar_id
            for state in ranked
            if (decision := decisions.get(state.stub.semantic_scholar_id)) is not None
            and decision.decision is SelectionDecision.SELECTED
        )
        selected_ids = frozenset(selected_order[:limit])

        def candidate_value(state: _CandidateState, rank: int) -> SearchCandidate:
            semantic_scholar_id = state.stub.semantic_scholar_id
            decision = decisions.get(semantic_scholar_id)
            if decision is None:
                return replace(
                    self._pending_candidate(session_id, state, rank=rank),
                    decision_reason="Selector returned no usable decision for this candidate.",
                )
            if (
                decision.decision is SelectionDecision.SELECTED
                and semantic_scholar_id not in selected_ids
            ):
                return replace(
                    self._pending_candidate(session_id, state, rank=rank),
                    decision_reason="Excluded by the configured local selection bound.",
                )
            return SearchCandidate(
                id=stable_search_candidate_id(session_id, semantic_scholar_id),
                session_id=session_id,
                external_paper_id=state.stub.id,
                semantic_scholar_id=semantic_scholar_id,
                local_paper_id=state.local_paper_id,
                local_paper_version_id=state.local_paper_version_id,
                discovered_by_action_id=state.first_action_id,
                origins=tuple(sorted(state.origins, key=lambda item: item.value)),
                relation_depth=state.relation_depth,
                scores=state.scores(),
                rank=rank,
                decision=decision.decision,
                decision_reason=decision.reason,
                provider=generated.provider,
                configured_model=generated.configured_model,
                model_version=generated.model_version,
                prompt_version=generated.prompt_version,
                generated_at=generated.generated_at,
                verification_status=VerificationStatus.UNVERIFIED,
                schema_version=1,
                created_at=generated.generated_at,
            )

        return (
            tuple(candidate_value(state, rank) for rank, state in enumerate(ranked, start=1)),
            generated,
        )

    def _pending_candidate(
        self, session_id: UUID, state: _CandidateState, *, rank: int
    ) -> SearchCandidate:
        return SearchCandidate(
            id=stable_search_candidate_id(session_id, state.stub.semantic_scholar_id),
            session_id=session_id,
            external_paper_id=state.stub.id,
            semantic_scholar_id=state.stub.semantic_scholar_id,
            local_paper_id=state.local_paper_id,
            local_paper_version_id=state.local_paper_version_id,
            discovered_by_action_id=state.first_action_id,
            origins=tuple(sorted(state.origins, key=lambda item: item.value)),
            relation_depth=state.relation_depth,
            scores=state.scores(),
            rank=max(1, rank),
            decision=SelectionDecision.PENDING,
            decision_reason="Awaiting bounded Selector evaluation.",
            provider=None,
            configured_model=None,
            model_version=None,
            prompt_version=None,
            generated_at=None,
            verification_status=VerificationStatus.UNVERIFIED,
            schema_version=1,
            created_at=self._aware_now(),
        )

    def _discovery(
        self,
        session_id: UUID,
        semantic_scholar_id: str,
        *,
        origin: CandidateOrigin,
        action_id: UUID | None,
        depth: int,
    ) -> SearchCandidateDiscovery:
        candidate_id = stable_search_candidate_id(session_id, semantic_scholar_id)
        return SearchCandidateDiscovery(
            id=stable_candidate_discovery_id(candidate_id, origin.value, action_id, depth),
            candidate_id=candidate_id,
            action_id=action_id,
            origin=origin,
            relation_depth=depth,
            discovered_at=self._aware_now(),
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise DomainInvariantError("related-work clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def build_related_work_objective(topic: TopicConfig) -> str:
    """Describe the configured topic for evidence-backed prior-work retrieval."""

    return (
        f"Identify historical and related work for {topic.name}: {topic.description} "
        "Use persisted evidence for systematic comparison to the source paper."
    )


def allowed_search_tools() -> frozenset[SearchTool]:
    return frozenset(SearchTool)


def _search_model_provenance(
    crawler: GeneratedCrawlerPlan | None,
    selector: GeneratedCandidateSelection | None,
) -> SearchModelProvenance | None:
    generated = tuple(item for item in (crawler, selector) if item is not None)
    if not generated:
        return None
    first = generated[0]
    if any(
        (
            item.provider,
            item.configured_model,
            item.model_version,
        )
        != (
            first.provider,
            first.configured_model,
            first.model_version,
        )
        for item in generated[1:]
    ):
        raise DomainInvariantError(
            "crawler and selector model provenance must identify the same model"
        )
    estimated_costs = tuple(item.usage.estimated_cost_usd for item in generated)
    estimated_cost = (
        None
        if any(value is None for value in estimated_costs)
        else sum((value for value in estimated_costs if value is not None), Decimal(0))
    )
    usage = ModelUsage(
        prompt_tokens=sum(item.usage.prompt_tokens for item in generated),
        completion_tokens=sum(item.usage.completion_tokens for item in generated),
        total_tokens=sum(item.usage.total_tokens for item in generated),
        call_count=sum(item.usage.call_count for item in generated),
        duration_ms=sum(item.usage.duration_ms for item in generated),
        estimated_cost_usd=estimated_cost,
    )
    return SearchModelProvenance(
        provider=first.provider,
        configured_model=first.configured_model,
        model_version=first.model_version,
        prompt_version="+".join(item.prompt_version for item in generated),
        usage=usage,
    )


def _ranked_states(candidates: dict[str, _CandidateState]) -> list[_CandidateState]:
    return sorted(
        candidates.values(),
        key=lambda state: (-state.scores().final, state.stub.semantic_scholar_id),
    )


def _within_year_window(
    stub: ExternalPaperStub,
    *,
    year_from: int | None,
    year_to: int | None,
) -> bool:
    if year_from is None and year_to is None:
        return True
    if year_from is None or year_to is None:
        raise DomainInvariantError("candidate year bounds must be supplied together")
    return stub.year is not None and year_from <= stub.year <= year_to


def _action_reason(tool: SearchTool) -> str:
    reasons = {
        SearchTool.SEARCH_PAPERS: "Execute one bounded historical literature query.",
        SearchTool.GET_PAPER: "Retrieve validated metadata for one queued paper.",
        SearchTool.GET_REFERENCES: "Expand one queued paper through explicit references.",
        SearchTool.GET_CITATIONS: "Expand one queued paper through explicit citations.",
        SearchTool.GET_RECOMMENDATIONS: "Expand from bounded positive paper identities.",
        SearchTool.READ_ARXIV_PAPER: "Read one approved arXiv-hosted paper.",
    }
    return reasons[tool]


def _concise_detail(error: Exception) -> str:
    detail = " ".join(str(error).split())
    return (detail or type(error).__name__)[:1000]


def _bounded_duration_ms(duration_seconds: float) -> int:
    return min(600_000, max(0, round(duration_seconds * 1000)))
