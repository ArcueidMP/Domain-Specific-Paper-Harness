"""PostgreSQL integration coverage for durable M3 scholarly-search state."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from tests.fakes import FakeArxiv

from paper_harness.adapters.postgres import PostgresRepository
from paper_harness.adapters.specter2 import (
    SPECTER2_DIMENSION,
    SPECTER2_EMBEDDING_SOURCE,
    SPECTER2_MODEL_IDENTIFIER,
    SPECTER2_MODEL_PROVENANCE,
    SPECTER2_MODEL_REVISION,
    SPECTER2_PREPROCESSING_CONTRACT,
    SPECTER2_TOKENIZER_IDENTIFIER,
    SPECTER2_TOKENIZER_REVISION,
)
from paper_harness.application.analyze_papers import AnalyzePapers
from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.domain.analysis import (
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
from paper_harness.domain.historical import (
    COMPARISON_DIMENSION_ORDER,
    BackfillStatus,
    CandidateOrigin,
    CandidateScoreComponents,
    CandidateSelectionRequest,
    ComparabilityStatus,
    Comparison,
    ComparisonBundle,
    ComparisonDimension,
    ComparisonRequest,
    CrawlerPlanRequest,
    ExternalPaperStub,
    GeneratedCandidateSelection,
    GeneratedComparison,
    GeneratedCrawlerPlan,
    HistoricalBackfillRun,
    HistoricalCorpusEntry,
    PaperRelation,
    PaperRelationType,
    RelationProvenance,
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
    stable_analysis_id,
    stable_candidate_discovery_id,
    stable_comparison_dimension_id,
    stable_comparison_id,
    stable_embedding_id,
    stable_external_paper_id,
    stable_historical_corpus_entry_id,
    stable_paper_id,
    stable_paper_relation_id,
    stable_paper_version_id,
    stable_search_action_id,
    stable_search_candidate_id,
)
from paper_harness.domain.models import TopicConfig
from paper_harness.ports.arxiv import ArxivPaperRecord
from paper_harness.ports.repository import RepositoryIntegrityError

pytestmark = pytest.mark.integration

NOW = datetime(2026, 1, 10, 5, tzinfo=UTC)


class GroundedAnalysisLLM:
    """Return one deterministic claim grounded in the supplied abstract passage."""

    def analyze(self, request: AnalysisRequest) -> GeneratedAnalysis:
        passage = request.passages[0]
        return GeneratedAnalysis(
            provider="deepseek",
            configured_model="deepseek-v4-flash",
            model_version="DeepSeek-V4-Flash-2026-04-24",
            prompt_version="m2-analysis-v1",
            generated_at=NOW + timedelta(minutes=2),
            summary=f"Evidence-backed analysis of {request.title}.",
            research_problem="Reliable evaluation of tool-using LLM agents.",
            method_summary="The authors evaluate a bounded tool-using agent.",
            key_contributions=("A grounded agent evaluation.",),
            limitations=("The abstract reports limited implementation detail.",),
            claims=(
                GeneratedClaim(
                    key="method",
                    claim_type=ClaimType.METHOD,
                    text="The paper evaluates a tool-using language model agent.",
                ),
            ),
            evidence=(
                GeneratedEvidence(
                    key="method_evidence",
                    claim_keys=("method",),
                    passage_id=passage.id,
                    excerpt=passage.text[:120],
                    evidence_type=EvidenceType.SUPPORTS,
                ),
            ),
            usage=ModelUsage(
                prompt_tokens=80,
                completion_tokens=20,
                total_tokens=100,
                call_count=1,
                duration_ms=250,
                estimated_cost_usd=None,
            ),
        )

    def select_prior_work(
        self,
        request: CandidateSelectionRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> GeneratedCandidateSelection:
        del timeout_seconds
        del request
        raise AssertionError("structured analysis must not invoke the M3 selector")

    def plan_scholarly_search(
        self,
        request: CrawlerPlanRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> GeneratedCrawlerPlan:
        del request, timeout_seconds
        raise AssertionError("structured analysis must not invoke the M3 crawler")

    def compare_papers(self, request: ComparisonRequest) -> GeneratedComparison:
        del request
        raise AssertionError("structured analysis must not invoke M3 comparison")


class RevisedGroundedAnalysisLLM(GroundedAnalysisLLM):
    """Create a second exact analysis for one already-versioned paper."""

    def analyze(self, request: AnalysisRequest) -> GeneratedAnalysis:
        return replace(
            super().analyze(request),
            model_version="DeepSeek-V4-Flash-2026-05-01",
            generated_at=NOW + timedelta(days=1, minutes=2),
        )


def _ingest(
    repository: PostgresRepository,
    topic: TopicConfig,
    records: tuple[ArxivPaperRecord, ...],
    *,
    now: datetime = NOW,
) -> None:
    IngestArxiv(
        arxiv=FakeArxiv(records),
        repository=repository,
        clock=lambda: now,
    ).execute(topic, logical_date=now.date())
    AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=GroundedAnalysisLLM(),
        repository=repository,
        clock=lambda: now + timedelta(minutes=2),
    ).execute(
        topic,
        paper_ids=tuple(stable_paper_id(record.canonical_arxiv_id) for record in records),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=now.date(),
    )


def _abstract_analysis_id(paper_version_id: UUID) -> UUID:
    return stable_analysis_id(
        paper_version_id,
        AnalysisScope.ABSTRACT_ONLY.value,
        None,
        "deepseek",
        "deepseek-v4-flash",
        "DeepSeek-V4-Flash-2026-04-24",
        "m2-analysis-v1",
    )


def _second_arxiv_record(record: ArxivPaperRecord) -> ArxivPaperRecord:
    return replace(
        record,
        canonical_arxiv_id="2601.05678",
        title="A Historical Web Agent",
        abstract="We compare a web agent with a bounded tool-using LLM agent.",
        pdf_url="https://arxiv.org/pdf/2601.05678v1",
        source_url="https://arxiv.org/abs/2601.05678v1",
    )


def _external_stub(
    record: ArxivPaperRecord,
    *,
    semantic_scholar_id: str,
    now: datetime = NOW,
) -> ExternalPaperStub:
    return ExternalPaperStub(
        id=stable_external_paper_id(
            semantic_scholar_id,
            arxiv_id=record.canonical_arxiv_id,
            doi=f"10.1000/{record.canonical_arxiv_id}",
        ),
        semantic_scholar_id=semantic_scholar_id,
        title=record.title,
        abstract=record.abstract,
        year=record.submitted_at.year,
        publication_date=record.submitted_at.date(),
        venue="arXiv",
        authors=record.authors,
        external_ids=(
            ("ArXiv", record.canonical_arxiv_id),
            ("DOI", f"10.1000/{record.canonical_arxiv_id}"),
        ),
        arxiv_id=record.canonical_arxiv_id,
        doi=f"10.1000/{record.canonical_arxiv_id}",
        citation_count=12,
        influential_citation_count=3,
        full_text_available=True,
        source="semantic_scholar",
        schema_version=1,
        created_at=now,
        updated_at=now,
    )


def _search_session(
    session_id: UUID,
    *,
    topic_id: UUID,
    source_paper_id: UUID,
    source_paper_version_id: UUID,
    started_at: datetime,
    objective: str,
) -> SearchSession:
    return SearchSession(
        id=session_id,
        topic_id=topic_id,
        source_paper_id=source_paper_id,
        source_paper_version_id=source_paper_version_id,
        source_analysis_id=_abstract_analysis_id(source_paper_version_id),
        source_analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        requested_year_from=2025,
        effective_year_to=2026,
        objective=objective,
        status=SearchSessionStatus.RUNNING,
        limits=SearchLimits(max_steps=4, max_queries=2, max_candidates=20),
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


def _running_search_action(session_id: UUID, *, created_at: datetime) -> SearchAction:
    return SearchAction(
        id=stable_search_action_id(session_id, 1),
        session_id=session_id,
        step=1,
        tool=SearchTool.SEARCH_PAPERS,
        status=SearchActionStatus.RUNNING,
        query="bounded tool-using LLM agent",
        target_semantic_scholar_id=None,
        target_arxiv_id=None,
        positive_paper_ids=(),
        year_from=2025,
        year_to=2026,
        requested_limit=10,
        result_count=0,
        relation_depth=0,
        decision_reason="Find closely related historical work.",
        error_code=None,
        retryable=None,
        error_detail=None,
        duration_ms=0,
        created_at=created_at,
        completed_at=None,
    )


def _pending_local_candidate(
    session_id: UUID,
    external: ExternalPaperStub,
    *,
    created_at: datetime,
) -> tuple[SearchCandidate, SearchCandidateDiscovery]:
    candidate = SearchCandidate(
        id=stable_search_candidate_id(session_id, external.semantic_scholar_id),
        session_id=session_id,
        external_paper_id=external.id,
        semantic_scholar_id=external.semantic_scholar_id,
        local_paper_id=None,
        local_paper_version_id=None,
        discovered_by_action_id=None,
        origins=(CandidateOrigin.LOCAL_LEXICAL,),
        relation_depth=0,
        scores=CandidateScoreComponents(lexical=0.8, final=0.8),
        rank=1,
        decision=SelectionDecision.PENDING,
        decision_reason="Awaiting bounded selector review.",
        provider=None,
        configured_model=None,
        model_version=None,
        prompt_version=None,
        generated_at=None,
        verification_status=VerificationStatus.UNVERIFIED,
        schema_version=1,
        created_at=created_at,
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
        discovered_at=created_at,
    )
    return candidate, discovery


def test_backfill_page_atomically_persists_identity_embedding_and_cursor(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _ingest(postgres_repository, topic_config, (arxiv_record_v1,))
    paper_id = stable_paper_id(arxiv_record_v1.canonical_arxiv_id)
    version_id = stable_paper_version_id(arxiv_record_v1.canonical_arxiv_id, 1)
    external = _external_stub(arxiv_record_v1, semantic_scholar_id="a" * 40)
    run = HistoricalBackfillRun(
        id=UUID("0fc7af85-57e6-4675-b5c5-d06982f1a026"),
        topic_id=topic_config.id,
        window_from=date(2025, 7, 10),
        window_to=date(2026, 1, 10),
        query_plan=("LLM agent",),
        max_results_per_query=500,
        overall_timeout_seconds=3600.0,
        embedding_model_identifier=SPECTER2_MODEL_IDENTIFIER,
        embedding_model_revision=SPECTER2_MODEL_REVISION,
        embedding_tokenizer_identifier=SPECTER2_TOKENIZER_IDENTIFIER,
        embedding_tokenizer_revision=SPECTER2_TOKENIZER_REVISION,
        embedding_dimension=SPECTER2_DIMENSION,
        embedding_preprocessing_contract=SPECTER2_PREPROCESSING_CONTRACT,
        embedding_model_provenance=SPECTER2_MODEL_PROVENANCE,
        embedding_source=SPECTER2_EMBEDDING_SOURCE,
        status=BackfillStatus.RUNNING,
        next_query_index=0,
        discovered_count=0,
        persisted_count=0,
        representative_count=0,
        started_at=NOW,
        completed_at=None,
        error_code=None,
        error_detail=None,
        schema_version=1,
        created_at=NOW,
    )
    entry = HistoricalCorpusEntry(
        id=stable_historical_corpus_entry_id(topic_config.id, external.id),
        topic_id=topic_config.id,
        external_paper_id=external.id,
        local_paper_id=None,
        local_paper_version_id=None,
        representative_rank=None,
        first_seen_at=NOW,
        last_seen_at=NOW,
        schema_version=1,
    )
    embedding = ScientificEmbedding(
        id=stable_embedding_id(
            external.id,
            model_identifier=SPECTER2_MODEL_IDENTIFIER,
            model_revision=SPECTER2_MODEL_REVISION,
            tokenizer_identifier=SPECTER2_TOKENIZER_IDENTIFIER,
            tokenizer_revision=SPECTER2_TOKENIZER_REVISION,
            dimension=SPECTER2_DIMENSION,
            preprocessing_contract=SPECTER2_PREPROCESSING_CONTRACT,
            model_provenance=SPECTER2_MODEL_PROVENANCE,
            source=SPECTER2_EMBEDDING_SOURCE,
        ),
        paper_version_id=None,
        external_paper_id=external.id,
        model_identifier=SPECTER2_MODEL_IDENTIFIER,
        model_revision=SPECTER2_MODEL_REVISION,
        tokenizer_identifier=SPECTER2_TOKENIZER_IDENTIFIER,
        tokenizer_revision=SPECTER2_TOKENIZER_REVISION,
        dimension=SPECTER2_DIMENSION,
        preprocessing_contract=SPECTER2_PREPROCESSING_CONTRACT,
        model_provenance=SPECTER2_MODEL_PROVENANCE,
        vector=(0.125,) * SPECTER2_DIMENSION,
        generated_at=NOW,
        source=SPECTER2_EMBEDDING_SOURCE,
        schema_version=1,
        created_at=NOW,
    )

    postgres_repository.start_historical_backfill(run)
    with pytest.raises(RepositoryIntegrityError, match="query plan is exhausted"):
        postgres_repository.finalize_historical_backfill(
            run.id,
            representatives=(),
            completed_at=NOW + timedelta(milliseconds=500),
        )
    with pytest.raises(RepositoryIntegrityError, match="model provenance"):
        postgres_repository.persist_historical_backfill_page(
            run.id,
            expected_query_index=0,
            next_query_index=1,
            papers=(external,),
            entries=(entry,),
            embeddings=(replace(embedding, model_revision="wrong-revision"),),
            discovered_count=1,
            persisted_count=1,
            persisted_at=NOW + timedelta(seconds=1),
        )
    advanced = postgres_repository.persist_historical_backfill_page(
        run.id,
        expected_query_index=0,
        next_query_index=1,
        papers=(external,),
        entries=(entry,),
        embeddings=(embedding,),
        discovered_count=1,
        persisted_count=1,
        persisted_at=NOW + timedelta(seconds=1),
    )

    assert advanced.next_query_index == 1
    assert advanced.discovered_count == 1
    assert advanced.persisted_count == 1
    replayed = postgres_repository.persist_historical_backfill_page(
        run.id,
        expected_query_index=0,
        next_query_index=1,
        papers=(external,),
        entries=(entry,),
        embeddings=(embedding,),
        discovered_count=1,
        persisted_count=1,
        persisted_at=NOW + timedelta(seconds=1),
    )
    assert replayed == advanced
    with postgres_engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT h.local_paper_id, h.local_paper_version_id, "
                "b.next_query_index, vector_dims(e.vector) "
                "FROM historical_corpus_entries h "
                "JOIN historical_backfill_runs b ON b.id = :run_id "
                "JOIN scientific_embeddings e ON e.external_paper_id = h.external_paper_id "
                "WHERE h.external_paper_id = :external_id"
            ),
            {"run_id": run.id, "external_id": external.id},
        ).one()
        identifiers = set(
            connection.execute(
                text(
                    "SELECT identifier_type, identifier_value "
                    "FROM external_paper_identifiers WHERE external_paper_id = :external_id"
                ),
                {"external_id": external.id},
            ).all()
        )
    assert stored == (paper_id, version_id, 1, SPECTER2_DIMENSION)
    assert identifiers == set(external.external_ids)
    vector_matches = postgres_repository.search_historical_by_vector(
        topic_config.id,
        vector=(0.125,) * SPECTER2_DIMENSION,
        model_identifier=SPECTER2_MODEL_IDENTIFIER,
        model_revision=SPECTER2_MODEL_REVISION,
        tokenizer_identifier=SPECTER2_TOKENIZER_IDENTIFIER,
        tokenizer_revision=SPECTER2_TOKENIZER_REVISION,
        dimension=SPECTER2_DIMENSION,
        preprocessing_contract=SPECTER2_PREPROCESSING_CONTRACT,
        model_provenance=SPECTER2_MODEL_PROVENANCE,
        source=SPECTER2_EMBEDDING_SOURCE,
        limit=5,
    )
    assert len(vector_matches) == 1
    assert vector_matches[0].external_paper.id == external.id
    assert vector_matches[0].score == pytest.approx(1.0)
    assert (
        postgres_repository.search_historical_by_vector(
            topic_config.id,
            vector=(0.125,) * SPECTER2_DIMENSION,
            model_identifier=SPECTER2_MODEL_IDENTIFIER,
            model_revision="different-revision",
            tokenizer_identifier=SPECTER2_TOKENIZER_IDENTIFIER,
            tokenizer_revision=SPECTER2_TOKENIZER_REVISION,
            dimension=SPECTER2_DIMENSION,
            preprocessing_contract=SPECTER2_PREPROCESSING_CONTRACT,
            model_provenance=SPECTER2_MODEL_PROVENANCE,
            source=SPECTER2_EMBEDDING_SOURCE,
            limit=5,
        )
        == ()
    )


def test_failed_search_action_persists_without_result_rows(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _ingest(postgres_repository, topic_config, (arxiv_record_v1,))
    paper_id = stable_paper_id(arxiv_record_v1.canonical_arxiv_id)
    version_id = stable_paper_version_id(arxiv_record_v1.canonical_arxiv_id, 1)
    session = _search_session(
        UUID("e9844361-e9df-4f7a-98fa-3178de64d260"),
        topic_id=topic_config.id,
        source_paper_id=paper_id,
        source_paper_version_id=version_id,
        started_at=NOW + timedelta(minutes=1),
        objective="Find prior work for a failed provider request.",
    )
    running_action = _running_search_action(session.id, created_at=session.started_at)
    failed_action = replace(
        running_action,
        status=SearchActionStatus.FAILED,
        error_code="SEMANTIC_SCHOLAR_RATE_LIMITED",
        retryable=True,
        error_detail="Semantic Scholar exhausted the bounded retry policy.",
        duration_ms=325,
        completed_at=session.started_at + timedelta(seconds=1),
    )
    crawler_plan = GeneratedCrawlerPlan(
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-04-24",
        prompt_version="m3-crawler-v1",
        generated_at=session.started_at,
        queries=("bounded tool-using LLM agent",),
        use_recommendations=False,
        expand_references=True,
        expand_citations=True,
        decision_reason="Use a bounded query and citation expansion.",
        usage=ModelUsage(
            prompt_tokens=20,
            completion_tokens=10,
            total_tokens=30,
            call_count=1,
            duration_ms=150,
            estimated_cost_usd=None,
        ),
    )

    postgres_repository.start_search_session(session)
    persisted_plan = postgres_repository.persist_search_crawler_plan(session.id, crawler_plan)
    assert persisted_plan.crawler_queries == crawler_plan.queries
    with pytest.raises(RepositoryIntegrityError, match="session limits"):
        postgres_repository.start_search_action(
            replace(
                running_action,
                id=stable_search_action_id(session.id, session.limits.max_steps + 1),
                step=session.limits.max_steps + 1,
            )
        )
    postgres_repository.start_search_action(running_action)
    postgres_repository.persist_search_action_result(
        failed_action,
        papers=(),
        candidates=(),
        discoveries=(),
    )
    failed_session = postgres_repository.fail_search_session(
        session.id,
        completed_at=session.started_at + timedelta(seconds=2),
        error_code="SEMANTIC_SCHOLAR_RATE_LIMITED",
        error_detail="The bounded provider operation failed.",
        provenance=SearchModelProvenance(
            provider=crawler_plan.provider,
            configured_model=crawler_plan.configured_model,
            model_version=crawler_plan.model_version,
            prompt_version=crawler_plan.prompt_version,
            usage=crawler_plan.usage,
        ),
    )
    assert failed_session.crawler_decision_reason == crawler_plan.decision_reason
    with pytest.raises(RepositoryIntegrityError, match="not running"):
        postgres_repository.start_search_action(
            replace(
                running_action,
                id=stable_search_action_id(session.id, 2),
                step=2,
            )
        )

    detail = postgres_repository.get_search_session(session.id)
    assert detail is not None
    assert detail.actions == (failed_action,)
    assert detail.candidates == ()
    assert detail.discoveries == ()
    assert detail.session.status is SearchSessionStatus.FAILED
    assert detail.session.provider == crawler_plan.provider
    assert detail.session.usage == crawler_plan.usage


def test_candidate_discovery_depth_is_bounded_by_owning_session_before_write(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _ingest(postgres_repository, topic_config, (arxiv_record_v1,))
    source_paper_id = stable_paper_id(arxiv_record_v1.canonical_arxiv_id)
    source_version_id = stable_paper_version_id(arxiv_record_v1.canonical_arxiv_id, 1)
    search_session = _search_session(
        UUID("d0ec8932-12bd-47b8-b5b6-827ca452cb56"),
        topic_id=topic_config.id,
        source_paper_id=source_paper_id,
        source_paper_version_id=source_version_id,
        started_at=NOW,
        objective="Reject candidate discovery paths beyond the persisted depth bound.",
    )
    search_session = replace(
        search_session,
        limits=replace(search_session.limits, max_citation_depth=0),
    )
    external = _external_stub(_second_arxiv_record(arxiv_record_v1), semantic_scholar_id="9" * 40)
    candidate, discovery = _pending_local_candidate(
        search_session.id,
        external,
        created_at=NOW,
    )
    too_deep = replace(
        discovery,
        id=stable_candidate_discovery_id(
            candidate.id,
            discovery.origin.value,
            discovery.action_id,
            1,
        ),
        relation_depth=1,
    )

    postgres_repository.start_search_session(search_session)
    with pytest.raises(RepositoryIntegrityError, match="candidate discovery.*session limits"):
        postgres_repository.persist_local_search_candidates(
            search_session.id,
            papers=(external,),
            candidates=(candidate,),
            discoveries=(too_deep,),
        )

    detail = postgres_repository.get_search_session(search_session.id)
    assert detail is not None
    assert detail.candidates == ()
    assert detail.discoveries == ()
    with postgres_engine.connect() as connection:
        external_count = connection.execute(
            text("SELECT count(*) FROM external_paper_stubs WHERE id = :external_id"),
            {"external_id": external.id},
        ).scalar_one()
    assert external_count == 0


def test_arxiv_identity_reconciles_semantic_scholar_aliases_idempotently(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _ingest(postgres_repository, topic_config, (arxiv_record_v1,))
    first = _external_stub(arxiv_record_v1, semantic_scholar_id="1" * 40)
    alias = _external_stub(arxiv_record_v1, semantic_scholar_id="2" * 40)
    assert first.id == alias.id
    run = HistoricalBackfillRun(
        id=UUID("65c7422a-2af2-49f4-9fa0-f45f8f76c73e"),
        topic_id=topic_config.id,
        window_from=date(2025, 7, 10),
        window_to=date(2026, 1, 10),
        query_plan=("LLM agents", "tool-using agents"),
        max_results_per_query=10,
        overall_timeout_seconds=600.0,
        embedding_model_identifier=SPECTER2_MODEL_IDENTIFIER,
        embedding_model_revision=SPECTER2_MODEL_REVISION,
        embedding_tokenizer_identifier=SPECTER2_TOKENIZER_IDENTIFIER,
        embedding_tokenizer_revision=SPECTER2_TOKENIZER_REVISION,
        embedding_dimension=SPECTER2_DIMENSION,
        embedding_preprocessing_contract=SPECTER2_PREPROCESSING_CONTRACT,
        embedding_model_provenance=SPECTER2_MODEL_PROVENANCE,
        embedding_source=SPECTER2_EMBEDDING_SOURCE,
        status=BackfillStatus.RUNNING,
        next_query_index=0,
        discovered_count=0,
        persisted_count=0,
        representative_count=0,
        started_at=NOW,
        completed_at=None,
        error_code=None,
        error_detail=None,
        schema_version=1,
        created_at=NOW,
    )
    entry = HistoricalCorpusEntry(
        id=stable_historical_corpus_entry_id(topic_config.id, first.id),
        topic_id=topic_config.id,
        external_paper_id=first.id,
        local_paper_id=None,
        local_paper_version_id=None,
        representative_rank=None,
        first_seen_at=NOW,
        last_seen_at=NOW,
        schema_version=1,
    )

    def embedding() -> ScientificEmbedding:
        return ScientificEmbedding(
            id=stable_embedding_id(
                first.id,
                model_identifier=SPECTER2_MODEL_IDENTIFIER,
                model_revision=SPECTER2_MODEL_REVISION,
                tokenizer_identifier=SPECTER2_TOKENIZER_IDENTIFIER,
                tokenizer_revision=SPECTER2_TOKENIZER_REVISION,
                dimension=SPECTER2_DIMENSION,
                preprocessing_contract=SPECTER2_PREPROCESSING_CONTRACT,
                model_provenance=SPECTER2_MODEL_PROVENANCE,
                source=SPECTER2_EMBEDDING_SOURCE,
            ),
            paper_version_id=None,
            external_paper_id=first.id,
            model_identifier=SPECTER2_MODEL_IDENTIFIER,
            model_revision=SPECTER2_MODEL_REVISION,
            tokenizer_identifier=SPECTER2_TOKENIZER_IDENTIFIER,
            tokenizer_revision=SPECTER2_TOKENIZER_REVISION,
            dimension=SPECTER2_DIMENSION,
            preprocessing_contract=SPECTER2_PREPROCESSING_CONTRACT,
            model_provenance=SPECTER2_MODEL_PROVENANCE,
            vector=(0.125,) * SPECTER2_DIMENSION,
            generated_at=NOW,
            source=SPECTER2_EMBEDDING_SOURCE,
            schema_version=1,
            created_at=NOW,
        )

    postgres_repository.start_historical_backfill(run)
    postgres_repository.persist_historical_backfill_page(
        run.id,
        expected_query_index=0,
        next_query_index=1,
        papers=(first,),
        entries=(entry,),
        embeddings=(embedding(),),
        discovered_count=1,
        persisted_count=1,
        persisted_at=NOW,
    )
    postgres_repository.persist_historical_backfill_page(
        run.id,
        expected_query_index=1,
        next_query_index=2,
        papers=(alias,),
        entries=(entry,),
        embeddings=(embedding(),),
        discovered_count=2,
        persisted_count=2,
        persisted_at=NOW + timedelta(seconds=1),
    )

    with postgres_engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT count(*), max(semantic_scholar_id) "
                "FROM external_paper_stubs WHERE arxiv_id = :arxiv_id"
            ),
            {"arxiv_id": arxiv_record_v1.canonical_arxiv_id},
        ).one()
    assert stored == (1, alias.semantic_scholar_id)


def test_semantic_scholar_alias_rekeys_existing_candidate_and_discovery(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _ingest(postgres_repository, topic_config, (arxiv_record_v1,))
    paper_id = stable_paper_id(arxiv_record_v1.canonical_arxiv_id)
    version_id = stable_paper_version_id(arxiv_record_v1.canonical_arxiv_id, 1)
    search_session = _search_session(
        UUID("24804d37-53d1-4fa4-966f-e6a8f0015c35"),
        topic_id=topic_config.id,
        source_paper_id=paper_id,
        source_paper_version_id=version_id,
        started_at=NOW,
        objective="Reconcile a changed Semantic Scholar alias.",
    )
    first = _external_stub(arxiv_record_v1, semantic_scholar_id="3" * 40)
    alias = _external_stub(
        arxiv_record_v1,
        semantic_scholar_id="4" * 40,
        now=NOW + timedelta(seconds=1),
    )
    first_candidate, first_discovery = _pending_local_candidate(
        search_session.id,
        first,
        created_at=NOW,
    )
    alias_candidate, alias_discovery = _pending_local_candidate(
        search_session.id,
        alias,
        created_at=NOW + timedelta(seconds=1),
    )

    postgres_repository.start_search_session(search_session)
    postgres_repository.persist_local_search_candidates(
        search_session.id,
        papers=(first,),
        candidates=(first_candidate,),
        discoveries=(first_discovery,),
    )
    postgres_repository.persist_local_search_candidates(
        search_session.id,
        papers=(alias,),
        candidates=(alias_candidate,),
        discoveries=(alias_discovery,),
    )

    detail = postgres_repository.get_search_session(search_session.id)
    assert detail is not None
    assert len(detail.candidates) == 1
    assert detail.candidates[0].id == alias_candidate.id
    assert detail.candidates[0].external_paper_id == alias.id
    assert detail.candidates[0].semantic_scholar_id == alias.semantic_scholar_id
    assert detail.candidates[0].local_paper_id == paper_id
    assert detail.candidates[0].local_paper_version_id == version_id
    assert len(detail.discoveries) == 1
    assert detail.discoveries[0].id == alias_discovery.id
    assert detail.discoveries[0].candidate_id == alias_candidate.id
    with postgres_engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT c.external_paper_id, c.semantic_scholar_id, c.id, d.id "
                "FROM search_candidates c "
                "JOIN search_candidate_discoveries d ON d.candidate_id = c.id "
                "WHERE c.session_id = :session_id"
            ),
            {"session_id": search_session.id},
        ).one()
    assert stored == (
        alias.id,
        alias.semantic_scholar_id,
        alias_candidate.id,
        alias_discovery.id,
    )


def test_semantic_scholar_only_identity_promotes_to_canonical_arxiv_identity(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _ingest(postgres_repository, topic_config, (arxiv_record_v1,))
    source_paper_id = stable_paper_id(arxiv_record_v1.canonical_arxiv_id)
    source_version_id = stable_paper_version_id(arxiv_record_v1.canonical_arxiv_id, 1)
    search_session = replace(
        _search_session(
            UUID("8d64bd01-5f2e-4ca5-aee5-d98aa783f6c5"),
            topic_id=topic_config.id,
            source_paper_id=source_paper_id,
            source_paper_version_id=source_version_id,
            started_at=NOW,
            objective="Promote a Semantic Scholar-only identity after arXiv enrichment.",
        ),
        limits=SearchLimits(
            max_steps=4,
            max_queries=2,
            max_queue_size=2,
            max_candidates=2,
            max_selected_candidates=1,
        ),
    )
    target_record = _second_arxiv_record(arxiv_record_v1)
    enriched = _external_stub(target_record, semantic_scholar_id="5" * 40)
    canonical_alias = _external_stub(target_record, semantic_scholar_id="6" * 40)
    semantic_scholar_only = replace(
        enriched,
        id=stable_external_paper_id(enriched.semantic_scholar_id),
        external_ids=(),
        arxiv_id=None,
        doi=None,
        full_text_available=False,
    )
    initial_candidate, initial_discovery = _pending_local_candidate(
        search_session.id,
        semantic_scholar_only,
        created_at=NOW,
    )
    alias_candidate, alias_discovery = _pending_local_candidate(
        search_session.id,
        canonical_alias,
        created_at=NOW + timedelta(milliseconds=500),
    )
    promoted_candidate, promoted_discovery = _pending_local_candidate(
        search_session.id,
        enriched,
        created_at=NOW + timedelta(seconds=1),
    )

    postgres_repository.start_search_session(search_session)
    postgres_repository.persist_local_search_candidates(
        search_session.id,
        papers=(semantic_scholar_only,),
        candidates=(initial_candidate,),
        discoveries=(initial_discovery,),
    )
    postgres_repository.persist_local_search_candidates(
        search_session.id,
        papers=(canonical_alias,),
        candidates=(alias_candidate,),
        discoveries=(alias_discovery,),
    )
    postgres_repository.persist_local_search_candidates(
        search_session.id,
        papers=(enriched,),
        candidates=(promoted_candidate,),
        discoveries=(promoted_discovery,),
    )

    detail = postgres_repository.get_search_session(search_session.id)
    assert detail is not None
    assert len(detail.candidates) == 1
    assert detail.candidates[0].id == promoted_candidate.id
    assert detail.candidates[0].external_paper_id == enriched.id
    assert detail.candidates[0].semantic_scholar_id == enriched.semantic_scholar_id
    assert len(detail.discoveries) == 1
    assert detail.discoveries[0].id == promoted_discovery.id
    assert detail.discoveries[0].candidate_id == promoted_candidate.id
    with postgres_engine.connect() as connection:
        root = connection.execute(
            text(
                "SELECT id, arxiv_id, doi FROM external_paper_stubs "
                "WHERE semantic_scholar_id = :semantic_scholar_id"
            ),
            {"semantic_scholar_id": enriched.semantic_scholar_id},
        ).one()
        old_root_count = connection.execute(
            text("SELECT count(*) FROM external_paper_stubs WHERE id = :old_id"),
            {"old_id": semantic_scholar_only.id},
        ).scalar_one()
        alias_root_count = connection.execute(
            text(
                "SELECT count(*) FROM external_paper_stubs "
                "WHERE semantic_scholar_id = :semantic_scholar_id"
            ),
            {"semantic_scholar_id": canonical_alias.semantic_scholar_id},
        ).scalar_one()
        local_paper_count = connection.execute(
            text("SELECT count(*) FROM papers WHERE canonical_arxiv_id = :arxiv_id"),
            {"arxiv_id": target_record.canonical_arxiv_id},
        ).scalar_one()
    assert root == (enriched.id, enriched.arxiv_id, enriched.doi)
    assert old_root_count == 0
    assert alias_root_count == 0
    assert local_paper_count == 0
    assert detail.candidates[0].external_paper_id == enriched.id
    assert detail.candidates[0].local_paper_id is None
    assert detail.candidates[0].local_paper_version_id is None


def test_sparse_external_refresh_preserves_non_conflicting_identifiers_and_doi(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _ingest(postgres_repository, topic_config, (arxiv_record_v1,))
    source_paper_id = stable_paper_id(arxiv_record_v1.canonical_arxiv_id)
    source_version_id = stable_paper_version_id(arxiv_record_v1.canonical_arxiv_id, 1)
    search_session = _search_session(
        UUID("148661b5-8075-4881-8476-23b356aa54c3"),
        topic_id=topic_config.id,
        source_paper_id=source_paper_id,
        source_paper_version_id=source_version_id,
        started_at=NOW,
        objective="Preserve approved identifiers across sparse metadata refreshes.",
    )
    semantic_scholar_id = "7" * 40
    original = replace(
        _external_stub(
            _second_arxiv_record(arxiv_record_v1),
            semantic_scholar_id=semantic_scholar_id,
        ),
        id=stable_external_paper_id(semantic_scholar_id),
        external_ids=(("CorpusId", "7001"), ("DOI", "10.1000/preserved")),
        arxiv_id=None,
        doi="10.1000/preserved",
        full_text_available=False,
    )
    original_candidate, original_discovery = _pending_local_candidate(
        search_session.id,
        original,
        created_at=NOW,
    )
    sparse = replace(
        original,
        external_ids=(("CorpusId", "7001"), ("ACL", "2026.acl-long.1")),
        doi=None,
        updated_at=NOW + timedelta(seconds=1),
    )
    sparse_candidate, sparse_discovery = _pending_local_candidate(
        search_session.id,
        sparse,
        created_at=NOW + timedelta(seconds=1),
    )

    postgres_repository.start_search_session(search_session)
    postgres_repository.persist_local_search_candidates(
        search_session.id,
        papers=(original,),
        candidates=(original_candidate,),
        discoveries=(original_discovery,),
    )
    postgres_repository.persist_local_search_candidates(
        search_session.id,
        papers=(sparse,),
        candidates=(sparse_candidate,),
        discoveries=(sparse_discovery,),
    )

    with postgres_engine.connect() as connection:
        stored_doi = connection.execute(
            text("SELECT doi FROM external_paper_stubs WHERE id = :external_id"),
            {"external_id": original.id},
        ).scalar_one()
        stored_identifiers = tuple(
            connection.execute(
                text(
                    "SELECT identifier_type, identifier_value "
                    "FROM external_paper_identifiers "
                    "WHERE external_paper_id = :external_id ORDER BY identifier_type"
                ),
                {"external_id": original.id},
            )
        )
    assert stored_doi == "10.1000/preserved"
    assert stored_identifiers == (
        ("ACL", "2026.acl-long.1"),
        ("CorpusId", "7001"),
        ("DOI", "10.1000/preserved"),
    )


def test_external_refresh_rejects_identifier_conflicts_without_mutation(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _ingest(postgres_repository, topic_config, (arxiv_record_v1,))
    source_paper_id = stable_paper_id(arxiv_record_v1.canonical_arxiv_id)
    source_version_id = stable_paper_version_id(arxiv_record_v1.canonical_arxiv_id, 1)
    search_session = _search_session(
        UUID("86384677-ec03-488b-b5be-0c10a4242a43"),
        topic_id=topic_config.id,
        source_paper_id=source_paper_id,
        source_paper_version_id=source_version_id,
        started_at=NOW,
        objective="Reject conflicting external identity refreshes.",
    )
    semantic_scholar_id = "8" * 40
    original = replace(
        _external_stub(
            _second_arxiv_record(arxiv_record_v1),
            semantic_scholar_id=semantic_scholar_id,
        ),
        id=stable_external_paper_id(semantic_scholar_id),
        external_ids=(("CorpusId", "8001"), ("DOI", "10.1000/original")),
        arxiv_id=None,
        doi="10.1000/original",
        full_text_available=False,
    )
    candidate, discovery = _pending_local_candidate(
        search_session.id,
        original,
        created_at=NOW,
    )

    postgres_repository.start_search_session(search_session)
    postgres_repository.persist_local_search_candidates(
        search_session.id,
        papers=(original,),
        candidates=(candidate,),
        discoveries=(discovery,),
    )
    conflicting_identifier = replace(
        original,
        external_ids=(("CorpusId", "8002"), ("DOI", "10.1000/original")),
        updated_at=NOW + timedelta(seconds=1),
    )
    conflicting_doi = replace(
        original,
        external_ids=(("CorpusId", "8001"), ("DOI", "10.1000/replacement")),
        doi="10.1000/replacement",
        updated_at=NOW + timedelta(seconds=2),
    )
    for conflict in (conflicting_identifier, conflicting_doi):
        with pytest.raises(RepositoryIntegrityError, match="identifier conflict"):
            postgres_repository.persist_local_search_candidates(
                search_session.id,
                papers=(conflict,),
                candidates=(candidate,),
                discoveries=(discovery,),
            )

    with postgres_engine.connect() as connection:
        stored_doi = connection.execute(
            text("SELECT doi FROM external_paper_stubs WHERE id = :external_id"),
            {"external_id": original.id},
        ).scalar_one()
        stored_identifiers = tuple(
            connection.execute(
                text(
                    "SELECT identifier_type, identifier_value "
                    "FROM external_paper_identifiers "
                    "WHERE external_paper_id = :external_id ORDER BY identifier_type"
                ),
                {"external_id": original.id},
            )
        )
    assert stored_doi == "10.1000/original"
    assert stored_identifiers == (
        ("CorpusId", "8001"),
        ("DOI", "10.1000/original"),
    )


def test_related_work_uses_latest_session_and_resolves_local_candidate_identity(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    target_record = _second_arxiv_record(arxiv_record_v1)
    _ingest(postgres_repository, topic_config, (arxiv_record_v1, target_record))
    source_paper_id = stable_paper_id(arxiv_record_v1.canonical_arxiv_id)
    source_version_id = stable_paper_version_id(arxiv_record_v1.canonical_arxiv_id, 1)
    target_paper_id = stable_paper_id(target_record.canonical_arxiv_id)
    target_version_id = stable_paper_version_id(target_record.canonical_arxiv_id, 1)
    older_session = _search_session(
        UUID("73ad297a-f764-44e9-80f9-6dd37f24847e"),
        topic_id=topic_config.id,
        source_paper_id=source_paper_id,
        source_paper_version_id=source_version_id,
        started_at=NOW,
        objective="An older related-work search.",
    )
    latest_session = _search_session(
        UUID("7702255d-2910-4fc8-a86f-d2bb291e9ecf"),
        topic_id=topic_config.id,
        source_paper_id=source_paper_id,
        source_paper_version_id=source_version_id,
        started_at=NOW + timedelta(minutes=1),
        objective="The latest related-work search.",
    )
    action = _running_search_action(latest_session.id, created_at=latest_session.started_at)
    source_external = _external_stub(arxiv_record_v1, semantic_scholar_id="d" * 40)
    external = _external_stub(target_record, semantic_scholar_id="b" * 40)
    candidate = SearchCandidate(
        id=stable_search_candidate_id(latest_session.id, external.semantic_scholar_id),
        session_id=latest_session.id,
        external_paper_id=external.id,
        semantic_scholar_id=external.semantic_scholar_id,
        local_paper_id=None,
        local_paper_version_id=None,
        discovered_by_action_id=action.id,
        origins=(CandidateOrigin.SEARCH,),
        relation_depth=0,
        scores=CandidateScoreComponents(semantic_scholar=0.8, final=0.8),
        rank=1,
        decision=SelectionDecision.PENDING,
        decision_reason="Awaiting bounded selector review.",
        provider=None,
        configured_model=None,
        model_version=None,
        prompt_version=None,
        generated_at=None,
        verification_status=VerificationStatus.UNVERIFIED,
        schema_version=1,
        created_at=latest_session.started_at,
    )
    discovery = SearchCandidateDiscovery(
        id=stable_candidate_discovery_id(
            candidate.id,
            CandidateOrigin.SEARCH.value,
            action.id,
            0,
        ),
        candidate_id=candidate.id,
        action_id=action.id,
        origin=CandidateOrigin.SEARCH,
        relation_depth=0,
        discovered_at=latest_session.started_at + timedelta(milliseconds=100),
    )
    completed_action = replace(
        action,
        status=SearchActionStatus.COMPLETED,
        result_count=2,
        duration_ms=210,
        completed_at=latest_session.started_at + timedelta(seconds=1),
    )

    postgres_repository.start_search_session(older_session)
    postgres_repository.start_search_session(latest_session)
    postgres_repository.start_search_action(action)
    postgres_repository.persist_search_action_result(
        completed_action,
        papers=(source_external, external),
        candidates=(candidate,),
        discoveries=(discovery,),
    )

    related = postgres_repository.get_related_work(source_paper_id)
    assert related is not None
    assert related.session.id == latest_session.id
    assert related.actions == (completed_action,)
    assert len(related.items) == 1
    item = related.items[0]
    assert item.candidate.local_paper_id == target_paper_id
    assert item.candidate.local_paper_version_id == target_version_id
    assert item.external_paper == external
    assert item.discoveries == (discovery,)
    assert item.relations == ()
    assert item.comparison_id is None


def test_comparison_bundle_validates_evidence_ownership_and_round_trips_atomically(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    target_record = _second_arxiv_record(arxiv_record_v1)
    _ingest(postgres_repository, topic_config, (arxiv_record_v1, target_record))
    source_paper_id = stable_paper_id(arxiv_record_v1.canonical_arxiv_id)
    source_version_id = stable_paper_version_id(arxiv_record_v1.canonical_arxiv_id, 1)
    target_paper_id = stable_paper_id(target_record.canonical_arxiv_id)
    target_version_id = stable_paper_version_id(target_record.canonical_arxiv_id, 1)
    source_analysis = postgres_repository.get_paper_analysis(
        source_paper_id,
        paper_version_id=source_version_id,
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
    )
    target_analysis = postgres_repository.get_paper_analysis(
        target_paper_id,
        paper_version_id=target_version_id,
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
    )
    assert source_analysis is not None
    assert target_analysis is not None
    source_evidence_id = source_analysis.evidence[0].id
    target_evidence_id = target_analysis.evidence[0].id
    AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=RevisedGroundedAnalysisLLM(),
        repository=postgres_repository,
        clock=lambda: NOW + timedelta(days=1, minutes=2),
    ).execute(
        topic_config,
        paper_ids=(source_paper_id,),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=NOW.date() + timedelta(days=1),
    )
    revised_source_analysis = postgres_repository.get_paper_analysis(
        source_paper_id,
        paper_version_id=source_version_id,
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
    )
    assert revised_source_analysis is not None
    assert revised_source_analysis.analysis.id != source_analysis.analysis.id
    assert revised_source_analysis.evidence[0].id != source_evidence_id
    pinned_source = postgres_repository.get_comparison_paper_input(
        source_version_id,
        analysis_id=source_analysis.analysis.id,
    )
    preferred_source = postgres_repository.get_comparison_paper_input(source_version_id)
    assert pinned_source is not None
    assert preferred_source is not None
    assert pinned_source.analysis_id == source_analysis.analysis.id
    assert preferred_source.analysis_id == revised_source_analysis.analysis.id
    search_session = _search_session(
        UUID("cc7feb15-57d2-4c8e-84b2-93ad20596a43"),
        topic_id=topic_config.id,
        source_paper_id=source_paper_id,
        source_paper_version_id=source_version_id,
        started_at=NOW + timedelta(minutes=3),
        objective="Compare the new paper against grounded historical work.",
    )
    postgres_repository.start_search_session(search_session)
    external_target = _external_stub(target_record, semantic_scholar_id="c" * 40)
    selected_candidate = SearchCandidate(
        id=stable_search_candidate_id(
            search_session.id,
            external_target.semantic_scholar_id,
        ),
        session_id=search_session.id,
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
        decision_reason="Selected as grounded historical work.",
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-04-24",
        prompt_version="m3-selector-v1",
        generated_at=NOW + timedelta(minutes=3),
        verification_status=VerificationStatus.UNVERIFIED,
        schema_version=1,
        created_at=NOW + timedelta(minutes=3),
    )
    local_discovery = SearchCandidateDiscovery(
        id=stable_candidate_discovery_id(
            selected_candidate.id,
            CandidateOrigin.LOCAL_LEXICAL.value,
            None,
            0,
        ),
        candidate_id=selected_candidate.id,
        action_id=None,
        origin=CandidateOrigin.LOCAL_LEXICAL,
        relation_depth=0,
        discovered_at=NOW + timedelta(minutes=3),
    )
    postgres_repository.persist_local_search_candidates(
        search_session.id,
        papers=(external_target,),
        candidates=(selected_candidate,),
        discoveries=(local_discovery,),
    )
    postgres_repository.complete_search_session(
        search_session.id,
        completed_at=NOW + timedelta(minutes=3, seconds=1),
        stop_reason=SearchStopReason.QUEUE_EXHAUSTED,
        provenance=SearchModelProvenance(
            provider="deepseek",
            configured_model="deepseek-v4-flash",
            model_version="DeepSeek-V4-Flash-2026-04-24",
            prompt_version="m3-crawler-v1+m3-selector-v1",
            usage=ModelUsage(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                call_count=2,
                duration_ms=300,
                estimated_cost_usd=None,
            ),
        ),
    )

    comparison_id = stable_comparison_id(
        search_session.id,
        source_version_id,
        source_analysis.analysis.id,
        target_version_id,
        target_analysis.analysis.id,
        "deepseek",
        "deepseek-v4-flash",
        "DeepSeek-V4-Flash-2026-04-24",
        "m3-comparison-v1",
    )
    revised_comparison_id = stable_comparison_id(
        search_session.id,
        source_version_id,
        revised_source_analysis.analysis.id,
        target_version_id,
        target_analysis.analysis.id,
        "deepseek",
        "deepseek-v4-flash",
        "DeepSeek-V4-Flash-2026-04-24",
        "m3-comparison-v1",
    )
    assert revised_comparison_id != comparison_id
    assert revised_comparison_id == stable_comparison_id(
        search_session.id,
        source_version_id,
        revised_source_analysis.analysis.id,
        target_version_id,
        target_analysis.analysis.id,
        "deepseek",
        "deepseek-v4-flash",
        "DeepSeek-V4-Flash-2026-04-24",
        "m3-comparison-v1",
    )

    def build_bundle(
        *,
        reverse_evidence_ownership: bool,
        source_evidence_override: UUID | None = None,
    ) -> ComparisonBundle:
        selected_source_evidence_id = source_evidence_override or source_evidence_id
        dimension_source_evidence_id = (
            target_evidence_id if reverse_evidence_ownership else selected_source_evidence_id
        )
        dimension_target_evidence_id = (
            source_evidence_id if reverse_evidence_ownership else target_evidence_id
        )
        dimensions = tuple(
            ComparisonDimension(
                id=stable_comparison_dimension_id(comparison_id, name.value),
                comparison_id=comparison_id,
                name=name,
                position=position,
                source_value=f"Source {name.value.lower()}.",
                target_value=f"Target {name.value.lower()}.",
                assessment=f"Grounded assessment for {name.value.lower()}.",
                source_evidence_ids=((dimension_source_evidence_id,) if position == 0 else ()),
                target_evidence_ids=((dimension_target_evidence_id,) if position == 0 else ()),
                schema_version=1,
                created_at=NOW + timedelta(minutes=4),
            )
            for position, name in enumerate(COMPARISON_DIMENSION_ORDER)
        )
        comparison = Comparison(
            id=comparison_id,
            search_session_id=search_session.id,
            source_paper_id=source_paper_id,
            source_paper_version_id=source_version_id,
            source_analysis_id=source_analysis.analysis.id,
            source_analysis_scope=source_analysis.analysis.analysis_scope,
            target_paper_id=target_paper_id,
            target_paper_version_id=target_version_id,
            target_analysis_id=target_analysis.analysis.id,
            target_analysis_scope=target_analysis.analysis.analysis_scope,
            comparability_status=ComparabilityStatus.PARTIALLY_COMPARABLE,
            comparability_reason="The abstracts support a scoped qualitative comparison.",
            summary="Both papers evaluate bounded LLM-agent workflows.",
            dimensions=dimensions,
            provider="deepseek",
            configured_model="deepseek-v4-flash",
            model_version="DeepSeek-V4-Flash-2026-04-24",
            prompt_version="m3-comparison-v1",
            generated_at=NOW + timedelta(minutes=4),
            source="deepseek_comparison",
            verification_status=VerificationStatus.UNVERIFIED,
            usage=ModelUsage(
                prompt_tokens=200,
                completion_tokens=100,
                total_tokens=300,
                call_count=1,
                duration_ms=600,
                estimated_cost_usd=None,
            ),
            schema_version=1,
            created_at=NOW + timedelta(minutes=4),
        )
        relation_evidence_ids = tuple(
            sorted((dimension_source_evidence_id, dimension_target_evidence_id), key=str)
        )
        relation = PaperRelation(
            id=stable_paper_relation_id(
                comparison_id,
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
            evidence_ids=relation_evidence_ids,
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
        return ComparisonBundle(comparison=comparison, relations=(relation,))

    invalid_bundle = build_bundle(reverse_evidence_ownership=True)
    with pytest.raises(RepositoryIntegrityError, match="wrong owner"):
        postgres_repository.persist_comparison_bundle(invalid_bundle)
    assert postgres_repository.get_comparison(comparison_id) is None

    wrong_analysis_bundle = build_bundle(
        reverse_evidence_ownership=False,
        source_evidence_override=revised_source_analysis.evidence[0].id,
    )
    with pytest.raises(RepositoryIntegrityError, match="wrong owner"):
        postgres_repository.persist_comparison_bundle(wrong_analysis_bundle)
    assert postgres_repository.get_comparison(comparison_id) is None

    expected = build_bundle(reverse_evidence_ownership=False)
    postgres_repository.persist_comparison_bundle(expected)
    stored_detail = postgres_repository.get_comparison(comparison_id)
    assert stored_detail is not None
    assert stored_detail.comparison == expected.comparison
    assert stored_detail.relations == expected.relations
    assert {item.id for item in stored_detail.evidence} == {
        source_evidence_id,
        target_evidence_id,
    }
    assert all(
        item.analysis_scope is AnalysisScope.ABSTRACT_ONLY for item in stored_detail.evidence
    )
