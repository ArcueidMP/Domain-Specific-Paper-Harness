from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_harness.adapters.http_retry import HttpRetryPolicy
from paper_harness.adapters.semantic_scholar import (
    SemanticScholarClient,
    SemanticScholarSettings,
)
from paper_harness.ports.scholarly_search import (
    ScholarlyPaperNotFoundError,
    ScholarlySearchAuthenticationError,
    ScholarlySearchConfigurationError,
    ScholarlySearchRequestError,
    ScholarlySearchResponseError,
    ScholarlySearchUnavailableError,
)

FIXTURES = Path(__file__).parents[1] / "contract" / "fixtures"
PAPER_ID = "1111111111111111111111111111111111111111"


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.value += delay


def _paper_payload() -> dict[str, Any]:
    return json.loads((FIXTURES / "semantic_scholar_paper.json").read_text(encoding="utf-8"))


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    fake_time: FakeTime | None = None,
    max_retries: int = 0,
    max_response_bytes: int = 5 * 1024 * 1024,
) -> SemanticScholarClient:
    fake_time = fake_time or FakeTime()
    return SemanticScholarClient(
        SemanticScholarSettings(api_key="unit-test-key"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry_policy=HttpRetryPolicy(
            max_retries=max_retries,
            request_timeout_seconds=5,
            total_timeout_seconds=30,
            backoff_seconds=1,
            max_retry_after_seconds=5,
        ),
        max_response_bytes=max_response_bytes,
        monotonic=fake_time.monotonic,
        sleep=fake_time.sleep,
        clock=lambda: datetime(2026, 1, 20, tzinfo=UTC),
    )


def test_settings_require_an_explicit_non_blank_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for value in (None, "", "   ", "key with spaces"):
        if value is None:
            monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
        else:
            monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", value)
        with pytest.raises(ScholarlySearchConfigurationError):
            SemanticScholarSettings.from_environment()

    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "configured-test-key")
    assert SemanticScholarSettings.from_environment().api_key == "configured-test-key"


def test_get_paper_sends_the_required_key_and_requested_external_id_fields() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_paper_payload(), request=request)

    paper = _client(handler).get_paper(PAPER_ID.upper())

    assert paper.semantic_scholar_id == PAPER_ID
    assert requests[0].url.path == f"/graph/v1/paper/{PAPER_ID}"
    assert requests[0].headers["x-api-key"] == "unit-test-key"
    assert "externalIds" in requests[0].url.params["fields"]


def test_get_paper_by_arxiv_id_uses_the_official_identifier_form() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_paper_payload(), request=request)

    paper = _client(handler).get_paper_by_arxiv_id("2601.01234")

    assert paper.semantic_scholar_id == PAPER_ID
    assert requests[0].url.path == "/graph/v1/paper/ARXIV:2601.01234"
    assert requests[0].headers["x-api-key"] == "unit-test-key"


def test_get_paper_by_arxiv_id_safely_encodes_a_legacy_canonical_id() -> None:
    requests: list[httpx.Request] = []
    payload = _paper_payload()
    payload["externalIds"]["ArXiv"] = "hep-th/9901001"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload, request=request)

    paper = _client(handler).get_paper_by_arxiv_id("hep-th/9901001")

    assert paper.external_ids.arxiv_id == "hep-th/9901001"
    assert requests[0].url.raw_path.startswith(b"/graph/v1/paper/ARXIV:hep-th%2F9901001?")


def test_get_paper_by_arxiv_id_rejects_a_mismatched_provider_identity() -> None:
    payload = _paper_payload()
    payload["externalIds"]["ArXiv"] = "2601.99999"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    with pytest.raises(ScholarlySearchResponseError, match="different external identity"):
        _client(handler).get_paper_by_arxiv_id("2601.01234")


def test_get_paper_by_arxiv_id_maps_not_found_without_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, request=request)

    with pytest.raises(ScholarlyPaperNotFoundError):
        _client(handler, max_retries=2).get_paper_by_arxiv_id("2601.01234")

    assert calls == 1


@pytest.mark.parametrize("status_code", [429, 500, 502, 503])
def test_transient_statuses_retry_only_the_same_operation(status_code: int) -> None:
    calls = 0
    fake_time = FakeTime()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                status_code,
                headers={"Retry-After": "1"},
                request=request,
            )
        return httpx.Response(200, json=_paper_payload(), request=request)

    paper = _client(handler, fake_time=fake_time, max_retries=1).get_paper(PAPER_ID)

    assert paper.semantic_scholar_id == PAPER_ID
    assert calls == 2
    assert fake_time.sleeps == [1.0]


def test_timeout_retries_but_other_transport_errors_do_not() -> None:
    timeout_calls = 0

    def timeout_once(request: httpx.Request) -> httpx.Response:
        nonlocal timeout_calls
        timeout_calls += 1
        if timeout_calls == 1:
            raise httpx.ReadTimeout("fixture timeout", request=request)
        return httpx.Response(200, json=_paper_payload(), request=request)

    assert _client(timeout_once, max_retries=1).get_paper(PAPER_ID).semantic_scholar_id == PAPER_ID
    assert timeout_calls == 2

    transport_calls = 0

    def connect_failure(request: httpx.Request) -> httpx.Response:
        nonlocal transport_calls
        transport_calls += 1
        raise httpx.ConnectError("fixture connection failure", request=request)

    with pytest.raises(ScholarlySearchUnavailableError):
        _client(connect_failure, max_retries=2).get_paper(PAPER_ID)
    assert transport_calls == 1


def test_exhausted_transient_response_has_a_stable_retryable_error() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, request=request)

    with pytest.raises(ScholarlySearchUnavailableError) as raised:
        _client(handler, max_retries=1).get_paper(PAPER_ID)

    assert calls == 2
    assert raised.value.error_code == "SCHOLARLY_SEARCH_UNAVAILABLE"
    assert raised.value.retryable is True


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (400, ScholarlySearchRequestError),
        (401, ScholarlySearchAuthenticationError),
        (403, ScholarlySearchAuthenticationError),
        (404, ScholarlyPaperNotFoundError),
        (422, ScholarlySearchRequestError),
    ],
)
def test_non_retryable_statuses_fail_once(status_code: int, error_type: type[Exception]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, request=request)

    with pytest.raises(error_type):
        _client(handler, max_retries=2).get_paper(PAPER_ID)
    assert calls == 1


@pytest.mark.parametrize("case", ["missing", "wrong_type", "extra", "invalid_arxiv"])
def test_malformed_or_domain_invalid_paper_responses_are_rejected(case: str) -> None:
    payload = _paper_payload()
    if case == "missing":
        del payload["referenceCount"]
    elif case == "wrong_type":
        payload["citationCount"] = "12"
    elif case == "extra":
        payload["unexpected"] = True
    else:
        payload["externalIds"] = {"ArXiv": "invalid"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    with pytest.raises(ScholarlySearchResponseError):
        _client(handler).get_paper(PAPER_ID)


@pytest.mark.parametrize("case", ["invalid_json", "content_type", "oversized"])
def test_invalid_json_content_type_and_size_are_rejected(case: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if case == "invalid_json":
            return httpx.Response(
                200,
                content=b"not-json",
                headers={"Content-Type": "application/json"},
                request=request,
            )
        if case == "content_type":
            return httpx.Response(
                200,
                content=b"{}",
                headers={"Content-Type": "text/html"},
                request=request,
            )
        return httpx.Response(
            200,
            content=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "2048"},
            request=request,
        )

    with pytest.raises(ScholarlySearchResponseError, match="Semantic Scholar"):
        _client(handler, max_response_bytes=1024).get_paper(PAPER_ID)


def test_invalid_inputs_and_recommendation_bounds_fail_before_http() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, request=request)

    client = _client(handler)
    invalid_calls = (
        lambda: client.search_papers("", 2025, 2026, 10),
        lambda: client.search_papers("agents\nignore", 2025, 2026, 10),
        lambda: client.search_papers("agents", 2027, 2026, 10),
        lambda: client.search_papers("agents", 2025, 2026, 501),
        lambda: client.get_paper("not-a-paper-id"),
        lambda: client.get_paper_by_arxiv_id(PAPER_ID),
        lambda: client.get_paper_by_arxiv_id("2601.01234v1"),
        lambda: client.get_paper_by_arxiv_id(" 2601.01234"),
        lambda: client.get_recommendations(()),
        lambda: client.get_recommendations((PAPER_ID, PAPER_ID)),
    )
    for operation in invalid_calls:
        with pytest.raises(ScholarlySearchRequestError):
            operation()
    assert calls == 0


def test_rate_limiter_uses_injected_monotonic_clock_and_sleep() -> None:
    fake_time = FakeTime()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_paper_payload(), request=request)

    client = _client(handler, fake_time=fake_time)
    client.get_paper(PAPER_ID)
    client.get_paper(PAPER_ID)

    assert fake_time.sleeps == [1.0]


def test_rate_limit_wait_is_subtracted_from_the_supplied_request_budget() -> None:
    fake_time = FakeTime()
    request_timeouts: list[dict[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, json=_paper_payload(), request=request)

    client = _client(handler, fake_time=fake_time)
    client.get_paper(PAPER_ID)
    client.get_paper(PAPER_ID, timeout_seconds=1.5)

    assert fake_time.sleeps == [1.0]
    assert request_timeouts[1] == {
        "connect": 0.5,
        "read": 0.5,
        "write": 0.5,
        "pool": 0.5,
    }


def test_pagination_shares_one_total_operation_deadline() -> None:
    fake_time = FakeTime()
    page = json.loads(
        (FIXTURES / "semantic_scholar_search_page_1.json").read_text(encoding="utf-8")
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        fake_time.value += 29.5
        return httpx.Response(200, json=page, request=request)

    with pytest.raises(ScholarlySearchUnavailableError, match="total timeout"):
        _client(handler, fake_time=fake_time).search_papers("agent planning", 2025, 2026, 3)

    assert calls == 1


def test_supplied_timeout_bounds_http_request_and_shared_pagination_deadline() -> None:
    fake_time = FakeTime()
    page = json.loads(
        (FIXTURES / "semantic_scholar_search_page_1.json").read_text(encoding="utf-8")
    )
    request_timeouts: list[dict[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_timeouts.append(request.extensions["timeout"])
        fake_time.value += 1.5
        return httpx.Response(200, json=page, request=request)

    with pytest.raises(ScholarlySearchUnavailableError, match="total timeout"):
        _client(handler, fake_time=fake_time).search_papers(
            "agent planning",
            2025,
            2026,
            3,
            timeout_seconds=2,
        )

    assert len(request_timeouts) == 1
    assert request_timeouts[0] == {
        "connect": 2.0,
        "read": 2.0,
        "write": 2.0,
        "pool": 2.0,
    }


@pytest.mark.parametrize("timeout_seconds", [0.0, -1.0, float("nan")])
def test_invalid_supplied_timeout_fails_before_http(timeout_seconds: float) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_paper_payload(), request=request)

    with pytest.raises(ScholarlySearchUnavailableError, match="remaining timeout budget"):
        _client(handler).get_paper(PAPER_ID, timeout_seconds=timeout_seconds)

    assert calls == 0
