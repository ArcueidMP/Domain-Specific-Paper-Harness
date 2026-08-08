from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from paper_harness.adapters.grobid import GrobidClient
from paper_harness.adapters.grobid import client as grobid_client_module
from paper_harness.ports.pdf_parser import (
    PdfParserAuthenticationError,
    PdfParseRequest,
    PdfParserOutputError,
    PdfParserRequestError,
    PdfParserUnavailableError,
)

_FIXTURE = Path("tests/contract/fixtures/grobid_fulltext_0_9_0.tei.xml").read_bytes()
_NOW = datetime(2026, 8, 8, 12, tzinfo=UTC)


def test_default_client_bypasses_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, bool] = {}
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/xml"},
            content=_FIXTURE,
            request=request,
        )

    def client_factory(*, follow_redirects: bool, trust_env: bool) -> httpx.Client:
        observed["follow_redirects"] = follow_redirects
        observed["trust_env"] = trust_env
        return real_client(
            follow_redirects=follow_redirects,
            trust_env=trust_env,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(grobid_client_module.httpx, "Client", client_factory)
    GrobidClient("http://grobid:8070", clock=lambda: _NOW).parse(_request())

    assert observed == {"follow_redirects": False, "trust_env": False}


def test_client_posts_strict_options_coordinates_and_ephemeral_bearer_token() -> None:
    requests: list[httpx.Request] = []
    token_calls = 0

    def token_provider() -> str:
        nonlocal token_calls
        token_calls += 1
        return "short-lived-private-token"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"Content-Type": "application/xml"}, content=_FIXTURE)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        parsed = GrobidClient(
            "https://grobid.internal.example/",
            bearer_token_provider=token_provider,
            client=http_client,
            clock=lambda: _NOW,
        ).parse(_request())

    assert parsed.parser_name == "grobid"
    assert parsed.parser_version == "0.9.0"
    assert parsed.call_count == 1
    assert parsed.duration_ms >= 0
    assert token_calls == 1
    assert len(requests) == 1
    outbound = requests[0]
    assert outbound.url.path == "/api/processFulltextDocument"
    assert outbound.headers["Authorization"] == "Bearer short-lived-private-token"
    assert outbound.headers["Accept"] == "application/xml"
    assert outbound.headers["Accept-Encoding"] == "identity"
    body = outbound.content
    for name, value in (
        ("consolidateHeader", "0"),
        ("consolidateCitations", "0"),
        ("consolidateFunders", "0"),
        ("includeRawCitations", "1"),
        ("segmentSentences", "1"),
        ("generateIDs", "1"),
    ):
        assert f'name="{name}"'.encode() in body
        assert f"\r\n\r\n{value}\r\n".encode() in body
    assert body.count(b'name="teiCoordinates"') == 5
    for coordinate_element in ("head", "p", "s", "ref", "biblStruct"):
        assert f"\r\n\r\n{coordinate_element}\r\n".encode() in body
    assert b'name="input"; filename="paper.pdf"' in body
    assert b"%PDF-1.7" in body


def test_client_retries_only_transient_status_for_the_same_operation() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/xml"},
            content=_FIXTURE,
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        parsed = GrobidClient(
            "http://grobid:8070",
            client=http_client,
            sleep=sleeps.append,
            monotonic=lambda: 0.0,
            clock=lambda: _NOW,
        ).parse(_request())

    assert parsed.sections[0].title == "Introduction"
    assert parsed.call_count == 2
    assert parsed.duration_ms == 0
    assert calls == 2
    assert sleeps == [0.0]


def test_client_retries_timeout_but_not_invalid_tei() -> None:
    calls = 0

    def timeout_once(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("test timeout", request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/xml"},
            content=_FIXTURE,
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(timeout_once)) as http_client:
        GrobidClient(
            "http://grobid:8070",
            client=http_client,
            retry_backoff_seconds=0,
            sleep=lambda _delay: None,
            monotonic=lambda: 0.0,
            clock=lambda: _NOW,
        ).parse(_request())
    assert calls == 2

    invalid_calls = 0

    def invalid_tei(request: httpx.Request) -> httpx.Response:
        nonlocal invalid_calls
        invalid_calls += 1
        return httpx.Response(
            200,
            headers={"Content-Type": "application/xml"},
            content=b"<TEI>",
            request=request,
        )

    with (
        httpx.Client(transport=httpx.MockTransport(invalid_tei)) as http_client,
        pytest.raises(PdfParserOutputError, match="malformed"),
    ):
        GrobidClient("http://grobid:8070", client=http_client, clock=lambda: _NOW).parse(_request())
    assert invalid_calls == 1


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (400, PdfParserRequestError),
        (422, PdfParserRequestError),
        (401, PdfParserAuthenticationError),
        (403, PdfParserAuthenticationError),
        (204, PdfParserOutputError),
        (404, PdfParserRequestError),
        (504, PdfParserRequestError),
    ],
)
def test_nontransient_statuses_are_mapped_without_retry(
    status_code: int, error_type: type[Exception]
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status_code,
            content=b"private response text must not escape",
            request=request,
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http_client,
        pytest.raises(error_type) as raised,
    ):
        GrobidClient("http://grobid:8070", client=http_client, clock=lambda: _NOW).parse(_request())
    assert calls == 1
    assert "private response text" not in str(raised.value)


def test_retry_after_above_bound_fails_without_an_early_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "31"}, request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http_client,
        pytest.raises(PdfParserUnavailableError, match="Retry-After"),
    ):
        GrobidClient("http://grobid:8070", client=http_client, clock=lambda: _NOW).parse(_request())
    assert calls == 1


def test_invalid_retry_after_uses_bounded_backoff() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, headers={"Retry-After": "NaN"}, request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/xml"},
            content=_FIXTURE,
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        GrobidClient(
            "http://grobid:8070",
            client=http_client,
            retry_backoff_seconds=2,
            sleep=sleeps.append,
            monotonic=lambda: 0.0,
            clock=lambda: _NOW,
        ).parse(_request())
    assert calls == 2
    assert sleeps == [2.0]


def test_client_rejects_empty_wrong_type_declared_oversized_and_large_pdf() -> None:
    responses = (
        httpx.Response(200, headers={"Content-Type": "application/xml"}, content=b""),
        httpx.Response(200, headers={"Content-Type": "text/plain"}, content=_FIXTURE),
        httpx.Response(
            200,
            headers={"Content-Type": "application/xml", "Content-Length": "100"},
            content=b"<TEI />",
        ),
        httpx.Response(200, headers={"Content-Type": "application/xml"}, content=_FIXTURE),
    )
    for response in responses:
        with (
            httpx.Client(transport=httpx.MockTransport(_return_response(response))) as http_client,
            pytest.raises(PdfParserOutputError),
        ):
            GrobidClient(
                "http://grobid:8070",
                client=http_client,
                max_tei_bytes=50,
                clock=lambda: _NOW,
            ).parse(_request())

    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _request: pytest.fail("HTTP must not be called"))
        ) as http_client,
        pytest.raises(PdfParserRequestError, match="input limit"),
    ):
        GrobidClient(
            "http://grobid:8070",
            client=http_client,
            max_pdf_bytes=5,
            clock=lambda: _NOW,
        ).parse(_request())


@pytest.mark.parametrize("content_encoding", ["gzip", "br"])
def test_client_rejects_encoded_tei_before_httpx_decompression(content_encoding: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={
                "Content-Encoding": content_encoding,
                "Content-Type": "application/xml",
            },
            stream=httpx.ByteStream(b"not-compressed-test-data"),
            request=request,
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http_client,
        pytest.raises(PdfParserOutputError, match="unsupported encoded response") as raised,
    ):
        GrobidClient(
            "http://grobid:8070",
            client=http_client,
            clock=lambda: _NOW,
        ).parse(_request())
    assert content_encoding not in str(raised.value)
    assert calls == 1


def test_token_provider_failure_is_sanitized_and_not_retried() -> None:
    http_calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(200, content=_FIXTURE)

    def token_provider() -> str:
        raise RuntimeError("secret-provider-detail")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http_client,
        pytest.raises(PdfParserAuthenticationError) as raised,
    ):
        GrobidClient(
            "https://grobid.internal.example",
            bearer_token_provider=token_provider,
            client=http_client,
            clock=lambda: _NOW,
        ).parse(_request())
    assert http_calls == 0
    assert "secret-provider-detail" not in str(raised.value)


@pytest.mark.parametrize("token", [" token", "token ", "token value", "token-é"])
def test_client_rejects_unsafe_bearer_tokens_before_transport(token: str) -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _request: pytest.fail("HTTP must not be called"))
        ) as http_client,
        pytest.raises(PdfParserAuthenticationError, match="invalid token"),
    ):
        GrobidClient(
            "https://grobid.internal.example",
            bearer_token_provider=lambda: token,
            client=http_client,
            clock=lambda: _NOW,
        ).parse(_request())


def test_client_rejects_limits_above_the_cloud_run_safe_bound() -> None:
    above_bound = 30 * 1024 * 1024 + 1
    with pytest.raises(ValueError, match="PDF limit"):
        GrobidClient("http://grobid:8070", max_pdf_bytes=above_bound)
    with pytest.raises(ValueError, match="TEI limit"):
        GrobidClient("http://grobid:8070", max_tei_bytes=above_bound)


def test_slow_streaming_tei_cannot_exceed_the_total_operation_deadline() -> None:
    clock = [0.0]
    calls = 0

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):  # type: ignore[no-untyped-def]
            clock[0] = 181.0
            yield _FIXTURE

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"Content-Type": "application/xml"},
            stream=SlowStream(),
            request=request,
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http_client,
        pytest.raises(PdfParserUnavailableError, match="timed out"),
    ):
        GrobidClient(
            "http://grobid:8070",
            client=http_client,
            retry_backoff_seconds=0,
            monotonic=lambda: clock[0],
            sleep=lambda _delay: None,
            clock=lambda: _NOW,
        ).parse(_request())
    assert calls == 1


def _request() -> PdfParseRequest:
    return PdfParseRequest(
        paper_id=UUID("8f018024-3b47-54ab-a248-326c3e2b96ae"),
        paper_version_id=UUID("a844bcec-145d-5f9a-96e8-82f06d8b58b5"),
        canonical_arxiv_id="2601.01234",
        arxiv_version=1,
        content=b"%PDF-1.7\nminimal deterministic test PDF\n%%EOF",
    )


def _return_response(response: httpx.Response) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return response

    return handler
