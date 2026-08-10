from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

import httpx
import pytest

from paper_harness.adapters.deepseek import DeepSeekClient, DeepSeekSettings
from paper_harness.adapters.http_retry import HttpRetryPolicy
from paper_harness.domain.analysis import VerificationStatus
from paper_harness.domain.models import RunStatus
from paper_harness.domain.reports import (
    ReportComparisonHighlight,
    ReportCounts,
    ReportEntityHighlight,
    ReportEvidenceReference,
    ReportGraphChanges,
    ReportLineageHighlight,
    ReportNarrativeRequest,
    ReportPaperHighlight,
    ReportSectionKind,
    ReportType,
)
from paper_harness.ports.llm import LLMOutputError, LLMRequestError

PAPER_ID = UUID("dd05928c-f952-4fe0-83f1-5740d4dc13a3")
PAPER_VERSION_ID = UUID("fa5a79e9-4eb1-4497-b3b8-2b95d0a7c34d")
EVIDENCE_ID = UUID("52a3a16b-050f-4f8a-b9c1-7f47be68f563")
GRAPH_ENTITY_ID = UUID("16cd329e-a138-4433-84f3-5c234815bb65")
COMPARISON_ID = UUID("582f0e3b-b49b-4894-9424-af1b6c41884f")
TARGET_PAPER_ID = UUID("f00df0e1-b71d-4a57-974c-af4a60e78133")
TARGET_PAPER_VERSION_ID = UUID("9b89a036-0450-4fbf-9958-8fb984786b9d")
LINEAGE_ID = UUID("8a93c56d-4ca6-4990-b0dc-c604ed13e6c8")


def _request() -> ReportNarrativeRequest:
    return ReportNarrativeRequest(
        report_type=ReportType.DAILY,
        period_start=date(2026, 8, 10),
        period_end=date(2026, 8, 10),
        status=RunStatus.COMPLETE,
        counts=ReportCounts(
            retrieved=2,
            selected=1,
            processed=1,
            completed=1,
            failed=0,
        ),
        highlighted_papers=(
            ReportPaperHighlight(
                paper_id=PAPER_ID,
                paper_version_id=PAPER_VERSION_ID,
                title="A Bounded Planning Agent",
                reason="The persisted analysis reports a bounded planning method.",
                evidence_ids=(EVIDENCE_ID,),
            ),
        ),
        major_entities=(
            ReportEntityHighlight(
                graph_entity_id=GRAPH_ENTITY_ID,
                entity_type="Method",
                label="Bounded tree planning",
                distinct_paper_count=1,
            ),
        ),
        notable_comparisons=(
            ReportComparisonHighlight(
                comparison_id=COMPARISON_ID,
                source_paper_id=PAPER_ID,
                source_paper_version_id=PAPER_VERSION_ID,
                target_paper_id=TARGET_PAPER_ID,
                target_paper_version_id=TARGET_PAPER_VERSION_ID,
                summary="The persisted comparison is only partially comparable.",
                comparability_status="PARTIALLY_COMPARABLE",
                evidence_ids=(EVIDENCE_ID,),
            ),
        ),
        graph_changes=ReportGraphChanges(
            entity_count=3,
            edge_count=2,
            new_entity_count=1,
            inferred_edge_count=1,
        ),
        trend_summaries=("The 7-day window contains one completed paper.",),
        lineage_highlights=(
            ReportLineageHighlight(
                lineage_snapshot_id=LINEAGE_ID,
                root_paper_id=PAPER_ID,
                summary="The currently retrieved lineage contains one evidenced relation.",
                uncertain=True,
            ),
        ),
        failures=(),
        limitations=("The persisted corpus is small.",),
        evidence=(
            ReportEvidenceReference(
                id=EVIDENCE_ID,
                paper_id=PAPER_ID,
                paper_version_id=PAPER_VERSION_ID,
                section="Methods",
                excerpt="We use bounded tree planning for tool selection.",
                evidence_type="SUPPORTS",
                verification_status=VerificationStatus.UNVERIFIED,
            ),
        ),
        missing_sections=("No benchmark entity is available for this period.",),
    )


def _payload() -> dict[str, object]:
    return {
        "summary": "The daily corpus contains one completed bounded-planning paper.",
        "sections": [
            {
                "kind": kind.value,
                "narrative": f"Concise {kind.value.lower()} narrative for the retrieved corpus.",
                "evidence_ids": [str(EVIDENCE_ID)]
                if kind in (ReportSectionKind.OVERVIEW, ReportSectionKind.COMPARISONS)
                else [],
            }
            for kind in ReportSectionKind
        ],
    }


def _response(payload: object) -> dict[str, object]:
    return {
        "id": "completion-m4-report",
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
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 120,
        },
    }


def _client(handler: httpx.MockTransport) -> DeepSeekClient:
    return DeepSeekClient(
        DeepSeekSettings(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key="test-only-key",
        ),
        client=httpx.Client(
            transport=handler,
            base_url="https://api.deepseek.com",
        ),
        retry_policy=HttpRetryPolicy(
            max_retries=2,
            request_timeout_seconds=5,
            total_timeout_seconds=20,
            backoff_seconds=0,
            max_retry_after_seconds=5,
        ),
        clock=lambda: datetime(2026, 8, 10, 5, tzinfo=UTC),
        sleep=lambda _delay: None,
    )


def test_report_maps_strict_sections_provenance_and_bounded_structured_input() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["body"] = json.loads(request.content)
        return httpx.Response(200, json=_response(_payload()))

    result = _client(httpx.MockTransport(handler)).generate_report(_request())

    assert result.prompt_version == "m4-report-v1"
    assert result.model_version == "DeepSeek-V4-Flash-2026-04-24"
    assert tuple(section.kind for section in result.sections) == tuple(ReportSectionKind)
    assert result.sections[0].evidence_ids == (EVIDENCE_ID,)
    body = cast(dict[str, object], observed["body"])
    assert body["thinking"] == {"type": "disabled"}
    assert body["response_format"] == {"type": "json_object"}
    messages = cast(list[dict[str, str]], body["messages"])
    system_prompt = messages[0]["content"]
    assert "untrusted data" in system_prompt
    assert "Do not invent statistics" in system_prompt
    assert "priority, superiority" in system_prompt
    assert "Never imply global completeness" in system_prompt
    encoded_source = json.loads(messages[1]["content"].split("\n", maxsplit=1)[1])
    assert encoded_source["evidence"][0]["excerpt"] == (
        "We use bounded tree planning for tool selection."
    )
    assert encoded_source["required_section_order"] == [kind.value for kind in ReportSectionKind]
    assert encoded_source["section_evidence_allowlist"] == {
        "OVERVIEW": [str(EVIDENCE_ID)],
        "TRENDS": [],
        "COMPARISONS": [str(EVIDENCE_ID)],
        "LINEAGE": [],
        "LIMITATIONS": [],
    }
    assert encoded_source["missing_sections"] == [
        "No benchmark entity is available for this period."
    ]
    assert encoded_source["major_entities"][0]["distinct_paper_count"] == 1
    assert "mention_count" not in encoded_source["major_entities"][0]


def test_report_rejects_unordered_sections() -> None:
    payload = _payload()
    sections = list(cast(list[dict[str, object]], payload["sections"]))
    payload["sections"] = list(reversed(sections))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response(payload))

    with pytest.raises(LLMOutputError, match="incomplete or unordered"):
        _client(httpx.MockTransport(handler)).generate_report(_request())


def test_report_rejects_unknown_evidence_without_retrying_invalid_output() -> None:
    payload = _payload()
    sections = list(cast(list[dict[str, object]], payload["sections"]))
    sections[0] = {
        **sections[0],
        "evidence_ids": ["8d829336-6735-4216-af80-a10d17c24248"],
    }
    payload["sections"] = sections
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response(payload))

    with pytest.raises(LLMOutputError, match="unknown evidence"):
        _client(httpx.MockTransport(handler)).generate_report(_request())
    assert calls == 1


def test_report_rejects_known_evidence_in_the_wrong_section() -> None:
    payload = _payload()
    sections = list(cast(list[dict[str, object]], payload["sections"]))
    sections[1] = {**sections[1], "evidence_ids": [str(EVIDENCE_ID)]}
    payload["sections"] = sections
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response(payload))

    with pytest.raises(LLMOutputError, match="not permitted for its section"):
        _client(httpx.MockTransport(handler)).generate_report(_request())
    assert calls == 1


def test_report_rejects_schema_extensions_without_retrying_invalid_output() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response({**_payload(), "reasoning": "hidden"}))

    with pytest.raises(LLMOutputError, match="schema validation"):
        _client(httpx.MockTransport(handler)).generate_report(_request())
    assert calls == 1


def test_report_rejects_numeric_literals_so_statistics_remain_deterministic() -> None:
    payload = _payload()
    payload["summary"] = "The model claims a 42% increase."
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response(payload))

    with pytest.raises(LLMOutputError, match="domain validation"):
        _client(httpx.MockTransport(handler)).generate_report(_request())
    assert calls == 1


def test_report_rejects_input_above_character_bound_before_network() -> None:
    request = replace(
        _request(),
        trend_summaries=tuple("x" * 4000 for _ in range(230)),
    )
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response(_payload()))

    with pytest.raises(LLMRequestError, match="character bound"):
        _client(httpx.MockTransport(handler)).generate_report(request)
    assert calls == 0
