"""Ephemeral Cloud Run service-to-service identity tokens from the metadata server."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

from paper_harness.adapters.http_retry import HttpRetryPolicy, send_with_retry
from paper_harness.ports.pdf_parser import (
    PdfParserAuthenticationError,
    PdfParserUnavailableError,
)

_METADATA_IDENTITY_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
)
_MAX_ID_TOKEN_BYTES = 20_000
_JWT_PATTERN = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


class CloudRunIdTokenProvider:
    def __init__(
        self,
        audience: str,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 5.0,
        retry_policy: HttpRetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        normalized = audience.strip().rstrip("/")
        try:
            parsed = httpx.URL(normalized)
        except (TypeError, ValueError):
            raise ValueError("GROBID_AUDIENCE must be a valid HTTPS URL") from None
        if (
            parsed.scheme != "https"
            or not parsed.host
            or parsed.username
            or parsed.password
            or parsed.port is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("GROBID_AUDIENCE must be a plain HTTPS service URL")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("metadata identity timeout must be between 1 and 30 seconds")
        self._audience = normalized
        self._client = client
        self._retry_policy = retry_policy or HttpRetryPolicy(
            max_retries=2,
            request_timeout_seconds=timeout_seconds,
            total_timeout_seconds=max(timeout_seconds, 15.0),
            backoff_seconds=1.0,
            max_retry_after_seconds=5.0,
        )
        self._sleep = sleep
        self._monotonic = monotonic
        self._clock = clock or (lambda: datetime.now(UTC))

    def __call__(self) -> str:
        query = urlencode({"audience": self._audience, "format": "full"})
        url = f"{_METADATA_IDENTITY_URL}?{query}"

        if self._client is not None:
            return self._request_token(self._client, url)
        # The metadata endpoint is link-local and carries workload identity.
        # Never route this request through an environment-configured proxy.
        with httpx.Client(follow_redirects=False, trust_env=False) as client:
            return self._request_token(client, url)

    def _request_token(self, client: httpx.Client, url: str) -> str:
        operation_deadline = self._monotonic() + self._retry_policy.total_timeout_seconds

        def send(timeout: float) -> httpx.Response:
            outbound = client.build_request(
                "GET",
                url,
                headers={
                    "Accept-Encoding": "identity",
                    "Metadata-Flavor": "Google",
                },
                timeout=timeout,
            )
            response = client.send(outbound, stream=True)
            if response.status_code != 200:
                return response
            try:
                content = self._bounded_token_content(
                    response,
                    deadline=operation_deadline,
                )
            finally:
                response.close()
            return httpx.Response(
                status_code=200,
                headers=response.headers,
                content=content,
                request=outbound,
            )

        try:
            response = send_with_retry(
                send,
                policy=self._retry_policy,
                sleep=self._sleep,
                monotonic=self._monotonic,
                now=self._clock,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise PdfParserUnavailableError(
                "Cloud Run identity-token service is unavailable"
            ) from error
        try:
            if response.status_code in {429, 500, 502, 503}:
                raise PdfParserUnavailableError(
                    f"Cloud Run identity-token HTTP {response.status_code} "
                    "exhausted bounded retries"
                )
            if response.status_code != 200:
                raise PdfParserAuthenticationError(
                    f"Cloud Run identity-token endpoint returned HTTP {response.status_code}"
                )
            token = response.text
            if _JWT_PATTERN.fullmatch(token) is None:
                raise PdfParserAuthenticationError(
                    "Cloud Run identity-token endpoint returned an invalid token"
                )
            return token
        finally:
            response.close()

    def _bounded_token_content(self, response: httpx.Response, *, deadline: float) -> bytes:
        content_encoding = response.headers.get("Content-Encoding")
        if content_encoding is not None and content_encoding.strip().lower() not in {
            "",
            "identity",
        }:
            raise PdfParserAuthenticationError(
                "Cloud Run identity-token endpoint returned an invalid response"
            )
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                declared_bytes = int(declared)
            except ValueError:
                raise PdfParserAuthenticationError(
                    "Cloud Run identity-token endpoint returned an invalid response"
                ) from None
            if declared_bytes < 0 or declared_bytes > _MAX_ID_TOKEN_BYTES:
                raise PdfParserAuthenticationError(
                    "Cloud Run identity-token endpoint returned an invalid response"
                )
        content = bytearray()
        for chunk in response.iter_bytes():
            if self._monotonic() >= deadline:
                raise httpx.TimeoutException(
                    "Cloud Run identity-token response exceeded its total timeout"
                )
            content.extend(chunk)
            if len(content) > _MAX_ID_TOKEN_BYTES:
                raise PdfParserAuthenticationError(
                    "Cloud Run identity-token endpoint returned an invalid response"
                )
        if self._monotonic() >= deadline:
            raise httpx.TimeoutException(
                "Cloud Run identity-token response exceeded its total timeout"
            )
        return bytes(content)
