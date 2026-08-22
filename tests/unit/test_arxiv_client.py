# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import arxiv
import pytest
import requests
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeoutError

from paper_harness.adapters.arxiv.client import ArxivClient, BoundedArxivSession
from paper_harness.ports.arxiv import (
    MAX_ARXIV_ID_LOOKUP,
    ArxivPdfError,
    ArxivResponseError,
    ArxivResultLimitError,
    ArxivUnavailableError,
)

_VALID_EMPTY_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <title>arXiv Query: search_query=cat:cs.AI</title>
  <id>https://export.arxiv.org/api/query</id>
  <updated>2026-01-01T00:00:00Z</updated>
  <opensearch:totalResults>0</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <opensearch:itemsPerPage>0</opensearch:itemsPerPage>
</feed>"""

_VALID_ONE_RESULT_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <title>arXiv Query: search_query=cat:cs.AI</title>
  <id>https://export.arxiv.org/api/query</id>
  <updated>2026-01-11T04:00:00Z</updated>
  <opensearch:totalResults>1</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <opensearch:itemsPerPage>1</opensearch:itemsPerPage>
  <entry>
    <id>http://arxiv.org/abs/2601.01234v2</id>
    <updated>2026-01-10T04:00:00Z</updated>
    <published>2026-01-09T04:00:00Z</published>
    <title>A bounded agent paper</title>
    <summary>Evidence-grounded agent analysis.</summary>
    <author><name>Ada Lovelace</name></author>
    <arxiv:primary_category term="cs.AI" />
    <category term="cs.AI" />
    <link href="https://arxiv.org/pdf/2601.01234v2" title="pdf"
          rel="related" type="application/pdf" />
  </entry>
</feed>"""

_VALID_SECOND_ENTRY = b"""
  <entry>
    <id>https://arxiv.org/abs/2601.05678v1</id>
    <updated>2026-01-10T05:00:00Z</updated>
    <published>2026-01-10T05:00:00Z</published>
    <title>A second valid agent paper</title>
    <summary>Valid metadata remains usable.</summary>
    <author><name>Grace Hopper</name></author>
    <arxiv:primary_category term="cs.AI" />
    <category term="cs.AI" />
    <link href="https://arxiv.org/pdf/2601.05678v1" title="pdf"
          rel="related" type="application/pdf" />
  </entry>
"""


def _result(
    arxiv_id: str,
    updated_at: datetime,
    *,
    published_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        updated=updated_at,
        published=published_at or updated_at,
        get_short_id=lambda: arxiv_id,
        title=f"Paper {arxiv_id}",
        summary="A bounded agent paper.",
        primary_category="cs.AI",
        categories=["cs.AI"],
        authors=[SimpleNamespace(name="Ada Lovelace")],
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        entry_id=f"https://arxiv.org/abs/{arxiv_id}",
    )


def test_search_requests_last_updated_descending_with_a_bounded_candidate_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[Any, Any, int | None]] = []

    def results(_self: Any, search: Any) -> Iterator[SimpleNamespace]:
        captured.append((search.sort_by, search.sort_order, search.max_results))
        return iter(())

    monkeypatch.setattr(arxiv.Client, "results", results)
    ArxivClient(max_retries=0, sleep=lambda _delay: None).search(
        query="cat:cs.AI",
        updated_from=datetime(2026, 1, 1, tzinfo=UTC),
        updated_until=datetime(2026, 1, 2, tzinfo=UTC),
        max_results=2,
    )

    assert captured == [
        (
            arxiv.SortCriterion.LastUpdatedDate,
            arxiv.SortOrder.Descending,
            102,
        )
    ]


def test_exact_id_lookup_returns_explicit_versions_in_requested_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _result("2601.00001v3", datetime(2026, 1, 12, tzinfo=UTC))
    second = _result("2601.00002v2", datetime(2026, 1, 10, tzinfo=UTC))
    captured: list[tuple[list[str], int | None]] = []

    def results(_self: Any, search: Any) -> Iterator[SimpleNamespace]:
        captured.append((search.id_list, search.max_results))
        return iter((second, first))

    monkeypatch.setattr(arxiv.Client, "results", results)
    client = ArxivClient(max_retries=0, sleep=lambda _delay: None)

    records = client.get_papers_by_ids(
        canonical_arxiv_ids=("2601.00001", "2601.00002"),
    )

    assert [(record.canonical_arxiv_id, record.version) for record in records] == [
        ("2601.00001", 3),
        ("2601.00002", 2),
    ]
    assert captured == [(["2601.00001", "2601.00002"], 2)]


@pytest.mark.parametrize(
    ("canonical_arxiv_ids", "message"),
    [
        ((), "between 1"),
        (("not-an-arxiv-id",), "invalid canonical"),
        (
            tuple(f"2601.{index:05d}" for index in range(MAX_ARXIV_ID_LOOKUP + 1)),
            "between 1",
        ),
    ],
)
def test_exact_id_lookup_rejects_unbounded_or_invalid_requests_before_transport(
    monkeypatch: pytest.MonkeyPatch,
    canonical_arxiv_ids: tuple[str, ...],
    message: str,
) -> None:
    def unexpected_results(_self: Any, _search: Any) -> Iterator[SimpleNamespace]:
        raise AssertionError("invalid ID lookup must not reach arXiv")
        yield

    monkeypatch.setattr(arxiv.Client, "results", unexpected_results)

    with pytest.raises(ValueError, match=message):
        ArxivClient(max_retries=0, sleep=lambda _delay: None).get_papers_by_ids(
            canonical_arxiv_ids=canonical_arxiv_ids,
        )


@pytest.mark.parametrize(
    ("returned_ids", "expected_ids"),
    [
        (("2601.00001v1",), ("2601.00001",)),
        (("2601.00001v1", "2601.00003v1"), ("2601.00001",)),
        (("2601.00001v1", "2601.00001v1"), ("2601.00001",)),
    ],
)
def test_exact_id_lookup_returns_the_available_requested_subset(
    monkeypatch: pytest.MonkeyPatch,
    returned_ids: tuple[str, ...],
    expected_ids: tuple[str, ...],
) -> None:
    def results(_self: Any, _search: Any) -> Iterator[SimpleNamespace]:
        return iter(
            _result(arxiv_id, datetime(2026, 1, 10, tzinfo=UTC)) for arxiv_id in returned_ids
        )

    monkeypatch.setattr(arxiv.Client, "results", results)

    records = ArxivClient(max_retries=0, sleep=lambda _delay: None).get_papers_by_ids(
        canonical_arxiv_ids=("2601.00001", "2601.00002"),
    )

    assert tuple(record.canonical_arxiv_id for record in records) == expected_ids


def test_timestamp_tie_at_result_cap_is_locally_and_stably_bounded(
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

    records = client.search(
        query="cat:cs.AI",
        updated_from=updated_at - timedelta(hours=1),
        updated_until=updated_at + timedelta(hours=1),
        max_results=2,
    )

    assert [record.canonical_arxiv_id for record in records] == [
        "2601.00001",
        "2601.00002",
    ]


def test_shuffled_results_are_deduplicated_and_stably_sorted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = datetime(2026, 1, 11, 4, tzinfo=UTC)
    tied = datetime(2026, 1, 10, 4, tzinfo=UTC)
    older = datetime(2026, 1, 9, 4, tzinfo=UTC)
    records = (
        _result("2601.00004v1", older),
        _result("2601.00001v2", tied, published_at=older),
        _result("2601.00003v1", newest, published_at=tied),
        _result("2601.00001v1", tied, published_at=older),
        _result("2601.00002v1", newest, published_at=older),
        _result("2601.00003v1", newest, published_at=tied),
    )
    upstream_orders = iter((records, tuple(reversed(records))))

    def results(_self: Any, _search: Any) -> Iterator[SimpleNamespace]:
        return iter(next(upstream_orders))

    monkeypatch.setattr(arxiv.Client, "results", results)
    client = ArxivClient(max_retries=0, sleep=lambda _delay: None)
    outputs = tuple(
        client.search(
            query="cat:cs.AI",
            updated_from=older - timedelta(hours=1),
            updated_until=newest + timedelta(hours=1),
            max_results=10,
        )
        for _ in range(2)
    )

    expected = (
        ("2601.00003", 1),
        ("2601.00002", 1),
        ("2601.00001", 1),
        ("2601.00001", 2),
        ("2601.00004", 1),
    )
    assert tuple((record.canonical_arxiv_id, record.version) for record in outputs[0]) == expected
    assert outputs[1] == outputs[0]


def test_aware_timestamps_are_normalized_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    source_zone = timezone(timedelta(hours=8))
    updated_at = datetime(2026, 1, 10, 12, tzinfo=source_zone)

    def results(_self: Any, _search: Any) -> Iterator[SimpleNamespace]:
        return iter((_result("2601.00001v1", updated_at),))

    monkeypatch.setattr(arxiv.Client, "results", results)
    records = ArxivClient(max_retries=0, sleep=lambda _delay: None).search(
        query="cat:cs.AI",
        updated_from=datetime(2026, 1, 10, 3, tzinfo=UTC),
        updated_until=datetime(2026, 1, 10, 5, tzinfo=UTC),
        max_results=2,
    )

    assert records[0].updated_at == datetime(2026, 1, 10, 4, tzinfo=UTC)
    assert records[0].submitted_at == datetime(2026, 1, 10, 4, tzinfo=UTC)
    assert records[0].updated_at.tzinfo is UTC
    assert records[0].submitted_at.tzinfo is UTC


def test_identical_duplicates_do_not_consume_the_unique_result_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated_at = datetime(2026, 1, 10, 4, tzinfo=UTC)
    first = _result("2601.00001v1", updated_at)
    second = _result("2601.00002v1", updated_at)

    def results(_self: Any, _search: Any) -> Iterator[SimpleNamespace]:
        return iter((first, first, second, second))

    monkeypatch.setattr(arxiv.Client, "results", results)
    records = ArxivClient(max_retries=0, sleep=lambda _delay: None).search(
        query="cat:cs.AI",
        updated_from=updated_at - timedelta(hours=1),
        updated_until=updated_at + timedelta(hours=1),
        max_results=2,
    )

    assert [(record.canonical_arxiv_id, record.version) for record in records] == [
        ("2601.00001", 1),
        ("2601.00002", 1),
    ]


def test_conflicting_duplicate_canonical_version_is_resolved_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated_at = datetime(2026, 1, 10, 4, tzinfo=UTC)
    first = _result("2601.00001v1", updated_at)
    conflicting = _result("2601.00001v1", updated_at)
    conflicting.title = "Conflicting metadata"

    def results(_self: Any, _search: Any) -> Iterator[SimpleNamespace]:
        return iter((first, conflicting))

    monkeypatch.setattr(arxiv.Client, "results", results)
    records = ArxivClient(max_retries=0, sleep=lambda _delay: None).search(
        query="cat:cs.AI",
        updated_from=updated_at - timedelta(hours=1),
        updated_until=updated_at + timedelta(hours=1),
        max_results=2,
    )

    assert len(records) == 1
    assert records[0].title == "Paper 2601.00001v1"


def test_newer_row_is_filtered_before_local_top_n(
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
    records = client.search(
        query="cat:cs.AI",
        updated_from=in_window - timedelta(hours=1),
        updated_until=updated_until,
        max_results=2,
    )
    assert [record.canonical_arxiv_id for record in records] == [
        "2601.00001",
        "2601.00002",
    ]
    assert captured_max_results == [102]


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
    assert captured_max_results == [102]
    assert client._client.num_retries == 0  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("entry_id", "pdf_url"),
    [
        (
            "https://attacker.invalid/abs/2601.01234v2",
            "https://arxiv.org/pdf/2601.01234v2",
        ),
        (
            "https://arxiv.org/abs/2601.01234v1",
            "https://arxiv.org/pdf/2601.01234v2",
        ),
        (
            "https://arxiv.org/abs/2601.01234v2",
            "https://attacker.invalid/pdf/2601.01234v2",
        ),
        (
            "https://arxiv.org/abs/2601.01234v2",
            "https://arxiv.org/pdf/2601.01234v1",
        ),
    ],
)
def test_candidate_with_mismatched_urls_is_omitted_without_losing_valid_results(
    monkeypatch: pytest.MonkeyPatch,
    entry_id: str,
    pdf_url: str,
) -> None:
    updated_at = datetime(2026, 1, 10, 4, tzinfo=UTC)
    result = _result("2601.01234v2", updated_at)
    result.entry_id = entry_id
    result.pdf_url = pdf_url
    valid = _result("2601.05678v1", updated_at)

    def results(_self: Any, _search: Any) -> Iterator[SimpleNamespace]:
        return iter((result, valid))

    monkeypatch.setattr(arxiv.Client, "results", results)

    records = ArxivClient(max_retries=0, sleep=lambda _delay: None).search(
        query="cat:cs.AI",
        updated_from=updated_at - timedelta(hours=1),
        updated_until=updated_at + timedelta(hours=1),
        max_results=2,
    )

    assert [record.canonical_arxiv_id for record in records] == ["2601.05678"]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("title", "   "),
        ("title", 42),
        ("primary_category", 42),
        ("categories", "cs.AI"),
        ("published", "2026-01-10T04:00:00Z"),
        ("updated", "2026-01-10T04:00:00Z"),
    ],
)
def test_candidate_required_metadata_remains_strict_without_failing_the_collection(
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    value: object,
) -> None:
    updated_at = datetime(2026, 1, 10, 4, tzinfo=UTC)
    result = _result("2601.01234v2", updated_at)
    setattr(result, field_name, value)
    valid = _result("2601.05678v1", updated_at)

    def results(_self: Any, _search: Any) -> Iterator[SimpleNamespace]:
        return iter((result, valid))

    monkeypatch.setattr(arxiv.Client, "results", results)
    records = ArxivClient(max_retries=0, sleep=lambda _delay: None).search(
        query="cat:cs.AI",
        updated_from=updated_at - timedelta(hours=1),
        updated_until=updated_at + timedelta(hours=1),
        max_results=2,
    )

    assert [record.canonical_arxiv_id for record in records] == ["2601.05678"]


def test_required_arxiv_identity_remains_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    updated_at = datetime(2026, 1, 10, 4, tzinfo=UTC)
    result = _result("2601.01234v2", updated_at)
    result.get_short_id = lambda: ""
    valid = _result("2601.05678v1", updated_at)

    def results(_self: Any, _search: Any) -> Iterator[SimpleNamespace]:
        return iter((result, valid))

    monkeypatch.setattr(arxiv.Client, "results", results)
    records = ArxivClient(max_retries=0, sleep=lambda _delay: None).search(
        query="cat:cs.AI",
        updated_from=updated_at - timedelta(hours=1),
        updated_until=updated_at + timedelta(hours=1),
        max_results=2,
    )

    assert [record.canonical_arxiv_id for record in records] == ["2601.05678"]


def test_arxiv_dependency_value_error_is_mapped_without_leaking_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_results(_self: Any, _search: Any) -> Iterator[Any]:
        raise ValueError("private dependency detail")
        yield

    monkeypatch.setattr(arxiv.Client, "results", invalid_results)
    with pytest.raises(ArxivResponseError, match="invalid feed data") as raised:
        ArxivClient(max_retries=0, sleep=lambda _delay: None).search(
            query="cat:cs.AI",
            updated_from=datetime(2026, 1, 1, tzinfo=UTC),
            updated_until=datetime(2026, 1, 2, tzinfo=UTC),
            max_results=2,
        )
    assert "private dependency detail" not in str(raised.value)


def test_retry_after_and_timeout_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    responses: list[requests.Response | Exception] = [
        _response(429, retry_after="2"),
        requests.exceptions.Timeout("read timed out"),
        _response(200, content=_VALID_EMPTY_ATOM),
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
        atom_max_bytes=1024,
        sleep=sleeps.append,
        monotonic=lambda: 0,
    )
    with session.operation():
        assert session.get("https://export.arxiv.org/api/query").status_code == 200
    assert sleeps == [2.0, 2.0]
    assert [call["timeout"] for call in calls] == [7, 7, 7]


def test_nonfinite_retry_after_uses_the_bounded_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        _response(429, retry_after="NaN"),
        _response(200, content=_VALID_EMPTY_ATOM),
    ]

    def request(
        _self: requests.Session, _method: str, _url: str, **_kwargs: Any
    ) -> requests.Response:
        return responses.pop(0)

    monkeypatch.setattr(requests.Session, "request", request)
    sleeps: list[float] = []
    session = BoundedArxivSession(
        request_timeout_seconds=7,
        max_retries=1,
        retry_backoff_seconds=3,
        max_retry_after_seconds=10,
        max_total_seconds=30,
        atom_max_bytes=1024,
        sleep=sleeps.append,
        monotonic=lambda: 0,
    )

    with session.operation():
        assert session.get("https://export.arxiv.org/api/query").status_code == 200
    assert sleeps == [3]


def test_total_deadline_is_shared_across_retries_and_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    calls: list[dict[str, Any]] = []
    responses = [
        _response(429, retry_after="4"),
        _response(200, content=_VALID_EMPTY_ATOM),
    ]

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
        atom_max_bytes=1024,
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
            header=SimpleNamespace(total_results=3, start_index=0),
            malformed=False,
        ),
        SimpleNamespace(
            results=[_result("2601.00002v1", updated_at)],
            header=SimpleNamespace(total_results=3, start_index=1),
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


def test_cross_page_disorder_does_not_stop_before_a_later_in_window_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated_from = datetime(2026, 1, 10, 3, tzinfo=UTC)
    stale = updated_from - timedelta(days=1)
    older_in_window = updated_from + timedelta(minutes=30)
    in_window = updated_from + timedelta(hours=1)
    feeds = [
        SimpleNamespace(
            results=[_result("2601.00001v1", older_in_window)],
            header=SimpleNamespace(total_results=3, start_index=0),
            malformed=False,
        ),
        SimpleNamespace(
            results=[_result("2601.00002v1", stale)],
            header=SimpleNamespace(total_results=3, start_index=1),
            malformed=False,
        ),
        SimpleNamespace(
            results=[_result("2601.00003v1", in_window)],
            header=SimpleNamespace(total_results=3, start_index=2),
            malformed=False,
        ),
    ]

    def parse_feed(_self: Any, _url: str, first_page: bool = True, _try_index: int = 0) -> Any:
        del first_page, _try_index
        return feeds.pop(0)

    monkeypatch.setattr(arxiv.Client, "_parse_feed", parse_feed)
    records = ArxivClient(
        page_size=1,
        max_pages=3,
        delay_seconds=0,
        max_retries=0,
        sleep=lambda _delay: None,
    ).search(
        query="cat:cs.AI",
        updated_from=updated_from,
        updated_until=in_window + timedelta(hours=1),
        max_results=3,
    )

    assert [(record.canonical_arxiv_id, record.version) for record in records] == [
        ("2601.00003", 1),
        ("2601.00001", 1),
    ]
    assert feeds == []


def test_response_start_index_is_not_a_paper_validity_invariant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated_at = datetime(2026, 1, 10, 4, tzinfo=UTC)
    feeds = [
        SimpleNamespace(
            results=[_result("2601.00001v1", updated_at)],
            header=SimpleNamespace(total_results=2, start_index=0),
            malformed=False,
        ),
        SimpleNamespace(
            results=[_result("2601.00001v1", updated_at)],
            header=SimpleNamespace(total_results=2, start_index=0),
            malformed=False,
        ),
    ]

    def parse_feed(_self: Any, _url: str, first_page: bool = True, _try_index: int = 0) -> Any:
        del first_page, _try_index
        return feeds.pop(0)

    monkeypatch.setattr(arxiv.Client, "_parse_feed", parse_feed)
    client = ArxivClient(
        page_size=1,
        max_pages=2,
        delay_seconds=0,
        max_retries=0,
        sleep=lambda _delay: None,
    )
    records = client.search(
        query="cat:cs.AI",
        updated_from=updated_at - timedelta(hours=1),
        updated_until=updated_at + timedelta(hours=1),
        max_results=10,
    )

    assert [record.canonical_arxiv_id for record in records] == ["2601.00001"]


def test_total_results_display_metadata_can_change_across_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated_at = datetime(2026, 1, 10, 4, tzinfo=UTC)
    feeds = [
        SimpleNamespace(
            results=[_result("2601.00001v1", updated_at)],
            header=SimpleNamespace(total_results=2, start_index=0),
            malformed=False,
        ),
        SimpleNamespace(
            results=[_result("2601.00002v1", updated_at)],
            header=SimpleNamespace(total_results=3, start_index=1),
            malformed=False,
        ),
    ]

    def parse_feed(_self: Any, _url: str, first_page: bool = True, _try_index: int = 0) -> Any:
        del first_page, _try_index
        return feeds.pop(0)

    monkeypatch.setattr(arxiv.Client, "_parse_feed", parse_feed)
    client = ArxivClient(
        page_size=1,
        max_pages=3,
        delay_seconds=0,
        max_retries=0,
        sleep=lambda _delay: None,
    )
    records = client.search(
        query="cat:cs.AI",
        updated_from=updated_at - timedelta(hours=1),
        updated_until=updated_at + timedelta(hours=1),
        max_results=10,
    )

    assert [record.canonical_arxiv_id for record in records] == [
        "2601.00001",
        "2601.00002",
    ]


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
    def valid_empty_response(
        _self: requests.Session, _method: str, _url: str, **_kwargs: Any
    ) -> requests.Response:
        return _response(200, content=_VALID_EMPTY_ATOM)

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


def test_atom_entry_accepts_legacy_http_id_and_normalizes_source_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    urls: list[str] = []

    def response(
        _self: requests.Session, _method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        urls.append(url)
        calls.append(kwargs)
        return _response(200, content=_VALID_ONE_RESULT_ATOM)

    monkeypatch.setattr(requests.Session, "request", response)
    client = ArxivClient(delay_seconds=0, max_retries=0, sleep=lambda _delay: None)
    records = client.search(
        query="cat:cs.AI",
        updated_from=datetime(2026, 1, 1, tzinfo=UTC),
        updated_until=datetime(2026, 1, 11, tzinfo=UTC),
        max_results=2,
    )

    assert len(records) == 1
    assert records[0].source_url == "https://arxiv.org/abs/2601.01234v2"
    assert records[0].pdf_url == "https://arxiv.org/pdf/2601.01234v2"
    assert client._session.headers["Accept-Encoding"] == "identity"
    query = parse_qs(urlsplit(urls[0]).query)
    assert query["sortBy"] == ["lastUpdatedDate"]
    assert query["sortOrder"] == ["descending"]
    assert calls == [
        {
            "headers": {"user-agent": f"arxiv.py/{arxiv.__version__}"},
            "allow_redirects": False,
            "stream": True,
            "timeout": 20.0,
        }
    ]


@pytest.mark.parametrize(
    "content",
    [
        b"<feed xmlns='http://www.w3.org/2005/Atom'><title>broken</feed>",
        _VALID_EMPTY_ATOM.replace(
            b'<feed xmlns="http://www.w3.org/2005/Atom"',
            b'<feed xmlns="https://example.invalid/not-atom"',
        ),
    ],
)
def test_atom_rejects_mismatched_xml_and_wrong_root_namespace(
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
) -> None:
    monkeypatch.setattr(
        requests.Session,
        "request",
        _request_returning(_response(200, content=content)),
    )
    with pytest.raises(ArxivResponseError, match="Atom|feed"):
        ArxivClient(delay_seconds=0, max_retries=0, sleep=lambda _delay: None).search(
            query="cat:cs.AI",
            updated_from=datetime(2026, 1, 1, tzinfo=UTC),
            updated_until=datetime(2026, 1, 11, tzinfo=UTC),
            max_results=2,
        )


@pytest.mark.parametrize(
    ("needle", "replacement"),
    [
        (
            b"<id>http://arxiv.org/abs/2601.01234v2</id>",
            b"<id> </id>",
        ),
        (
            b"<updated>2026-01-10T04:00:00Z</updated>",
            b"<updated> </updated>",
        ),
        (
            b"<published>2026-01-09T04:00:00Z</published>",
            b"",
        ),
    ],
)
def test_atom_omits_only_the_entry_that_lacks_required_metadata(
    monkeypatch: pytest.MonkeyPatch,
    needle: bytes,
    replacement: bytes,
) -> None:
    invalid_entry = _VALID_ONE_RESULT_ATOM.replace(needle, replacement, 1).replace(
        b"</feed>",
        _VALID_SECOND_ENTRY + b"</feed>",
    )
    monkeypatch.setattr(
        requests.Session,
        "request",
        _request_returning(_response(200, content=invalid_entry)),
    )
    records = ArxivClient(delay_seconds=0, max_retries=0, sleep=lambda _delay: None).search(
        query="cat:cs.AI",
        updated_from=datetime(2026, 1, 1, tzinfo=UTC),
        updated_until=datetime(2026, 1, 11, tzinfo=UTC),
        max_results=2,
    )

    assert [record.canonical_arxiv_id for record in records] == ["2601.05678"]


def test_atom_omits_only_the_entry_with_an_invalid_required_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _VALID_ONE_RESULT_ATOM.replace(
        b"<published>2026-01-09T04:00:00Z</published>",
        b"<published>2026-13-99 04:00:00</published>",
    ).replace(b"</feed>", _VALID_SECOND_ENTRY + b"</feed>")
    monkeypatch.setattr(
        requests.Session,
        "request",
        _request_returning(_response(200, content=content)),
    )
    records = ArxivClient(delay_seconds=0, max_retries=0, sleep=lambda _delay: None).search(
        query="cat:cs.AI",
        updated_from=datetime(2026, 1, 1, tzinfo=UTC),
        updated_until=datetime(2026, 1, 11, tzinfo=UTC),
        max_results=2,
    )

    assert [record.canonical_arxiv_id for record in records] == ["2601.05678"]


def test_atom_accepts_out_of_order_entry_before_cursor_cutoff_and_sorts_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    second_entry = b"""
  <entry>
    <id>https://arxiv.org/abs/2601.05678v1</id>
    <updated>2026-01-10T05:00:00Z</updated>
    <published>2026-01-10T05:00:00Z</published>
    <title>A hidden newer paper</title>
    <summary>This entry must not be hidden after a stale cutoff.</summary>
    <author><name>Grace Hopper</name></author>
    <arxiv:primary_category term="cs.AI" />
    <category term="cs.AI" />
    <link href="https://arxiv.org/pdf/2601.05678v1" title="pdf"
          rel="related" type="application/pdf" />
  </entry>
"""
    content = (
        _VALID_ONE_RESULT_ATOM.replace(
            b"<opensearch:totalResults>1</opensearch:totalResults>",
            b"<opensearch:totalResults>2</opensearch:totalResults>",
        )
        .replace(
            b"<opensearch:itemsPerPage>1</opensearch:itemsPerPage>",
            b"<opensearch:itemsPerPage>2</opensearch:itemsPerPage>",
        )
        .replace(
            b"<updated>2026-01-10T04:00:00Z</updated>",
            b"<updated>2025-12-31T04:00:00Z</updated>",
            1,
        )
        .replace(b"</feed>", second_entry + b"</feed>")
    )
    monkeypatch.setattr(
        requests.Session,
        "request",
        _request_returning(_response(200, content=content)),
    )
    records = ArxivClient(
        delay_seconds=0,
        max_retries=0,
        sleep=lambda _delay: None,
    ).search(
        query="cat:cs.AI",
        updated_from=datetime(2026, 1, 1, tzinfo=UTC),
        updated_until=datetime(2026, 1, 11, tzinfo=UTC),
        max_results=2,
    )

    assert [(record.canonical_arxiv_id, record.version) for record in records] == [
        ("2601.05678", 1)
    ]


@pytest.mark.parametrize(
    "content",
    [
        _VALID_EMPTY_ATOM.replace(b"  <opensearch:startIndex>0</opensearch:startIndex>\n", b""),
        _VALID_EMPTY_ATOM.replace(
            b"<opensearch:totalResults>0</opensearch:totalResults>",
            b"<opensearch:totalResults>-1</opensearch:totalResults>",
        ),
        _VALID_EMPTY_ATOM.replace(
            b"<opensearch:startIndex>0</opensearch:startIndex>",
            b"<opensearch:startIndex>many</opensearch:startIndex>",
        ),
        _VALID_ONE_RESULT_ATOM.replace(
            b"<opensearch:itemsPerPage>1</opensearch:itemsPerPage>",
            b"<opensearch:itemsPerPage>0</opensearch:itemsPerPage>",
        ),
        _VALID_EMPTY_ATOM.replace(
            b"<opensearch:totalResults>0</opensearch:totalResults>",
            b"<opensearch:totalResults>1</opensearch:totalResults>",
        ).replace(
            b"<opensearch:itemsPerPage>0</opensearch:itemsPerPage>",
            b"<opensearch:itemsPerPage>1</opensearch:itemsPerPage>",
        ),
    ],
)
def test_atom_ignores_missing_or_invalid_pagination_display_metadata(
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
) -> None:
    monkeypatch.setattr(
        requests.Session,
        "request",
        _request_returning(_response(200, content=content)),
    )
    ArxivClient(delay_seconds=0, max_retries=0, sleep=lambda _delay: None).search(
        query="cat:cs.AI",
        updated_from=datetime(2026, 1, 1, tzinfo=UTC),
        updated_until=datetime(2026, 1, 11, tzinfo=UTC),
        max_results=2,
    )


def test_atom_redirect_is_not_followed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def redirect(
        _self: requests.Session, _method: str, _url: str, **kwargs: Any
    ) -> requests.Response:
        calls.append(kwargs)
        response = _response(302)
        response.headers["Location"] = "https://attacker.invalid/feed"
        return response

    monkeypatch.setattr(requests.Session, "request", redirect)
    with pytest.raises(ArxivResponseError, match="HTTP 302"):
        ArxivClient(delay_seconds=0, max_retries=0, sleep=lambda _delay: None).search(
            query="cat:cs.AI",
            updated_from=datetime(2026, 1, 1, tzinfo=UTC),
            updated_until=datetime(2026, 1, 11, tzinfo=UTC),
            max_results=2,
        )
    assert len(calls) == 1
    assert calls[0]["allow_redirects"] is False


def test_atom_request_url_is_restricted_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def request(
        _self: requests.Session, _method: str, _url: str, **_kwargs: Any
    ) -> requests.Response:
        nonlocal calls
        calls += 1
        return _response(200, content=_VALID_EMPTY_ATOM)

    monkeypatch.setattr(requests.Session, "request", request)
    session = BoundedArxivSession(
        request_timeout_seconds=7,
        max_retries=0,
        retry_backoff_seconds=1,
        max_retry_after_seconds=10,
        max_total_seconds=30,
        atom_max_bytes=1024,
        sleep=lambda _delay: None,
        monotonic=lambda: 0,
    )
    with session.operation(), pytest.raises(ArxivResponseError, match="approved endpoint"):
        session.get("https://attacker.invalid/api/query?search_query=all")
    assert calls == 0


@pytest.mark.parametrize("content_encoding", ["gzip", "br", "identity, gzip"])
def test_atom_relies_on_the_decoded_body_instead_of_encoding_headers(
    monkeypatch: pytest.MonkeyPatch,
    content_encoding: str,
) -> None:
    response = _StreamingResponse(_VALID_EMPTY_ATOM)
    response.headers["Content-Encoding"] = content_encoding
    iterations = 0

    def iter_content(chunk_size: int | None = 1, decode_unicode: bool = False) -> Iterator[bytes]:
        nonlocal iterations
        del chunk_size, decode_unicode
        iterations += 1
        yield _VALID_EMPTY_ATOM

    response.iter_content = iter_content  # type: ignore[method-assign]
    monkeypatch.setattr(
        requests.Session,
        "request",
        _request_returning(response),
    )
    records = ArxivClient(delay_seconds=0, max_retries=0, sleep=lambda _delay: None).search(
        query="cat:cs.AI",
        updated_from=datetime(2026, 1, 1, tzinfo=UTC),
        updated_until=datetime(2026, 1, 11, tzinfo=UTC),
        max_results=2,
    )
    assert records == ()
    assert iterations == 1


def test_atom_ignores_declared_length_and_enforces_the_actual_body_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _StreamingResponse(_VALID_EMPTY_ATOM)
    response.headers["Content-Length"] = "999999999"
    response.headers["Content-Type"] = "text/plain"
    monkeypatch.setattr(requests.Session, "request", _request_returning(response))

    records = ArxivClient(
        atom_max_bytes=1024,
        delay_seconds=0,
        max_retries=0,
        sleep=lambda _delay: None,
    ).search(
        query="cat:cs.AI",
        updated_from=datetime(2026, 1, 1, tzinfo=UTC),
        updated_until=datetime(2026, 1, 11, tzinfo=UTC),
        max_results=2,
    )

    assert records == ()


@pytest.mark.parametrize("declared_length", ["1025", None])
def test_atom_page_rejects_oversized_declared_or_streamed_body(
    monkeypatch: pytest.MonkeyPatch,
    declared_length: str | None,
) -> None:
    calls: list[dict[str, Any]] = []
    response = _StreamingResponse(b"x" * 1025)
    if declared_length is not None:
        response.headers["Content-Length"] = declared_length

    def request(
        _self: requests.Session, _method: str, _url: str, **kwargs: Any
    ) -> requests.Response:
        calls.append(kwargs)
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    client = ArxivClient(
        atom_max_bytes=1024,
        delay_seconds=0,
        max_retries=0,
        sleep=lambda _delay: None,
    )

    with pytest.raises(ArxivResponseError, match="Atom response exceeds"):
        client.search(
            query="cat:cs.AI",
            updated_from=datetime(2026, 1, 1, tzinfo=UTC),
            updated_until=datetime(2026, 1, 2, tzinfo=UTC),
            max_results=2,
        )
    assert calls[0]["stream"] is True


def test_atom_page_checks_shared_deadline_while_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_time = 0.0

    def advance_past_deadline() -> None:
        nonlocal current_time
        current_time = 6.0

    def request(
        _self: requests.Session, _method: str, _url: str, **_kwargs: Any
    ) -> requests.Response:
        return _StreamingResponse(_VALID_EMPTY_ATOM, on_chunk=advance_past_deadline)

    monkeypatch.setattr(requests.Session, "request", request)
    client = ArxivClient(
        delay_seconds=0,
        max_retries=0,
        request_timeout_seconds=5,
        max_total_seconds=5,
        sleep=lambda _delay: None,
        monotonic=lambda: current_time,
    )

    with pytest.raises(ArxivUnavailableError, match="Timeout"):
        client.search(
            query="cat:cs.AI",
            updated_from=datetime(2026, 1, 1, tzinfo=UTC),
            updated_until=datetime(2026, 1, 2, tzinfo=UTC),
            max_results=2,
        )


def test_atom_page_retries_transient_mid_body_timeout_without_partial_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: list[requests.Response] = [
        _StreamingResponse(
            b"<feed>partial",
            requests.exceptions.ConnectionError(
                Urllib3ReadTimeoutError(
                    cast(Any, None), "https://export.arxiv.org", "stream stalled"
                )
            ),
        ),
        _StreamingResponse(_VALID_EMPTY_ATOM),
    ]
    calls: list[dict[str, Any]] = []
    sleeps: list[float] = []

    def request(
        _self: requests.Session, _method: str, _url: str, **kwargs: Any
    ) -> requests.Response:
        calls.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr(requests.Session, "request", request)
    client = ArxivClient(
        delay_seconds=0,
        max_retries=1,
        retry_backoff_seconds=1,
        sleep=sleeps.append,
    )

    assert (
        client.search(
            query="cat:cs.AI",
            updated_from=datetime(2026, 1, 1, tzinfo=UTC),
            updated_until=datetime(2026, 1, 2, tzinfo=UTC),
            max_results=2,
        )
        == ()
    )
    assert len(calls) == 2
    assert all(call["stream"] is True for call in calls)
    assert sleeps == [1]


def test_download_pdf_requires_the_exact_https_arxiv_version_and_valid_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    content = b"%PDF-1.7\nvalidated arXiv PDF\n%%EOF"

    def request(
        _self: requests.Session, _method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        calls.append((url, kwargs))
        return _response(200, content=content, content_type="application/pdf")

    monkeypatch.setattr(requests.Session, "request", request)
    client = ArxivClient(max_retries=0, sleep=lambda _delay: None)
    result = client.download_pdf(
        canonical_arxiv_id="2601.01234",
        version=2,
        pdf_url="https://arxiv.org/pdf/2601.01234v2",
    )

    assert result.content == content
    assert result.version == 2
    assert calls[0][0] == "https://arxiv.org/pdf/2601.01234v2"
    assert calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["stream"] is True

    for invalid_url in (
        "http://arxiv.org/pdf/2601.01234v2",
        "https://export.arxiv.org/pdf/2601.01234v2",
        "https://arxiv.org/pdf/2601.01234v1",
        "https://arxiv.org/pdf/2601.01234v2?download=1",
    ):
        with pytest.raises(ArxivPdfError, match="canonical arXiv version"):
            client.download_pdf(
                canonical_arxiv_id="2601.01234",
                version=2,
                pdf_url=invalid_url,
            )
    assert len(calls) == 1


def test_download_pdf_retries_a_timeout_while_streaming_the_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    complete_pdf = b"%PDF-1.7\ncomplete retry\n%%EOF"
    responses: list[requests.Response] = [
        _StreamingResponse(
            b"%PDF-1.7\npartial",
            requests.exceptions.ConnectionError(
                Urllib3ReadTimeoutError(cast(Any, None), "https://arxiv.org", "stream stalled")
            ),
        ),
        _StreamingResponse(complete_pdf),
    ]
    calls = 0
    sleeps: list[float] = []

    def request(
        _self: requests.Session, _method: str, _url: str, **_kwargs: Any
    ) -> requests.Response:
        nonlocal calls
        calls += 1
        return responses.pop(0)

    monkeypatch.setattr(requests.Session, "request", request)
    result = ArxivClient(
        max_retries=1,
        retry_backoff_seconds=1,
        sleep=sleeps.append,
    ).download_pdf(
        canonical_arxiv_id="2601.01234",
        version=2,
        pdf_url="https://arxiv.org/pdf/2601.01234v2",
    )

    assert result.content == complete_pdf
    assert calls == 2
    assert sleeps == [1]


def test_download_pdf_relies_on_signature_and_actual_body_not_encoding_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _StreamingResponse(b"%PDF-1.7\ncompressed bytes")
    response.headers["Content-Encoding"] = "gzip"
    iterations = 0

    def iter_content(chunk_size: int | None = 1, decode_unicode: bool = False) -> Iterator[bytes]:
        nonlocal iterations
        del chunk_size, decode_unicode
        iterations += 1
        yield b"%PDF-1.7\ncompressed bytes"

    response.iter_content = iter_content  # type: ignore[method-assign]
    monkeypatch.setattr(
        requests.Session,
        "request",
        _request_returning(response),
    )

    pdf = ArxivClient(max_retries=0, sleep=lambda _delay: None).download_pdf(
        canonical_arxiv_id="2601.01234",
        version=2,
        pdf_url="https://arxiv.org/pdf/2601.01234v2",
    )
    assert pdf.content.startswith(b"%PDF-")
    assert iterations == 1


def test_download_pdf_checks_the_total_deadline_during_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_time = 0.0

    def advance_past_deadline() -> None:
        nonlocal current_time
        current_time = 6.0

    def request(
        _self: requests.Session, _method: str, _url: str, **_kwargs: Any
    ) -> requests.Response:
        return _StreamingResponse(
            b"%PDF-1.7\nslow stream\n%%EOF",
            on_chunk=advance_past_deadline,
        )

    monkeypatch.setattr(requests.Session, "request", request)
    client = ArxivClient(
        max_retries=0,
        request_timeout_seconds=5,
        max_total_seconds=5,
        sleep=lambda _delay: None,
        monotonic=lambda: current_time,
    )

    with pytest.raises(ArxivUnavailableError, match="Timeout"):
        client.download_pdf(
            canonical_arxiv_id="2601.01234",
            version=2,
            pdf_url="https://arxiv.org/pdf/2601.01234v2",
        )


@pytest.mark.parametrize(
    ("status_code", "content", "content_type", "content_length"),
    [
        (200, b"not a PDF", "application/pdf", None),
        (404, b"private response text", "text/plain", None),
    ],
)
def test_download_pdf_rejects_invalid_body_or_http_status(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    content: bytes,
    content_type: str,
    content_length: str | None,
) -> None:
    response = _response(
        status_code,
        content=content,
        content_type=content_type,
        content_length=content_length,
    )

    def request(
        _self: requests.Session, _method: str, _url: str, **_kwargs: Any
    ) -> requests.Response:
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    with pytest.raises(ArxivPdfError) as raised:
        ArxivClient(pdf_max_bytes=1024, max_retries=0, sleep=lambda _delay: None).download_pdf(
            canonical_arxiv_id="2601.01234",
            version=2,
            pdf_url="https://arxiv.org/pdf/2601.01234v2",
        )
    assert "private response text" not in str(raised.value)


@pytest.mark.parametrize(
    ("content_type", "content_length", "content_encoding"),
    [
        ("text/html", None, None),
        ("application/pdf", "-1", None),
        ("application/pdf", "999999999", "gzip"),
    ],
)
def test_download_pdf_ignores_unreliable_transport_headers(
    monkeypatch: pytest.MonkeyPatch,
    content_type: str,
    content_length: str | None,
    content_encoding: str | None,
) -> None:
    response = _response(
        200,
        content=b"%PDF-1.7\nvalid bytes\n%%EOF",
        content_type=content_type,
        content_length=content_length,
    )
    if content_encoding is not None:
        response.headers["Content-Encoding"] = content_encoding
    monkeypatch.setattr(requests.Session, "request", _request_returning(response))

    pdf = ArxivClient(pdf_max_bytes=1024, max_retries=0, sleep=lambda _delay: None).download_pdf(
        canonical_arxiv_id="2601.01234",
        version=2,
        pdf_url="https://arxiv.org/pdf/2601.01234v2",
    )

    assert pdf.content.startswith(b"%PDF-")


def test_download_pdf_enforces_the_actual_streamed_size_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _StreamingResponse(b"%PDF-1.7\n" + b"x" * 1024)
    monkeypatch.setattr(requests.Session, "request", _request_returning(response))

    with pytest.raises(ArxivPdfError, match="size bound"):
        ArxivClient(pdf_max_bytes=1024, max_retries=0, sleep=lambda _delay: None).download_pdf(
            canonical_arxiv_id="2601.01234",
            version=2,
            pdf_url="https://arxiv.org/pdf/2601.01234v2",
        )


def _response(
    status_code: int,
    *,
    retry_after: str | None = None,
    content: bytes = b"",
    content_type: str | None = None,
    content_length: str | None = None,
) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = content
    setattr(response, "_content_consumed", True)  # noqa: B010
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    if content_type is not None:
        response.headers["Content-Type"] = content_type
    if content_length is not None:
        response.headers["Content-Length"] = content_length
    return response


def _request_returning(
    response: requests.Response,
) -> Callable[..., requests.Response]:
    def request(
        _self: requests.Session,
        _method: str,
        _url: str,
        **_kwargs: Any,
    ) -> requests.Response:
        return response

    return request


class _StreamingResponse(requests.Response):
    def __init__(
        self,
        *chunks: bytes | Exception,
        on_chunk: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.status_code = 200
        self.headers["Content-Type"] = "application/pdf"
        self._chunks = chunks
        self._on_chunk = on_chunk
        setattr(self, "_content_consumed", True)  # noqa: B010

    def iter_content(
        self, chunk_size: int | None = 1, decode_unicode: bool = False
    ) -> Iterator[bytes]:
        del chunk_size, decode_unicode
        for chunk in self._chunks:
            if self._on_chunk is not None:
                self._on_chunk()
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk
