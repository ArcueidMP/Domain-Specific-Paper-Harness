"""Alembic environment for explicit PostgreSQL migrations."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import text

from paper_harness.adapters.postgres.database import (
    create_postgres_engine,
    normalize_database_schema,
    normalize_database_url,
)
from paper_harness.adapters.postgres.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for explicit Alembic operations")
    return normalize_database_url(value)


def _database_schema() -> str:
    return normalize_database_schema()


def _include_object(
    _object: object,
    name: str | None,
    type_: str,
    _reflected: bool,
    _compare_to: object | None,
) -> bool:
    return not (type_ == "table" and name == "alembic_version")


def run_migrations_offline() -> None:
    database_schema = _database_schema()
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_include_object,
        include_schemas=False,
        version_table_schema=database_schema,
    )
    if database_schema != "public":
        context.execute(f"SET search_path TO {database_schema}, public")
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    database_schema = _database_schema()
    engine = create_postgres_engine(_database_url(), database_schema=database_schema)
    with engine.connect() as connection:
        if database_schema != "public":
            connection.execute(text(f"SET search_path TO {database_schema}, public"))
            connection.commit()
        # Alembic treats the selected schema as the unqualified/default schema. This keeps
        # the schema-less ORM metadata and migration operations scoped to one tenant while
        # allowing public extension types such as vector to remain resolvable.
        connection.dialect.default_schema_name = database_schema
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=_include_object,
            include_schemas=False,
            version_table_schema=database_schema,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
