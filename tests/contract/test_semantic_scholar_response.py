from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from paper_harness.adapters.http_retry import HttpRetryPolicy
from paper_harness.adapters.semantic_scholar import (
    SemanticScholarClient,
    SemanticScholarSettings,
)

FIXTURES = Path(__file__).parent / "fixtures"
PAPER_ID = "1111111111111111111111111111111111111111"
_MISSING = object()


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.value += delay


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    fake_time: FakeTime | None = None,
) -> SemanticScholarClient:
    fake_time = fake_time or FakeTime()
    return SemanticScholarClient(
        SemanticScholarSettings(api_key="fixture-api-key"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry_policy=HttpRetryPolicy(
            max_retries=0,
            request_timeout_seconds=5,
            total_timeout_seconds=30,
            backoff_seconds=1,
            max_retry_after_seconds=5,
        ),
        page_size=2,
        max_relation_results=2,
        recommendation_limit=2,
        monotonic=fake_time.monotonic,
        sleep=fake_time.sleep,
        clock=lambda: datetime(2026, 1, 20, tzinfo=UTC),
    )


def test_official_semantic_scholar_fixture_contracts_and_request_shapes() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        query = parse_qs(request.url.query.decode("ascii"))
        if path == "/graph/v1/paper/search":
            fixture = (
                "semantic_scholar_search_page_1.json"
                if query["offset"] == ["0"]
                else "semantic_scholar_search_page_2.json"
            )
        elif path in {
            f"/graph/v1/paper/{PAPER_ID}",
            "/graph/v1/paper/ARXIV:2601.01234",
        }:
            fixture = "semantic_scholar_paper.json"
        elif path == f"/graph/v1/paper/{PAPER_ID}/references":
            fixture = "semantic_scholar_references.json"
        elif path == f"/graph/v1/paper/{PAPER_ID}/citations":
            fixture = "semantic_scholar_citations.json"
        elif path == "/recommendations/v1/papers/":
            fixture = "semantic_scholar_recommendations.json"
        else:
            raise AssertionError(f"unexpected Semantic Scholar request: {request.url}")
        return httpx.Response(200, json=_fixture(fixture), request=request)

    fake_time = FakeTime()
    client = _client(handler, fake_time=fake_time)

    search = client.search_papers("language model agents", 2025, 2026, 3)
    paper = client.get_paper(PAPER_ID)
    arxiv_paper = client.get_paper_by_arxiv_id("2601.01234")
    references = client.get_references(PAPER_ID)
    citations = client.get_citations(PAPER_ID)
    recommendations = client.get_recommendations((PAPER_ID,))

    assert [item.semantic_scholar_id for item in search] == [
        "1111111111111111111111111111111111111111",
        "2222222222222222222222222222222222222222",
        "3333333333333333333333333333333333333333",
    ]
    assert paper.external_ids.arxiv_id == "2601.01234"
    assert paper.external_ids.doi == "10.1000/agent.1"
    assert ("CorpusId", "101") in paper.external_ids.values
    assert paper.influential_citation_count == 4
    assert paper.authors[0].name == "Ada North"
    assert arxiv_paper.semantic_scholar_id == paper.semantic_scholar_id
    assert references[0].semantic_scholar_id.startswith("2")
    assert citations[0].semantic_scholar_id.startswith("3")
    assert [item.semantic_scholar_id for item in recommendations] == [
        references[0].semantic_scholar_id,
        citations[0].semantic_scholar_id,
    ]

    assert len(requests) == 7
    assert all(request.headers["x-api-key"] == "fixture-api-key" for request in requests)
    search_queries = [
        parse_qs(request.url.query.decode("ascii"))
        for request in requests
        if request.url.path == "/graph/v1/paper/search"
    ]
    assert search_queries[0]["query"] == ["language model agents"]
    assert search_queries[0]["year"] == ["2025-2026"]
    assert search_queries[0]["limit"] == ["2"]
    assert search_queries[1]["offset"] == ["2"]
    recommendation_request = requests[-1]
    assert recommendation_request.method == "POST"
    assert json.loads(recommendation_request.content) == {
        "positivePaperIds": [PAPER_ID],
        "negativePaperIds": [],
    }
    assert fake_time.sleeps == [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]


@pytest.mark.parametrize("field_name", ["abstract", "venue"])
@pytest.mark.parametrize(
    ("provider_value", "expected"),
    [
        pytest.param(_MISSING, None, id="missing"),
        pytest.param(None, None, id="null"),
        pytest.param("", None, id="empty"),
        pytest.param(" \t ", None, id="whitespace"),
        pytest.param("  optional metadata \t", "optional metadata", id="trimmed"),
    ],
)
def test_optional_paper_text_metadata_is_normalized_at_the_response_boundary(
    field_name: str,
    provider_value: object,
    expected: str | None,
) -> None:
    payload = _fixture("semantic_scholar_search_page_1.json")
    paper_payload = payload["data"][0]
    if provider_value is _MISSING:
        paper_payload.pop(field_name)
    else:
        paper_payload[field_name] = provider_value

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    paper = _client(handler).search_papers("language model agents", 2025, 2026, 2)[0]

    assert getattr(paper, field_name) == expected


@pytest.mark.parametrize("field_name", ["abstract", "venue"])
@pytest.mark.parametrize("provider_value", [1, [], {}], ids=["integer", "array", "object"])
def test_optional_paper_text_metadata_invalid_on_one_candidate_does_not_lose_the_page(
    field_name: str,
    provider_value: object,
) -> None:
    payload = _fixture("semantic_scholar_search_page_1.json")
    payload["data"][0][field_name] = provider_value
    payload["next"] = None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).search_papers("language model agents", 2025, 2026, 2)

    assert [paper.semantic_scholar_id for paper in papers] == ["2" * 40]


@pytest.mark.parametrize("field_name", ["status", "license", "disclaimer"])
@pytest.mark.parametrize(
    "provider_value",
    [
        pytest.param(None, id="null"),
        pytest.param("", id="empty"),
        pytest.param(" \t ", id="whitespace"),
        pytest.param("  optional metadata \t", id="trimmed"),
    ],
)
def test_optional_open_access_text_metadata_uses_the_same_normalization_contract(
    field_name: str,
    provider_value: str | None,
) -> None:
    payload = _fixture("semantic_scholar_search_page_1.json")
    open_access_pdf = payload["data"][0]["openAccessPdf"]
    open_access_pdf[field_name] = provider_value

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).search_papers("language model agents", 2025, 2026, 2)

    assert papers[0].semantic_scholar_id == PAPER_ID


@pytest.mark.parametrize("field_name", ["status", "license", "disclaimer"])
@pytest.mark.parametrize("provider_value", [1, [], {}], ids=["integer", "array", "object"])
def test_unconsumed_open_access_text_metadata_does_not_invalidate_the_paper(
    field_name: str,
    provider_value: object,
) -> None:
    payload = _fixture("semantic_scholar_search_page_1.json")
    payload["data"][0]["openAccessPdf"][field_name] = provider_value

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).search_papers("language model agents", 2025, 2026, 2)

    assert [paper.semantic_scholar_id for paper in papers] == [PAPER_ID, "2" * 40]


@pytest.mark.parametrize("field_name", ["license", "disclaimer"])
def test_omitted_optional_open_access_text_metadata_remains_valid(field_name: str) -> None:
    payload = _fixture("semantic_scholar_search_page_1.json")
    payload["data"][0]["openAccessPdf"].pop(field_name, None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).search_papers("language model agents", 2025, 2026, 2)

    assert papers[0].semantic_scholar_id == PAPER_ID


@pytest.mark.parametrize("field_name", ["title", "paperId"])
@pytest.mark.parametrize("provider_value", ["", " \t "], ids=["empty", "whitespace"])
def test_required_paper_identity_text_remains_strict(
    field_name: str,
    provider_value: str,
) -> None:
    payload = _fixture("semantic_scholar_search_page_1.json")
    payload["data"][0][field_name] = provider_value
    payload["next"] = None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    papers = _client(handler).search_papers("language model agents", 2025, 2026, 2)

    assert [paper.semantic_scholar_id for paper in papers] == ["2" * 40]
