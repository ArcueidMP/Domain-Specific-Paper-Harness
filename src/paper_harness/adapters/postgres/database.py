"""PostgreSQL engine construction with bounded, session-affine production use."""

from __future__ import annotations

import os
import re

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

DATABASE_POOL_SIZE = 3
DATABASE_MAX_OVERFLOW = 0
DATABASE_POOL_TIMEOUT_SECONDS = 30

_TLS_REQUIRED_MODES = frozenset({"require", "verify-ca", "verify-full"})
_TRUTHY_QUERY_VALUES = frozenset({"1", "on", "true", "yes"})
_POSTGRES_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*\Z")
_POSTGRES_IDENTIFIER_MAX_LENGTH = 63


def normalize_database_schema(database_schema: str | None = None) -> str:
    """Return a safe unquoted PostgreSQL schema identifier."""

    value = (
        os.environ.get("DATABASE_SCHEMA", "public") if database_schema is None else database_schema
    ).strip()
    if (
        not value
        or len(value) > _POSTGRES_IDENTIFIER_MAX_LENGTH
        or _POSTGRES_IDENTIFIER.fullmatch(value) is None
        or value.startswith("pg_")
        or value == "information_schema"
    ):
        raise ValueError(
            "DATABASE_SCHEMA must be a non-system lowercase PostgreSQL identifier "
            "containing only letters, digits, and underscores"
        )
    return value


def _production_enabled(production: bool | None) -> bool:
    if production is not None:
        return production
    return os.environ.get("APP_ENV", "").strip().lower() == "production"


def _query_value(url: URL, key: str) -> str | None:
    value = url.query.get(key)
    return value if isinstance(value, str) else None


def _validate_production_url(url: URL) -> None:
    hostname = (url.host or "").lower()
    sslmode = (_query_value(url, "sslmode") or "").lower()
    pool_mode = (_query_value(url, "pool_mode") or "").lower()
    pgbouncer = (_query_value(url, "pgbouncer") or "").lower()

    if not hostname:
        raise ValueError("Production DATABASE_URL must include a database hostname")
    if not url.username or not url.password or not url.database:
        raise ValueError(
            "Production DATABASE_URL must include a database name and explicit "
            "username and password"
        )
    if sslmode not in _TLS_REQUIRED_MODES:
        raise ValueError(
            "Production DATABASE_URL must require TLS with sslmode=require, "
            "verify-ca, or verify-full"
        )
    if (
        url.port == 6543
        or "-pooler." in hostname
        or pool_mode == "transaction"
        or pgbouncer in _TRUTHY_QUERY_VALUES
    ):
        raise ValueError(
            "Production DATABASE_URL must use a direct or session-affine "
            "PostgreSQL endpoint, not transaction pooling"
        )


def normalize_database_url(database_url: str, *, production: bool | None = None) -> str:
    value = database_url.strip()
    if value.startswith("postgres://"):
        value = "postgresql+psycopg://" + value.removeprefix("postgres://")
    elif value.startswith("postgresql://"):
        value = "postgresql+psycopg://" + value.removeprefix("postgresql://")
    elif not value.startswith("postgresql+psycopg://"):
        raise ValueError("DATABASE_URL must use PostgreSQL with the psycopg 3 driver")

    try:
        parsed = make_url(value)
        if parsed.drivername != "postgresql+psycopg":
            raise ValueError("DATABASE_URL must use PostgreSQL with the psycopg 3 driver")
        if _production_enabled(production):
            _validate_production_url(parsed)
    except (ArgumentError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith(
            ("DATABASE_URL", "Production DATABASE_URL")
        ):
            raise
        raise ValueError("DATABASE_URL is not a valid PostgreSQL connection URL") from None
    return value


def create_postgres_engine(
    database_url: str,
    *,
    production: bool | None = None,
    database_schema: str | None = None,
) -> Engine:
    schema = normalize_database_schema(database_schema)
    engine_options: dict[str, object] = {
        "pool_pre_ping": True,
        "pool_size": DATABASE_POOL_SIZE,
        "max_overflow": DATABASE_MAX_OVERFLOW,
        "pool_timeout": DATABASE_POOL_TIMEOUT_SECONDS,
        "future": True,
    }
    if schema != "public":
        engine_options["connect_args"] = {"options": f"-csearch_path={schema},pg_catalog"}
    return create_engine(
        normalize_database_url(database_url, production=production),
        **engine_options,
    )
