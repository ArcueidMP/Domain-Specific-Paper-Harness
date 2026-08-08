"""Shared bounded retry execution for approved transient HTTP failures."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503})


@dataclass(frozen=True, slots=True)
class HttpRetryPolicy:
    max_retries: int = 2
    request_timeout_seconds: float = 60.0
    total_timeout_seconds: float = 180.0
    backoff_seconds: float = 1.0
    max_retry_after_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not 0 <= self.max_retries <= 3:
            raise ValueError("max_retries must be between 0 and 3")
        if not 1 <= self.request_timeout_seconds <= 600:
            raise ValueError("request timeout must be between 1 and 600 seconds")
        if not self.request_timeout_seconds <= self.total_timeout_seconds <= 1800:
            raise ValueError("total timeout must be bounded and at least the request timeout")
        if not 0 <= self.backoff_seconds <= 30:
            raise ValueError("retry backoff must be between 0 and 30 seconds")
        if not 1 <= self.max_retry_after_seconds <= 300:
            raise ValueError("Retry-After bound must be between 1 and 300 seconds")


def send_with_retry(
    send: Callable[[float], httpx.Response],
    *,
    policy: HttpRetryPolicy,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> httpx.Response:
    """Repeat only one identical request for timeout and approved transient statuses."""

    deadline = monotonic() + policy.total_timeout_seconds
    for attempt in range(policy.max_retries + 1):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise httpx.TimeoutException("HTTP operation exceeded its total timeout")
        try:
            response = send(min(policy.request_timeout_seconds, remaining))
        except httpx.TimeoutException:
            if attempt == policy.max_retries:
                raise
            _bounded_sleep(
                min(policy.backoff_seconds * (2**attempt), policy.max_retry_after_seconds),
                deadline=deadline,
                sleep=sleep,
                monotonic=monotonic,
            )
            continue

        if deadline - monotonic() <= 0:
            response.close()
            raise httpx.TimeoutException("HTTP operation exceeded its total timeout")

        if response.status_code not in RETRYABLE_HTTP_STATUSES or attempt == policy.max_retries:
            return response
        delay = _retry_delay(
            response,
            attempt=attempt,
            policy=policy,
            now=now,
        )
        if delay is None:
            return response
        response.close()
        _bounded_sleep(delay, deadline=deadline, sleep=sleep, monotonic=monotonic)
    raise AssertionError("bounded retry loop must return or raise")


def _retry_delay(
    response: httpx.Response,
    *,
    attempt: int,
    policy: HttpRetryPolicy,
    now: Callable[[], datetime],
) -> float | None:
    fallback = min(policy.backoff_seconds * (2**attempt), policy.max_retry_after_seconds)
    retry_after = response.headers.get("Retry-After")
    if retry_after is None:
        return fallback
    try:
        delay = float(retry_after)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
        except (TypeError, ValueError, OverflowError):
            return fallback
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        delay = max(0.0, (retry_at.astimezone(UTC) - now().astimezone(UTC)).total_seconds())
    if not math.isfinite(delay) or delay < 0:
        return fallback
    if delay > policy.max_retry_after_seconds:
        return None
    return max(0.0, delay)


def _bounded_sleep(
    delay: float,
    *,
    deadline: float,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> None:
    if delay > deadline - monotonic():
        raise httpx.TimeoutException("HTTP retry would exceed its total timeout")
    sleep(delay)
