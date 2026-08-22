"""Explicit, resumable six-month Semantic Scholar historical backfill."""

from __future__ import annotations

import calendar
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

from paper_harness.application.scholarly_mapping import external_stub_from_scholarly_paper
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import (
    MAX_HISTORICAL_QUERIES,
    MAX_HISTORICAL_RESULTS_PER_QUERY,
    MAX_HISTORICAL_TIMEOUT_SECONDS,
    BackfillStatus,
    ExternalPaperStub,
    HistoricalBackfillRun,
    HistoricalCorpusEntry,
    ScientificEmbedding,
)
from paper_harness.domain.identity import (
    stable_embedding_id,
    stable_historical_backfill_id,
    stable_historical_corpus_entry_id,
)
from paper_harness.domain.models import TopicConfig
from paper_harness.ports.repository import RepositoryPort
from paper_harness.ports.scholarly_search import (
    ScholarlyPaper,
    ScholarlySearchError,
    ScholarlySearchPort,
)
from paper_harness.ports.scientific_embedding import (
    ScientificEmbeddingOutputError,
    ScientificEmbeddingPort,
    ScientificEmbeddingPortError,
    ScientificPaperText,
)

HISTORICAL_EMBEDDING_BATCH_SIZE = 64


class HistoricalBackfillTimeoutError(RuntimeError):
    error_code = "HISTORICAL_BACKFILL_TIMEOUT"
    retryable = True


def six_month_window(through: date) -> tuple[date, date]:
    """Return the inclusive calendar window starting six months before ``through``."""

    month_index = through.year * 12 + through.month - 1 - 6
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(through.day, calendar.monthrange(year, month)[1])
    return date(year, month, day), through


class HistoricalBackfill:
    def __init__(
        self,
        *,
        repository: RepositoryPort,
        scholarly_search: ScholarlySearchPort,
        embeddings: ScientificEmbeddingPort,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repository = repository
        self._scholarly_search = scholarly_search
        self._embeddings = embeddings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic

    def execute(
        self,
        *,
        topic: TopicConfig,
        through: date,
        max_queries: int = MAX_HISTORICAL_QUERIES,
        per_query_limit: int = MAX_HISTORICAL_RESULTS_PER_QUERY,
        overall_timeout_seconds: float = 3600.0,
    ) -> HistoricalBackfillRun:
        window_from, window_to = six_month_window(through)
        queries = _historical_queries(topic, max_queries=max_queries)
        if not 1 <= per_query_limit <= MAX_HISTORICAL_RESULTS_PER_QUERY:
            raise DomainInvariantError("historical per-query result limit is not bounded")
        if not 1 <= overall_timeout_seconds <= MAX_HISTORICAL_TIMEOUT_SECONDS:
            raise DomainInvariantError("historical backfill timeout is not bounded")
        existing = self._repository.get_historical_backfill(topic.id, window_from, window_to)
        if existing is None:
            started_at = self._aware_now()
            run = self._repository.start_historical_backfill(
                HistoricalBackfillRun(
                    id=stable_historical_backfill_id(topic.id, window_from, window_to),
                    topic_id=topic.id,
                    window_from=window_from,
                    window_to=window_to,
                    query_plan=queries,
                    max_results_per_query=per_query_limit,
                    overall_timeout_seconds=overall_timeout_seconds,
                    embedding_model_identifier=self._embeddings.model_identifier,
                    embedding_model_revision=self._embeddings.model_revision,
                    embedding_tokenizer_identifier=self._embeddings.tokenizer_identifier,
                    embedding_tokenizer_revision=self._embeddings.tokenizer_revision,
                    embedding_dimension=self._embeddings.dimension,
                    embedding_preprocessing_contract=self._embeddings.preprocessing_contract,
                    embedding_model_provenance=self._embeddings.model_provenance,
                    embedding_source=self._embeddings.source,
                    status=BackfillStatus.RUNNING,
                    next_query_index=0,
                    discovered_count=0,
                    persisted_count=0,
                    representative_count=0,
                    started_at=started_at,
                    completed_at=None,
                    error_code=None,
                    error_detail=None,
                    schema_version=1,
                    created_at=started_at,
                )
            )
        else:
            if existing.status is BackfillStatus.COMPLETE:
                return existing
            if existing.status is BackfillStatus.FAILED:
                queries = existing.query_plan
                per_query_limit = existing.max_results_per_query
                run = self._repository.start_historical_backfill(
                    replace(
                        existing,
                        overall_timeout_seconds=overall_timeout_seconds,
                        status=BackfillStatus.RUNNING,
                        started_at=self._aware_now(),
                        completed_at=None,
                        error_code=None,
                        error_detail=None,
                    )
                )
            else:
                queries = existing.query_plan
                per_query_limit = existing.max_results_per_query
                run = existing
        deadline = self._monotonic() + overall_timeout_seconds
        try:
            for query_index in range(run.next_query_index, len(queries)):
                self._require_time_remaining(deadline)
                observed_at = self._aware_now()
                records = self._scholarly_search.search_papers(
                    queries[query_index],
                    window_from.year,
                    window_to.year,
                    per_query_limit,
                    timeout_seconds=self._remaining_seconds(deadline),
                )
                self._require_time_remaining(deadline)
                stubs = _bounded_window_stubs(
                    records,
                    topic=topic,
                    window_from=window_from,
                    window_to=window_to,
                    observed_at=observed_at,
                )
                entries = tuple(
                    HistoricalCorpusEntry(
                        id=stable_historical_corpus_entry_id(topic.id, stub.id),
                        topic_id=topic.id,
                        external_paper_id=stub.id,
                        local_paper_id=None,
                        local_paper_version_id=None,
                        representative_rank=None,
                        first_seen_at=observed_at,
                        last_seen_at=observed_at,
                        schema_version=1,
                    )
                    for stub in stubs
                )
                self._require_time_remaining(deadline)
                embedding_records = self._embed_stubs(
                    stubs,
                    generated_at=observed_at,
                    deadline=deadline,
                )
                self._require_time_remaining(deadline)
                run = self._repository.persist_historical_backfill_page(
                    run.id,
                    expected_query_index=query_index,
                    next_query_index=query_index + 1,
                    papers=stubs,
                    entries=entries,
                    embeddings=embedding_records,
                    discovered_count=run.discovered_count + len(records),
                    persisted_count=run.persisted_count + len(stubs),
                    persisted_at=observed_at,
                )

            representatives = self._select_representatives(
                topic,
                queries=queries,
                window_from=window_from,
                window_to=window_to,
                deadline=deadline,
            )
            completed_at = self._aware_now()
            self._require_time_remaining(deadline)
            return self._repository.finalize_historical_backfill(
                run.id,
                representatives=representatives,
                completed_at=completed_at,
            )
        except (
            ScholarlySearchError,
            ScientificEmbeddingPortError,
            DomainInvariantError,
            HistoricalBackfillTimeoutError,
        ) as error:
            self._repository.fail_historical_backfill(
                run.id,
                completed_at=self._aware_now(),
                error_code=getattr(error, "error_code", "HISTORICAL_BACKFILL_INVALID"),
                error_detail=_concise_detail(error),
            )
            raise

    def _embed_stubs(
        self,
        stubs: tuple[ExternalPaperStub, ...],
        *,
        generated_at: datetime,
        deadline: float,
    ) -> tuple[ScientificEmbedding, ...]:
        embeddable = tuple(stub for stub in stubs if stub.abstract is not None)
        persisted: list[ScientificEmbedding] = []
        for batch in _batches(embeddable, HISTORICAL_EMBEDDING_BATCH_SIZE):
            self._require_time_remaining(deadline)
            generated = self._embeddings.encode(
                tuple(
                    ScientificPaperText(
                        key=stub.semantic_scholar_id,
                        title=stub.title,
                        abstract=stub.abstract or "",
                    )
                    for stub in batch
                )
            )
            by_key = {item.key: item for item in generated}
            if len(by_key) != len(batch) or set(by_key) != {
                item.semantic_scholar_id for item in batch
            }:
                raise ScientificEmbeddingOutputError(
                    "SPECTER2 did not return exactly one embedding for each paper"
                )
            for stub in batch:
                vector = by_key[stub.semantic_scholar_id].vector
                persisted.append(
                    ScientificEmbedding(
                        id=stable_embedding_id(
                            stub.id,
                            model_identifier=self._embeddings.model_identifier,
                            model_revision=self._embeddings.model_revision,
                            tokenizer_identifier=self._embeddings.tokenizer_identifier,
                            tokenizer_revision=self._embeddings.tokenizer_revision,
                            dimension=self._embeddings.dimension,
                            preprocessing_contract=self._embeddings.preprocessing_contract,
                            model_provenance=self._embeddings.model_provenance,
                            source=self._embeddings.source,
                        ),
                        paper_version_id=None,
                        external_paper_id=stub.id,
                        model_identifier=self._embeddings.model_identifier,
                        model_revision=self._embeddings.model_revision,
                        tokenizer_identifier=self._embeddings.tokenizer_identifier,
                        tokenizer_revision=self._embeddings.tokenizer_revision,
                        dimension=self._embeddings.dimension,
                        preprocessing_contract=self._embeddings.preprocessing_contract,
                        model_provenance=self._embeddings.model_provenance,
                        vector=vector,
                        generated_at=generated_at,
                        source=self._embeddings.source,
                        schema_version=1,
                        created_at=generated_at,
                    )
                )
        return tuple(persisted)

    def _select_representatives(
        self,
        topic: TopicConfig,
        *,
        queries: tuple[str, ...],
        window_from: date,
        window_to: date,
        deadline: float,
    ) -> tuple[tuple[UUID, int], ...]:
        best: dict[UUID, float] = {}
        for query in queries:
            self._require_time_remaining(deadline)
            matches = self._repository.search_historical_lexically(
                topic.id,
                query=query,
                limit=min(1000, topic.representative_full_text_count * 4),
            )
            self._require_time_remaining(deadline)
            for match in matches:
                if not match.external_paper.full_text_available:
                    continue
                publication_date = match.external_paper.publication_date
                if publication_date is None or not window_from <= publication_date <= window_to:
                    continue
                best[match.external_paper.id] = max(
                    best.get(match.external_paper.id, 0.0), match.score
                )
            self._require_time_remaining(deadline)
        ranked = sorted(best.items(), key=lambda item: (-item[1], str(item[0])))
        representatives = tuple(
            (paper_id, rank)
            for rank, (paper_id, _score) in enumerate(
                ranked[: topic.representative_full_text_count], start=1
            )
        )
        self._require_time_remaining(deadline)
        return representatives

    def _require_time_remaining(self, deadline: float) -> None:
        if self._monotonic() >= deadline:
            raise HistoricalBackfillTimeoutError(
                "historical backfill exhausted its overall execution timeout"
            )

    def _remaining_seconds(self, deadline: float) -> float:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise HistoricalBackfillTimeoutError(
                "historical backfill exhausted its overall execution timeout"
            )
        return remaining

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise DomainInvariantError("historical backfill clock must be timezone-aware")
        return value.astimezone(UTC)


def _historical_queries(topic: TopicConfig, *, max_queries: int) -> tuple[str, ...]:
    if not 1 <= max_queries <= MAX_HISTORICAL_QUERIES:
        raise DomainInvariantError("historical query count is not bounded")
    queries = tuple(dict.fromkeys(term.strip() for term in topic.include_terms if term.strip()))
    if len(queries) > max_queries:
        raise DomainInvariantError(
            "topic include terms exceed the configured historical query bound"
        )
    return queries


def _bounded_window_stubs(
    records: Iterable[ScholarlyPaper],
    *,
    topic: TopicConfig,
    window_from: date,
    window_to: date,
    observed_at: datetime,
) -> tuple[ExternalPaperStub, ...]:
    by_id: dict[str, ExternalPaperStub] = {}
    for value in records:
        if value.publication_date is None or not window_from <= value.publication_date <= window_to:
            continue
        if not _is_topic_relevant(value, topic):
            continue
        stub = external_stub_from_scholarly_paper(value, observed_at=observed_at)
        by_id[stub.semantic_scholar_id] = stub
    return tuple(by_id[key] for key in sorted(by_id))


def _is_topic_relevant(paper: ScholarlyPaper, topic: TopicConfig) -> bool:
    text = f"{paper.title} {paper.abstract or ''}".casefold()
    if any(term.strip().casefold() in text for term in topic.exclude_terms if term.strip()):
        return False
    paper_tokens = set(re.findall(r"[a-z0-9]+", text))
    topic_tokens = {
        token
        for term in topic.include_terms
        for token in re.findall(r"[a-z0-9]+", term.casefold())
        if len(token) >= 3
    }
    return bool(paper_tokens.intersection(topic_tokens))


def _batches(
    values: tuple[ExternalPaperStub, ...], size: int
) -> Iterable[tuple[ExternalPaperStub, ...]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _concise_detail(error: Exception) -> str:
    detail = " ".join(str(error).split())
    return (detail or type(error).__name__)[:1000]
