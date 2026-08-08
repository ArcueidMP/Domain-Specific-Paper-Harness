"""Shared deterministic M1 fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from paper_harness.domain.models import TopicConfig
from paper_harness.ports.arxiv import ArxivPaperRecord


@pytest.fixture
def topic_config() -> TopicConfig:
    return TopicConfig(
        id=UUID("4b7db6d4-349c-5c06-bc41-f84091580fcb"),
        slug="broad-llm-agents",
        name="Broad LLM Agents",
        description="Broad LLM-agent research.",
        categories=("cs.AI", "cs.CL"),
        include_terms=("LLM agent", "web agent"),
        exclude_terms=("agent-based social simulation",),
        overlap_hours=48,
        initial_lookback_days=7,
        max_results=500,
        representative_full_text_count=100,
        schema_version=1,
    )


@pytest.fixture
def arxiv_record_v1() -> ArxivPaperRecord:
    return ArxivPaperRecord(
        canonical_arxiv_id="2601.01234",
        version=1,
        title="A Reliable LLM Agent",
        abstract="We evaluate a tool-using language model agent.",
        submitted_at=datetime(2026, 1, 2, 8, tzinfo=UTC),
        updated_at=datetime(2026, 1, 2, 8, tzinfo=UTC),
        primary_category="cs.AI",
        categories=("cs.AI", "cs.CL"),
        authors=("Ada Lovelace", "Alan Turing"),
        pdf_url="https://arxiv.org/pdf/2601.01234v1",
        source_url="https://arxiv.org/abs/2601.01234v1",
    )
