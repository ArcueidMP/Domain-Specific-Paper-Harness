"""Deadline, failure, and session-ownership boundaries of the arXiv request gate."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest
from psycopg.errors import LockNotAvailable, QueryCanceled
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from paper_harness.adapters.postgres.arxiv_request_gate import PostgresArxivRequestGate
from paper_harness.ports.arxiv import ArxivUnavailableError
from paper_harness.ports.repository import RepositoryUnavailableError


class VirtualClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.oversleep = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds + self.oversleep


@pytest.fixture
def gate_harness() -> tuple[PostgresArxivRequestGate, MagicMock, MagicMock, VirtualClock]:
    engine = MagicMock(spec=Engine)
    connection = MagicMock(spec=Connection)
    connection.invalidated = False
    connection.__enter__.return_value = connection
    connection.execute.return_value.scalar_one.return_value = True
    engine.connect.return_value = connection
    clock = VirtualClock()
    gate = PostgresArxivRequestGate(
        cast(Engine, engine), sleep=clock.sleep, monotonic=clock.monotonic
    )
    return gate, engine, connection, clock


def _database_error(cause: Exception | None = None) -> OperationalError:
    return OperationalError(
        "SELECT private_statement",
        {"password": "must-not-escape"},
        cause or RuntimeError("offline"),
    )


def _statements(connection: MagicMock) -> tuple[str, ...]:
    return tuple(str(call.args[0]) for call in connection.execute.call_args_list)


def test_gate_ends_acquisition_transaction_and_waits_before_allowing_request(
    gate_harness: tuple[PostgresArxivRequestGate, MagicMock, MagicMock, VirtualClock],
) -> None:
    gate, engine, connection, clock = gate_harness
    with gate(20):
        assert clock.now == 103.0
        assert clock.sleeps == [3.0]
        connection.commit.assert_called_once()
        assert not any("pg_advisory_unlock" in statement for statement in _statements(connection))
    engine.connect.assert_called_once()
    assert connection.commit.call_count == 2
    assert connection.execute.call_args_list[0].args[1] == {"timeout": "20000ms"}
    assert "pg_advisory_lock(:namespace, :resource)" in _statements(connection)[1]
    assert "pg_advisory_unlock(:namespace, :resource)" in _statements(connection)[2]
    assert (
        connection.execute.call_args_list[1].args[1]
        == (connection.execute.call_args_list[2].args[1])
    )
    connection.invalidate.assert_not_called()


def test_gate_releases_after_caller_failure_without_replacing_it(
    gate_harness: tuple[PostgresArxivRequestGate, MagicMock, MagicMock, VirtualClock],
) -> None:
    gate, _engine, connection, _clock = gate_harness
    failure = RuntimeError("stream failed")
    with pytest.raises(RuntimeError) as caught, gate(20):
        raise failure
    assert caught.value is failure
    assert "pg_advisory_unlock" in _statements(connection)[-1]
    connection.invalidate.assert_not_called()


@pytest.mark.parametrize("budget", (0, -1, float("inf"), float("nan")))
def test_invalid_remaining_budget_never_acquires_a_connection(
    gate_harness: tuple[PostgresArxivRequestGate, MagicMock, MagicMock, VirtualClock],
    budget: float,
) -> None:
    gate, engine, _connection, _clock = gate_harness
    with pytest.raises(ArxivUnavailableError), gate(budget):
        pytest.fail("request must not start")
    engine.connect.assert_not_called()


def test_expired_checkout_budget_never_attempts_the_lock(
    gate_harness: tuple[PostgresArxivRequestGate, MagicMock, MagicMock, VirtualClock],
) -> None:
    gate, engine, connection, clock = gate_harness

    def checkout() -> MagicMock:
        clock.now += 10
        return connection

    engine.connect.side_effect = checkout
    with pytest.raises(ArxivUnavailableError, match="deadline"), gate(5):
        pytest.fail("request must not start")
    connection.execute.assert_not_called()
    connection.__exit__.assert_called_once()


@pytest.mark.parametrize("pool_timeout", (False, True))
def test_checkout_failures_are_typed_without_exposing_database_details(
    gate_harness: tuple[PostgresArxivRequestGate, MagicMock, MagicMock, VirtualClock],
    pool_timeout: bool,
) -> None:
    gate, engine, _connection, _clock = gate_harness
    engine.connect.side_effect = (
        SQLAlchemyTimeoutError("private pool detail") if pool_timeout else _database_error()
    )
    expected_error = ArxivUnavailableError if pool_timeout else RepositoryUnavailableError
    with pytest.raises(expected_error) as caught, gate(20):
        pytest.fail("request must not start")
    assert "private" not in str(caught.value)
    assert "must-not-escape" not in str(caught.value)


@pytest.mark.parametrize("timeout", (QueryCanceled, LockNotAvailable))
def test_uncertain_timed_out_acquisition_discards_the_session(
    gate_harness: tuple[PostgresArxivRequestGate, MagicMock, MagicMock, VirtualClock],
    timeout: type[Exception],
) -> None:
    gate, _engine, connection, clock = gate_harness
    connection.execute.side_effect = [MagicMock(), _database_error(timeout("statement expired"))]
    with pytest.raises(ArxivUnavailableError, match="waiting"), gate(20):
        pytest.fail("request must not start")
    connection.invalidate.assert_called_once()
    assert clock.sleeps == []


def test_interruption_during_acquisition_discards_uncertain_session_ownership(
    gate_harness: tuple[PostgresArxivRequestGate, MagicMock, MagicMock, VirtualClock],
) -> None:
    gate, _engine, connection, _clock = gate_harness
    connection.execute.side_effect = [MagicMock(), KeyboardInterrupt()]
    with pytest.raises(KeyboardInterrupt), gate(20):
        pytest.fail("request must not start")
    connection.invalidate.assert_called_once()


def test_acquisition_database_outage_fails_closed_and_discards_session(
    gate_harness: tuple[PostgresArxivRequestGate, MagicMock, MagicMock, VirtualClock],
) -> None:
    gate, _engine, connection, _clock = gate_harness
    connection.execute.side_effect = [MagicMock(), _database_error()]
    with pytest.raises(RepositoryUnavailableError) as caught, gate(20):
        pytest.fail("request must not start")
    assert "must-not-escape" not in str(caught.value)
    connection.invalidate.assert_called_once()


def test_deadline_expiring_during_lock_acquisition_releases_without_sending(
    gate_harness: tuple[PostgresArxivRequestGate, MagicMock, MagicMock, VirtualClock],
) -> None:
    gate, _engine, connection, clock = gate_harness

    def commit() -> None:
        if connection.commit.call_count == 1:
            clock.now += 20

    connection.commit.side_effect = commit
    with pytest.raises(ArxivUnavailableError, match="deadline"), gate(10):
        pytest.fail("request must not start")
    assert clock.sleeps == []
    assert "pg_advisory_unlock" in _statements(connection)[-1]


@pytest.mark.parametrize("budget", (1, 3))
def test_insufficient_pacing_budget_releases_without_waiting_or_sending(
    gate_harness: tuple[PostgresArxivRequestGate, MagicMock, MagicMock, VirtualClock],
    budget: float,
) -> None:
    gate, _engine, connection, clock = gate_harness
    with pytest.raises(ArxivUnavailableError, match="pacing"), gate(budget):
        pytest.fail("request must not start")
    assert clock.sleeps == []
    assert "pg_advisory_unlock" in _statements(connection)[-1]


def test_deadline_is_rechecked_after_pacing_wait(
    gate_harness: tuple[PostgresArxivRequestGate, MagicMock, MagicMock, VirtualClock],
) -> None:
    gate, _engine, connection, clock = gate_harness
    clock.oversleep = 20
    with pytest.raises(ArxivUnavailableError, match="deadline"), gate(10):
        pytest.fail("request must not start")
    assert clock.sleeps == [3.0]
    assert "pg_advisory_unlock" in _statements(connection)[-1]


@pytest.mark.parametrize("release_error", (False, True))
def test_uncertain_release_never_returns_the_session_to_the_pool(
    gate_harness: tuple[PostgresArxivRequestGate, MagicMock, MagicMock, VirtualClock],
    release_error: bool,
) -> None:
    gate, _engine, connection, _clock = gate_harness
    with pytest.raises(RepositoryUnavailableError), gate(20):
        if release_error:
            connection.execute.side_effect = _database_error()
        else:
            connection.execute.return_value.scalar_one.return_value = False
    connection.invalidate.assert_called_once()


def test_acquisition_commit_failure_discards_the_locked_session(
    gate_harness: tuple[PostgresArxivRequestGate, MagicMock, MagicMock, VirtualClock],
) -> None:
    gate, _engine, connection, _clock = gate_harness
    connection.commit.side_effect = _database_error()
    with pytest.raises(RepositoryUnavailableError), gate(20):
        pytest.fail("request must not start")
    connection.invalidate.assert_called_once()
