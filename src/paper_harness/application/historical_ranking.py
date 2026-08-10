"""Deterministic, inspectable hybrid ranking for historical candidates."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import CandidateScoreComponents

SEMANTIC_SCHOLAR_WEIGHT = 0.25
LEXICAL_WEIGHT = 0.20
VECTOR_WEIGHT = 0.30
ENTITY_OVERLAP_WEIGHT = 0.10
CITATION_WEIGHT = 0.10
RECOMMENDATION_WEIGHT = 0.05

_TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RankingSignals:
    semantic_scholar_rank: int | None = None
    semantic_scholar_result_count: int = 0
    lexical_score: float = 0.0
    cosine_similarity: float | None = None
    shared_entity_count: int = 0
    source_entity_count: int = 0
    citation_related: bool = False
    recommendation_related: bool = False

    def __post_init__(self) -> None:
        if self.semantic_scholar_rank is not None:
            if not 1 <= self.semantic_scholar_rank <= self.semantic_scholar_result_count:
                raise DomainInvariantError("Semantic Scholar rank must fit its result count")
        elif self.semantic_scholar_result_count != 0:
            raise DomainInvariantError("Semantic Scholar result count requires a rank")
        if not math.isfinite(self.lexical_score) or not 0 <= self.lexical_score <= 1:
            raise DomainInvariantError("lexical score must be between zero and one")
        if self.cosine_similarity is not None and (
            not math.isfinite(self.cosine_similarity) or not -1 <= self.cosine_similarity <= 1
        ):
            raise DomainInvariantError("cosine similarity must be between minus one and one")
        if min(self.shared_entity_count, self.source_entity_count) < 0:
            raise DomainInvariantError("entity counts cannot be negative")
        if self.shared_entity_count > self.source_entity_count:
            raise DomainInvariantError("shared entity count cannot exceed source entities")


def combine_ranking_signals(signals: RankingSignals) -> CandidateScoreComponents:
    """Combine normalized signals with fixed documented weights.

    Missing signals contribute zero; they are not imputed by an LLM. Semantic
    Scholar order is normalized linearly within one returned page, cosine is
    mapped from [-1, 1] to [0, 1], and entity overlap is source-set recall.
    """

    semantic_scholar = (
        0.0
        if signals.semantic_scholar_rank is None
        else (signals.semantic_scholar_result_count - signals.semantic_scholar_rank + 1)
        / signals.semantic_scholar_result_count
    )
    vector = 0.0 if signals.cosine_similarity is None else (signals.cosine_similarity + 1.0) / 2.0
    entity_overlap = (
        0.0
        if signals.source_entity_count == 0
        else signals.shared_entity_count / signals.source_entity_count
    )
    citation = 1.0 if signals.citation_related else 0.0
    recommendation = 1.0 if signals.recommendation_related else 0.0
    final = (
        semantic_scholar * SEMANTIC_SCHOLAR_WEIGHT
        + signals.lexical_score * LEXICAL_WEIGHT
        + vector * VECTOR_WEIGHT
        + entity_overlap * ENTITY_OVERLAP_WEIGHT
        + citation * CITATION_WEIGHT
        + recommendation * RECOMMENDATION_WEIGHT
    )
    return CandidateScoreComponents(
        semantic_scholar=semantic_scholar,
        lexical=signals.lexical_score,
        vector=vector,
        entity_overlap=entity_overlap,
        citation=citation,
        recommendation=recommendation,
        final=min(1.0, max(0.0, final)),
    )


def lexical_similarity(source: str, candidate: str) -> float:
    """Return deterministic token-set Jaccard similarity for bounded fallback-free scoring."""

    source_tokens = {match.group(0).casefold() for match in _TOKEN.finditer(source)}
    candidate_tokens = {match.group(0).casefold() for match in _TOKEN.finditer(candidate)}
    if not source_tokens or not candidate_tokens:
        return 0.0
    return len(source_tokens & candidate_tokens) / len(source_tokens | candidate_tokens)
