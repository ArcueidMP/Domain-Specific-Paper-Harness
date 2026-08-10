from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx

from paper_harness.adapters.http_retry import HttpRetryPolicy
from paper_harness.adapters.semantic_scholar import (
    SemanticScholarClient,
    SemanticScholarSettings,
)

FIXTURES = Path(__file__).parent / "fixtures"
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


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


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
    client = SemanticScholarClient(
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
