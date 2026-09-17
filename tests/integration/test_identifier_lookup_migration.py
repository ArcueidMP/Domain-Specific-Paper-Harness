"""Verify lossless normalized identifier migration in isolated PostgreSQL schemas."""

from __future__ import annotations

import os
from collections.abc import Generator
from io import StringIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, Text, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from paper_harness.adapters.postgres import PostgresRepository
from paper_harness.adapters.postgres.database import create_postgres_engine
from paper_harness.adapters.postgres.repository import EXPECTED_DATABASE_REVISION
from paper_harness.ports.repository import MigrationIncompatibleError

pytestmark = pytest.mark.integration

_PREVIOUS_REVISION = "0008_enrichment_failures"
_LOOKUP_REVISION = "0009_identifier_lookup"


@pytest.fixture
def identifier_migration(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[Config, Engine]]:
    database_schema = f"identifier_migration_{uuid4().hex}"
    with postgres_engine.begin() as connection:
        connection.execute(CreateSchema(database_schema))
    engine = create_postgres_engine(os.environ["DATABASE_URL"], database_schema=database_schema)
    try:
        monkeypatch.setenv("DATABASE_SCHEMA", database_schema)
        yield Config(str(Path("alembic.ini").resolve())), engine
    finally:
        engine.dispose()
        with postgres_engine.begin() as connection:
            connection.execute(DropSchema(database_schema, cascade=True))


def _seed_identifiers(engine: Engine, rows: tuple[tuple[UUID, str, str], ...]) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO external_paper_stubs "
                "(id, semantic_scholar_id, title, authors, citation_count, "
                "influential_citation_count, full_text_available, source, schema_version, "
                "created_at, updated_at) VALUES "
                "(:id, :semantic_scholar_id, 'Identifier migration fixture', ARRAY[]::text[], "
                "0, 0, false, 'semantic_scholar', 1, now(), now())"
            ),
            [
                {"id": paper_id, "semantic_scholar_id": f"migration-{paper_id.hex}"}
                for paper_id in sorted({paper_id for paper_id, _, _ in rows})
            ],
        )
        connection.execute(
            text(
                "INSERT INTO external_paper_identifiers "
                "(external_paper_id, identifier_type, identifier_value) "
                "VALUES (:paper_id, :identifier_type, :identifier_value)"
            ),
            [
                {
                    "paper_id": paper_id,
                    "identifier_type": identifier_type,
                    "identifier_value": identifier_value,
                }
                for paper_id, identifier_type, identifier_value in rows
            ],
        )


def _source_rows(engine: Engine) -> tuple[tuple[object, ...], ...]:
    with engine.connect() as connection:
        return tuple(
            tuple(row)
            for row in connection.execute(
                text(
                    "SELECT external_paper_id, identifier_type, identifier_value "
                    "FROM external_paper_identifiers ORDER BY external_paper_id, identifier_type"
                )
            )
        )


def _assert_derived_values(engine: Engine, rows: tuple[tuple[UUID, str, str], ...]) -> None:
    with engine.connect() as connection:
        actual = {
            (paper_id, identifier_type): (identifier_value, normalized_type, normalized_value)
            for paper_id, identifier_type, identifier_value, normalized_type, normalized_value in (
                connection.execute(
                    text(
                        "SELECT external_paper_id, identifier_type, identifier_value, "
                        "normalized_type, normalized_value FROM external_paper_identifiers"
                    )
                )
            )
        }
    expected = {
        (paper_id, identifier_type): (
            identifier_value,
            identifier_type.casefold(),
            identifier_value.casefold()
            if identifier_type.casefold() in {"arxiv", "doi"}
            else identifier_value,
        )
        for paper_id, identifier_type, identifier_value in rows
    }
    assert actual == expected


def test_identifier_lookup_clean_upgrade_matches_schema_and_readiness(
    identifier_migration: tuple[Config, Engine],
) -> None:
    config, engine = identifier_migration
    command.upgrade(config, "head")
    command.check(config)
    PostgresRepository(engine).check_ready()

    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("external_paper_identifiers")
    }
    for name in ("normalized_type", "normalized_value"):
        assert isinstance(columns[name]["type"], Text)
        assert columns[name]["nullable"] is False
    constraints = {
        constraint["name"]: constraint["column_names"]
        for constraint in inspect(engine).get_unique_constraints("external_paper_identifiers")
    }
    assert constraints["uq_external_paper_identifiers_external"] == [
        "identifier_type",
        "identifier_value",
    ]
    assert constraints["uq_external_paper_identifiers_normalized"] == [
        "normalized_type",
        "normalized_value",
    ]
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            EXPECTED_DATABASE_REVISION
        )
        assert connection.scalar(text("SELECT count(*) FROM external_paper_identifiers")) == 0


def test_populated_identifier_upgrade_casefolds_in_batches_and_downgrade_preserves_sources(
    identifier_migration: tuple[Config, Engine],
) -> None:
    config, engine = identifier_migration
    command.upgrade(config, _PREVIOUS_REVISION)
    rows = (
        (UUID(int=1), "DOI", "10.1234/ΣςStraße"),
        (UUID(int=1), "ArXiv", "HeP-TH/9901001"),
        (UUID(int=1), "Straße", "CaseSensitiveΣςStraße"),
        (UUID(int=2), "DOI", "10.1/" + "ß" * 507),
        (UUID(int=2), "ß" * 40, "ExactExternalValue"),
        (UUID(int=3), "Custom", "CaseSensitive"),
        (UUID(int=4), "custom", "casesensitive"),
        *(
            (UUID(int=5 + index // 2), f"External{index:04}", f"Value{index:04}")
            for index in range(501)
        ),
    )
    _seed_identifiers(engine, rows)
    baseline = _source_rows(engine)
    with engine.connect() as connection:
        baseline_stubs = tuple(
            connection.scalars(
                text("SELECT row_to_json(t) FROM external_paper_stubs t ORDER BY id")
            )
        )
    with pytest.raises(MigrationIncompatibleError):
        PostgresRepository(engine).check_ready()

    command.upgrade(config, _LOOKUP_REVISION)
    PostgresRepository(engine).check_ready()
    assert _source_rows(engine) == baseline
    _assert_derived_values(engine, rows)

    with engine.begin() as connection:
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO external_paper_identifiers "
                    "(external_paper_id, identifier_type, identifier_value, "
                    "normalized_type, normalized_value) VALUES "
                    "(:id, 'doi', '10.1234/ΣΣSTRASSE', 'doi', :normalized_value)"
                ),
                {"id": UUID(int=2), "normalized_value": "10.1234/ΣςStraße".casefold()},
            )
        for column in ("normalized_type", "normalized_value"):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        f"UPDATE external_paper_identifiers SET {column} = NULL "
                        "WHERE external_paper_id = :id AND identifier_type = 'DOI'"
                    ),
                    {"id": UUID(int=1)},
                )

    command.downgrade(config, _PREVIOUS_REVISION)
    assert _source_rows(engine) == baseline
    assert {
        column["name"] for column in inspect(engine).get_columns("external_paper_identifiers")
    } == {"external_paper_id", "identifier_type", "identifier_value"}
    with engine.connect() as connection:
        assert (
            tuple(
                connection.scalars(
                    text("SELECT row_to_json(t) FROM external_paper_stubs t ORDER BY id")
                )
            )
            == baseline_stubs
        )
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            _PREVIOUS_REVISION
        )

    command.upgrade(config, _LOOKUP_REVISION)
    _assert_derived_values(engine, rows)
    PostgresRepository(engine).check_ready()


@pytest.mark.parametrize("same_owner", [False, True])
def test_identifier_collision_rolls_back_derived_columns_and_preserves_source_identifiers(
    identifier_migration: tuple[Config, Engine],
    same_owner: bool,
) -> None:
    config, engine = identifier_migration
    command.upgrade(config, _PREVIOUS_REVISION)
    _seed_identifiers(
        engine,
        (
            (UUID(int=1), "DOI", "10.1234/Straße"),
            (UUID(int=1 if same_owner else 2), "doi", "10.1234/STRASSE"),
        ),
    )
    baseline = _source_rows(engine)
    with pytest.raises(RuntimeError, match="normalized identifier collisions"):
        command.upgrade(config, _LOOKUP_REVISION)
    assert _source_rows(engine) == baseline
    assert {
        column["name"] for column in inspect(engine).get_columns("external_paper_identifiers")
    } == {"external_paper_id", "identifier_type", "identifier_value"}
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            _PREVIOUS_REVISION
        )
    with pytest.raises(MigrationIncompatibleError):
        PostgresRepository(engine).check_ready()


def test_identifier_lookup_offline_upgrade_refuses_unavailable_python_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost/offline_migration")
    monkeypatch.setenv("DATABASE_SCHEMA", "public")
    monkeypatch.setenv("APP_ENV", "test")
    output = StringIO()
    config = Config(str(Path("alembic.ini").resolve()), output_buffer=output)
    with pytest.raises(RuntimeError, match="online database connection.*Python casefold"):
        command.upgrade(config, f"{_PREVIOUS_REVISION}:{_LOOKUP_REVISION}", sql=True)
    assert "ALTER TABLE external_paper_identifiers" not in output.getvalue()
