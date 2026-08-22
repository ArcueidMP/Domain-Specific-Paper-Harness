"""Deterministic relevance filtering for the bounded full Daily pipeline."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from paper_harness.domain.models import TopicConfig

_WHITESPACE = re.compile(r"\s+")
DAILY_SELECTION_POLICY_VERSION = "daily-selection-v1"


@dataclass(frozen=True, slots=True)
class DailySelectionCandidate:
    paper_id: UUID
    paper_version_id: UUID
    canonical_arxiv_id: str
    title: str
    abstract: str
    categories: tuple[str, ...]
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.abstract.strip() or not self.categories:
            raise ValueError("daily selection candidate metadata is incomplete")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("daily selection candidate timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class RankedDailyPaper:
    candidate: DailySelectionCandidate
    relevance_score: int

    def __post_init__(self) -> None:
        if self.relevance_score < 0:
            raise ValueError("daily paper relevance score cannot be negative")


@dataclass(frozen=True, slots=True)
class DailySelection:
    eligible: tuple[RankedDailyPaper, ...]
    selected: tuple[RankedDailyPaper, ...]
    evaluated_count: int
    limit: int

    def __post_init__(self) -> None:
        if self.evaluated_count < len(self.eligible) or len(self.selected) > self.limit:
            raise ValueError("daily selection counts are inconsistent")
        if self.selected != self.eligible[: self.limit]:
            raise ValueError("daily selection must be the bounded prefix of the stable ranking")


def select_daily_papers(
    topic: TopicConfig,
    candidates: tuple[DailySelectionCandidate, ...],
    *,
    limit: int,
) -> DailySelection:
    """Filter and rank persisted arXiv metadata with a defined non-probabilistic score.

    A title match contributes two ranking points and an abstract match contributes one.
    Exclusion phrases reject the candidate. The score is only a stable ordering device; it
    is not confidence, probability, or an LLM relevance judgment.
    """

    if not 1 <= limit <= topic.representative_full_text_count:
        raise ValueError("daily selection limit exceeds the configured topic bound")
    paper_ids = {candidate.paper_id for candidate in candidates}
    version_ids = {candidate.paper_version_id for candidate in candidates}
    if len(paper_ids) != len(candidates) or len(version_ids) != len(candidates):
        raise ValueError("daily selection candidates must have unique paper and version IDs")

    ranked: list[RankedDailyPaper] = []
    topic_categories = set(topic.categories)
    for candidate in candidates:
        if not topic_categories.intersection(candidate.categories):
            continue
        title = _normalize(candidate.title)
        abstract = _normalize(candidate.abstract)
        combined = f"{title} {abstract}"
        if any(_normalize(term) in combined for term in topic.exclude_terms):
            continue
        score = sum(
            (2 if normalized_term in title else 0) + (1 if normalized_term in abstract else 0)
            for term in topic.include_terms
            if (normalized_term := _normalize(term))
        )
        ranked.append(RankedDailyPaper(candidate=candidate, relevance_score=score))

    ranked.sort(
        key=lambda item: (
            -item.relevance_score,
            -item.candidate.updated_at.timestamp(),
            item.candidate.canonical_arxiv_id,
            str(item.candidate.paper_version_id),
        )
    )
    eligible = tuple(ranked)
    return DailySelection(
        eligible=eligible,
        selected=eligible[:limit],
        evaluated_count=len(candidates),
        limit=limit,
    )


def _normalize(value: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip().casefold()
