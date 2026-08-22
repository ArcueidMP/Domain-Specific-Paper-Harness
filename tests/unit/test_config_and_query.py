from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from paper_harness.adapters.config import TopicDocument, load_topic_config
from paper_harness.application.arxiv_query import build_arxiv_query
from paper_harness.domain.models import TopicConfig


@pytest.mark.parametrize(
    ("filename", "slug"),
    (
        ("broad-llm-agents.yaml", "broad-llm-agents"),
        ("brain-computer-interfaces.yaml", "brain-computer-interfaces"),
        ("world-models.yaml", "world-models"),
    ),
)
def test_checked_in_topic_configs_are_valid_and_bounded(filename: str, slug: str) -> None:
    topic = load_topic_config(Path("configs/topics") / filename)
    assert topic.slug == slug
    assert topic.overlap_hours == 48
    assert topic.representative_full_text_count == 100
    assert topic.max_results == 500


def test_checked_in_topic_configs_have_distinct_stable_identities() -> None:
    topics = tuple(
        load_topic_config(path) for path in sorted(Path("configs/topics").glob("*.yaml"))
    )

    assert {topic.slug for topic in topics} == {
        "broad-llm-agents",
        "brain-computer-interfaces",
        "world-models",
    }
    assert len({topic.id for topic in topics}) == len(topics)


@pytest.mark.parametrize(
    ("filename", "category", "term"),
    (
        ("brain-computer-interfaces.yaml", "q-bio.NC", "brain-computer interface"),
        ("world-models.yaml", "cs.RO", "world model"),
    ),
)
def test_new_topics_build_their_own_arxiv_query(
    filename: str,
    category: str,
    term: str,
) -> None:
    query = build_arxiv_query(load_topic_config(Path("configs/topics") / filename))

    assert f"cat:{category}" in query
    assert f'all:"{term}"' in query


def test_query_is_owned_and_built_by_application(topic_config: TopicConfig) -> None:
    assert build_arxiv_query(topic_config) == (
        '(cat:cs.AI OR cat:cs.CL) AND (all:"LLM agent" OR all:"web agent") '
        'ANDNOT (all:"agent-based social simulation")'
    )


def test_topic_document_rejects_query_syntax_injection() -> None:
    with pytest.raises(ValidationError):
        TopicDocument.model_validate(
            {
                "schema_version": 1,
                "topic_id": "4b7db6d4-349c-5c06-bc41-f84091580fcb",
                "slug": "test",
                "name": "Test",
                "description": "Test topic",
                "arxiv": {
                    "categories": ["cs.AI"],
                    "include_terms": ['agent" OR all:*'],
                },
                "discovery": {
                    "overlap_hours": 48,
                    "initial_lookback_days": 7,
                    "max_results": 100,
                },
                "representative_full_text_count": 10,
            }
        )


def test_topic_document_normalizes_duplicate_categories_and_terms() -> None:
    document = TopicDocument.model_validate(
        {
            "schema_version": 1,
            "topic_id": "4b7db6d4-349c-5c06-bc41-f84091580fcb",
            "slug": "test",
            "name": "Test",
            "description": "Test topic",
            "arxiv": {
                "categories": [" cs.AI ", "cs.AI", "cs.CL"],
                "include_terms": [" LLM   agent ", "llm agent", "web agent"],
                "exclude_terms": ["simulation", " Simulation "],
            },
            "discovery": {
                "overlap_hours": 48,
                "initial_lookback_days": 7,
                "max_results": 100,
            },
            "representative_full_text_count": 10,
        }
    )

    assert document.arxiv.categories == ("cs.AI", "cs.CL")
    assert document.arxiv.include_terms == ("LLM agent", "web agent")
    assert document.arxiv.exclude_terms == ("simulation",)


def test_topic_document_rejects_unbounded_full_text_selection() -> None:
    with pytest.raises(ValidationError):
        TopicDocument.model_validate(
            {
                "schema_version": 1,
                "topic_id": "4b7db6d4-349c-5c06-bc41-f84091580fcb",
                "slug": "test",
                "name": "Test",
                "description": "Test topic",
                "arxiv": {
                    "categories": ["cs.AI"],
                    "include_terms": ["LLM agent"],
                },
                "discovery": {
                    "overlap_hours": 48,
                    "initial_lookback_days": 7,
                    "max_results": 100,
                },
                "representative_full_text_count": 201,
            }
        )
