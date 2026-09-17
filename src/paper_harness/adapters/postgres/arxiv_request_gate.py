"""Serialize arXiv wire requests across runtimes sharing one PostgreSQL database."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager

from psycopg.errors import LockNotAvailable, QueryCanceled
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from paper_harness.ports.arxiv import ArxivUnavailableError
from paper_harness.ports.repository import RepositoryUnavailableError

# The two-integer advisory namespace is separate from the bigint pipeline/run keys.
_LOCK_NAMESPACE = 0x41525856
_LOCK_RESOURCE = 1
_MIN_REQUEST_GAP_SECONDS = 3.0


class PostgresArxivRequestGate:
    """Hold the third pool connection only for pacing and one complete HTTP response.

    Daily pipelines can already hold two connections for their pipeline and child-run
    locks. Callers must release this context before any repository operation; taking
    another pooled connection inside it would exhaust the configured three-slot pool.
    The pre-send wait follows the previous holder's release, so failed requests also
    leave the required gap without extending their own expired operation deadline.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._sleep = sleep
        self._monotonic = monotonic

    @contextmanager
    def __call__(self, timeout_seconds: float) -> Generator[None]:
        if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
            raise ArxivUnavailableError("arXiv request coordination has no remaining time budget")
        deadline = self._monotonic() + timeout_seconds
        try:
            connection = self._engine.connect()
        except SQLAlchemyTimeoutError as error:
            raise ArxivUnavailableError(
                "arXiv request coordination timed out acquiring a database connection"
            ) from error
        except DBAPIError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL arXiv request coordination is unavailable"
            ) from error

        with connection:
            self._remaining_seconds(deadline)
            self._acquire(connection, deadline)
            try:
                if self._remaining_seconds(deadline) <= _MIN_REQUEST_GAP_SECONDS:
                    raise ArxivUnavailableError(
                        "arXiv request pacing would exhaust the operation deadline"
                    )
                self._sleep(_MIN_REQUEST_GAP_SECONDS)
                self._remaining_seconds(deadline)
                yield
            finally:
                self._release(connection)

    def _acquire(self, connection: Connection, deadline: float) -> None:
        acquisition_started = False
        acquisition_complete = False
        try:
            remaining_ms = max(1, int(self._remaining_seconds(deadline) * 1000))
            connection.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": f"{remaining_ms}ms"},
            )
            self._remaining_seconds(deadline)
            acquisition_started = True
            connection.execute(
                text("SELECT pg_advisory_lock(:namespace, :resource)"),
                {"namespace": _LOCK_NAMESPACE, "resource": _LOCK_RESOURCE},
            )
            # End the transaction and its local timeout before waiting or doing HTTP work.
            connection.commit()
            acquisition_complete = True
        except DBAPIError as error:
            if isinstance(error.orig, (QueryCanceled, LockNotAvailable)):
                raise ArxivUnavailableError(
                    "arXiv request coordination timed out waiting for another request"
                ) from error
            raise RepositoryUnavailableError(
                "PostgreSQL arXiv request coordination is unavailable"
            ) from error
        finally:
            if acquisition_started and not acquisition_complete:
                # A cancelled statement may acquire its session lock before cancellation
                # reaches the server. Never return uncertain ownership to the pool.
                connection.invalidate()

    @staticmethod
    def _release(connection: Connection) -> None:
        released = False
        try:
            if connection.invalidated:
                raise RepositoryUnavailableError(
                    "PostgreSQL arXiv request lock connection was lost"
                )
            unlocked = connection.execute(
                text("SELECT pg_advisory_unlock(:namespace, :resource)"),
                {"namespace": _LOCK_NAMESPACE, "resource": _LOCK_RESOURCE},
            ).scalar_one()
            if unlocked is not True:
                raise RepositoryUnavailableError("PostgreSQL arXiv request lock ownership was lost")
            connection.commit()
            released = True
        except DBAPIError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL arXiv request lock release is unavailable"
            ) from error
        finally:
            if not released:
                connection.invalidate()

    def _remaining_seconds(self, deadline: float) -> float:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise ArxivUnavailableError(
                "arXiv request coordination exhausted the operation deadline"
            )
        return remaining
