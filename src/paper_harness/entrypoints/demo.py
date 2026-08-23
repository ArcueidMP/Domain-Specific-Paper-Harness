"""Explicit operator entrypoints for the isolated public-demo data snapshot."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from paper_harness.adapters.postgres.database import (
    create_postgres_engine,
    normalize_database_schema,
    normalize_database_url,
)
from paper_harness.adapters.postgres.demo_schema import (
    DEMO_SCHEMA,
    DEMO_SYNC_ROLE,
    DemoSchemaBootstrapResult,
    bootstrap_demo_schema,
)
from paper_harness.adapters.postgres.demo_snapshot import (
    DemoSnapshotResult,
    DemoSnapshotSynchronizer,
    default_demo_snapshot_manifest,
)


class DemoOperationError(RuntimeError):
    """An explicit demo bootstrap or snapshot operation could not complete."""


def execute_demo_schema_bootstrap() -> DemoSchemaBootstrapResult:
    """Bootstrap the demo schema using owner credentials from the environment."""

    owner_url = _required_environment("DATABASE_URL")
    sync_password = _required_environment("DEMO_SYNC_DB_PASSWORD", preserve_whitespace=True)
    read_password = _required_environment("DEMO_READ_DB_PASSWORD", preserve_whitespace=True)
    manifest = default_demo_snapshot_manifest()
    source_columns = {table.name: table.source_columns for table in manifest.tables}
    owner_engine = create_postgres_engine(owner_url, database_schema="public")
    sync_url = (
        make_url(normalize_database_url(owner_url))
        .set(username=DEMO_SYNC_ROLE, password=sync_password)
        .render_as_string(hide_password=False)
    )
    sync_engine = create_postgres_engine(sync_url, database_schema=DEMO_SCHEMA)

    def migrate() -> None:
        with _temporary_environment(DATABASE_URL=sync_url, DATABASE_SCHEMA=DEMO_SCHEMA):
            command.upgrade(_alembic_config(), "head")

    try:
        return bootstrap_demo_schema(
            owner_engine,
            sync_engine,
            sync_password=sync_password,
            read_password=read_password,
            migrate=migrate,
            source_columns=source_columns,
        )
    except (CommandError, SQLAlchemyError) as error:
        raise DemoOperationError(
            f"demo schema bootstrap failed at {type(error).__name__}"
        ) from None
    finally:
        owner_engine.dispose()
        sync_engine.dispose()


def execute_demo_snapshot_sync() -> DemoSnapshotResult:
    """Atomically replace demo data using the restricted synchronization role."""

    database_url = _required_environment("DATABASE_URL")
    database_schema = normalize_database_schema()
    if database_schema != DEMO_SCHEMA:
        raise DemoOperationError("sync-demo-schema requires DATABASE_SCHEMA=demo")
    engine = create_postgres_engine(database_url, database_schema=database_schema)
    try:
        with engine.connect() as connection:
            current_user = connection.exec_driver_sql("SELECT current_user").scalar_one()
        if current_user != DEMO_SYNC_ROLE:
            raise DemoOperationError(
                f"sync-demo-schema requires the restricted role {DEMO_SYNC_ROLE}"
            )
        return DemoSnapshotSynchronizer(engine).synchronize()
    except SQLAlchemyError as error:
        raise DemoOperationError(
            f"demo snapshot synchronization failed at {type(error).__name__}"
        ) from None
    finally:
        engine.dispose()


def _required_environment(name: str, *, preserve_whitespace: bool = False) -> str:
    raw_value = os.environ.get(name)
    value = raw_value if preserve_whitespace else ("" if raw_value is None else raw_value.strip())
    if raw_value is None or not value:
        raise DemoOperationError(f"{name} is required")
    return value


def _alembic_config() -> Config:
    repository_root = Path(__file__).resolve().parents[3]
    return Config(str(repository_root / "alembic.ini"))


@contextmanager
def _temporary_environment(**values: str) -> Generator[None]:
    previous = {name: os.environ.get(name) for name in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


__all__ = [
    "DemoOperationError",
    "execute_demo_schema_bootstrap",
    "execute_demo_snapshot_sync",
]
