from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import httpx
import pytest

from paper_harness.adapters.deepseek import DeepSeekClient, DeepSeekSettings
from paper_harness.adapters.http_retry import HttpRetryPolicy
from paper_harness.domain.analysis import AnalysisScope
from paper_harness.domain.historical import (
    COMPARISON_DIMENSION_ORDER,
    CandidateScoreComponents,
    CandidateSelectionInput,
    CandidateSelectionRequest,
    ComparabilityStatus,
    ComparisonEvidenceInput,
    ComparisonPaperInput,
    ComparisonRequest,
    CrawlerPlanRequest,
    SelectionDecision,
)
from paper_harness.ports.llm import LLMOutputError, LLMUnavailableError

SOURCE_PAPER_ID = UUID("b90313ea-8eed-4fb5-886e-f3e4900acc0e")
SOURCE_VERSION_ID = UUID("3a89625f-dabb-4220-81eb-75830644840d")
SOURCE_ANALYSIS_ID = UUID("795aa6c2-cb02-4a55-8cca-d7ce8ed1e44a")
TARGET_PAPER_ID = UUID("9e9425c0-ce66-4313-bbc2-905417425187")
TARGET_VERSION_ID = UUID("aa2e6d67-1830-449c-af76-633454c7b019")
TARGET_ANALYSIS_ID = UUID("5f219957-a4a6-474c-9bd4-d60f16b741d4")
SOURCE_EVIDENCE_ID = UUID("99bb13cc-b022-4a05-81d3-b0a7d03b3b81")
TARGET_EVIDENCE_ID = UUID("cc3f57e8-62d3-42ee-b104-9d68fd4cba0f")


def _response(payload: object) -> dict[str, object]:
    return {
        "id": "completion-m3",
        "model": "DeepSeek-V4-Flash-2026-04-24",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(payload),
                    "reasoning_content": None,
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 100,
        },
    }


def _client(payload: object, observed: dict[str, object] | None = None) -> DeepSeekClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if observed is not None:
            observed["body"] = json.loads(request.content)
        return httpx.Response(200, json=_response(payload))

    return DeepSeekClient(
        DeepSeekSettings(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key="test-only-key",
        ),
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.deepseek.com",
        ),
        retry_policy=HttpRetryPolicy(
            max_retries=0,
            request_timeout_seconds=5,
            total_timeout_seconds=10,
            backoff_seconds=0,
            max_retry_after_seconds=5,
        ),
        clock=lambda: datetime(2026, 8, 9, 5, tzinfo=UTC),
    )


def _deadline_client(
    observed_timeouts: list[float],
    elapsed: list[float],
) -> DeepSeekClient:
    def handler(request: httpx.Request) -> httpx.Response:
        timeout = cast(dict[str, float], request.extensions["timeout"])
        observed_timeouts.append(timeout["read"])
        elapsed[0] = 2.0 if len(observed_timeouts) == 1 else 3.0
        return httpx.Response(503, headers={"Retry-After": "0"})

    return DeepSeekClient(
        DeepSeekSettings(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key="test-only-key",
        ),
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.deepseek.com",
        ),
        retry_policy=HttpRetryPolicy(
            max_retries=2,
            request_timeout_seconds=5,
            total_timeout_seconds=10,
            backoff_seconds=0,
            max_retry_after_seconds=5,
        ),
        clock=lambda: datetime(2026, 8, 9, 5, tzinfo=UTC),
        monotonic=lambda: elapsed[0],
        sleep=lambda _delay: None,
    )


def _selection_request() -> CandidateSelectionRequest:
    return CandidateSelectionRequest(
        objective="Find methodologically relevant planning-agent prior work.",
        source_title="Bounded Agent Planning",
        source_research_problem="Agents need reliable long-horizon planning.",
        source_method="A bounded tree planner.",
        candidates=(
            CandidateSelectionInput(
                semantic_scholar_id="a" * 40,
                title="Prior Planning Agent",
                abstract="A related bounded planning method.",
                year=2025,
                venue="ACL",
                scores=CandidateScoreComponents(final=0.8),
            ),
            CandidateSelectionInput(
                semantic_scholar_id="b" * 40,
                title="Unrelated Chatbot",
                abstract="A conversational response model.",
                year=2025,
                venue="EMNLP",
                scores=CandidateScoreComponents(final=0.4),
            ),
        ),
        max_selected_candidates=1,
    )


def _crawler_request() -> CrawlerPlanRequest:
    return CrawlerPlanRequest(
        objective="Find methodologically relevant planning-agent prior work.",
        source_title="Bounded Agent Planning",
        source_research_problem="Agents need reliable long-horizon planning.",
        source_method="A bounded tree planner.",
        topic_include_terms=("LLM agent", "web agent"),
        topic_exclude_terms=("traditional reinforcement learning",),
        year_from=2025,
        year_to=2026,
        max_queries=2,
    )


def _crawler_payload() -> dict[str, object]:
    return {
        "queries": ["bounded LLM agent planning", "tool-using agent tree search"],
        "use_recommendations": True,
        "expand_references": True,
        "expand_citations": False,
        "decision_reason": "Search mechanisms first, then expand direct references.",
    }


def _selection_payload() -> dict[str, object]:
    return {
        "decisions": [
            {
                "semantic_scholar_id": "a" * 40,
                "decision": "SELECTED",
                "reason": "The candidate studies the same bounded planning mechanism.",
            },
            {
                "semantic_scholar_id": "b" * 40,
                "decision": "REJECTED",
                "reason": "The candidate is a chatbot without an agent planning workflow.",
            },
        ]
    }


def _comparison_request() -> ComparisonRequest:
    return ComparisonRequest(
        source=ComparisonPaperInput(
            paper_id=SOURCE_PAPER_ID,
            paper_version_id=SOURCE_VERSION_ID,
            analysis_id=SOURCE_ANALYSIS_ID,
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
            title="New Planner",
            summary="The authors report a bounded planner.",
            research_problem="Reliable planning.",
            method_summary="Tree search.",
            limitations=("One benchmark.",),
            evidence=(
                ComparisonEvidenceInput(
                    id=SOURCE_EVIDENCE_ID,
                    analysis_id=SOURCE_ANALYSIS_ID,
                    paper_id=SOURCE_PAPER_ID,
                    paper_version_id=SOURCE_VERSION_ID,
                    section="Results",
                    excerpt="The authors report 72% success.",
                ),
            ),
        ),
        target=ComparisonPaperInput(
            paper_id=TARGET_PAPER_ID,
            paper_version_id=TARGET_VERSION_ID,
            analysis_id=TARGET_ANALYSIS_ID,
            analysis_scope=AnalysisScope.FULL_TEXT,
            title="Historical Planner",
            summary="The authors report an earlier planner.",
            research_problem="Reliable planning.",
            method_summary="Beam search.",
            limitations=("Different benchmark version.",),
            evidence=(
                ComparisonEvidenceInput(
                    id=TARGET_EVIDENCE_ID,
                    analysis_id=TARGET_ANALYSIS_ID,
                    paper_id=TARGET_PAPER_ID,
                    paper_version_id=TARGET_VERSION_ID,
                    section="Results",
                    excerpt="The authors report 68% success on version 1.",
                ),
            ),
        ),
    )


def _comparison_payload() -> dict[str, object]:
    return {
        "comparability_status": "PARTIALLY_COMPARABLE",
        "comparability_reason": "The reported benchmark versions differ.",
        "summary": "The available evidence indicates related planning methods.",
        "dimensions": [
            {
                "name": name.value,
                "source_value": "Reported in the supplied evidence.",
                "target_value": "Reported in the supplied evidence.",
                "assessment": "The evidence is only partially comparable.",
                "source_evidence_ids": [str(SOURCE_EVIDENCE_ID)],
                "target_evidence_ids": [str(TARGET_EVIDENCE_ID)],
            }
            for name in COMPARISON_DIMENSION_ORDER
        ],
        "relations": [
            {
                "relation_type": "SIMILAR_TO",
                "justification": "Both papers address reliable planning.",
                "evidence_ids": [str(SOURCE_EVIDENCE_ID), str(TARGET_EVIDENCE_ID)],
                "confidence": 0.8,
            }
        ],
    }


def test_selector_validates_complete_bounded_decisions_and_disables_reasoning() -> None:
    observed: dict[str, object] = {}

    result = _client(_selection_payload(), observed).select_prior_work(_selection_request())

    assert [item.decision for item in result.decisions] == [
        SelectionDecision.SELECTED,
        SelectionDecision.REJECTED,
    ]
    assert result.prompt_version == "m3-selector-v1"
    body = observed["body"]
    assert isinstance(body, dict)
    assert body["thinking"] == {"type": "disabled"}


def test_crawler_returns_only_strict_bounded_plan_controls() -> None:
    observed: dict[str, object] = {}

    result = _client(_crawler_payload(), observed).plan_scholarly_search(_crawler_request())

    assert result.queries == (
        "bounded LLM agent planning",
        "tool-using agent tree search",
    )
    assert result.use_recommendations is True
    assert result.expand_references is True
    assert result.expand_citations is False
    assert result.prompt_version == "m3-crawler-v1"
    body = observed["body"]
    assert isinstance(body, dict)
    assert body["thinking"] == {"type": "disabled"}


def test_crawler_timeout_bounds_http_requests_and_the_shared_retry_deadline() -> None:
    observed_timeouts: list[float] = []
    elapsed = [0.0]

    with pytest.raises(LLMUnavailableError, match="TimeoutException"):
        _deadline_client(observed_timeouts, elapsed).plan_scholarly_search(
            _crawler_request(),
            timeout_seconds=3,
        )

    assert observed_timeouts == [3.0, 1.0]


def test_selector_timeout_bounds_http_requests_and_the_shared_retry_deadline() -> None:
    observed_timeouts: list[float] = []
    elapsed = [0.0]

    with pytest.raises(LLMUnavailableError, match="TimeoutException"):
        _deadline_client(observed_timeouts, elapsed).select_prior_work(
            _selection_request(),
            timeout_seconds=3,
        )

    assert observed_timeouts == [3.0, 1.0]


def test_crawler_deduplicates_and_caps_queries_in_model_order() -> None:
    payload = _crawler_payload()
    payload["queries"] = [None, "   ", " one ", 12, "one", "two", "three"]

    result = _client(payload).plan_scholarly_search(_crawler_request())

    assert result.queries == ("one", "two")


def test_selector_accepts_a_nonempty_subset_in_request_order() -> None:
    payload = _selection_payload()
    decisions = cast(list[dict[str, object]], payload["decisions"])
    payload["decisions"] = list(reversed(decisions))[:1]

    result = _client(payload).select_prior_work(_selection_request())

    assert tuple(item.semantic_scholar_id for item in result.decisions) == ("b" * 40,)
    assert result.decisions[0].decision is SelectionDecision.REJECTED


def test_selector_caps_over_selection_in_requested_order() -> None:
    payload = _selection_payload()
    decisions = list(cast(list[dict[str, object]], payload["decisions"]))
    decisions[1] = {**decisions[1], "decision": "SELECTED"}
    payload["decisions"] = list(reversed(decisions))

    result = _client(payload).select_prior_work(_selection_request())

    assert tuple(item.semantic_scholar_id for item in result.decisions) == ("a" * 40,)
    assert result.decisions[0].decision is SelectionDecision.SELECTED


def test_selector_discards_malformed_decisions_and_keeps_valid_siblings() -> None:
    payload = _selection_payload()
    decisions = list(cast(list[dict[str, object]], payload["decisions"]))
    payload["decisions"] = [
        None,
        {**decisions[0], "decision": "UNSUPPORTED"},
        {**decisions[1], "reason": "  Not methodologically relevant.  "},
    ]

    result = _client(payload).select_prior_work(_selection_request())

    assert tuple(item.semantic_scholar_id for item in result.decisions) == ("b" * 40,)
    assert result.decisions[0].reason == "Not methodologically relevant."


def test_selector_filters_unknown_and_conflicting_duplicate_candidate_ids() -> None:
    payload = _selection_payload()
    decisions = list(cast(list[dict[str, object]], payload["decisions"]))
    payload["decisions"] = [
        decisions[0],
        {**decisions[0], "decision": "REJECTED"},
        decisions[1],
        {**decisions[1]},
        {**decisions[0], "semantic_scholar_id": "unknown"},
    ]

    result = _client(payload).select_prior_work(_selection_request())

    assert tuple(item.semantic_scholar_id for item in result.decisions) == ("b" * 40,)


def test_selector_rejects_output_with_no_usable_requested_decision() -> None:
    payload = _selection_payload()
    payload["decisions"] = [
        {
            "semantic_scholar_id": "unknown",
            "decision": "REJECTED",
            "reason": "Not a requested candidate.",
        }
    ]

    with pytest.raises(LLMOutputError, match="no usable candidate decisions"):
        _client(payload).select_prior_work(_selection_request())


def test_comparison_maps_fixed_dimensions_evidence_and_provenance() -> None:
    result = _client(_comparison_payload()).compare_papers(_comparison_request())

    assert result.comparability_status is ComparabilityStatus.PARTIALLY_COMPARABLE
    assert tuple(item.name for item in result.dimensions) == COMPARISON_DIMENSION_ORDER
    assert result.dimensions[0].source_evidence_ids == (SOURCE_EVIDENCE_ID,)
    assert result.relations[0].confidence == 0.8
    assert result.prompt_version == "m3-comparison-v1"


def test_comparison_normalizes_order_deduplicates_and_keeps_honest_partial_dimensions() -> None:
    payload = _comparison_payload()
    dimensions = list(cast(list[dict[str, object]], payload["dimensions"]))
    payload["dimensions"] = list(reversed(dimensions))

    result = _client(payload).compare_papers(_comparison_request())

    assert tuple(item.name for item in result.dimensions) == COMPARISON_DIMENSION_ORDER

    payload["dimensions"] = [*dimensions[:-1], dimensions[0]]
    normalized = _client(payload).compare_papers(_comparison_request())

    assert tuple(item.name for item in normalized.dimensions) == COMPARISON_DIMENSION_ORDER[:-1]
    assert normalized.dimensions[0].source_value == dimensions[0]["source_value"]


def test_comparison_discards_malformed_optional_items_and_trims_core_text() -> None:
    payload = _comparison_payload()
    dimensions = list(cast(list[dict[str, object]], payload["dimensions"]))
    payload["comparability_reason"] = "  The reported benchmark versions differ.  "
    payload["summary"] = "  The available evidence indicates related planning methods.  "
    payload["dimensions"] = [
        {"name": "UNSUPPORTED", "source_value": "Bad"},
        {**dimensions[1], "assessment": "   "},
        {
            **dimensions[0],
            "source_value": "  Reported in the supplied evidence.  ",
            "source_evidence_ids": ["not-a-uuid", str(SOURCE_EVIDENCE_ID)],
        },
    ]
    payload["relations"] = [
        {
            "relation_type": "UNSUPPORTED",
            "justification": "Ignored.",
            "evidence_ids": [str(SOURCE_EVIDENCE_ID)],
            "confidence": 0.5,
        },
        {
            "relation_type": "SIMILAR_TO",
            "justification": "  Both papers address reliable planning.  ",
            "evidence_ids": ["not-a-uuid", str(SOURCE_EVIDENCE_ID)],
            "confidence": 0.8,
        },
    ]

    result = _client(payload).compare_papers(_comparison_request())

    assert result.comparability_reason == "The reported benchmark versions differ."
    assert result.summary == "The available evidence indicates related planning methods."
    assert tuple(item.name for item in result.dimensions) == (COMPARISON_DIMENSION_ORDER[0],)
    assert result.dimensions[0].source_value == "Reported in the supplied evidence."
    assert result.relations[0].justification == "Both papers address reliable planning."
    assert result.relations[0].evidence_ids == (SOURCE_EVIDENCE_ID,)


def test_comparison_accepts_omitted_optional_collections_without_defaults() -> None:
    payload = _comparison_payload()
    payload.pop("dimensions")
    payload["relations"] = None

    result = _client(payload).compare_papers(_comparison_request())

    assert result.dimensions == ()
    assert result.relations == ()


def test_comparison_filters_unknown_or_wrong_side_evidence_reference() -> None:
    payload = _comparison_payload()
    dimensions = list(cast(list[dict[str, object]], payload["dimensions"]))
    dimensions[0] = {
        **dimensions[0],
        "source_evidence_ids": ["85fe7a9e-c3f2-4c71-99b1-69df7445fcf6"],
    }
    payload["dimensions"] = dimensions

    result = _client(payload).compare_papers(_comparison_request())

    assert result.dimensions[0].source_evidence_ids == ()
    assert result.dimensions[0].target_evidence_ids == (TARGET_EVIDENCE_ID,)


def test_direct_comparison_without_bilateral_result_evidence_is_downgraded() -> None:
    payload = _comparison_payload()
    payload["comparability_status"] = "DIRECTLY_COMPARABLE"
    dimensions = list(cast(list[dict[str, object]], payload["dimensions"]))
    result_position = [item.value for item in COMPARISON_DIMENSION_ORDER].index("REPORTED_RESULTS")
    dimensions[result_position] = {**dimensions[result_position], "target_evidence_ids": []}
    payload["dimensions"] = dimensions

    result = _client(payload).compare_papers(_comparison_request())

    assert result.comparability_status is ComparabilityStatus.PARTIALLY_COMPARABLE


def test_direct_comparison_without_usable_dimensions_is_insufficient() -> None:
    payload = _comparison_payload()
    payload["comparability_status"] = "DIRECTLY_COMPARABLE"
    payload["dimensions"] = None

    result = _client(payload).compare_papers(_comparison_request())

    assert result.comparability_status is ComparabilityStatus.INSUFFICIENT_EVIDENCE
    assert result.dimensions == ()
    assert result.relations == ()


def test_ungrounded_improves_on_relation_is_filtered() -> None:
    payload = _comparison_payload()
    relations = list(cast(list[dict[str, object]], payload["relations"]))
    relations[0] = {
        **relations[0],
        "relation_type": "IMPROVES_ON",
        "evidence_ids": [str(SOURCE_EVIDENCE_ID)],
    }
    payload["relations"] = relations

    result = _client(payload).compare_papers(_comparison_request())

    assert result.relations == ()


def test_comparison_deduplicates_relation_types_with_stable_first_wins() -> None:
    payload = _comparison_payload()
    relation = cast(list[dict[str, object]], payload["relations"])[0]
    payload["relations"] = [relation, {**relation, "confidence": 0.1}]

    result = _client(payload).compare_papers(_comparison_request())

    assert len(result.relations) == 1
    assert result.relations[0].confidence == relation["confidence"]
