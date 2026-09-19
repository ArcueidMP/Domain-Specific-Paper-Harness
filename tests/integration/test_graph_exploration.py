# pyright: reportPrivateUsage=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

"""Published graph search and connected sampling against real PostgreSQL."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid5

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_m4_postgres_repository import NOW, _prepare_complete_source

from paper_harness.adapters.postgres import PostgresRepository
from paper_harness.application.publish_product import PublishProduct
from paper_harness.domain.knowledge import GraphEntityType
from paper_harness.domain.models import TopicConfig
from paper_harness.domain.reports import ReportNarrativeMode
from paper_harness.entrypoints.api import create_app
from paper_harness.ports.arxiv import ArxivPaperRecord

pytestmark = pytest.mark.integration


def test_graph_search_covers_published_nodes_outside_connected_canvas(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    _, logical_date = _prepare_complete_source(postgres_repository, topic_config, arxiv_record_v1)
    client = TestClient(create_app(postgres_repository))
    params = {"topic": topic_config.slug, "q": arxiv_record_v1.canonical_arxiv_id}
    assert client.get("/api/v1/graph/search", params=params).json()["total"] == 0
    PublishProduct(
        repository=postgres_repository, llm=None, clock=lambda: NOW + timedelta(days=1, minutes=10)
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
    )
    all_nodes = client.get("/api/v1/graph", params={"topic": topic_config.slug}).json()["nodes"]
    for budget in (2, 3, 4):
        graph = client.get(
            "/api/v1/graph",
            params={
                "topic": topic_config.slug,
                "max_nodes": budget,
                "max_edges": 1,
            },
        ).json()
        assert 0 < len(graph["nodes"]) <= budget
        assert len(graph["edges"]) == 1
        ids = {node["id"] for node in graph["nodes"]}
        assert all(
            edge["source_entity_id"] in ids and edge["target_entity_id"] in ids
            for edge in graph["edges"]
        )
    outside = next(node for node in all_nodes if node["id"] not in ids)
    matches = client.get(
        "/api/v1/graph/search",
        params={
            "topic": topic_config.slug,
            "q": outside["display_label"][:100],
        },
    ).json()
    assert outside["id"] in {node["id"] for node in matches["items"]}
    focused = client.get(
        "/api/v1/graph",
        params={
            "topic": topic_config.slug,
            "entity_id": outside["id"],
            "max_nodes": 2,
        },
    ).json()
    assert outside["id"] in {node["id"] for node in focused["nodes"]}
    assert focused["edges"]
    papers = client.get("/api/v1/graph/search", params={**params, "entity_type": "PAPER"}).json()
    assert papers["total"] == 1
    assert papers["items"][0]["entity_type"] == "PAPER"
    for query in ("%", "_", "nonexistent literal query"):
        literal = client.get("/api/v1/graph/search", params={**params, "q": query}).json()
        assert {node["id"] for node in literal["items"]} == {
            node["id"] for node in all_nodes if query in node["display_label"]
        }
    assert (
        client.get("/api/v1/graph/search", params={**params, "topic": "other-topic"}).json()[
            "total"
        ]
        == 0
    )
    first = client.get("/api/v1/graph/search", params={**params, "q": "a", "limit": 1}).json()
    second = client.get(
        "/api/v1/graph/search", params={**params, "q": "a", "limit": 1, "offset": 1}
    ).json()
    assert first["total"] == second["total"] > 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    typed = client.get(
        "/api/v1/graph",
        params={
            "topic": topic_config.slug,
            "entity_type": GraphEntityType.RESEARCH_PROBLEM.value,
        },
    ).json()
    assert typed["edges"]
    assert {node["entity_type"] for node in typed["nodes"]} >= {"RESEARCH_PROBLEM", "PAPER"}

    other_topic = replace(
        topic_config, id=uuid5(topic_config.id, "graph-search-other-topic"), slug="other-topic"
    )
    other_record = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.99998",
        pdf_url="https://arxiv.org/pdf/2601.99998v1",
        source_url="https://arxiv.org/abs/2601.99998v1",
    )
    _, other_date = _prepare_complete_source(postgres_repository, other_topic, other_record)
    PublishProduct(
        repository=postgres_repository, llm=None, clock=lambda: NOW + timedelta(days=1, minutes=10)
    ).execute(
        other_topic,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=other_date,
    )
    original_matches = client.get(
        "/api/v1/graph/search",
        params={"topic": topic_config.slug, "q": arxiv_record_v1.title, "entity_type": "PAPER"},
    ).json()
    other_matches = client.get(
        "/api/v1/graph/search",
        params={"topic": other_topic.slug, "q": arxiv_record_v1.title, "entity_type": "PAPER"},
    ).json()
    assert original_matches["total"] == other_matches["total"] == 1
    assert original_matches["items"][0]["id"] != other_matches["items"][0]["id"]
