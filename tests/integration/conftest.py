"""Opt-in PostgreSQL fixtures; TEST_DATABASE_URL must target a disposable database."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from paper_harness.adapters.postgres import PostgresRepository, create_postgres_engine


@pytest.fixture(scope="session")
def postgres_engine() -> Generator[Engine]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    config = Config(str(Path("alembic.ini").resolve()))
    command.upgrade(config, "head")
    engine = create_postgres_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url


@pytest.fixture
def postgres_repository(postgres_engine: Engine) -> Generator[PostgresRepository]:
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE relation_evidence_links, paper_relations, "
                "comparison_evidence_links, comparison_dimensions, comparisons, "
                "scientific_embeddings, search_candidate_discoveries, search_candidates, "
                "search_actions, search_sessions, historical_corpus_entries, "
                "historical_backfill_runs, external_paper_identifiers, external_paper_stubs, "
                "run_items, daily_runs, ingestion_cursors, topic_papers, "
                "paper_version_authors, paper_source_identities, paper_versions, authors, "
                "papers, topics CASCADE"
            )
        )
    repository = PostgresRepository(postgres_engine)
    yield repository
