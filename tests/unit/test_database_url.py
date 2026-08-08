from __future__ import annotations

import pytest

from paper_harness.adapters.postgres.database import normalize_database_url


@pytest.mark.parametrize(
    "value",
    [
        "postgres://user:pass@host/db",
        "postgresql://user:pass@host/db",
        "postgresql+psycopg://user:pass@host/db",
    ],
)
def test_postgresql_urls_resolve_to_psycopg_three(value: str) -> None:
    assert normalize_database_url(value).startswith("postgresql+psycopg://")


def test_non_postgresql_database_is_rejected() -> None:
    with pytest.raises(ValueError, match="PostgreSQL"):
        normalize_database_url("sqlite:///paper-harness.db")
