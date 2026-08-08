"""Bounded arxiv.py implementation of the discovery port."""

from __future__ import annotations

import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import arxiv  # pyright: ignore[reportMissingTypeStubs]
import requests

from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import parse_arxiv_identifier
from paper_harness.ports.arxiv import (
    ArxivPaperRecord,
    ArxivResponseError,
    ArxivResultLimitError,
    ArxivUnavailableError,
)

_RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503})


class ArxivClient:
    """Discover complete arXiv windows with bounded transport behavior."""

    def __init__(
        self,
        *,
        page_size: int = 100,
        max_pages: int = 50,
        delay_seconds: float = 3.0,
        max_retries: int = 2,
        request_timeout_seconds: float = 20.0,
        retry_backoff_seconds: float = 1.0,
        max_retry_after_seconds: float = 30.0,
        max_total_seconds: float = 90.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= page_size <= 2000:
            raise ValueError("arXiv page_size must be between 1 and 2000")
        if not 1 <= max_pages <= 100:
            raise ValueError("arXiv max_pages must be between 1 and 100")
        if not 0 <= delay_seconds <= 30:
            raise ValueError("arXiv delay_seconds must be between 0 and 30")
        if not 0 <= max_retries <= 3:
            raise ValueError("arXiv max_retries must be between 0 and 3")
        if not 1 <= request_timeout_seconds <= 60:
            raise ValueError("arXiv request timeout must be between 1 and 60 seconds")
        if not 0 <= retry_backoff_seconds <= 10:
            raise ValueError("arXiv retry backoff must be between 0 and 10 seconds")
        if not 1 <= max_retry_after_seconds <= 60:
            raise ValueError("arXiv Retry-After bound must be between 1 and 60 seconds")
        if not 1 <= max_total_seconds <= 300:
            raise ValueError("arXiv total-operation bound must be between 1 and 300 seconds")

        # arxiv.py owns query encoding, pagination, Atom parsing, and pacing. Its
        # broad built-in retry loop is disabled so only the explicit policy below
        # retries timeouts and the approved transient HTTP statuses.
        self._client = ValidatedArxivClient(
            page_size=page_size,
            delay_seconds=delay_seconds,
            num_retries=0,
            max_pages=max_pages,
        )
        self._session = BoundedArxivSession(
            request_timeout_seconds=request_timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            max_retry_after_seconds=max_retry_after_seconds,
            max_total_seconds=max_total_seconds,
            sleep=sleep,
            monotonic=monotonic,
        )
        self._client._session = self._session  # pyright: ignore[reportPrivateUsage]

    def search(
        self,
        *,
        query: str,
        updated_from: datetime,
        updated_until: datetime,
        max_results: int,
    ) -> tuple[ArxivPaperRecord, ...]:
        if updated_from.tzinfo is None or updated_until.tzinfo is None:
            raise ValueError("arXiv discovery window must be timezone-aware")
        if updated_from > updated_until:
            raise ValueError("arXiv discovery start cannot follow its end")
        if not 1 <= max_results <= 5000:
            raise ValueError("arXiv max_results must be between 1 and 5000")

        # The upstream result cap is deliberately disabled because it counts rows
        # before this adapter applies its time window. The adapter stops only after
        # observing max_results + 1 in-window rows, an older boundary, or feed
        # exhaustion. Deadline/page bounds raise rather than returning partial data.
        search = arxiv.Search(
            query=query,
            max_results=None,
            sort_by=arxiv.SortCriterion.LastUpdatedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        records: list[ArxivPaperRecord] = []
        with self._session.operation(), self._client.page_operation():
            try:
                for result in self._client.results(search):
                    updated_at = _as_utc(result.updated)
                    if updated_at > updated_until:
                        continue
                    if updated_at < updated_from:
                        break
                    if len(records) == max_results:
                        raise ArxivResultLimitError(
                            "arXiv result window reached max_results before exhaustion; "
                            "cursor was not advanced"
                        )
                    records.append(map_arxiv_result(result))
            except ArxivResultLimitError:
                raise
            except arxiv.HTTPError as error:
                if error.status in _RETRYABLE_HTTP_STATUSES:
                    raise ArxivUnavailableError(
                        f"arXiv transient HTTP {error.status} exhausted bounded retries"
                    ) from error
                raise ArxivResponseError(
                    f"arXiv rejected the request with HTTP {error.status}"
                ) from error
            except arxiv.UnexpectedEmptyPageError as error:
                raise ArxivUnavailableError("arXiv returned an unexpected empty page") from error
            except requests.exceptions.RequestException as error:
                raise ArxivUnavailableError(
                    f"bounded arXiv transport failed with {type(error).__name__}"
                ) from error
        return tuple(records)


class ValidatedArxivClient(arxiv.Client):
    """Narrow arxiv.py hook for page bounds and strict ParsedFeed validation."""

    def __init__(
        self,
        *,
        page_size: int,
        delay_seconds: float,
        num_retries: int,
        max_pages: int,
    ) -> None:
        super().__init__(
            page_size=page_size,
            delay_seconds=delay_seconds,
            num_retries=num_retries,
        )
        self._max_pages = max_pages
        self._page_count: int | None = None

    @contextmanager
    def page_operation(self) -> Generator[None]:
        if self._page_count is not None:
            raise RuntimeError("arXiv page operation is already active")
        self._page_count = 0
        try:
            yield
        finally:
            self._page_count = None

    def _parse_feed(self, url: str, first_page: bool = True, _try_index: int = 0) -> Any:
        if self._page_count is None:
            raise RuntimeError("arXiv feed parsing requires an active page operation")
        if self._page_count >= self._max_pages:
            raise ArxivResultLimitError(
                "arXiv page bound was reached before window exhaustion; cursor was not advanced"
            )
        self._page_count += 1
        feed = super()._parse_feed(url, first_page=first_page, _try_index=_try_index)
        if feed.malformed:
            raise ArxivResponseError("arXiv returned a malformed Atom feed")
        return feed


class BoundedArxivSession(requests.Session):
    """requests session with the repository's narrow transient retry policy."""

    def __init__(
        self,
        *,
        request_timeout_seconds: float,
        max_retries: int,
        retry_backoff_seconds: float,
        max_retry_after_seconds: float,
        max_total_seconds: float,
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
    ) -> None:
        super().__init__()
        self._request_timeout_seconds = request_timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._max_retry_after_seconds = max_retry_after_seconds
        self._max_total_seconds = max_total_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._deadline: float | None = None

    @contextmanager
    def operation(self) -> Generator[None]:
        if self._deadline is not None:
            raise RuntimeError("arXiv session operation is already active")
        self._deadline = self._monotonic() + self._max_total_seconds
        try:
            yield
        finally:
            self._deadline = None

    def get(self, url: str, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        for attempt in range(self._max_retries + 1):
            kwargs["timeout"] = min(self._request_timeout_seconds, self._remaining_seconds())
            try:
                response = super().get(url, **kwargs)
            except requests.exceptions.Timeout:
                if attempt == self._max_retries:
                    raise
                self._bounded_sleep(self._retry_backoff_seconds * (2**attempt))
                continue

            if response.status_code not in _RETRYABLE_HTTP_STATUSES:
                return response
            if attempt == self._max_retries:
                return response
            delay = _retry_delay_seconds(
                response,
                attempt=attempt,
                backoff_seconds=self._retry_backoff_seconds,
                max_retry_after_seconds=self._max_retry_after_seconds,
            )
            if delay is None:
                return response
            response.close()
            self._bounded_sleep(delay)
        raise AssertionError("bounded retry loop must return or raise")

    def _remaining_seconds(self) -> float:
        if self._deadline is None:
            raise RuntimeError("arXiv session requests require an active operation")
        remaining = self._deadline - self._monotonic()
        if remaining <= 0:
            raise requests.exceptions.Timeout("arXiv total-operation deadline exceeded")
        return remaining

    def _bounded_sleep(self, delay: float) -> None:
        if delay > self._remaining_seconds():
            raise requests.exceptions.Timeout("arXiv retry would exceed total-operation deadline")
        self._sleep(delay)


def _retry_delay_seconds(
    response: requests.Response,
    *,
    attempt: int,
    backoff_seconds: float,
    max_retry_after_seconds: float,
) -> float | None:
    retry_after = response.headers.get("Retry-After")
    if retry_after is None:
        return min(backoff_seconds * (2**attempt), max_retry_after_seconds)
    try:
        delay = float(retry_after)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
        except (TypeError, ValueError, OverflowError):
            return min(backoff_seconds * (2**attempt), max_retry_after_seconds)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        delay = max(0.0, (retry_at.astimezone(UTC) - datetime.now(UTC)).total_seconds())
    if delay > max_retry_after_seconds:
        # Do not retry earlier than the server requested; fail this bounded call.
        return None
    return max(0.0, delay)


def map_arxiv_result(result: Any) -> ArxivPaperRecord:
    try:
        canonical_id, version = parse_arxiv_identifier(result.get_short_id())
        authors = tuple(_normalize_text(author.name) for author in result.authors)
        categories = tuple(str(category) for category in result.categories)
        pdf_url = str(result.pdf_url or "")
        source_url = str(result.entry_id or "")
        return ArxivPaperRecord(
            canonical_arxiv_id=canonical_id,
            version=version,
            title=_normalize_text(result.title),
            abstract=_normalize_text(result.summary),
            submitted_at=_as_utc(result.published),
            updated_at=_as_utc(result.updated),
            primary_category=str(result.primary_category),
            categories=categories,
            authors=authors,
            pdf_url=pdf_url,
            source_url=source_url,
        )
    except (AttributeError, TypeError, ValueError, DomainInvariantError) as error:
        raise ArxivResponseError(f"arXiv returned invalid paper metadata: {error}") from error


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ArxivResponseError("arXiv returned a timestamp without a time zone")
    return value.astimezone(UTC)
