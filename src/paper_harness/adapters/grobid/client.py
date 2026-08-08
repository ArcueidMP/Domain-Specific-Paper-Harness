"""Bounded authenticated httpx client for the private GROBID service."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import httpx

from paper_harness.adapters.http_retry import HttpRetryPolicy, send_with_retry
from paper_harness.domain.analysis import ParsedPaper
from paper_harness.ports.pdf_parser import (
    PdfParserAuthenticationError,
    PdfParserConfigurationError,
    PdfParseRequest,
    PdfParserOutputError,
    PdfParserPortError,
    PdfParserRequestError,
    PdfParserUnavailableError,
)

from .tei import DEFAULT_MAX_TEI_BYTES, map_grobid_tei

BearerTokenProvider = Callable[[], str]

_PROCESS_FULLTEXT_PATH = "/api/processFulltextDocument"
_RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503})
_AUTHENTICATION_HTTP_STATUSES = frozenset({401, 403})
_REQUEST_HTTP_STATUSES = frozenset({400, 422})
_TEI_COORDINATE_ELEMENTS = ("head", "p", "s", "ref", "biblStruct")
_DEFAULT_MAX_PDF_BYTES = 30 * 1024 * 1024


class GrobidClient:
    """Parse one PDF with GROBID 0.9.0 and no alternate parser or metadata lookup."""

    def __init__(
        self,
        base_url: str,
        *,
        bearer_token_provider: BearerTokenProvider | None = None,
        client: httpx.Client | None = None,
        max_retries: int = 2,
        request_timeout_seconds: float = 60.0,
        retry_backoff_seconds: float = 1.0,
        max_retry_after_seconds: float = 30.0,
        max_total_seconds: float = 180.0,
        max_pdf_bytes: int = _DEFAULT_MAX_PDF_BYTES,
        max_tei_bytes: int = DEFAULT_MAX_TEI_BYTES,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._endpoint = _validated_endpoint(base_url)
        if not 0 <= max_retries <= 3:
            raise ValueError("GROBID max_retries must be between 0 and 3")
        if not 1 <= request_timeout_seconds <= 300:
            raise ValueError("GROBID request timeout must be between 1 and 300 seconds")
        if not 0 <= retry_backoff_seconds <= 30:
            raise ValueError("GROBID retry backoff must be between 0 and 30 seconds")
        if not 1 <= max_retry_after_seconds <= 300:
            raise ValueError("GROBID Retry-After bound must be between 1 and 300 seconds")
        if not 1 <= max_total_seconds <= 900:
            raise ValueError("GROBID total-operation bound must be between 1 and 900 seconds")
        if not 1 <= max_pdf_bytes <= _DEFAULT_MAX_PDF_BYTES:
            raise ValueError("GROBID PDF limit must be between 1 and 30 MiB")
        if not 1 <= max_tei_bytes <= DEFAULT_MAX_TEI_BYTES:
            raise ValueError("GROBID TEI limit must be between 1 and 30 MiB")

        self._bearer_token_provider = bearer_token_provider
        self._client = client
        self._retry_policy = HttpRetryPolicy(
            max_retries=max_retries,
            request_timeout_seconds=request_timeout_seconds,
            total_timeout_seconds=max_total_seconds,
            backoff_seconds=retry_backoff_seconds,
            max_retry_after_seconds=max_retry_after_seconds,
        )
        self._max_pdf_bytes = max_pdf_bytes
        self._max_tei_bytes = max_tei_bytes
        self._sleep = sleep
        self._monotonic = monotonic
        self._clock = clock or (lambda: datetime.now(UTC))

    def parse(self, request: PdfParseRequest) -> ParsedPaper:
        started = self._monotonic()
        if len(request.content) > self._max_pdf_bytes:
            raise PdfParserRequestError("PDF exceeds the configured GROBID input limit")
        if self._client is not None:
            response, call_count = self._post_with_retries(self._client, request)
        else:
            # GROBID is a private service boundary. Environment proxy settings
            # must not redirect PDFs or identity tokens through another host.
            with httpx.Client(follow_redirects=False, trust_env=False) as client:
                response, call_count = self._post_with_retries(client, request)
        try:
            content = self._validated_tei_content(response)
        finally:
            response.close()
        parsed_at = self._clock()
        if parsed_at.tzinfo is None or parsed_at.utcoffset() is None:
            raise PdfParserConfigurationError("GROBID clock must return a timezone-aware time")
        return replace(
            map_grobid_tei(
                content,
                paper_id=request.paper_id,
                paper_version_id=request.paper_version_id,
                parsed_at=parsed_at.astimezone(UTC),
                max_tei_bytes=self._max_tei_bytes,
            ),
            call_count=call_count,
            duration_ms=max(0, round((self._monotonic() - started) * 1000)),
        )

    def _post_with_retries(
        self, client: httpx.Client, request: PdfParseRequest
    ) -> tuple[httpx.Response, int]:
        operation_deadline = self._monotonic() + self._retry_policy.total_timeout_seconds
        call_count = 0

        def send(timeout: float) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            outbound = client.build_request(
                "POST",
                self._endpoint,
                headers=self._request_headers(),
                data=_request_form_data(),
                files={"input": ("paper.pdf", request.content, "application/pdf")},
                timeout=timeout,
            )
            response = client.send(outbound, stream=True)
            if response.status_code != 200:
                return response
            try:
                content = self._bounded_tei_response_content(
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
        except httpx.TimeoutException:
            raise PdfParserUnavailableError("GROBID timed out after bounded retries") from None
        except httpx.HTTPError as error:
            raise PdfParserPortError(
                f"GROBID HTTP request failed ({type(error).__name__})"
            ) from None
        if response.status_code in _RETRYABLE_HTTP_STATUSES:
            status_code = response.status_code
            response.close()
            raise PdfParserUnavailableError(
                f"GROBID transient HTTP {status_code} exhausted bounded retries or "
                "its Retry-After exceeded the configured bound"
            )
        return response, call_count

    def _bounded_tei_response_content(
        self,
        response: httpx.Response,
        *,
        deadline: float,
    ) -> bytes:
        content_encoding = response.headers.get("Content-Encoding")
        if content_encoding is not None and content_encoding.strip().lower() not in {
            "",
            "identity",
        }:
            raise PdfParserOutputError("GROBID returned an unsupported encoded response")
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                raise PdfParserOutputError("GROBID returned an invalid Content-Length") from None
            if declared_length < 0:
                raise PdfParserOutputError("GROBID returned an invalid Content-Length")
            if declared_length > self._max_tei_bytes:
                raise PdfParserOutputError(
                    "GROBID declared a TEI document above the configured limit"
                )
        content_type = response.headers.get("Content-Type")
        if content_type is not None:
            media_type = content_type.split(";", maxsplit=1)[0].strip().lower()
            if media_type not in {"application/xml", "text/xml"}:
                raise PdfParserOutputError("GROBID returned a non-XML content type")
        content = bytearray()
        for chunk in response.iter_bytes():
            if self._monotonic() >= deadline:
                raise httpx.TimeoutException("GROBID response exceeded the total operation timeout")
            content.extend(chunk)
            if len(content) > self._max_tei_bytes:
                raise PdfParserOutputError(
                    "GROBID returned a TEI document above the configured limit"
                )
        if self._monotonic() >= deadline:
            raise httpx.TimeoutException("GROBID response exceeded the total operation timeout")
        return bytes(content)

    def _request_headers(self) -> dict[str, str]:
        # Request identity encoding so the response-size bound applies to the
        # exact TEI bytes and cannot be undermined by transport decompression.
        headers = {"Accept": "application/xml", "Accept-Encoding": "identity"}
        if self._bearer_token_provider is None:
            return headers
        try:
            token = self._bearer_token_provider()
        except PdfParserPortError:
            raise
        except Exception:
            raise PdfParserAuthenticationError("GROBID bearer token acquisition failed") from None
        if not token or any(not 33 <= ord(character) <= 126 for character in token):
            raise PdfParserAuthenticationError(
                "GROBID bearer token provider returned an invalid token"
            )
        headers["Authorization"] = f"Bearer {token}"
        return headers

    def _validated_tei_content(self, response: httpx.Response) -> bytes:
        status_code = response.status_code
        if status_code in _AUTHENTICATION_HTTP_STATUSES:
            raise PdfParserAuthenticationError(
                f"GROBID authentication failed with HTTP {status_code}"
            )
        if status_code in _REQUEST_HTTP_STATUSES:
            raise PdfParserRequestError(f"GROBID rejected the PDF with HTTP {status_code}")
        if status_code == 204:
            raise PdfParserOutputError("GROBID returned no extractable TEI content")
        if status_code != 200:
            raise PdfParserRequestError(f"GROBID returned unsupported HTTP {status_code}")

        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                raise PdfParserOutputError("GROBID returned an invalid Content-Length") from None
            if declared_length < 0:
                raise PdfParserOutputError("GROBID returned an invalid Content-Length")
            if declared_length > self._max_tei_bytes:
                raise PdfParserOutputError(
                    "GROBID declared a TEI document above the configured limit"
                )

        content_type = response.headers.get("Content-Type")
        if content_type is not None:
            media_type = content_type.split(";", maxsplit=1)[0].strip().lower()
            if media_type not in {"application/xml", "text/xml"}:
                raise PdfParserOutputError("GROBID returned a non-XML content type")

        content = response.content
        if len(content) > self._max_tei_bytes:
            raise PdfParserOutputError("GROBID returned a TEI document above the configured limit")
        if not content.strip():
            raise PdfParserOutputError("GROBID returned an empty TEI document")
        return content


def _request_form_data() -> dict[str, str | list[str]]:
    return {
        "consolidateHeader": "0",
        "consolidateCitations": "0",
        "consolidateFunders": "0",
        "includeRawCitations": "1",
        "segmentSentences": "1",
        "generateIDs": "1",
        "teiCoordinates": list(_TEI_COORDINATE_ELEMENTS),
    }


def _validated_endpoint(base_url: str) -> str:
    value = base_url.strip()
    if not value:
        raise PdfParserConfigurationError("GROBID_URL is required for full-text parsing")
    try:
        url = httpx.URL(value)
    except (TypeError, ValueError):
        raise PdfParserConfigurationError("GROBID_URL must be a valid HTTP URL") from None
    if url.scheme not in {"http", "https"} or not url.host:
        raise PdfParserConfigurationError("GROBID_URL must use HTTP or HTTPS with a host")
    if url.username or url.password:
        raise PdfParserConfigurationError("GROBID_URL must not contain credentials")
    if url.query or url.fragment:
        raise PdfParserConfigurationError("GROBID_URL must not contain a query or fragment")
    return f"{str(url).rstrip('/')}{_PROCESS_FULLTEXT_PATH}"
