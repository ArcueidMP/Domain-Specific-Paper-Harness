from __future__ import annotations

import pytest

from paper_harness.application.historical_ranking import (
    RankingSignals,
    combine_ranking_signals,
    lexical_similarity,
)
from paper_harness.domain.errors import DomainInvariantError


def test_hybrid_ranking_keeps_every_component_inspectable() -> None:
    scores = combine_ranking_signals(
        RankingSignals(
            semantic_scholar_rank=2,
            semantic_scholar_result_count=4,
            lexical_score=0.5,
            cosine_similarity=0.8,
            shared_entity_count=2,
            source_entity_count=4,
            citation_related=True,
            recommendation_related=False,
        )
    )

    assert scores.semantic_scholar == pytest.approx(0.75)
    assert scores.lexical == 0.5
    assert scores.vector == pytest.approx(0.9)
    assert scores.entity_overlap == 0.5
    assert scores.citation == 1.0
    assert scores.recommendation == 0.0
    assert scores.final == pytest.approx(0.7075)


def test_missing_ranking_signals_contribute_zero_without_imputation() -> None:
    scores = combine_ranking_signals(RankingSignals())

    assert scores.final == 0.0
    assert scores.vector == 0.0


def test_ranking_rejects_inconsistent_semantic_rank() -> None:
    with pytest.raises(DomainInvariantError, match="rank must fit"):
        RankingSignals(semantic_scholar_rank=3, semantic_scholar_result_count=2)


def test_lexical_similarity_is_deterministic_token_jaccard() -> None:
    assert lexical_similarity("LLM agent planning", "planning for an LLM agent") == pytest.approx(
        3 / 5
    )
    assert lexical_similarity("", "candidate") == 0.0
