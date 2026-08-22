from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import httpx
import pytest

from paper_harness.adapters.deepseek import DeepSeekClient, DeepSeekSettings
from paper_harness.adapters.deepseek.client import MAX_RESPONSE_BYTES
from paper_harness.adapters.http_retry import HttpRetryPolicy
from paper_harness.domain.analysis import (
    MAX_MODEL_TOKEN_COUNT,
    AnalysisPassage,
    AnalysisRequest,
    AnalysisScope,
)
from paper_harness.ports.llm import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMOutputError,
    LLMRequestError,
    LLMUnavailableError,
)


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        paper_id=UUID("91c198f8-c23a-40e3-bd86-246b92be7813"),
        paper_version_id=UUID("029b4bec-9d07-45f6-9af1-b557ec6ece03"),
        canonical_arxiv_id="2601.01234",
        arxiv_version=1,
        title="A Reliable LLM Agent",
        scope=AnalysisScope.ABSTRACT_ONLY,
        passages=(
            AnalysisPassage(
                id="abstract",
                section="Abstract",
                text="We introduce a reliable tool-using agent and report a 12% gain.",
            ),
        ),
    )


def _payload() -> dict[str, object]:
    return {
        "claims": [
            {
                "key": "result_1",
                "claim_type": "RESULT",
                "text": "The reported gain is 12%.",
            }
        ],
        "evidence": [
            {
                "key": "evidence_1",
                "claim_keys": ["result_1"],
                "passage_ids": ["abstract"],
                "evidence_type": "SUPPORTS",
                "rationale": "The passage reports the measured result.",
            }
        ],
    }


def _response(
    payload: object,
    *,
    finish_reason: str = "stop",
    content: str | None = None,
) -> dict[str, object]:
    return {
        "id": "completion-1",
        "model": "DeepSeek-V4-Flash-2026-04-24",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(payload) if content is None else content,
                    "reasoning_content": None,
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_cache_hit_tokens": 25,
            "prompt_cache_miss_tokens": 75,
        },
    }


def _client(handler: httpx.MockTransport) -> DeepSeekClient:
    settings = DeepSeekSettings(
        provider="deepseek",
        model="deepseek-v4-flash",
        api_key="test-only-key",
    )
    return DeepSeekClient(
        settings,
        client=httpx.Client(transport=handler, base_url="https://api.deepseek.com"),
        retry_policy=HttpRetryPolicy(
            max_retries=2,
            request_timeout_seconds=5,
            total_timeout_seconds=20,
            backoff_seconds=0,
            max_retry_after_seconds=5,
        ),
        clock=lambda: datetime(2026, 8, 8, 5, tzinfo=UTC),
        sleep=lambda _delay: None,
    )


def test_deepseek_validates_and_maps_strict_json_without_exposing_reasoning() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers["Authorization"]
        observed["accept_encoding"] = request.headers["Accept-Encoding"]
        observed["body"] = json.loads(request.content)
        return httpx.Response(200, json=_response(_payload()))

    result = _client(httpx.MockTransport(handler)).analyze(_request())

    assert result.configured_model == "deepseek-v4-flash"
    assert result.model_version == "DeepSeek-V4-Flash-2026-04-24"
    assert result.prompt_version == "m2-analysis-v1"
    assert result.claims[0].key == "result_1"
    assert result.usage.total_tokens == 120
    assert result.usage.estimated_cost_usd is not None
    body = cast(dict[str, object], observed["body"])
    assert body["thinking"] == {"type": "disabled"}
    assert body["response_format"] == {"type": "json_object"}
    messages = cast(list[object], body["messages"])
    assert isinstance(messages, list)
    assert messages
    assert all(
        isinstance(message, dict)
        and isinstance(cast(dict[str, object], message).get("content"), str)
        for message in messages
    )
    serialized_body = json.dumps(body, sort_keys=True)
    for multimodal_field in ("image_url", "input_image", "images", "file_id"):
        assert multimodal_field not in serialized_body
    assert observed["authorization"] == "Bearer test-only-key"
    assert observed["accept_encoding"] == "identity"


def test_response_header_presentation_does_not_override_valid_decoded_content() -> None:
    calls = 0
    encoded = gzip.compress(json.dumps(_response(_payload())).encode())

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={
                "Content-Encoding": "gzip",
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Length": str(len(encoded) + 17),
            },
            stream=httpx.ByteStream(encoded),
        )

    result = _client(httpx.MockTransport(handler)).analyze(_request())

    assert result.claims[0].key == "result_1"
    assert calls == 1


def test_finish_reason_and_extra_keys_do_not_trigger_schema_retry() -> None:
    calls = 0
    payload = {
        **_payload(),
        "unexpected": True,
        "claims": [{**cast(list[dict[str, object]], _payload()["claims"])[0], "extra": 1}],
    }
    response_json = {
        **_response(payload, finish_reason="length"),
        "provider_metadata": {"request_id": "provider-only"},
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=response_json)

    result = _client(httpx.MockTransport(handler)).analyze(_request())

    assert result.claims[0].key == "result_1"
    assert calls == 1


def test_analysis_keeps_usable_grounded_items_and_normalizes_text_references() -> None:
    payload = _payload()
    payload["claims"] = [
        {"key": "bad", "claim_type": "UNSUPPORTED", "text": "Ignored."},
        {
            "key": "  result_1  ",
            "claim_type": "RESULT",
            "text": "  The reported gain is 12%.  ",
        },
    ]
    payload["evidence"] = [
        {
            "key": "bad_evidence",
            "claim_keys": ["result_1"],
            "passage_ids": ["abstract"],
            "evidence_type": "UNSUPPORTED",
        },
        {
            "key": "  evidence_1  ",
            "claim_keys": [None, "missing", "  result_1  ", "result_1"],
            "passage_ids": [12, "missing", "  abstract  ", "abstract"],
            "evidence_type": "SUPPORTS",
            "rationale": "   ",
        },
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response(payload))

    result = _client(httpx.MockTransport(handler)).analyze(_request())

    assert tuple(item.key for item in result.claims) == ("result_1",)
    assert result.claims[0].text == "The reported gain is 12%."
    assert tuple(item.key for item in result.evidence) == ("evidence_1",)
    assert result.evidence[0].claim_keys == ("result_1",)
    assert result.evidence[0].passage_ids == ("abstract",)
    assert result.evidence[0].rationale is None


@pytest.mark.parametrize(
    ("response_json", "match"),
    [
        (_response(_payload(), content=""), "empty structured output"),
        (_response(_payload(), content="not json"), "malformed JSON"),
        (
            _response(
                {
                    **_payload(),
                    "claims": [
                        {
                            "key": "result_1",
                            "claim_type": "RESULT",
                            "text": "invalid\x00claim",
                        }
                    ],
                }
            ),
            "domain validation",
        ),
        (
            _response(
                {
                    **_payload(),
                    "evidence": [
                        {
                            "key": "evidence_1",
                            "claim_keys": ["missing_claim"],
                            "passage_ids": ["abstract"],
                            "evidence_type": "SUPPORTS",
                        }
                    ],
                }
            ),
            "domain validation",
        ),
    ],
)
def test_invalid_model_output_is_rejected_without_retry(
    response_json: dict[str, object], match: str
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=response_json)

    with pytest.raises(LLMOutputError, match=match):
        _client(httpx.MockTransport(handler)).analyze(_request())
    assert calls == 1


def test_transient_status_retries_only_the_same_operation_with_a_bound() -> None:
    statuses = iter((503, 429, 200))
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        status = next(statuses)
        return (
            httpx.Response(status, headers={"Retry-After": "0"})
            if status != 200
            else httpx.Response(200, json=_response(_payload()))
        )

    result = _client(httpx.MockTransport(handler)).analyze(_request())
    assert result.claims
    assert result.usage.call_count == 3
    assert len(bodies) == 3
    assert bodies[0] == bodies[1] == bodies[2]


@pytest.mark.parametrize(
    ("status_code", "exception_type"),
    [
        (400, LLMRequestError),
        (401, LLMAuthenticationError),
        (422, LLMRequestError),
        (503, LLMUnavailableError),
    ],
)
def test_status_mapping_is_explicit(status_code: int, exception_type: type[Exception]) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code)

    with pytest.raises(exception_type):
        _client(httpx.MockTransport(handler)).analyze(_request())
    assert calls == (3 if status_code == 503 else 1)


def test_operation_scoped_settings_reject_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(LLMConfigurationError, match="non-empty DEEPSEEK_API_KEY"):
        DeepSeekSettings.from_environment()


@pytest.mark.parametrize(
    "api_key",
    [" key", "key ", "key value", "key\nvalue", "key\x7fvalue", "key-é"],
)
def test_settings_reject_unsafe_api_key_characters(api_key: str) -> None:
    with pytest.raises(ValueError, match="printable ASCII"):
        DeepSeekSettings(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key=api_key,
        )


@pytest.mark.parametrize(
    ("cache_hit", "cache_miss", "normalized_hit", "normalized_miss"),
    [
        (101, 0, 100, 0),
        (25, 70, 25, 75),
    ],
)
def test_inconsistent_usage_cache_counts_are_normalized_deterministically(
    cache_hit: int,
    cache_miss: int,
    normalized_hit: int,
    normalized_miss: int,
) -> None:
    response_json = _response(_payload())
    usage = response_json["usage"]
    assert isinstance(usage, dict)
    usage["prompt_cache_hit_tokens"] = cache_hit
    usage["prompt_cache_miss_tokens"] = cache_miss

    normalized_response = _response(_payload())
    normalized_usage = normalized_response["usage"]
    assert isinstance(normalized_usage, dict)
    normalized_usage["prompt_cache_hit_tokens"] = normalized_hit
    normalized_usage["prompt_cache_miss_tokens"] = normalized_miss

    result = _client(
        httpx.MockTransport(lambda _request: httpx.Response(200, json=response_json))
    ).analyze(_request())
    expected = _client(
        httpx.MockTransport(lambda _request: httpx.Response(200, json=normalized_response))
    ).analyze(_request())

    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (100, 20)
    assert result.usage.total_tokens == 120
    assert result.usage.estimated_cost_usd == expected.usage.estimated_cost_usd


@pytest.mark.parametrize("prompt_tokens", ["100", MAX_MODEL_TOKEN_COUNT + 1])
def test_usage_wrong_types_and_oversized_counts_are_rejected(prompt_tokens: object) -> None:
    response_json = _response(_payload())
    usage = response_json["usage"]
    assert isinstance(usage, dict)
    usage["prompt_tokens"] = prompt_tokens

    with pytest.raises(LLMOutputError, match="invalid response envelope"):
        _client(
            httpx.MockTransport(lambda _request: httpx.Response(200, json=response_json))
        ).analyze(_request())


def test_choice_index_rejects_a_coerced_json_string() -> None:
    response_json = _response(_payload())
    choices = response_json["choices"]
    assert isinstance(choices, list)
    choice = cast(dict[str, object], choices[0])
    choice["index"] = "0"

    with pytest.raises(LLMOutputError, match="invalid response envelope"):
        _client(
            httpx.MockTransport(lambda _request: httpx.Response(200, json=response_json))
        ).analyze(_request())


def test_completion_usage_cannot_exceed_the_requested_output_bound() -> None:
    response_json = _response(_payload())
    usage = response_json["usage"]
    assert isinstance(usage, dict)
    usage["prompt_tokens"] = 1
    usage["completion_tokens"] = 16_001
    usage["total_tokens"] = 16_002
    usage["prompt_cache_hit_tokens"] = 0
    usage["prompt_cache_miss_tokens"] = 1

    with pytest.raises(LLMOutputError, match="invalid response envelope"):
        _client(
            httpx.MockTransport(lambda _request: httpx.Response(200, json=response_json))
        ).analyze(_request())


def test_response_body_is_rejected_at_the_transport_size_bound_without_retry() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))

    with pytest.raises(LLMOutputError, match="size bound"):
        _client(httpx.MockTransport(handler)).analyze(_request())
    assert calls == 1


def test_deeply_nested_json_is_mapped_to_a_stable_output_error() -> None:
    deeply_nested_json = "[" * 10_000 + "0" + "]" * 10_000

    with pytest.raises(LLMOutputError, match="malformed JSON output"):
        _client(
            httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json=_response(_payload(), content=deeply_nested_json),
                )
            )
        ).analyze(_request())

    with pytest.raises(LLMOutputError, match="invalid response envelope"):
        _client(
            httpx.MockTransport(lambda _request: httpx.Response(200, content=deeply_nested_json))
        ).analyze(_request())


def test_slow_streaming_response_cannot_exceed_the_total_operation_deadline() -> None:
    clock = [0.0]
    calls = 0

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):  # type: ignore[no-untyped-def]
            clock[0] = 21.0
            yield b"{}"

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=SlowStream())

    settings = DeepSeekSettings(
        provider="deepseek",
        model="deepseek-v4-flash",
        api_key="test-only-key",
    )
    client = DeepSeekClient(
        settings,
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.deepseek.com",
        ),
        retry_policy=HttpRetryPolicy(
            max_retries=2,
            request_timeout_seconds=5,
            total_timeout_seconds=20,
            backoff_seconds=0,
            max_retry_after_seconds=5,
        ),
        monotonic=lambda: clock[0],
        sleep=lambda _delay: None,
    )

    with pytest.raises(LLMUnavailableError, match="TimeoutException"):
        client.analyze(_request())
    assert calls == 1
