from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from paper_harness.adapters import gcp_identity as gcp_identity_module
from paper_harness.adapters.gcp_identity import CloudRunIdTokenProvider
from paper_harness.adapters.http_retry import HttpRetryPolicy
from paper_harness.ports.pdf_parser import (
    PdfParserAuthenticationError,
    PdfParserUnavailableError,
)


def test_default_metadata_client_bypasses_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, bool] = {}
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="header.payload.signature", request=request)

    def client_factory(*, follow_redirects: bool, trust_env: bool) -> httpx.Client:
        observed["follow_redirects"] = follow_redirects
        observed["trust_env"] = trust_env
        return real_client(
            follow_redirects=follow_redirects,
            trust_env=trust_env,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(gcp_identity_module.httpx, "Client", client_factory)
    token = CloudRunIdTokenProvider("https://paper-harness-grobid.example.run.app")()

    assert token == "header.payload.signature"
    assert observed == {"follow_redirects": False, "trust_env": False}


def test_cloud_run_identity_provider_requests_a_bounded_audience_token() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = request.url
        observed["accept_encoding"] = request.headers["Accept-Encoding"]
        observed["metadata_flavor"] = request.headers["Metadata-Flavor"]
        return httpx.Response(200, text="header.payload.signature", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        token = CloudRunIdTokenProvider(
            "https://paper-harness-grobid.example.run.app/",
            client=client,
        )()

    assert token == "header.payload.signature"
    assert observed["accept_encoding"] == "identity"
    assert observed["metadata_flavor"] == "Google"
    url = observed["url"]
    assert isinstance(url, httpx.URL)
    assert url.host == "metadata.google.internal"
    query = parse_qs(url.query.decode())
    assert query == {
        "audience": ["https://paper-harness-grobid.example.run.app"],
        "format": ["full"],
    }


@pytest.mark.parametrize(
    "audience",
    [
        "http://paper-harness-grobid.example.run.app",
        "https://user@paper-harness-grobid.example.run.app",
        "https://paper-harness-grobid.example.run.app:8443",
        "https://paper-harness-grobid.example.run.app/api",
        "https://paper-harness-grobid.example.run.app?token=bad",
    ],
)
def test_cloud_run_identity_provider_rejects_non_service_audiences(audience: str) -> None:
    with pytest.raises(ValueError, match="plain HTTPS service URL"):
        CloudRunIdTokenProvider(audience)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, text="private metadata response"),
        httpx.Response(200, text="not-a-jwt"),
        httpx.Response(200, text="header.payload.signature with-space"),
        httpx.Response(200, text="header.payload.signature\n"),
        httpx.Response(200, text="header.payléad.signature"),
    ],
)
def test_cloud_run_identity_provider_rejects_invalid_responses_without_leaking_body(
    response: httpx.Response,
) -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    response.status_code,
                    text=response.text,
                    request=request,
                )
            )
        ) as client,
        pytest.raises(PdfParserAuthenticationError) as raised,
    ):
        CloudRunIdTokenProvider(
            "https://paper-harness-grobid.example.run.app",
            client=client,
        )()
    assert "private metadata response" not in str(raised.value)


def test_cloud_run_identity_provider_retries_only_transient_metadata_failures() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, text="header.payload.signature", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        token = CloudRunIdTokenProvider(
            "https://paper-harness-grobid.example.run.app",
            client=client,
            retry_policy=_retry_policy(),
            sleep=lambda _delay: None,
            monotonic=lambda: 0.0,
        )()
    assert token == "header.payload.signature"
    assert calls == 2


def test_cloud_run_identity_provider_maps_exhausted_timeout_to_retryable_unavailable() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("metadata timeout", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(PdfParserUnavailableError) as raised,
    ):
        CloudRunIdTokenProvider(
            "https://paper-harness-grobid.example.run.app",
            client=client,
            retry_policy=_retry_policy(),
            sleep=lambda _delay: None,
            monotonic=lambda: 0.0,
        )()
    assert raised.value.retryable is True
    assert calls == 2


def test_cloud_run_identity_provider_bounds_the_streamed_token_body() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"x" * 20_001, request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(PdfParserAuthenticationError, match="invalid response"),
    ):
        CloudRunIdTokenProvider(
            "https://paper-harness-grobid.example.run.app",
            client=client,
        )()
    assert calls == 1


@pytest.mark.parametrize("content_encoding", ["gzip", "br"])
def test_cloud_run_identity_provider_rejects_encoded_response_before_decompression(
    content_encoding: str,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"Content-Encoding": content_encoding},
            stream=httpx.ByteStream(b"not-compressed-test-data"),
            request=request,
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(PdfParserAuthenticationError, match="invalid response") as raised,
    ):
        CloudRunIdTokenProvider(
            "https://paper-harness-grobid.example.run.app",
            client=client,
        )()
    assert content_encoding not in str(raised.value)
    assert calls == 1


def _retry_policy() -> HttpRetryPolicy:
    return HttpRetryPolicy(
        max_retries=1,
        request_timeout_seconds=2,
        total_timeout_seconds=5,
        backoff_seconds=0,
        max_retry_after_seconds=1,
    )
