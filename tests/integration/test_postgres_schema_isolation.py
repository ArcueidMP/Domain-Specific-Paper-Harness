"""Focused migration coverage for isolated PostgreSQL schemas."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, String, column, select, table, text
from sqlalchemy.schema import CreateSchema, DropSchema

from paper_harness.adapters.postgres.database import create_postgres_engine


def test_selected_schema_has_independent_migrations_and_runtime_search_path(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_schema = f"demo_migration_{uuid4().hex}"
    version_table = table(
        "alembic_version",
        column("version_num", String),
        schema=database_schema,
    )
    with postgres_engine.begin() as connection:
        public_revision = connection.execute(
            text("SELECT version_num FROM public.alembic_version")
        ).scalar_one()
        connection.execute(CreateSchema(database_schema))

    demo_engine = None
    try:
        monkeypatch.setenv("DATABASE_SCHEMA", database_schema)
        config = Config(str(Path("alembic.ini").resolve()))
        command.upgrade(config, "head")
        command.check(config)

        demo_engine = create_postgres_engine(
            os.environ["DATABASE_URL"], database_schema=database_schema
        )
        with demo_engine.connect() as connection:
            assert connection.execute(text("SHOW search_path")).scalar_one() == (
                f"{database_schema},pg_catalog"
            )
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

        with postgres_engine.connect() as connection:
            assert connection.execute(select(version_table.c.version_num)).scalar_one() == (
                public_revision
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = :database_schema"
                    ),
                    {"database_schema": database_schema},
                ).scalar_one()
                > 1
            )
            assert (
                connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                ).scalar_one()
                == public_revision
            )
    finally:
        if demo_engine is not None:
            demo_engine.dispose()
        monkeypatch.setenv("DATABASE_SCHEMA", "public")
        with postgres_engine.begin() as connection:
            connection.execute(DropSchema(database_schema, cascade=True))
