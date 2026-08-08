"""Bounded arxiv.py implementation of the discovery port."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar, cast
from urllib.parse import parse_qs, urlsplit
from xml.parsers import expat

import arxiv  # pyright: ignore[reportMissingTypeStubs]
import requests
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeoutError

from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import parse_arxiv_identifier
from paper_harness.ports.arxiv import (
    ArxivPaperRecord,
    ArxivPdf,
    ArxivPdfError,
    ArxivResponseError,
    ArxivResultLimitError,
    ArxivUnavailableError,
)

_RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503})
_ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
_OPENSEARCH_NAMESPACE = "http://a9.com/-/spec/opensearch/1.1/"
_XML_NAMESPACE_SEPARATOR = "\x1f"
_ATOM_FEED = f"{_ATOM_NAMESPACE}{_XML_NAMESPACE_SEPARATOR}feed"
_ATOM_ENTRY = f"{_ATOM_NAMESPACE}{_XML_NAMESPACE_SEPARATOR}entry"
_REQUIRED_ENTRY_FIELDS = frozenset(
    {
        f"{_ATOM_NAMESPACE}{_XML_NAMESPACE_SEPARATOR}id",
        f"{_ATOM_NAMESPACE}{_XML_NAMESPACE_SEPARATOR}updated",
        f"{_ATOM_NAMESPACE}{_XML_NAMESPACE_SEPARATOR}published",
    }
)
_PAGINATION_FIELDS = frozenset(
    {
        f"{_OPENSEARCH_NAMESPACE}{_XML_NAMESPACE_SEPARATOR}totalResults",
        f"{_OPENSEARCH_NAMESPACE}{_XML_NAMESPACE_SEPARATOR}startIndex",
        f"{_OPENSEARCH_NAMESPACE}{_XML_NAMESPACE_SEPARATOR}itemsPerPage",
    }
)
_RFC3339_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\Z"
)
_MAX_ATOM_DEPTH = 128
_StreamResult = TypeVar("_StreamResult")


class ArxivClient:
    """Discover complete arXiv windows with bounded transport behavior."""

    def __init__(
        self,
        *,
        page_size: int = 100,
        max_pages: int = 50,
        delay_seconds: float = 3.0,
        max_retries: int = 2,
        request_timeout_seconds: float = 20.0,
        retry_backoff_seconds: float = 1.0,
        max_retry_after_seconds: float = 30.0,
        max_total_seconds: float = 90.0,
        atom_max_bytes: int = 16 * 1024 * 1024,
        pdf_max_bytes: int = 30 * 1024 * 1024,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= page_size <= 2000:
            raise ValueError("arXiv page_size must be between 1 and 2000")
        if not 1 <= max_pages <= 100:
            raise ValueError("arXiv max_pages must be between 1 and 100")
        if not 0 <= delay_seconds <= 30:
            raise ValueError("arXiv delay_seconds must be between 0 and 30")
        if not 0 <= max_retries <= 3:
            raise ValueError("arXiv max_retries must be between 0 and 3")
        if not 1 <= request_timeout_seconds <= 60:
            raise ValueError("arXiv request timeout must be between 1 and 60 seconds")
        if not 0 <= retry_backoff_seconds <= 10:
            raise ValueError("arXiv retry backoff must be between 0 and 10 seconds")
        if not 1 <= max_retry_after_seconds <= 60:
            raise ValueError("arXiv Retry-After bound must be between 1 and 60 seconds")
        if not 1 <= max_total_seconds <= 300:
            raise ValueError("arXiv total-operation bound must be between 1 and 300 seconds")
        if not 1024 <= atom_max_bytes <= 100 * 1024 * 1024:
            raise ValueError("arXiv Atom size bound must be between 1 KiB and 100 MiB")
        if not 1024 <= pdf_max_bytes <= 100 * 1024 * 1024:
            raise ValueError("arXiv PDF size bound must be between 1 KiB and 100 MiB")

        # arxiv.py owns query encoding, pagination, Atom parsing, and pacing. Its
        # broad built-in retry loop is disabled so only the explicit policy below
        # retries timeouts and the approved transient HTTP statuses.
        self._client = ValidatedArxivClient(
            page_size=page_size,
            delay_seconds=delay_seconds,
            num_retries=0,
            max_pages=max_pages,
        )
        self._session = BoundedArxivSession(
            request_timeout_seconds=request_timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            max_retry_after_seconds=max_retry_after_seconds,
            max_total_seconds=max_total_seconds,
            atom_max_bytes=atom_max_bytes,
            sleep=sleep,
            monotonic=monotonic,
        )
        self._client._session = self._session  # pyright: ignore[reportPrivateUsage]
        self._pdf_max_bytes = pdf_max_bytes

    def search(
        self,
        *,
        query: str,
        updated_from: datetime,
        updated_until: datetime,
        max_results: int,
    ) -> tuple[ArxivPaperRecord, ...]:
        if updated_from.tzinfo is None or updated_until.tzinfo is None:
            raise ValueError("arXiv discovery window must be timezone-aware")
        if updated_from > updated_until:
            raise ValueError("arXiv discovery start cannot follow its end")
        if not 1 <= max_results <= 5000:
            raise ValueError("arXiv max_results must be between 1 and 5000")

        # The upstream result cap is deliberately disabled because it counts rows
        # before this adapter applies its time window. The adapter stops only after
        # observing max_results + 1 in-window rows, an older boundary, or feed
        # exhaustion. Deadline/page bounds raise rather than returning partial data.
        search = arxiv.Search(
            query=query,
            max_results=None,
            sort_by=arxiv.SortCriterion.LastUpdatedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        records: list[ArxivPaperRecord] = []
        previous_updated_at: datetime | None = None
        with self._session.operation(), self._client.page_operation():
            try:
                for result in self._client.results(search):
                    updated_at = _as_utc(result.updated)
                    if previous_updated_at is not None and updated_at > previous_updated_at:
                        raise ArxivResponseError(
                            "arXiv results are not sorted by descending update time"
                        )
                    previous_updated_at = updated_at
                    if updated_at > updated_until:
                        continue
                    if updated_at < updated_from:
                        break
                    if len(records) == max_results:
                        raise ArxivResultLimitError(
                            "arXiv result window reached max_results before exhaustion; "
                            "cursor was not advanced"
                        )
                    records.append(map_arxiv_result(result))
            except ArxivResultLimitError:
                raise
            except arxiv.HTTPError as error:
                if error.status in _RETRYABLE_HTTP_STATUSES:
                    raise ArxivUnavailableError(
                        f"arXiv transient HTTP {error.status} exhausted bounded retries"
                    ) from error
                raise ArxivResponseError(
                    f"arXiv rejected the request with HTTP {error.status}"
                ) from error
            except arxiv.UnexpectedEmptyPageError as error:
                raise ArxivUnavailableError("arXiv returned an unexpected empty page") from error
            except requests.exceptions.RequestException as error:
                raise ArxivUnavailableError(
                    f"bounded arXiv transport failed with {type(error).__name__}"
                ) from error
            except ValueError as error:
                raise ArxivResponseError("arXiv returned invalid feed data") from error
        return tuple(records)

    def download_pdf(
        self,
        *,
        canonical_arxiv_id: str,
        version: int,
        pdf_url: str,
    ) -> ArxivPdf:
        expected_path = f"/pdf/{canonical_arxiv_id}v{version}"
        parsed_url = urlsplit(pdf_url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != "arxiv.org"
            or parsed_url.port is not None
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
            or parsed_url.path.rstrip("/").removesuffix(".pdf").lower() != expected_path.lower()
        ):
            raise ArxivPdfError("PDF URL does not match the requested canonical arXiv version")

        def consume(response: requests.Response) -> bytes:
            if response.status_code in _RETRYABLE_HTTP_STATUSES:
                raise ArxivUnavailableError(
                    f"arXiv PDF HTTP {response.status_code} exhausted bounded retries"
                )
            if response.status_code != 200:
                raise ArxivPdfError(
                    f"arXiv PDF request was rejected with HTTP {response.status_code}"
                )
            _require_identity_content_encoding(
                response,
                error_type=ArxivPdfError,
                resource="PDF",
            )
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
            if content_type.strip().lower() != "application/pdf":
                raise ArxivPdfError("arXiv PDF response has an invalid content type")
            declared_length = response.headers.get("Content-Length")
            if declared_length is not None:
                try:
                    parsed_length = int(declared_length)
                    if parsed_length < 0:
                        raise ValueError
                    if parsed_length > self._pdf_max_bytes:
                        raise ArxivPdfError("arXiv PDF exceeds the configured size bound")
                except ValueError as error:
                    raise ArxivPdfError(
                        "arXiv PDF response has an invalid content length"
                    ) from error
            content = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                self._session.check_deadline()
                if not chunk:
                    continue
                content.extend(chunk)
                if len(content) > self._pdf_max_bytes:
                    raise ArxivPdfError("arXiv PDF exceeds the configured size bound")
            return bytes(content)

        try:
            with self._session.operation():
                content = self._session.consume_stream(
                    pdf_url,
                    consume=consume,
                    allow_redirects=False,
                )
        except ArxivPdfError:
            raise
        except requests.exceptions.RequestException as error:
            raise ArxivUnavailableError(
                f"bounded arXiv PDF transport failed with {type(error).__name__}"
            ) from error

        if not content.startswith(b"%PDF-"):
            raise ArxivPdfError("arXiv PDF response is missing its PDF signature")
        return ArxivPdf(
            canonical_arxiv_id=canonical_arxiv_id,
            version=version,
            source_url=pdf_url,
            content=content,
        )


class ValidatedArxivClient(arxiv.Client):
    """Narrow arxiv.py hook for page bounds and strict ParsedFeed validation."""

    def __init__(
        self,
        *,
        page_size: int,
        delay_seconds: float,
        num_retries: int,
        max_pages: int,
    ) -> None:
        super().__init__(
            page_size=page_size,
            delay_seconds=delay_seconds,
            num_retries=num_retries,
        )
        self._max_pages = max_pages
        self._page_count: int | None = None
        self._last_result_updated_at: datetime | None = None
        self._pinned_total_results: int | None = None

    @contextmanager
    def page_operation(self) -> Generator[None]:
        if self._page_count is not None:
            raise RuntimeError("arXiv page operation is already active")
        self._page_count = 0
        self._last_result_updated_at = None
        self._pinned_total_results = None
        try:
            yield
        finally:
            self._page_count = None
            self._last_result_updated_at = None
            self._pinned_total_results = None

    def _parse_feed(self, url: str, first_page: bool = True, _try_index: int = 0) -> Any:
        if self._page_count is None:
            raise RuntimeError("arXiv feed parsing requires an active page operation")
        if self._page_count >= self._max_pages:
            raise ArxivResultLimitError(
                "arXiv page bound was reached before window exhaustion; cursor was not advanced"
            )
        self._page_count += 1
        requested_start_index = _requested_start_index(url)
        feed = super()._parse_feed(url, first_page=first_page, _try_index=_try_index)
        if feed.malformed:
            raise ArxivResponseError("arXiv returned a malformed Atom feed")
        if feed.header.start_index != requested_start_index:
            raise ArxivResponseError("arXiv Atom startIndex does not match the requested page")
        if self._pinned_total_results is None:
            self._pinned_total_results = feed.header.total_results
        elif feed.header.total_results != self._pinned_total_results:
            raise ArxivResponseError("arXiv Atom totalResults changed during pagination")
        for result in feed.results:
            updated_at = _as_utc(result.updated)
            if (
                self._last_result_updated_at is not None
                and updated_at > self._last_result_updated_at
            ):
                raise ArxivResponseError("arXiv results are not sorted by descending update time")
            self._last_result_updated_at = updated_at
        return feed


class BoundedArxivSession(requests.Session):
    """requests session with the repository's narrow transient retry policy."""

    def __init__(
        self,
        *,
        request_timeout_seconds: float,
        max_retries: int,
        retry_backoff_seconds: float,
        max_retry_after_seconds: float,
        max_total_seconds: float,
        atom_max_bytes: int = 16 * 1024 * 1024,
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
    ) -> None:
        super().__init__()
        self._request_timeout_seconds = request_timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._max_retry_after_seconds = max_retry_after_seconds
        self._max_total_seconds = max_total_seconds
        self._atom_max_bytes = atom_max_bytes
        self._sleep = sleep
        self._monotonic = monotonic
        self._deadline: float | None = None
        self.headers["Accept-Encoding"] = "identity"

    @contextmanager
    def operation(self) -> Generator[None]:
        if self._deadline is not None:
            raise RuntimeError("arXiv session operation is already active")
        self._deadline = self._monotonic() + self._max_total_seconds
        try:
            yield
        finally:
            self._deadline = None

    def get(self, url: str, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        _validate_atom_request_url(url)
        kwargs["allow_redirects"] = False
        return self.consume_stream(url, consume=self._consume_atom_response, **kwargs)

    def _consume_atom_response(self, response: requests.Response) -> requests.Response:
        """Buffer one successful Atom response without exposing a partial body."""

        if response.status_code != requests.codes.OK:
            return response
        _require_identity_content_encoding(
            response,
            error_type=ArxivResponseError,
            resource="Atom",
        )
        declared_length = response.headers.get("Content-Length")
        if declared_length is not None:
            if not declared_length.isascii() or not declared_length.isdigit():
                raise ArxivResponseError("arXiv Atom response has an invalid content length")
            try:
                parsed_length = int(declared_length)
            except ValueError as error:
                raise ArxivResponseError(
                    "arXiv Atom response has an invalid content length"
                ) from error
            if parsed_length > self._atom_max_bytes:
                raise ArxivResponseError("arXiv Atom response exceeds the configured size bound")

        content = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            self.check_deadline()
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > self._atom_max_bytes:
                raise ArxivResponseError("arXiv Atom response exceeds the configured size bound")

        _validate_atom_document(bytes(content))

        # arxiv.py consumes Response.content after this streamed response has
        # been closed. Buffering the complete body restores that requests API
        # contract while ensuring a failed attempt can never expose its prefix.
        buffered_response = cast(Any, response)
        buffered_response._content = bytes(content)
        buffered_response._content_consumed = True
        return response

    def consume_stream(
        self,
        url: str,
        *,
        consume: Callable[[requests.Response], _StreamResult],
        **kwargs: Any,
    ) -> _StreamResult:
        """Consume one streamed GET inside the same bounded retry operation."""

        kwargs["stream"] = True
        for attempt in range(self._max_retries + 1):
            kwargs["timeout"] = min(self._request_timeout_seconds, self._remaining_seconds())
            try:
                response = super().get(url, **kwargs)
            except requests.exceptions.RequestException as error:
                if not _is_retryable_stream_timeout(error):
                    raise
                if attempt == self._max_retries:
                    raise
                self._bounded_sleep(self._retry_backoff_seconds * (2**attempt))
                continue

            if response.status_code in _RETRYABLE_HTTP_STATUSES and attempt < self._max_retries:
                delay = _retry_delay_seconds(
                    response,
                    attempt=attempt,
                    backoff_seconds=self._retry_backoff_seconds,
                    max_retry_after_seconds=self._max_retry_after_seconds,
                )
                if delay is not None:
                    response.close()
                    self._bounded_sleep(delay)
                    continue

            try:
                with response:
                    result = consume(response)
                    self.check_deadline()
                    return result
            except requests.exceptions.RequestException as error:
                if not _is_retryable_stream_timeout(error):
                    raise
                if attempt == self._max_retries:
                    raise
                self._bounded_sleep(self._retry_backoff_seconds * (2**attempt))
        raise AssertionError("bounded streamed retry loop must return or raise")

    def check_deadline(self) -> None:
        """Raise when the active operation has exceeded its total deadline."""

        self._remaining_seconds()

    def _remaining_seconds(self) -> float:
        if self._deadline is None:
            raise RuntimeError("arXiv session requests require an active operation")
        remaining = self._deadline - self._monotonic()
        if remaining <= 0:
            raise requests.exceptions.Timeout("arXiv total-operation deadline exceeded")
        return remaining

    def _bounded_sleep(self, delay: float) -> None:
        if delay > self._remaining_seconds():
            raise requests.exceptions.Timeout("arXiv retry would exceed total-operation deadline")
        self._sleep(delay)


def _retry_delay_seconds(
    response: requests.Response,
    *,
    attempt: int,
    backoff_seconds: float,
    max_retry_after_seconds: float,
) -> float | None:
    retry_after = response.headers.get("Retry-After")
    if retry_after is None:
        return min(backoff_seconds * (2**attempt), max_retry_after_seconds)
    try:
        delay = float(retry_after)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
        except (TypeError, ValueError, OverflowError):
            return min(backoff_seconds * (2**attempt), max_retry_after_seconds)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        delay = max(0.0, (retry_at.astimezone(UTC) - datetime.now(UTC)).total_seconds())
    if not math.isfinite(delay) or delay < 0:
        return min(backoff_seconds * (2**attempt), max_retry_after_seconds)
    if delay > max_retry_after_seconds:
        # Do not retry earlier than the server requested; fail this bounded call.
        return None
    return max(0.0, delay)


def _is_retryable_stream_timeout(error: requests.exceptions.RequestException) -> bool:
    if isinstance(error, requests.exceptions.Timeout):
        return True
    return isinstance(error, requests.exceptions.ConnectionError) and any(
        isinstance(argument, Urllib3ReadTimeoutError) for argument in error.args
    )


def _validate_atom_request_url(url: str) -> None:
    try:
        parsed_url = urlsplit(url)
        port = parsed_url.port
    except (TypeError, ValueError) as error:
        raise ArxivResponseError("arXiv Atom request URL is invalid") from error
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "export.arxiv.org"
        or port is not None
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.path != "/api/query"
        or parsed_url.fragment
    ):
        raise ArxivResponseError("arXiv Atom request URL is outside the approved endpoint")


def _requested_start_index(url: str) -> int:
    try:
        values = parse_qs(urlsplit(url).query, keep_blank_values=True, strict_parsing=True).get(
            "start"
        )
    except ValueError as error:
        raise ArxivResponseError("arXiv Atom request pagination is invalid") from error
    if values is None or len(values) != 1 or not values[0].isascii() or not values[0].isdigit():
        raise ArxivResponseError("arXiv Atom request pagination is invalid")
    return int(values[0])


def _require_identity_content_encoding(
    response: requests.Response,
    *,
    error_type: type[ArxivResponseError] | type[ArxivPdfError],
    resource: str,
) -> None:
    content_encoding = response.headers.get("Content-Encoding", "")
    if content_encoding and content_encoding.strip().lower() != "identity":
        raise error_type(f"arXiv {resource} response uses an unsupported content encoding")


def _validate_atom_document(content: bytes) -> None:
    """Validate the bounded Atom bytes before arxiv.py's recovering parser sees them."""

    element_stack: list[str] = []
    text_stack: list[list[str]] = []
    current_entry: dict[str, str] | None = None
    pagination: dict[str, str] = {}
    entry_count = 0
    last_entry_updated_at: datetime | None = None

    parser = expat.ParserCreate(namespace_separator=_XML_NAMESPACE_SEPARATOR)
    parser.buffer_text = True
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def reject_dtd_or_entity(*_args: Any) -> None:
        raise ArxivResponseError("arXiv Atom response contains a forbidden DTD or entity")

    def reject_external_entity(*_args: Any) -> int:
        raise ArxivResponseError("arXiv Atom response contains a forbidden external entity")

    def start_element(name: str, _attributes: dict[str, str]) -> None:
        nonlocal current_entry, entry_count
        if len(element_stack) >= _MAX_ATOM_DEPTH:
            raise ArxivResponseError("arXiv Atom response exceeds the XML depth bound")
        if not element_stack:
            if name != _ATOM_FEED:
                raise ArxivResponseError("arXiv Atom response has an invalid feed root")
        elif len(element_stack) == 1 and name == _ATOM_ENTRY:
            current_entry = {}
            entry_count += 1
        element_stack.append(name)
        text_stack.append([])

    def character_data(value: str) -> None:
        if text_stack:
            text_stack[-1].append(value)

    def end_element(name: str) -> None:
        nonlocal current_entry, last_entry_updated_at
        if not element_stack or element_stack[-1] != name:
            raise ArxivResponseError("arXiv returned a malformed Atom feed")
        value = "".join(text_stack[-1]).strip()
        if len(element_stack) == 2 and name in _PAGINATION_FIELDS:
            if name in pagination:
                raise ArxivResponseError("arXiv Atom pagination metadata is duplicated")
            pagination[name] = value
        elif (
            len(element_stack) == 3
            and element_stack[-2] == _ATOM_ENTRY
            and name in _REQUIRED_ENTRY_FIELDS
        ):
            if current_entry is None:
                raise ArxivResponseError("arXiv Atom entry validation state is invalid")
            if name in current_entry:
                raise ArxivResponseError("arXiv Atom entry metadata is duplicated")
            current_entry[name] = value
        elif len(element_stack) == 2 and name == _ATOM_ENTRY:
            if current_entry is None:
                raise ArxivResponseError("arXiv Atom entry validation state is invalid")
            missing = _REQUIRED_ENTRY_FIELDS.difference(current_entry)
            if missing or any(not current_entry[field] for field in _REQUIRED_ENTRY_FIELDS):
                raise ArxivResponseError("arXiv Atom entry is missing required metadata")
            updated_at = _parse_rfc3339_timestamp(
                current_entry[f"{_ATOM_NAMESPACE}{_XML_NAMESPACE_SEPARATOR}updated"]
            )
            _parse_rfc3339_timestamp(
                current_entry[f"{_ATOM_NAMESPACE}{_XML_NAMESPACE_SEPARATOR}published"]
            )
            if last_entry_updated_at is not None and updated_at > last_entry_updated_at:
                raise ArxivResponseError("arXiv results are not sorted by descending update time")
            last_entry_updated_at = updated_at
            current_entry = None
        element_stack.pop()
        text_stack.pop()

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.CharacterDataHandler = character_data
    parser.StartDoctypeDeclHandler = reject_dtd_or_entity
    parser.EntityDeclHandler = reject_dtd_or_entity
    parser.UnparsedEntityDeclHandler = reject_dtd_or_entity
    parser.ExternalEntityRefHandler = reject_external_entity
    try:
        parser.Parse(content, True)
    except ArxivResponseError:
        raise
    except (ValueError, expat.ExpatError) as error:
        raise ArxivResponseError("arXiv returned a malformed Atom feed") from error

    if element_stack or not content:
        raise ArxivResponseError("arXiv returned a malformed Atom feed")
    missing_pagination = _PAGINATION_FIELDS.difference(pagination)
    if missing_pagination:
        raise ArxivResponseError("arXiv Atom pagination metadata is incomplete")

    parsed_pagination: dict[str, int] = {}
    for name, value in pagination.items():
        if not value.isascii() or not value.isdigit():
            raise ArxivResponseError("arXiv Atom pagination metadata is invalid")
        parsed_pagination[name] = int(value)
    items_per_page = parsed_pagination[
        f"{_OPENSEARCH_NAMESPACE}{_XML_NAMESPACE_SEPARATOR}itemsPerPage"
    ]
    total_results = parsed_pagination[
        f"{_OPENSEARCH_NAMESPACE}{_XML_NAMESPACE_SEPARATOR}totalResults"
    ]
    start_index = parsed_pagination[f"{_OPENSEARCH_NAMESPACE}{_XML_NAMESPACE_SEPARATOR}startIndex"]
    if items_per_page == 0 and (entry_count > 0 or total_results > 0):
        raise ArxivResponseError("arXiv Atom itemsPerPage must be positive for results")
    if (
        start_index > total_results
        or start_index + entry_count > total_results
        or entry_count > items_per_page
        or (start_index < total_results and entry_count == 0)
    ):
        raise ArxivResponseError("arXiv Atom pagination metadata is inconsistent")


def _parse_rfc3339_timestamp(value: str) -> datetime:
    if _RFC3339_TIMESTAMP.fullmatch(value) is None:
        raise ArxivResponseError("arXiv Atom entry has an invalid RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ArxivResponseError("arXiv Atom entry has an invalid RFC3339 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ArxivResponseError("arXiv Atom entry has an invalid RFC3339 timestamp")
    return parsed.astimezone(UTC)


def map_arxiv_result(result: Any) -> ArxivPaperRecord:
    try:
        canonical_id, version = parse_arxiv_identifier(result.get_short_id())
        authors = tuple(_normalize_text(author.name) for author in result.authors)
        categories = tuple(str(category) for category in result.categories)
        pdf_url = str(result.pdf_url or "")
        source_url = str(result.entry_id or "")
        _validate_result_url(
            source_url,
            schemes=frozenset({"http", "https"}),
            path_prefix="abs",
            canonical_arxiv_id=canonical_id,
            version=version,
        )
        _validate_result_url(
            pdf_url,
            schemes=frozenset({"https"}),
            path_prefix="pdf",
            canonical_arxiv_id=canonical_id,
            version=version,
            allow_pdf_suffix=True,
        )
        return ArxivPaperRecord(
            canonical_arxiv_id=canonical_id,
            version=version,
            title=_normalize_text(result.title),
            abstract=_normalize_text(result.summary),
            submitted_at=_as_utc(result.published),
            updated_at=_as_utc(result.updated),
            primary_category=str(result.primary_category),
            categories=categories,
            authors=authors,
            pdf_url=pdf_url,
            source_url=f"https://arxiv.org/abs/{canonical_id}v{version}",
        )
    except (AttributeError, TypeError, ValueError, DomainInvariantError) as error:
        raise ArxivResponseError(f"arXiv returned invalid paper metadata: {error}") from error


def _validate_result_url(
    value: str,
    *,
    schemes: frozenset[str],
    path_prefix: str,
    canonical_arxiv_id: str,
    version: int,
    allow_pdf_suffix: bool = False,
) -> None:
    try:
        parsed_url = urlsplit(value)
        port = parsed_url.port
    except ValueError as error:
        raise DomainInvariantError("arXiv result URL is invalid") from error
    expected_path = f"/{path_prefix}/{canonical_arxiv_id}v{version}"
    allowed_paths = {expected_path}
    if allow_pdf_suffix:
        allowed_paths.add(f"{expected_path}.pdf")
    if (
        parsed_url.scheme not in schemes
        or parsed_url.hostname != "arxiv.org"
        or port is not None
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.path not in allowed_paths
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise DomainInvariantError("arXiv result URL does not match its canonical version")


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ArxivResponseError("arXiv returned a timestamp without a time zone")
    return value.astimezone(UTC)
