from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

import pytest

from paper_harness.application.historical_backfill import (
    HistoricalBackfill,
    HistoricalBackfillTimeoutError,
    six_month_window,
)
from paper_harness.application.read_models import HistoricalRetrievalMatch
from paper_harness.application.scholarly_mapping import external_stub_from_scholarly_paper
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import (
    BackfillStatus,
    ExternalPaperStub,
    HistoricalBackfillRun,
    HistoricalCorpusEntry,
    ScientificEmbedding,
)
from paper_harness.domain.models import TopicConfig
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


class _ScholarlySearch:
    def __init__(
        self,
        papers: tuple[ScholarlyPaper, ...],
        *,
        on_search: Callable[[], None] | None = None,
    ) -> None:
        self.papers = papers
        self.queries: list[str] = []
        self.timeouts: list[float | None] = []
        self.on_search = on_search

    def search_papers(
        self,
        query: str,
        year_from: int,
        year_to: int,
        limit: int,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[ScholarlyPaper, ...]:
        del year_from, year_to, limit
        self.queries.append(query)
        self.timeouts.append(timeout_seconds)
        if self.on_search is not None:
            self.on_search()
        return self.papers


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


class _Repository:
    def __init__(self, existing: HistoricalBackfillRun | None = None) -> None:
        self.run = existing
        self.pages: list[
            tuple[
                tuple[ExternalPaperStub, ...],
                tuple[HistoricalCorpusEntry, ...],
                tuple[ScientificEmbedding, ...],
            ]
        ] = []
        self.representatives: tuple[tuple[UUID, int], ...] = ()
        self.matches: tuple[HistoricalRetrievalMatch, ...] = ()
        self.historical_queries: list[str] = []
        self.on_historical_search: Callable[[], None] | None = None
        self.finalize_calls = 0

    def get_historical_backfill(
        self, topic_id: UUID, window_from: date, window_to: date
    ) -> HistoricalBackfillRun | None:
        del topic_id, window_from, window_to
        return self.run

    def start_historical_backfill(self, run: HistoricalBackfillRun) -> HistoricalBackfillRun:
        self.run = run
        return run

    def persist_historical_backfill_page(
        self,
        run_id: UUID,
        *,
        expected_query_index: int,
        next_query_index: int,
        papers: tuple[ExternalPaperStub, ...],
        entries: tuple[HistoricalCorpusEntry, ...],
        embeddings: tuple[ScientificEmbedding, ...],
        discovered_count: int,
        persisted_count: int,
        persisted_at: datetime,
    ) -> HistoricalBackfillRun:
        del run_id, persisted_at
        assert self.run is not None
        assert self.run.next_query_index == expected_query_index
        self.pages.append((papers, entries, embeddings))
        self.run = replace(
            self.run,
            next_query_index=next_query_index,
            discovered_count=discovered_count,
            persisted_count=persisted_count,
        )
        return self.run

    def search_historical_lexically(
        self, topic_id: UUID, *, query: str, limit: int
    ) -> tuple[HistoricalRetrievalMatch, ...]:
        del topic_id, limit
        self.historical_queries.append(query)
        if self.on_historical_search is not None:
            self.on_historical_search()
        return self.matches

    def finalize_historical_backfill(
        self,
        run_id: UUID,
        *,
        representatives: tuple[tuple[UUID, int], ...],
        completed_at: datetime,
    ) -> HistoricalBackfillRun:
        del run_id
        assert self.run is not None
        self.finalize_calls += 1
        self.representatives = representatives
        self.run = replace(
            self.run,
            status=BackfillStatus.COMPLETE,
            representative_count=len(representatives),
            completed_at=completed_at,
        )
        return self.run

    def fail_historical_backfill(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> HistoricalBackfillRun:
        del run_id
        assert self.run is not None
        self.run = replace(
            self.run,
            status=BackfillStatus.FAILED,
            completed_at=completed_at,
            error_code=error_code,
            error_detail=error_detail,
        )
        return self.run


def _paper(
    paper_id: str,
    *,
    publication_date: date,
    arxiv_id: str | None = "2603.01234",
    abstract: str | None = "A planning-agent method.",
) -> ScholarlyPaper:
    values = () if arxiv_id is None else (("ArXiv", arxiv_id),)
    return ScholarlyPaper(
        semantic_scholar_id=paper_id,
        corpus_id=int(paper_id[:8], 16) + 1,
        external_ids=ScholarlyExternalIds(
            arxiv_id=arxiv_id,
            doi=None,
            values=values,
        ),
        url=f"https://www.semanticscholar.org/paper/{paper_id}",
        title=f"LLM Agent Paper {paper_id[:4]}",
        abstract=abstract,
        venue="ACL",
        year=publication_date.year,
        publication_date=publication_date,
        authors=(ScholarlyAuthor(author_id="1", name="Ada Lovelace"),),
        citation_count=10,
        influential_citation_count=2,
        reference_count=5,
    )


def _service(
    repository: _Repository,
    scholarly: _ScholarlySearch,
    *,
    monotonic: Callable[[], float] = lambda: 0.0,
) -> HistoricalBackfill:
    return HistoricalBackfill(
        repository=cast(RepositoryPort, repository),
        scholarly_search=cast(ScholarlySearchPort, scholarly),
        embeddings=cast(ScientificEmbeddingPort, _Embeddings()),
        clock=lambda: NOW,
        monotonic=monotonic,
    )


def test_six_month_window_handles_calendar_month_length() -> None:
    assert six_month_window(date(2026, 8, 31)) == (
        date(2026, 2, 28),
        date(2026, 8, 31),
    )


def test_external_stub_rejects_conflicting_canonical_identifier_metadata() -> None:
    stub = external_stub_from_scholarly_paper(
        _paper("1" * 40, publication_date=date(2026, 3, 1)),
        observed_at=NOW,
    )

    with pytest.raises(DomainInvariantError, match="canonical identifiers"):
        replace(
            stub,
            external_ids=stub.external_ids + (("DOI", "10.1000/conflicting"),),
        )


def test_backfill_filters_exact_window_and_persists_embeddings_atomically(
    topic_config: TopicConfig,
) -> None:
    in_window = _paper("a" * 40, publication_date=date(2026, 4, 1))
    outside = _paper("b" * 40, publication_date=date(2025, 12, 1))
    missing_abstract = _paper(
        "c" * 40,
        publication_date=date(2026, 6, 1),
        abstract=None,
    )
    scholarly = _ScholarlySearch((in_window, outside, missing_abstract))
    repository = _Repository()

    result = _service(repository, scholarly).execute(
        topic=topic_config,
        through=date(2026, 8, 9),
    )

    assert result.status is BackfillStatus.COMPLETE
    assert scholarly.queries == ["LLM agent", "web agent"]
    assert len(repository.pages) == 2
    assert all(len(page[0]) == 2 for page in repository.pages)
    assert all(len(page[2]) == 1 for page in repository.pages)
    assert all(page[2][0].dimension == 768 for page in repository.pages)
    assert result.query_plan == ("LLM agent", "web agent")
    assert result.embedding_model_revision == _Embeddings.model_revision


def test_backfill_resumes_at_persisted_query_boundary(topic_config: TopicConfig) -> None:
    window_from, window_to = six_month_window(date(2026, 8, 9))
    existing = HistoricalBackfillRun(
        id=UUID("824a0698-c1e2-4cb1-a978-2cf3da723e70"),
        topic_id=topic_config.id,
        window_from=window_from,
        window_to=window_to,
        query_plan=("LLM agent", "web agent"),
        max_results_per_query=500,
        overall_timeout_seconds=3600.0,
        embedding_model_identifier=_Embeddings.model_identifier,
        embedding_model_revision=_Embeddings.model_revision,
        embedding_tokenizer_identifier=_Embeddings.tokenizer_identifier,
        embedding_tokenizer_revision=_Embeddings.tokenizer_revision,
        embedding_dimension=_Embeddings.dimension,
        embedding_preprocessing_contract=_Embeddings.preprocessing_contract,
        embedding_model_provenance=_Embeddings.model_provenance,
        embedding_source=_Embeddings.source,
        status=BackfillStatus.RUNNING,
        next_query_index=1,
        discovered_count=3,
        persisted_count=2,
        representative_count=0,
        started_at=NOW,
        completed_at=None,
        error_code=None,
        error_detail=None,
        schema_version=1,
        created_at=NOW,
    )
    repository = _Repository(existing)
    scholarly = _ScholarlySearch((_paper("d" * 40, publication_date=date(2026, 5, 1)),))

    result = _service(repository, scholarly).execute(
        topic=topic_config,
        through=date(2026, 8, 9),
    )

    assert result.status is BackfillStatus.COMPLETE
    assert scholarly.queries == ["web agent"]
    assert result.next_query_index == 2


def test_completed_backfill_is_idempotent(topic_config: TopicConfig) -> None:
    window_from, window_to = six_month_window(date(2026, 8, 9))
    existing = HistoricalBackfillRun(
        id=UUID("824a0698-c1e2-4cb1-a978-2cf3da723e70"),
        topic_id=topic_config.id,
        window_from=window_from,
        window_to=window_to,
        query_plan=("LLM agent", "web agent"),
        max_results_per_query=500,
        overall_timeout_seconds=3600.0,
        embedding_model_identifier=_Embeddings.model_identifier,
        embedding_model_revision=_Embeddings.model_revision,
        embedding_tokenizer_identifier=_Embeddings.tokenizer_identifier,
        embedding_tokenizer_revision=_Embeddings.tokenizer_revision,
        embedding_dimension=_Embeddings.dimension,
        embedding_preprocessing_contract=_Embeddings.preprocessing_contract,
        embedding_model_provenance=_Embeddings.model_provenance,
        embedding_source=_Embeddings.source,
        status=BackfillStatus.COMPLETE,
        next_query_index=2,
        discovered_count=3,
        persisted_count=2,
        representative_count=1,
        started_at=NOW,
        completed_at=NOW,
        error_code=None,
        error_detail=None,
        schema_version=1,
        created_at=NOW,
    )
    repository = _Repository(existing)
    scholarly = _ScholarlySearch(())

    result = _service(repository, scholarly).execute(
        topic=topic_config,
        through=date(2026, 8, 9),
    )

    assert result is existing
    assert scholarly.queries == []


def test_smoke_and_normal_profiles_reuse_the_same_weekly_backfill_contract(
    topic_config: TopicConfig,
) -> None:
    repository = _Repository()
    smoke_search = _ScholarlySearch(())
    smoke = _service(repository, smoke_search).execute(
        topic=topic_config,
        through=date(2026, 8, 9),
        max_queries=8,
        per_query_limit=100,
        overall_timeout_seconds=1800,
    )
    normal_search = _ScholarlySearch(())

    normal = _service(repository, normal_search).execute(
        topic=topic_config,
        through=date(2026, 8, 9),
        max_queries=8,
        per_query_limit=100,
        overall_timeout_seconds=1800,
    )

    assert smoke.status is BackfillStatus.COMPLETE
    assert normal is smoke
    assert normal_search.queries == []

    changed_profile = _service(repository, _ScholarlySearch(())).execute(
        topic=topic_config,
        through=date(2026, 8, 9),
        max_queries=8,
        per_query_limit=50,
        overall_timeout_seconds=1800,
    )
    assert changed_profile is smoke


def test_completed_backfill_remains_terminal_after_model_configuration_changes(
    topic_config: TopicConfig,
) -> None:
    window_from, window_to = six_month_window(date(2026, 8, 9))
    existing = HistoricalBackfillRun(
        id=UUID("824a0698-c1e2-4cb1-a978-2cf3da723e70"),
        topic_id=topic_config.id,
        window_from=window_from,
        window_to=window_to,
        query_plan=("LLM agent", "web agent"),
        max_results_per_query=500,
        overall_timeout_seconds=3600.0,
        embedding_model_identifier=_Embeddings.model_identifier,
        embedding_model_revision="superseded-revision",
        embedding_tokenizer_identifier=_Embeddings.tokenizer_identifier,
        embedding_tokenizer_revision=_Embeddings.tokenizer_revision,
        embedding_dimension=_Embeddings.dimension,
        embedding_preprocessing_contract=_Embeddings.preprocessing_contract,
        embedding_model_provenance=_Embeddings.model_provenance,
        embedding_source=_Embeddings.source,
        status=BackfillStatus.COMPLETE,
        next_query_index=2,
        discovered_count=3,
        persisted_count=2,
        representative_count=1,
        started_at=NOW,
        completed_at=NOW,
        error_code=None,
        error_detail=None,
        schema_version=1,
        created_at=NOW,
    )
    scholarly = _ScholarlySearch(())

    result = _service(_Repository(existing), scholarly).execute(
        topic=topic_config,
        through=date(2026, 8, 9),
    )

    assert result is existing
    assert scholarly.queries == []


def test_running_backfill_continues_its_persisted_query_plan(topic_config: TopicConfig) -> None:
    window_from, window_to = six_month_window(date(2026, 8, 9))
    existing = HistoricalBackfillRun(
        id=UUID("824a0698-c1e2-4cb1-a978-2cf3da723e70"),
        topic_id=topic_config.id,
        window_from=window_from,
        window_to=window_to,
        query_plan=("different query",),
        max_results_per_query=500,
        overall_timeout_seconds=3600.0,
        embedding_model_identifier=_Embeddings.model_identifier,
        embedding_model_revision=_Embeddings.model_revision,
        embedding_tokenizer_identifier=_Embeddings.tokenizer_identifier,
        embedding_tokenizer_revision=_Embeddings.tokenizer_revision,
        embedding_dimension=_Embeddings.dimension,
        embedding_preprocessing_contract=_Embeddings.preprocessing_contract,
        embedding_model_provenance=_Embeddings.model_provenance,
        embedding_source=_Embeddings.source,
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
    scholarly = _ScholarlySearch(())

    result = _service(_Repository(existing), scholarly).execute(
        topic=topic_config,
        through=date(2026, 8, 9),
    )

    assert result.status is BackfillStatus.COMPLETE
    assert scholarly.queries == ["different query"]


def test_backfill_excludes_out_of_scope_results(topic_config: TopicConfig) -> None:
    relevant = _paper("a" * 40, publication_date=date(2026, 4, 1))
    excluded = replace(
        _paper("b" * 40, publication_date=date(2026, 4, 1)),
        title="LLM Agent-Based Social Simulation",
        abstract="An agent-based social simulation without an LLM-centered workflow.",
    )
    repository = _Repository()

    _service(repository, _ScholarlySearch((relevant, excluded))).execute(
        topic=topic_config,
        through=date(2026, 8, 9),
    )

    assert all(len(page[0]) == 1 for page in repository.pages)
    assert all(page[0][0].semantic_scholar_id == "a" * 40 for page in repository.pages)


def test_backfill_timeout_is_bounded_and_records_failure(topic_config: TopicConfig) -> None:
    elapsed = [0.0]

    def exhaust_deadline() -> None:
        elapsed[0] = 2.0

    repository = _Repository()
    scholarly = _ScholarlySearch(
        (_paper("a" * 40, publication_date=date(2026, 4, 1)),),
        on_search=exhaust_deadline,
    )

    with pytest.raises(HistoricalBackfillTimeoutError):
        _service(repository, scholarly, monotonic=lambda: elapsed[0]).execute(
            topic=topic_config,
            through=date(2026, 8, 9),
            overall_timeout_seconds=1,
        )

    assert repository.run is not None
    assert repository.run.status is BackfillStatus.FAILED
    assert repository.run.next_query_index == 0
    assert scholarly.timeouts == [1.0]

    resumed_search = _ScholarlySearch((_paper("a" * 40, publication_date=date(2026, 4, 1)),))
    resumed = _service(repository, resumed_search).execute(
        topic=topic_config,
        through=date(2026, 8, 9),
        overall_timeout_seconds=1,
    )

    assert resumed.status is BackfillStatus.COMPLETE
    assert resumed.next_query_index == len(resumed.query_plan)
    assert resumed_search.queries == ["LLM agent", "web agent"]


def test_backfill_does_not_call_semantic_scholar_after_deadline(
    topic_config: TopicConfig,
) -> None:
    elapsed = iter((0.0, 2.0))
    last = [0.0]

    def monotonic() -> float:
        last[0] = next(elapsed, last[0])
        return last[0]

    repository = _Repository()
    scholarly = _ScholarlySearch(())

    with pytest.raises(HistoricalBackfillTimeoutError):
        _service(repository, scholarly, monotonic=monotonic).execute(
            topic=topic_config,
            through=date(2026, 8, 9),
            overall_timeout_seconds=1,
        )

    assert scholarly.queries == []
    assert scholarly.timeouts == []


def test_timeout_during_representative_selection_fails_without_finalizing_and_resumes(
    topic_config: TopicConfig,
) -> None:
    elapsed = [0.0]
    repository = _Repository()
    repository.on_historical_search = lambda: elapsed.__setitem__(0, 2.0)

    with pytest.raises(HistoricalBackfillTimeoutError):
        _service(
            repository,
            _ScholarlySearch(()),
            monotonic=lambda: elapsed[0],
        ).execute(
            topic=topic_config,
            through=date(2026, 8, 9),
            overall_timeout_seconds=1,
        )

    assert repository.run is not None
    assert repository.run.status is BackfillStatus.FAILED
    assert repository.run.error_code == HistoricalBackfillTimeoutError.error_code
    assert repository.run.next_query_index == len(repository.run.query_plan)
    assert repository.historical_queries == ["LLM agent"]
    assert repository.finalize_calls == 0

    elapsed[0] = 0.0
    repository.on_historical_search = None
    resumed_search = _ScholarlySearch(())
    resumed = _service(
        repository,
        resumed_search,
        monotonic=lambda: elapsed[0],
    ).execute(
        topic=topic_config,
        through=date(2026, 8, 9),
        overall_timeout_seconds=1,
    )

    assert resumed.status is BackfillStatus.COMPLETE
    assert resumed_search.queries == ["LLM agent", "web agent"]
    assert repository.finalize_calls == 1


def test_timeout_after_representative_selection_fails_without_finalizing(
    topic_config: TopicConfig,
) -> None:
    selection_queries = [0]
    checks_after_last_query = [0]
    repository = _Repository()
    repository.on_historical_search = lambda: selection_queries.__setitem__(
        0, selection_queries[0] + 1
    )

    def monotonic() -> float:
        if selection_queries[0] < 2:
            return 0.0
        checks_after_last_query[0] += 1
        return 0.0 if checks_after_last_query[0] <= 3 else 2.0

    with pytest.raises(HistoricalBackfillTimeoutError):
        _service(
            repository,
            _ScholarlySearch(()),
            monotonic=monotonic,
        ).execute(
            topic=topic_config,
            through=date(2026, 8, 9),
            overall_timeout_seconds=1,
        )

    assert repository.run is not None
    assert repository.run.status is BackfillStatus.FAILED
    assert repository.run.error_code == HistoricalBackfillTimeoutError.error_code
    assert repository.run.next_query_index == len(repository.run.query_plan)
    assert repository.historical_queries == ["LLM agent", "web agent"]
    assert repository.finalize_calls == 0


def test_representatives_are_scoped_to_the_backfill_window(topic_config: TopicConfig) -> None:
    in_window = _paper("a" * 40, publication_date=date(2026, 4, 1))
    out_of_window = _paper("b" * 40, publication_date=date(2025, 12, 1))
    in_stub = external_stub_from_scholarly_paper(in_window, observed_at=NOW)
    out_stub = external_stub_from_scholarly_paper(out_of_window, observed_at=NOW)
    repository = _Repository()
    repository.matches = (
        HistoricalRetrievalMatch(
            external_paper=out_stub,
            corpus_entry=HistoricalCorpusEntry(
                id=UUID("c136688c-781f-4adc-8e3a-0c5e8e66e361"),
                topic_id=topic_config.id,
                external_paper_id=out_stub.id,
                local_paper_id=None,
                local_paper_version_id=None,
                representative_rank=None,
                first_seen_at=NOW,
                last_seen_at=NOW,
                schema_version=1,
            ),
            score=0.99,
        ),
        HistoricalRetrievalMatch(
            external_paper=in_stub,
            corpus_entry=HistoricalCorpusEntry(
                id=UUID("7415db81-d351-4342-bee7-18c439cd28a5"),
                topic_id=topic_config.id,
                external_paper_id=in_stub.id,
                local_paper_id=None,
                local_paper_version_id=None,
                representative_rank=None,
                first_seen_at=NOW,
                last_seen_at=NOW,
                schema_version=1,
            ),
            score=0.8,
        ),
    )

    _service(repository, _ScholarlySearch((in_window,))).execute(
        topic=topic_config,
        through=date(2026, 8, 9),
    )

    assert repository.representatives == ((in_stub.id, 1),)
