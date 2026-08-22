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


def _search_page_payload() -> dict[str, Any]:
    return json.loads(
        (FIXTURES / "semantic_scholar_search_page_1.json").read_text(encoding="utf-8")
    )


def _relation_page_payload(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


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


@pytest.mark.parametrize(
    "total",
    ["3", 3, "9223372036854775807"],
    ids=["documented-string", "legacy-integer", "maximum-bounded-string"],
)
def test_search_normalizes_documented_total_without_weakening_integer_compatibility(
    total: str | int,
) -> None:
    payload = _search_page_payload()
    payload["total"] = total

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).search_papers("agent planning", 2025, 2026, 2)

    assert len(papers) == 2


@pytest.mark.parametrize(
    "total",
    [
        "",
        "-1",
        "+1",
        "01",
        "1.0",
        " 3",
        "3 ",
        "not-a-count",
        "9223372036854775808",
        True,
        -1,
        None,
    ],
)
def test_search_rejects_noncanonical_or_unbounded_totals(total: object) -> None:
    payload = _search_page_payload()
    payload["total"] = total

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    with pytest.raises(ScholarlySearchResponseError, match=r"\(total:"):
        _client(handler).search_papers("agent planning", 2025, 2026, 2)


def test_schema_error_diagnostic_is_bounded_and_excludes_provider_values() -> None:
    payload = _search_page_payload()
    payload.update(
        {
            "total": "provider-total-secret",
            "offset": "provider-offset-secret",
            "next": "provider-next-secret",
            "data": "provider-data-secret",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    with pytest.raises(ScholarlySearchResponseError) as raised:
        _client(handler).search_papers("agent planning", 2025, 2026, 2)

    message = str(raised.value)
    assert message.endswith(
        "for operation=search (total:value_error, offset:int_type, next:int_type)"
    )
    assert "agent planning" not in message
    assert "provider-" not in message


def test_unknown_provider_metadata_does_not_invalidate_a_paper() -> None:
    payload = _paper_payload()
    payload["provider-secret-field"] = "provider-secret-value"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    paper = _client(handler).get_paper(PAPER_ID)

    assert paper.semantic_scholar_id == PAPER_ID


@pytest.mark.parametrize(
    ("venue", "expected"),
    [
        (None, None),
        ("", None),
        (" \t ", None),
        ("Agent Research Workshop", "Agent Research Workshop"),
        ("  Agent Research Workshop \t", "Agent Research Workshop"),
    ],
    ids=["null", "empty", "whitespace", "unchanged", "trimmed"],
)
def test_search_normalizes_optional_provider_venue(
    venue: str | None,
    expected: str | None,
) -> None:
    payload = _search_page_payload()
    payload["data"][0]["venue"] = venue
    payload["next"] = None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).search_papers("agent planning", 2025, 2026, 2)

    assert papers[0].venue == expected


def test_search_treats_omitted_provider_venue_as_missing_metadata() -> None:
    payload = _search_page_payload()
    del payload["data"][0]["venue"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).search_papers("agent planning", 2025, 2026, 2)

    assert papers[0].venue is None


@pytest.mark.parametrize(
    "venue",
    [1, [], "x" * 1001],
    ids=["non-string", "array", "oversized"],
)
def test_search_omits_only_the_candidate_with_invalid_optional_text(venue: object) -> None:
    payload = _search_page_payload()
    payload["data"][0]["venue"] = venue
    payload["next"] = None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).search_papers("agent planning", 2025, 2026, 2)

    assert [paper.semantic_scholar_id for paper in papers] == ["2" * 40]


def test_search_validates_and_discards_official_open_access_pdf_object_and_null() -> None:
    payload = _search_page_payload()
    payload_without_open_access_pdf = _search_page_payload()
    for item in payload_without_open_access_pdf["data"]:
        item.pop("openAccessPdf", None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    def baseline_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload_without_open_access_pdf, request=request)

    papers = _client(handler).search_papers("agent planning", 2025, 2026, 2)
    baseline = _client(baseline_handler).search_papers("agent planning", 2025, 2026, 2)

    assert payload["data"][0]["openAccessPdf"] == {
        "url": "https://arxiv.org/pdf/2601.01234",
        "status": "GREEN",
    }
    assert payload["data"][1]["openAccessPdf"] is None
    assert papers == baseline
    assert all(not hasattr(paper, "open_access_pdf") for paper in papers)


def test_search_open_access_pdf_accepts_bounded_optional_license_and_disclaimer() -> None:
    payload = _search_page_payload()
    payload["data"][0]["openAccessPdf"] = {
        "url": "https://example.org/paper.pdf",
        "status": "HYBRID",
        "license": "CCBY",
        "disclaimer": "Verify license details at the source.",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).search_papers("agent planning", 2025, 2026, 2)

    assert papers[0].semantic_scholar_id == PAPER_ID


@pytest.mark.parametrize("status", [None, "GREEN"], ids=["null-status", "known-status"])
def test_search_open_access_pdf_accepts_provider_unavailable_pdf_sentinel(
    status: str | None,
) -> None:
    payload = _search_page_payload()
    baseline_payload = _search_page_payload()
    payload["data"][0]["openAccessPdf"] = {"url": "", "status": status}
    baseline_payload["data"][0].pop("openAccessPdf")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    def baseline_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=baseline_payload, request=request)

    papers = _client(handler).search_papers("agent planning", 2025, 2026, 2)
    baseline = _client(baseline_handler).search_papers("agent planning", 2025, 2026, 2)

    assert papers == baseline
    assert all(not hasattr(paper, "open_access_pdf") for paper in papers)


@pytest.mark.parametrize(
    "open_access_pdf",
    [
        "not-an-object",
        [],
        {},
        {"url": "https://example.org/paper.pdf"},
        {"status": "GREEN"},
        {"url": None, "status": "GREEN"},
        {"url": " ", "status": "GREEN"},
        {
            "url": "https://example.org/paper.pdf",
            "status": "GREEN",
            "unexpected": True,
        },
    ],
)
def test_search_ignores_unconsumed_open_access_pdf_metadata(
    open_access_pdf: object,
) -> None:
    payload = _search_page_payload()
    payload["data"][0]["openAccessPdf"] = open_access_pdf

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).search_papers("agent planning", 2025, 2026, 2)

    assert [paper.semantic_scholar_id for paper in papers] == [PAPER_ID, "2" * 40]


@pytest.mark.parametrize("operation", ["paper", "references", "recommendations"])
def test_unrequested_open_access_pdf_metadata_is_ignored(operation: str) -> None:
    open_access_pdf = {
        "url": "https://example.org/paper.pdf",
        "status": "GREEN",
    }
    if operation == "paper":
        payload = _paper_payload()
        payload["openAccessPdf"] = open_access_pdf
    elif operation == "references":
        payload = _relation_page_payload("semantic_scholar_references.json")
        payload["data"][0]["citedPaper"]["openAccessPdf"] = open_access_pdf
    else:
        payload = _relation_page_payload("semantic_scholar_recommendations.json")
        payload["recommendedPapers"][0]["openAccessPdf"] = open_access_pdf

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    client = _client(handler)
    if operation == "paper":
        papers = (client.get_paper(PAPER_ID),)
    elif operation == "references":
        papers = client.get_references(PAPER_ID)
    else:
        papers = client.get_recommendations((PAPER_ID,))

    assert papers


@pytest.mark.parametrize(
    ("fixture_name", "operation", "expected_paper_id"),
    [
        (
            "semantic_scholar_references.json",
            "get_references",
            "2222222222222222222222222222222222222222",
        ),
        (
            "semantic_scholar_citations.json",
            "get_citations",
            "3333333333333333333333333333333333333333",
        ),
    ],
)
def test_official_relation_envelopes_are_validated_but_not_exposed(
    fixture_name: str,
    operation: str,
    expected_paper_id: str,
) -> None:
    payload = _relation_page_payload(fixture_name)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = getattr(_client(handler), operation)(PAPER_ID)

    assert [paper.semantic_scholar_id for paper in papers] == [expected_paper_id]
    assert not hasattr(papers[0], "contexts")
    assert not hasattr(papers[0], "intents")


def test_relation_envelope_fields_may_be_omitted() -> None:
    payload = _relation_page_payload("semantic_scholar_references.json")
    for field in ("contexts", "intents", "contextsWithIntent", "isInfluential"):
        del payload["data"][0][field]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).get_references(PAPER_ID)

    assert papers[0].semantic_scholar_id == "2222222222222222222222222222222222222222"


@pytest.mark.parametrize(
    ("fixture_name", "operation", "paper_key", "expected_id"),
    [
        ("semantic_scholar_references.json", "get_references", "citedPaper", "2" * 40),
        ("semantic_scholar_citations.json", "get_citations", "citingPaper", "3" * 40),
    ],
)
def test_relation_collection_omits_only_a_malformed_candidate(
    fixture_name: str,
    operation: str,
    paper_key: str,
    expected_id: str,
) -> None:
    payload = _relation_page_payload(fixture_name)
    payload["data"].insert(0, {paper_key: {"paperId": "", "title": ""}})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = getattr(_client(handler), operation)(PAPER_ID)

    assert [paper.semantic_scholar_id for paper in papers] == [expected_id]


def test_recommendations_omit_only_a_malformed_candidate() -> None:
    payload = _relation_page_payload("semantic_scholar_recommendations.json")
    payload["recommendedPapers"][0]["title"] = " "

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).get_recommendations((PAPER_ID,))

    assert [paper.semantic_scholar_id for paper in papers] == ["3" * 40]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contexts", "not-an-array"),
        ("intents", "not-an-array"),
        ("contextsWithIntent", "not-an-array"),
        ("isInfluential", "false"),
        ("isInfluential", None),
    ],
)
def test_unconsumed_relation_metadata_does_not_invalidate_the_paper(
    field: str,
    value: object,
) -> None:
    payload = _relation_page_payload("semantic_scholar_references.json")
    payload["data"][0][field] = value

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).get_references(PAPER_ID)

    assert [paper.semantic_scholar_id for paper in papers] == ["2" * 40]


@pytest.mark.parametrize(
    "contexts_with_intent",
    [
        ["not-an-object"],
        [{"intents": ["methodology"]}],
        [{"context": "context", "intents": "methodology"}],
    ],
)
def test_unconsumed_nested_relation_metadata_is_dropped(
    contexts_with_intent: object,
) -> None:
    payload = _relation_page_payload("semantic_scholar_citations.json")
    payload["data"][0]["contextsWithIntent"] = contexts_with_intent

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).get_citations(PAPER_ID)

    assert [paper.semantic_scholar_id for paper in papers] == ["3" * 40]


def test_relation_envelope_ignores_unknown_fields() -> None:
    payload = _relation_page_payload("semantic_scholar_references.json")
    payload["data"][0]["unexpected"] = True

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    assert _client(handler).get_references(PAPER_ID)


def test_search_ignores_relation_metadata_not_used_by_the_port() -> None:
    payload = _search_page_payload()
    payload["data"][0]["contexts"] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    assert _client(handler).search_papers("agent planning", 2025, 2026, 2)


@pytest.mark.parametrize("case", ["missing_optional_count", "extra", "invalid_arxiv"])
def test_optional_or_unknown_paper_metadata_does_not_lose_the_paper(case: str) -> None:
    payload = _paper_payload()
    if case == "missing_optional_count":
        del payload["referenceCount"]
    elif case == "extra":
        payload["unexpected"] = True
    else:
        payload["externalIds"] = {"ArXiv": "invalid"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    paper = _client(handler).get_paper(PAPER_ID)

    assert paper.semantic_scholar_id == PAPER_ID
    if case == "invalid_arxiv":
        assert paper.external_ids.arxiv_id is None
        assert ("ArXiv", "invalid") not in paper.external_ids.values


def test_direct_paper_still_rejects_an_invalid_required_field_type() -> None:
    payload = _paper_payload()
    payload["citationCount"] = "12"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    with pytest.raises(ScholarlySearchResponseError):
        _client(handler).get_paper(PAPER_ID)


@pytest.mark.parametrize("provider_url", [None, 42, "https://example.invalid/paper"])
def test_paper_url_is_derived_from_the_validated_identity(provider_url: object) -> None:
    payload = _paper_payload()
    payload["url"] = provider_url

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    paper = _client(handler).get_paper(PAPER_ID)

    assert paper.url == f"https://www.semanticscholar.org/paper/{PAPER_ID}"


@pytest.mark.parametrize("field_name", ["paperId", "title"])
def test_direct_paper_required_identity_fields_remain_strict(field_name: str) -> None:
    payload = _paper_payload()
    payload[field_name] = "   "

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    with pytest.raises(ScholarlySearchResponseError):
        _client(handler).get_paper(PAPER_ID)


@pytest.mark.parametrize("case", ["invalid_json", "oversized"])
def test_invalid_json_and_actual_size_are_rejected(case: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if case == "invalid_json":
            return httpx.Response(
                200,
                content=b"not-json",
                headers={"Content-Type": "application/json"},
                request=request,
            )
        return httpx.Response(
            200,
            content=b"{" + (b" " * 2048) + b"}",
            headers={"Content-Type": "application/json"},
            request=request,
        )

    with pytest.raises(ScholarlySearchResponseError, match="Semantic Scholar"):
        _client(handler, max_response_bytes=1024).get_paper(PAPER_ID)


def test_valid_json_is_not_rejected_for_transport_presentation_headers() -> None:
    encoded = json.dumps(_paper_payload()).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=encoded,
            headers={
                "Content-Type": "text/plain",
                "Content-Encoding": "identity",
                "Content-Length": str(len(encoded) + 1000),
            },
            request=request,
        )

    paper = _client(handler).get_paper(PAPER_ID)

    assert paper.semantic_scholar_id == PAPER_ID


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
