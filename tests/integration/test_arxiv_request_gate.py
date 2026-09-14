# pyright: reportPrivateUsage=false
"""PostgreSQL coordination across runtimes without additional pool capacity."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.engine.interfaces import DBAPICursor, ExecutionContext
from sqlalchemy.exc import DBAPIError

from paper_harness.adapters.postgres import PostgresRepository, create_postgres_engine
from paper_harness.adapters.postgres import arxiv_request_gate as gate_module
from paper_harness.adapters.postgres.arxiv_request_gate import PostgresArxivRequestGate
from paper_harness.ports.arxiv import ArxivUnavailableError

pytestmark = pytest.mark.integration


def _lock_parameters() -> dict[str, int]:
    return {"namespace": gate_module._LOCK_NAMESPACE, "resource": gate_module._LOCK_RESOURCE}


def _assert_lock_is_free(engine: Engine) -> None:
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT pg_try_advisory_lock(:namespace, :resource)"), _lock_parameters()
            )
            is True
        )
        assert (
            connection.scalar(
                text("SELECT pg_advisory_unlock(:namespace, :resource)"), _lock_parameters()
            )
            is True
        )
        connection.commit()


def test_gate_serializes_separate_runtime_engines_with_a_gap_and_two_existing_run_locks(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
) -> None:
    peer_engine = create_postgres_engine(os.environ["DATABASE_URL"])
    peer_waiting = Event()

    def observe_peer_lock(
        _connection: Connection,
        _cursor: DBAPICursor,
        statement: str,
        _parameters: object,
        _context: ExecutionContext,
        _executemany: bool,
    ) -> None:
        if "SELECT pg_advisory_lock(" in statement:
            peer_waiting.set()

    def peer_request() -> float:
        with PostgresArxivRequestGate(peer_engine)(20):
            return monotonic()

    event.listen(peer_engine, "before_cursor_execute", observe_peer_lock)
    try:
        with (
            postgres_repository.daily_pipeline_lock(uuid4()),
            postgres_repository.daily_run_lock(uuid4(), date(2026, 9, 14)),
        ):
            with postgres_engine.connect() as spare:
                gate_backend_id = spare.scalar(text("SELECT pg_backend_pid()"))
            with ThreadPoolExecutor(max_workers=1) as executor:
                with PostgresArxivRequestGate(postgres_engine)(20):
                    with peer_engine.connect() as observer:
                        assert (
                            observer.scalar(
                                text("SELECT state FROM pg_stat_activity WHERE pid = :pid"),
                                {"pid": gate_backend_id},
                            )
                            == "idle"
                        )
                    peer_future = executor.submit(peer_request)
                    assert peer_waiting.wait(timeout=5)
                    assert not peer_future.done()
                    first_request_finished = monotonic()
                # The first request returned the only spare pool slot before persistence.
                with postgres_engine.connect() as available:
                    assert available.scalar(text("SELECT 1")) == 1
                second_request_started = peer_future.result(timeout=10)
            assert second_request_started - first_request_finished >= 3.0
        _assert_lock_is_free(peer_engine)
    finally:
        event.remove(peer_engine, "before_cursor_execute", observe_peer_lock)
        peer_engine.dispose()


def test_gate_acquisition_timeout_invalidates_the_waiting_session(postgres_engine: Engine) -> None:
    peer_engine = create_postgres_engine(os.environ["DATABASE_URL"])
    invalidations: list[bool] = []

    def record_invalidation(
        _dbapi_connection: object, _connection_record: object, _error: object
    ) -> None:
        invalidations.append(True)

    event.listen(peer_engine, "invalidate", record_invalidation)
    try:
        with postgres_engine.connect() as holder:
            holder.execute(
                text("SELECT pg_advisory_lock(:namespace, :resource)"), _lock_parameters()
            )
            holder.commit()
            try:
                gate = PostgresArxivRequestGate(peer_engine, sleep=lambda _delay: None)
                with pytest.raises(ArxivUnavailableError, match="waiting") as caught, gate(0.05):
                    pytest.fail("a contending request must not start")
                assert isinstance(caught.value.__cause__, DBAPIError)
                assert invalidations == [True]
            finally:
                holder.execute(
                    text("SELECT pg_advisory_unlock(:namespace, :resource)"), _lock_parameters()
                )
                holder.commit()
        _assert_lock_is_free(peer_engine)
    finally:
        event.remove(peer_engine, "invalidate", record_invalidation)
        peer_engine.dispose()


@pytest.mark.parametrize("insufficient_budget", (False, True))
def test_gate_releases_after_pacing_budget_or_caller_failure(
    postgres_engine: Engine,
    insufficient_budget: bool,
) -> None:
    peer_engine = create_postgres_engine(os.environ["DATABASE_URL"])
    gate = PostgresArxivRequestGate(postgres_engine, sleep=lambda _delay: None)
    try:
        if insufficient_budget:
            with pytest.raises(ArxivUnavailableError, match="pacing"), gate(1):
                pytest.fail("request must not start")
        else:
            failure = RuntimeError("provider stream failed")
            with pytest.raises(RuntimeError) as caught, gate(10):
                raise failure
            assert caught.value is failure
        _assert_lock_is_free(peer_engine)
    finally:
        peer_engine.dispose()
