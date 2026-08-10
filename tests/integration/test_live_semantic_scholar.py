"""Explicit opt-in Semantic Scholar authentication and schema smoke test."""

from __future__ import annotations

import os

import pytest

from paper_harness.adapters.semantic_scholar import (
    SemanticScholarClient,
    SemanticScholarSettings,
)

pytestmark = [pytest.mark.integration, pytest.mark.live]


def test_live_semantic_scholar_search_contract() -> None:
    if os.environ.get("RUN_LIVE_SEMANTIC_SCHOLAR_TEST") != "1":
        pytest.skip(
            "set RUN_LIVE_SEMANTIC_SCHOLAR_TEST=1 for the explicit Semantic Scholar smoke test"
        )
    if not os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip():
        pytest.fail("SEMANTIC_SCHOLAR_API_KEY is required when RUN_LIVE_SEMANTIC_SCHOLAR_TEST=1")

    papers = SemanticScholarClient(SemanticScholarSettings.from_environment()).search_papers(
        "Attention Is All You Need",
        2017,
        2017,
        1,
    )

    assert len(papers) == 1
    assert len(papers[0].semantic_scholar_id) == 40
    assert papers[0].title
