"""PostgreSQL engine construction without provider-specific behavior."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine


def normalize_database_url(database_url: str) -> str:
    value = database_url.strip()
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    if value.startswith("postgresql+psycopg://"):
        return value
    raise ValueError("DATABASE_URL must use PostgreSQL with the psycopg 3 driver")


def create_postgres_engine(database_url: str) -> Engine:
    return create_engine(
        normalize_database_url(database_url),
        pool_pre_ping=True,
        future=True,
    )
