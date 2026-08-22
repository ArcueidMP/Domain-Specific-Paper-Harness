"""Unit tests for bounded PostgreSQL engine and production URL policy."""

from __future__ import annotations

from typing import Any

import pytest

from paper_harness.adapters.postgres import database


def test_local_postgres_url_is_normalized_without_production_tls_policy() -> None:
    assert (
        database.normalize_database_url(
            "postgresql://user:password@localhost:5432/paper_harness",
            production=False,
        )
        == "postgresql+psycopg://user:password@localhost:5432/paper_harness"
    )


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://user:secret@db.example.com:5432/app",
        "postgresql+psycopg://user:secret@db.example.com:5432/app?sslmode=prefer",
        "postgresql+psycopg://user:secret@db.example.com:6543/app?sslmode=require",
        "postgresql+psycopg://user:secret@ep-green-pooler.example.neon.tech:5432/app?sslmode=require",
        "postgresql+psycopg://user:secret@db.example.com:5432/app?sslmode=require&pool_mode=transaction",
        "postgresql+psycopg://user:secret@db.example.com:5432/app?sslmode=require&pgbouncer=true",
        "postgresql+psycopg://db.example.com:5432/app?sslmode=require",
        "postgresql+psycopg://user@db.example.com:5432/app?sslmode=require",
        "postgresql+psycopg://user:secret@db.example.com:5432?sslmode=require",
    ],
)
def test_production_rejects_non_tls_or_transaction_pooled_urls(database_url: str) -> None:
    with pytest.raises(ValueError) as raised:
        database.normalize_database_url(database_url, production=True)

    assert database_url not in str(raised.value)
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("sslmode", ["require", "verify-ca", "verify-full"])
def test_production_accepts_direct_or_session_affine_tls_urls(sslmode: str) -> None:
    value = (
        "postgresql+psycopg://user:password@aws-0-ap-southeast-1."
        f"pooler.supabase.com:5432/app?sslmode={sslmode}"
    )

    assert database.normalize_database_url(value, production=True) == value


def test_app_env_enables_production_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(ValueError, match="require TLS"):
        database.normalize_database_url(
            "postgresql+psycopg://user:password@db.example.com:5432/app"
        )


def test_engine_has_small_non_bursting_bounded_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    def create_engine_stub(url: str, **kwargs: Any) -> object:
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(database, "create_engine", create_engine_stub)

    result = database.create_postgres_engine(
        "postgresql+psycopg://user:password@localhost:5432/app",
        production=False,
    )

    assert result is sentinel
    assert captured == {
        "url": "postgresql+psycopg://user:password@localhost:5432/app",
        "pool_pre_ping": True,
        "pool_size": 3,
        "max_overflow": 0,
        "pool_timeout": 30,
        "future": True,
    }


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///paper-harness.db",
        "postgresql+asyncpg://user:password@localhost/app",
        "not-a-url",
    ],
)
def test_non_psycopg_database_urls_are_rejected(database_url: str) -> None:
    with pytest.raises(ValueError, match="PostgreSQL with the psycopg 3 driver"):
        database.normalize_database_url(database_url, production=False)


def test_invalid_production_url_diagnostic_redacts_complete_secret_url() -> None:
    secret_url = (
        "postgresql+psycopg://private-user:very-private-password@"
        "db.example.com:not-a-port/private-db?sslmode=require"
    )

    with pytest.raises(ValueError) as raised:
        database.normalize_database_url(secret_url, production=True)

    diagnostic = str(raised.value)
    assert secret_url not in diagnostic
    assert "private-user" not in diagnostic
    assert "very-private-password" not in diagnostic
    assert "private-db" not in diagnostic
