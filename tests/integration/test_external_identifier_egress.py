# pyright: reportPrivateUsage=false
"""PostgreSQL egress and identity regressions for external-paper refreshes."""

from __future__ import annotations

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from uuid import UUID

import pytest
from psycopg.errors import UniqueViolation
from sqlalchemy import Connection, Engine, event, select, text
from sqlalchemy.engine.interfaces import DBAPICursor, ExecutionContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tests.integration.test_m3_postgres_repository import (
    NOW,
    _external_stub,
    _ingest,
    _pending_local_candidate,
    _search_session,
    _second_arxiv_record,
)

from paper_harness.adapters.postgres import PostgresRepository
from paper_harness.adapters.postgres.historical_repository import _upsert_external_paper
from paper_harness.adapters.postgres.models import (
    ExternalPaperIdentifierRow,
    ExternalPaperStubRow,
    HistoricalCorpusEntryRow,
    ScientificEmbeddingRow,
)
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
from paper_harness.domain.historical import ExternalPaperStub
from paper_harness.domain.identity import (
    stable_candidate_discovery_id,
    stable_embedding_id,
    stable_external_paper_id,
    stable_historical_corpus_entry_id,
    stable_paper_id,
    stable_paper_version_id,
    stable_search_candidate_id,
)
from paper_harness.domain.models import TopicConfig
from paper_harness.ports.arxiv import ArxivPaperRecord
from paper_harness.ports.repository import (
    ExternalPaperIdentifierConflictError,
    RepositoryIntegrityError,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("postgres_repository")]

_DEPENDENT_TABLES = (
    "historical_corpus_entries",
    "scientific_embeddings",
    "search_candidates",
    "search_candidate_discoveries",
)


def _stub(number: int, identifiers: tuple[tuple[str, str], ...]) -> ExternalPaperStub:
    semantic_scholar_id = f"{number:040x}"
    values = {key.casefold(): value for key, value in identifiers}
    arxiv_id = values.get("arxiv")
    return ExternalPaperStub(
        id=stable_external_paper_id(semantic_scholar_id, arxiv_id=arxiv_id),
        semantic_scholar_id=semantic_scholar_id,
        title=f"External research paper {number}",
        abstract="A bounded historical metadata record.",
        year=NOW.year,
        publication_date=NOW.date(),
        venue=None,
        authors=("Ada Lovelace",),
        external_ids=identifiers,
        arxiv_id=arxiv_id,
        doi=values.get("doi"),
        citation_count=0,
        influential_citation_count=0,
        full_text_available=arxiv_id is not None,
        source="semantic_scholar",
        schema_version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _persist(engine: Engine, *papers: ExternalPaperStub) -> None:
    with Session(engine) as session, session.begin():
        for paper in papers:
            _upsert_external_paper(session, paper)


@contextmanager
def _capture_selects(engine: Engine) -> Generator[list[tuple[str, int]]]:
    statements: list[tuple[str, int]] = []

    def capture(
        _connection: Connection,
        cursor: DBAPICursor,
        statement: str,
        _parameters: object,
        _context: ExecutionContext,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().lower().startswith("select"):
            statements.append((" ".join(statement.lower().split()), cursor.rowcount))

    event.listen(engine, "after_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "after_cursor_execute", capture)


def _snapshot(engine: Engine, tables: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    with engine.connect() as connection:
        return {
            table: tuple(
                connection.scalars(
                    text(f"SELECT to_jsonb(r)::text FROM {table} AS r ORDER BY to_jsonb(r)::text")
                )
            )
            for table in tables
        }


@pytest.mark.parametrize("unrelated_count", (0, 256))
def test_identifier_result_rows_stay_bounded_as_corpus_grows(
    postgres_engine: Engine,
    unrelated_count: int,
) -> None:
    original = _stub(1, (("DOI", "10.1000/target"), ("CorpusId", "target")))
    _persist(postgres_engine, original)
    _persist(
        postgres_engine,
        *(
            _stub(
                number + 2,
                (("DOI", f"10.1000/unrelated-{number}"), ("CorpusId", f"other-{number}")),
            )
            for number in range(unrelated_count)
        ),
    )

    with _capture_selects(postgres_engine) as statements:
        _persist(postgres_engine, replace(original, updated_at=NOW + timedelta(seconds=1)))

    returned_rows = [
        count for statement, count in statements if "from external_paper_identifiers" in statement
    ]
    assert returned_rows
    assert all(count >= 0 for count in returned_rows)
    assert sum(returned_rows) <= len(original.external_ids) + 1


@pytest.mark.parametrize(
    ("stored_identifier", "conflicting_identifier"),
    (
        (("ACL", "2026.example.1"), ("acl", "2026.example.1")),
        (("DOI", "10.1000/UpperCase"), ("doi", "10.1000/uppercase")),
        (("DOI", "10.1000/stra\u00dfe"), ("doi", "10.1000/strasse")),
        (("DOI", "10.1000/\u03c3"), ("doi", "10.1000/\u03c2")),
    ),
)
def test_cross_owner_identifier_conflict_rolls_back_entire_write(
    postgres_engine: Engine,
    stored_identifier: tuple[str, str],
    conflicting_identifier: tuple[str, str],
) -> None:
    original = _stub(1, (stored_identifier,))
    conflict = _stub(2, (conflicting_identifier,))
    unrelated = _stub(3, (("CorpusId", "transaction-must-rollback"),))
    _persist(postgres_engine, original)
    tables = ("external_paper_stubs", "external_paper_identifiers")
    before = _snapshot(postgres_engine, tables)

    with (
        _capture_selects(postgres_engine) as statements,
        pytest.raises(ExternalPaperIdentifierConflictError, match="identifier conflict"),
    ):
        _persist(postgres_engine, unrelated, conflict)

    assert _snapshot(postgres_engine, tables) == before
    conflict_rows = [
        count for statement, count in statements if "from external_paper_identifiers" in statement
    ]
    assert conflict_rows
    assert max(conflict_rows) <= 1


@pytest.mark.parametrize(
    ("original_identifier", "refreshed_identifier"),
    (
        (("ArXiv", "hep-th/9901001"), ("aRxIv", "HEP-TH/9901001")),
        (("DOI", "10.1000/UpperCase"), ("dOi", "10.1000/uppercase")),
        (("DOI", "10.1000/stra\u00dfe"), ("dOi", "10.1000/strasse")),
        (("DOI", "10.1000/\u03c3"), ("dOi", "10.1000/\u03c2")),
    ),
)
def test_equivalent_identifier_refresh_preserves_source_spelling(
    postgres_engine: Engine,
    original_identifier: tuple[str, str],
    refreshed_identifier: tuple[str, str],
) -> None:
    original = _stub(1, (original_identifier,))
    refreshed = _stub(1, (refreshed_identifier,))
    _persist(postgres_engine, original)
    _persist(postgres_engine, replace(refreshed, updated_at=NOW + timedelta(seconds=1)))

    with postgres_engine.connect() as connection:
        stored_identifiers = tuple(
            connection.execute(
                select(
                    ExternalPaperIdentifierRow.identifier_type,
                    ExternalPaperIdentifierRow.identifier_value,
                ).where(ExternalPaperIdentifierRow.external_paper_id == original.id)
            )
        )
        stored_identity = connection.execute(
            select(ExternalPaperStubRow.arxiv_id, ExternalPaperStubRow.doi).where(
                ExternalPaperStubRow.id == original.id
            )
        ).one()
    assert stored_identifiers == (original_identifier,)
    assert stored_identity == (original.arxiv_id, original.doi)


def test_noncanonical_identifier_values_remain_case_sensitive(postgres_engine: Engine) -> None:
    first = _stub(1, (("ACL", "MixedCase"),))
    second = _stub(2, (("acl", "mixedcase"),))
    _persist(postgres_engine, first, second)
    with postgres_engine.connect() as connection:
        identities = set(connection.scalars(select(ExternalPaperStubRow.id)))
    assert identities == {first.id, second.id}

    with pytest.raises(ExternalPaperIdentifierConflictError, match="identifier conflict"):
        _persist(postgres_engine, replace(first, external_ids=(("acl", "MIXEDCASE"),)))


def test_unchanged_identity_refresh_preserves_dependents_without_reading_them(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _ingest(postgres_repository, topic_config, (arxiv_record_v1,))
    paper = _external_stub(_second_arxiv_record(arxiv_record_v1), semantic_scholar_id="c" * 40)
    search_session = _search_session(
        UUID("ef7d22b8-c27e-4a58-8c1e-a9f9d3a1f481"),
        topic_id=topic_config.id,
        source_paper_id=stable_paper_id(arxiv_record_v1.canonical_arxiv_id),
        source_paper_version_id=stable_paper_version_id(arxiv_record_v1.canonical_arxiv_id, 1),
        started_at=NOW,
        objective="Preserve historical dependents across ordinary metadata refreshes.",
    )
    candidate, discovery = _pending_local_candidate(search_session.id, paper, created_at=NOW)
    postgres_repository.start_search_session(search_session)
    postgres_repository.persist_local_search_candidates(
        search_session.id,
        papers=(paper,),
        candidates=(candidate,),
        discoveries=(discovery,),
    )
    embedding_identity = {
        "model_identifier": SPECTER2_MODEL_IDENTIFIER,
        "model_revision": SPECTER2_MODEL_REVISION,
        "tokenizer_identifier": SPECTER2_TOKENIZER_IDENTIFIER,
        "tokenizer_revision": SPECTER2_TOKENIZER_REVISION,
        "dimension": SPECTER2_DIMENSION,
        "preprocessing_contract": SPECTER2_PREPROCESSING_CONTRACT,
        "model_provenance": SPECTER2_MODEL_PROVENANCE,
        "source": SPECTER2_EMBEDDING_SOURCE,
    }
    with Session(postgres_engine) as session, session.begin():
        session.add(
            HistoricalCorpusEntryRow(
                id=stable_historical_corpus_entry_id(topic_config.id, paper.id),
                topic_id=topic_config.id,
                external_paper_id=paper.id,
                local_paper_id=None,
                local_paper_version_id=None,
                representative_rank=1,
                first_seen_at=NOW,
                last_seen_at=NOW,
                schema_version=1,
            )
        )
        session.add(
            ScientificEmbeddingRow(
                id=stable_embedding_id(
                    paper.id,
                    model_identifier=SPECTER2_MODEL_IDENTIFIER,
                    model_revision=SPECTER2_MODEL_REVISION,
                    tokenizer_identifier=SPECTER2_TOKENIZER_IDENTIFIER,
                    tokenizer_revision=SPECTER2_TOKENIZER_REVISION,
                    dimension=SPECTER2_DIMENSION,
                    preprocessing_contract=SPECTER2_PREPROCESSING_CONTRACT,
                    model_provenance=SPECTER2_MODEL_PROVENANCE,
                    source=SPECTER2_EMBEDDING_SOURCE,
                ),
                external_paper_id=paper.id,
                paper_version_id=None,
                vector=[0.125] * SPECTER2_DIMENSION,
                generated_at=NOW,
                schema_version=1,
                created_at=NOW,
                **embedding_identity,
            )
        )
    before = _snapshot(postgres_engine, _DEPENDENT_TABLES)
    assert all(before.values())

    with _capture_selects(postgres_engine) as statements:
        _persist(
            postgres_engine,
            replace(paper, title="Refreshed research title", updated_at=NOW + timedelta(seconds=1)),
        )

    assert not [
        statement
        for statement, _ in statements
        if any(f"from {table}" in statement for table in _DEPENDENT_TABLES)
    ]
    assert _snapshot(postgres_engine, _DEPENDENT_TABLES) == before
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                select(ExternalPaperStubRow.title).where(ExternalPaperStubRow.id == paper.id)
            )
            == "Refreshed research title"
        )


def test_alias_merge_rekeys_discoveries_when_survivor_identity_is_unchanged(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _ingest(postgres_repository, topic_config, (arxiv_record_v1,))
    survivor = _stub(1, (("ArXiv", "2601.05678"),))
    duplicate = _stub(2, (("DOI", "10.1000/newly-linked"),))
    search_session = _search_session(
        UUID("cb08fb24-7354-4600-9521-9e489626ff46"),
        topic_id=topic_config.id,
        source_paper_id=stable_paper_id(arxiv_record_v1.canonical_arxiv_id),
        source_paper_version_id=stable_paper_version_id(arxiv_record_v1.canonical_arxiv_id, 1),
        started_at=NOW,
        objective="Reconcile discoveries from a merged identity alias.",
    )
    candidate, discovery = _pending_local_candidate(search_session.id, duplicate, created_at=NOW)
    _persist(postgres_engine, survivor)
    postgres_repository.start_search_session(search_session)
    postgres_repository.persist_local_search_candidates(
        search_session.id,
        papers=(duplicate,),
        candidates=(candidate,),
        discoveries=(discovery,),
    )
    enriched = replace(
        survivor,
        doi=duplicate.doi,
        external_ids=survivor.external_ids + duplicate.external_ids,
        updated_at=NOW + timedelta(seconds=1),
    )

    _persist(postgres_engine, enriched)

    detail = postgres_repository.get_search_session(search_session.id)
    assert detail is not None
    assert len(detail.candidates) == len(detail.discoveries) == 1
    candidate_id = stable_search_candidate_id(search_session.id, survivor.semantic_scholar_id)
    assert detail.candidates[0].id == candidate_id
    assert detail.candidates[0].external_paper_id == survivor.id
    assert detail.candidates[0].semantic_scholar_id == survivor.semantic_scholar_id
    assert detail.discoveries[0].candidate_id == candidate_id
    assert detail.discoveries[0].id == stable_candidate_discovery_id(
        candidate_id, discovery.origin.value, discovery.action_id, discovery.relation_depth
    )
    assert detail.discoveries[0].discovered_at == discovery.discovered_at
    with postgres_engine.connect() as connection:
        assert set(connection.scalars(select(ExternalPaperStubRow.id))) == {survivor.id}


@pytest.mark.parametrize(
    ("second_identifier_type", "shared_root", "expected_constraint"),
    (
        pytest.param(
            "ACL", False, "uq_external_paper_identifiers_external", id="raw-identifier-conflict"
        ),
        pytest.param(
            "acl", False, "uq_external_paper_identifiers_normalized", id="normalized-conflict"
        ),
        pytest.param("ACL", True, None, id="unrelated-root-conflict"),
    ),
)
def test_concurrent_candidate_writes_keep_identifier_conflicts_scoped_and_atomic(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
    second_identifier_type: str,
    shared_root: bool,
    expected_constraint: str | None,
) -> None:
    _ingest(postgres_repository, topic_config, (arxiv_record_v1,))
    first = _stub(1, () if shared_root else (("ACL", "2026.concurrent.1"),))
    second = (
        replace(first, title="A concurrent refresh of the same paper")
        if shared_root
        else _stub(2, ((second_identifier_type, "2026.concurrent.1"),))
    )
    papers = (first, second)
    sessions = tuple(
        _search_session(
            UUID(int=1000 + index),
            topic_id=topic_config.id,
            source_paper_id=stable_paper_id(arxiv_record_v1.canonical_arxiv_id),
            source_paper_version_id=stable_paper_version_id(arxiv_record_v1.canonical_arxiv_id, 1),
            started_at=NOW,
            objective=f"Concurrent identity persistence from independent search {index}.",
        )
        for index in range(2)
    )
    for search_session in sessions:
        postgres_repository.start_search_session(search_session)
    before_insert = Barrier(2, timeout=10)
    insert_table = "external_paper_stubs" if shared_root else "external_paper_identifiers"

    def synchronize_insert(
        _connection: Connection,
        _cursor: DBAPICursor,
        statement: str,
        _parameters: object,
        _context: ExecutionContext,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().lower().startswith(f"insert into {insert_table} "):
            before_insert.wait()

    def persist_candidate(index: int) -> RepositoryIntegrityError | None:
        search_session = sessions[index]
        paper = papers[index]
        candidate, discovery = _pending_local_candidate(search_session.id, paper, created_at=NOW)
        try:
            postgres_repository.persist_local_search_candidates(
                search_session.id,
                papers=(paper,),
                candidates=(candidate,),
                discoveries=(discovery,),
            )
        except RepositoryIntegrityError as error:
            return error
        return None

    event.listen(postgres_engine, "before_cursor_execute", synchronize_insert)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(persist_candidate, index) for index in range(2)]
            outcomes = tuple(future.result(timeout=20) for future in futures)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", synchronize_insert)

    failures = [error for error in outcomes if error is not None]
    assert len(failures) == 1
    failure = failures[0]
    sql_error = failure.__cause__
    assert isinstance(sql_error, IntegrityError)
    assert isinstance(sql_error.orig, UniqueViolation)
    if expected_constraint is None:
        assert type(failure) is RepositoryIntegrityError
        assert sql_error.orig.diag.constraint_name not in {
            "uq_external_paper_identifiers_external",
            "uq_external_paper_identifiers_normalized",
        }
    else:
        assert isinstance(failure, ExternalPaperIdentifierConflictError)
        assert sql_error.orig.diag.constraint_name == expected_constraint

    winner = next(index for index, outcome in enumerate(outcomes) if outcome is None)
    for index, search_session in enumerate(sessions):
        detail = postgres_repository.get_search_session(search_session.id)
        assert detail is not None
        assert len(detail.candidates) == len(detail.discoveries) == (1 if index == winner else 0)
        if index == winner:
            assert detail.candidates[0].external_paper_id == papers[winner].id
            assert detail.discoveries[0].candidate_id == detail.candidates[0].id
    with postgres_engine.connect() as connection:
        assert set(connection.scalars(select(ExternalPaperStubRow.id))) == {papers[winner].id}
        assert set(connection.scalars(select(ExternalPaperIdentifierRow.external_paper_id))) == (
            set() if shared_root else {papers[winner].id}
        )
