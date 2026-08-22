from datetime import UTC, datetime
from uuid import UUID

import pytest

from paper_harness.application.daily_selection import (
    DailySelectionCandidate,
    select_daily_papers,
)
from paper_harness.domain.models import TopicConfig


def _topic() -> TopicConfig:
    return TopicConfig(
        id=UUID("4b7db6d4-349c-5c06-bc41-f84091580fcb"),
        slug="broad-llm-agents",
        name="Broad LLM Agents",
        description="LLM-centered agent research",
        categories=("cs.AI", "cs.CL"),
        include_terms=("LLM agent", "web agent"),
        exclude_terms=("agent-based social simulation",),
        overlap_hours=48,
        initial_lookback_days=7,
        max_results=500,
        representative_full_text_count=100,
    )


def _candidate(
    suffix: int,
    *,
    title: str,
    abstract: str,
    category: str = "cs.AI",
    updated_at: datetime = datetime(2026, 8, 10, tzinfo=UTC),
) -> DailySelectionCandidate:
    return DailySelectionCandidate(
        paper_id=UUID(f"00000000-0000-0000-0000-{suffix:012d}"),
        paper_version_id=UUID(f"10000000-0000-0000-0000-{suffix:012d}"),
        canonical_arxiv_id=f"2608.{suffix:05d}",
        title=title,
        abstract=abstract,
        categories=(category,),
        updated_at=updated_at,
    )


def test_selection_filters_exclusions_and_uses_topic_terms_as_a_rank_boost() -> None:
    candidates = (
        _candidate(1, title="A web agent benchmark", abstract="An LLM agent evaluation."),
        _candidate(2, title="An LLM agent", abstract="Planning without another match."),
        _candidate(
            3,
            title="An LLM agent",
            abstract="An agent-based social simulation with language models.",
        ),
        _candidate(4, title="Language modeling", abstract="No selected phrase."),
        _candidate(
            5,
            title="An LLM agent",
            abstract="A relevant paper in an excluded category.",
            category="stat.ML",
        ),
    )

    selection = select_daily_papers(_topic(), candidates, limit=2)

    assert selection.evaluated_count == 5
    assert [item.candidate.paper_id for item in selection.eligible] == [
        candidates[0].paper_id,
        candidates[1].paper_id,
        candidates[3].paper_id,
    ]
    assert [item.relevance_score for item in selection.selected] == [3, 2]


def test_selection_uses_recency_then_stable_identity_for_equal_scores() -> None:
    older = _candidate(
        1,
        title="An LLM agent",
        abstract="Planning.",
        updated_at=datetime(2026, 8, 9, tzinfo=UTC),
    )
    newer_b = _candidate(
        3,
        title="An LLM agent",
        abstract="Planning.",
        updated_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    newer_a = _candidate(
        2,
        title="An LLM agent",
        abstract="Planning.",
        updated_at=datetime(2026, 8, 10, tzinfo=UTC),
    )

    selection = select_daily_papers(_topic(), (older, newer_b, newer_a), limit=3)

    assert [item.candidate.canonical_arxiv_id for item in selection.selected] == [
        "2608.00002",
        "2608.00003",
        "2608.00001",
    ]


def test_selection_rejects_an_unbounded_limit_and_duplicate_candidate() -> None:
    candidate = _candidate(1, title="An LLM agent", abstract="Planning.")

    with pytest.raises(ValueError, match="configured topic bound"):
        select_daily_papers(_topic(), (candidate,), limit=101)
    with pytest.raises(ValueError, match="unique paper and version"):
        select_daily_papers(_topic(), (candidate, candidate), limit=1)
