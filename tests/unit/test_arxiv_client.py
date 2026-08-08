# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import arxiv
import pytest
import requests

from paper_harness.adapters.arxiv.client import ArxivClient, BoundedArxivSession
from paper_harness.ports.arxiv import (
    ArxivResponseError,
    ArxivResultLimitError,
    ArxivUnavailableError,
)


def _result(arxiv_id: str, updated_at: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        updated=updated_at,
        published=updated_at,
        get_short_id=lambda: arxiv_id,
        title=f"Paper {arxiv_id}",
        summary="A bounded agent paper.",
        primary_category="cs.AI",
        categories=["cs.AI"],
        authors=[SimpleNamespace(name="Ada Lovelace")],
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        entry_id=f"https://arxiv.org/abs/{arxiv_id}",
    )


def test_timestamp_tie_at_result_cap_fails_without_partial_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated_at = datetime(2026, 1, 10, 4, tzinfo=UTC)
    results = (
        _result("2601.00001v1", updated_at),
        _result("2601.00002v1", updated_at),
        _result("2601.00003v1", updated_at),
    )

    def results_for_tie(_self: Any, _search: Any) -> Iterator[SimpleNamespace]:
        return iter(results)

    monkeypatch.setattr(arxiv.Client, "results", results_for_tie)
    client = ArxivClient(max_retries=0, sleep=lambda _delay: None)

    with pytest.raises(ArxivResultLimitError, match="cursor was not advanced"):
        client.search(
            query="cat:cs.AI",
            updated_from=updated_at - timedelta(hours=1),
            updated_until=updated_at + timedelta(hours=1),
            max_results=2,
        )


def test_newer_row_does_not_consume_in_window_saturation_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    in_window = datetime(2026, 1, 10, 4, tzinfo=UTC)
    updated_until = in_window + timedelta(hours=1)
    results = (
        _result("2601.00000v1", updated_until + timedelta(minutes=1)),
        _result("2601.00001v1", in_window),
        _result("2601.00002v1", in_window),
        _result("2601.00003v1", in_window),
    )
    captured_max_results: list[int | None] = []

    def upstream_results(_self: Any, search: Any) -> Iterator[SimpleNamespace]:
        captured_max_results.append(search.max_results)
        if search.max_results is None:
            return iter(results)
        return iter(results[: search.max_results])

    monkeypatch.setattr(arxiv.Client, "results", upstream_results)
    client = ArxivClient(max_retries=0, sleep=lambda _delay: None)
    with pytest.raises(ArxivResultLimitError, match="cursor was not advanced"):
        client.search(
            query="cat:cs.AI",
            updated_from=in_window - timedelta(hours=1),
            updated_until=updated_until,
            max_results=2,
        )
    assert captured_max_results == [None]


def test_exhausted_window_equal_to_cap_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    updated_at = datetime(2026, 1, 10, 4, tzinfo=UTC)
    results = (
        _result("2601.00001v1", updated_at),
        _result("2601.00002v1", updated_at),
    )
    captured_max_results: list[int | None] = []

    def results_for_search(_self: Any, search: Any) -> Iterator[SimpleNamespace]:
        captured_max_results.append(search.max_results)
        return iter(results)

    monkeypatch.setattr(arxiv.Client, "results", results_for_search)
    client = ArxivClient(max_retries=0, sleep=lambda _delay: None)
    records = client.search(
        query="cat:cs.AI",
        updated_from=updated_at - timedelta(hours=1),
        updated_until=updated_at + timedelta(hours=1),
        max_results=2,
    )
    assert len(records) == 2
    assert captured_max_results == [None]
    assert client._client.num_retries == 0  # pyright: ignore[reportPrivateUsage]


def test_retry_after_and_timeout_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    responses: list[requests.Response | Exception] = [
        _response(429, retry_after="2"),
        requests.exceptions.Timeout("read timed out"),
        _response(200),
    ]

    def request(
        _self: requests.Session, _method: str, _url: str, **kwargs: Any
    ) -> requests.Response:
        calls.append(kwargs)
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(requests.Session, "request", request)
    sleeps: list[float] = []
    session = BoundedArxivSession(
        request_timeout_seconds=7,
        max_retries=2,
        retry_backoff_seconds=1,
        max_retry_after_seconds=10,
        max_total_seconds=30,
        sleep=sleeps.append,
        monotonic=lambda: 0,
    )
    with session.operation():
        assert session.get("https://export.arxiv.org/api/query").status_code == 200
    assert sleeps == [2.0, 2.0]
    assert [call["timeout"] for call in calls] == [7, 7, 7]


def test_total_deadline_is_shared_across_retries_and_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    calls: list[dict[str, Any]] = []
    responses = [_response(429, retry_after="4"), _response(200)]

    def request(
        _self: requests.Session, _method: str, _url: str, **kwargs: Any
    ) -> requests.Response:
        calls.append(kwargs)
        return responses.pop(0)

    def sleep(delay: float) -> None:
        clock[0] += delay

    monkeypatch.setattr(requests.Session, "request", request)
    session = BoundedArxivSession(
        request_timeout_seconds=20,
        max_retries=2,
        retry_backoff_seconds=1,
        max_retry_after_seconds=10,
        max_total_seconds=5,
        sleep=sleep,
        monotonic=lambda: clock[0],
    )
    with session.operation():
        assert session.get("https://export.arxiv.org/api/query?page=1").status_code == 200
        clock[0] = 5.0
        with pytest.raises(requests.exceptions.Timeout, match="total-operation deadline"):
            session.get("https://export.arxiv.org/api/query?page=2")
    assert [call["timeout"] for call in calls] == [5, 1]


def test_connection_error_is_mapped_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def failed_results(_self: Any, _search: Any) -> Iterator[Any]:
        nonlocal calls
        calls += 1
        raise requests.exceptions.ConnectionError("offline")

    monkeypatch.setattr(arxiv.Client, "results", failed_results)
    client = ArxivClient(max_retries=3, sleep=lambda _delay: None)
    with pytest.raises(ArxivUnavailableError, match="ConnectionError"):
        client.search(
            query="cat:cs.AI",
            updated_from=datetime(2026, 1, 1, tzinfo=UTC),
            updated_until=datetime(2026, 1, 2, tzinfo=UTC),
            max_results=2,
        )
    assert calls == 1


def test_page_bound_fails_instead_of_returning_partial_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated_at = datetime(2026, 1, 10, 4, tzinfo=UTC)
    feeds = [
        SimpleNamespace(
            results=[_result("2601.00001v1", updated_at)],
            header=SimpleNamespace(total_results=3),
            malformed=False,
        ),
        SimpleNamespace(
            results=[_result("2601.00002v1", updated_at)],
            header=SimpleNamespace(total_results=3),
            malformed=False,
        ),
    ]

    def parse_feed(_self: Any, _url: str, first_page: bool = True, _try_index: int = 0) -> Any:
        del first_page, _try_index
        return feeds.pop(0)

    monkeypatch.setattr(arxiv.Client, "_parse_feed", parse_feed)
    client = ArxivClient(
        page_size=1, max_pages=2, delay_seconds=0, max_retries=0, sleep=lambda _delay: None
    )
    with pytest.raises(ArxivResultLimitError, match="page bound"):
        client.search(
            query="cat:cs.AI",
            updated_from=updated_at - timedelta(hours=1),
            updated_until=updated_at + timedelta(hours=1),
            max_results=10,
        )


def test_http_200_malformed_empty_atom_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def malformed_response(
        _self: requests.Session, _method: str, _url: str, **_kwargs: Any
    ) -> requests.Response:
        return _response(200, content=b"")

    monkeypatch.setattr(requests.Session, "request", malformed_response)
    client = ArxivClient(delay_seconds=0, max_retries=0, sleep=lambda _delay: None)
    with pytest.raises(ArxivResponseError, match="malformed Atom feed"):
        client.search(
            query="cat:cs.AI",
            updated_from=datetime(2026, 1, 1, tzinfo=UTC),
            updated_until=datetime(2026, 1, 2, tzinfo=UTC),
            max_results=2,
        )


def test_http_200_valid_zero_result_atom_is_empty_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_empty_atom = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <title>arXiv Query: search_query=cat:cs.AI</title>
  <id>https://export.arxiv.org/api/query</id>
  <updated>2026-01-01T00:00:00Z</updated>
  <opensearch:totalResults>0</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <opensearch:itemsPerPage>0</opensearch:itemsPerPage>
</feed>"""

    def valid_empty_response(
        _self: requests.Session, _method: str, _url: str, **_kwargs: Any
    ) -> requests.Response:
        return _response(200, content=valid_empty_atom)

    monkeypatch.setattr(requests.Session, "request", valid_empty_response)
    client = ArxivClient(delay_seconds=0, max_retries=0, sleep=lambda _delay: None)
    assert (
        client.search(
            query="cat:cs.AI",
            updated_from=datetime(2026, 1, 1, tzinfo=UTC),
            updated_until=datetime(2026, 1, 2, tzinfo=UTC),
            max_results=2,
        )
        == ()
    )


def _response(
    status_code: int, *, retry_after: str | None = None, content: bytes = b""
) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = content
    setattr(response, "_content_consumed", True)  # noqa: B010
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return response
