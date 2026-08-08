"""Synchronous PostgreSQL persistence adapter."""

from paper_harness.adapters.postgres.database import create_postgres_engine
from paper_harness.adapters.postgres.repository import PostgresRepository

__all__ = ["PostgresRepository", "create_postgres_engine"]
