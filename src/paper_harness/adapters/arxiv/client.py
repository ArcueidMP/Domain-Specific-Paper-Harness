"""Bounded arxiv.py implementation of the discovery port."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar, cast
from urllib.parse import urlsplit
from xml.etree import ElementTree
from xml.parsers import expat

import arxiv  # pyright: ignore[reportMissingTypeStubs]
import requests
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeoutError

from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import parse_arxiv_identifier, validate_canonical_arxiv_id
from paper_harness.ports.arxiv import (
    MAX_ARXIV_ID_LOOKUP,
    ArxivPaperRecord,
    ArxivPdf,
    ArxivPdfError,
    ArxivResponseError,
    ArxivResultLimitError,
    ArxivUnavailableError,
    normalize_arxiv_records,
)

_RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503})
_ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
_XML_NAMESPACE_SEPARATOR = "\x1f"
_ATOM_FEED = f"{_ATOM_NAMESPACE}{_XML_NAMESPACE_SEPARATOR}feed"
_ATOM_ENTRY_TAG = f"{{{_ATOM_NAMESPACE}}}entry"
_REQUIRED_ATOM_ENTRY_TAGS = tuple(
    f"{{{_ATOM_NAMESPACE}}}{field_name}" for field_name in ("id", "title", "published", "updated")
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
        self._candidate_lookahead = page_size
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

        # Fetch one complete page beyond the output cap, then normalize the whole
        # bounded candidate set locally. The API sort request is a retrieval hint,
        # never a response-validity or early-stop invariant.
        search = arxiv.Search(
            query=query,
            max_results=max_results + self._candidate_lookahead,
            sort_by=arxiv.SortCriterion.LastUpdatedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        candidates: list[ArxivPaperRecord] = []
        with self._session.operation(), self._client.page_operation():
            try:
                for result in self._client.results(search):
                    try:
                        candidates.append(map_arxiv_result(result))
                    except ArxivResponseError:
                        continue
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
        records = normalize_arxiv_records(
            candidates,
            updated_from=updated_from,
            updated_until=updated_until,
        )
        return records[:max_results]

    def get_papers_by_ids(
        self,
        *,
        canonical_arxiv_ids: tuple[str, ...],
    ) -> tuple[ArxivPaperRecord, ...]:
        canonical_arxiv_ids = tuple(dict.fromkeys(canonical_arxiv_ids))
        if not 1 <= len(canonical_arxiv_ids) <= MAX_ARXIV_ID_LOOKUP:
            raise ValueError(
                f"arXiv ID lookup must contain between 1 and {MAX_ARXIV_ID_LOOKUP} IDs"
            )
        try:
            for canonical_arxiv_id in canonical_arxiv_ids:
                validate_canonical_arxiv_id(canonical_arxiv_id)
        except DomainInvariantError as error:
            raise ValueError("arXiv ID lookup contains an invalid canonical ID") from error

        requested_ids = frozenset(canonical_arxiv_ids)
        search = arxiv.Search(
            id_list=list(canonical_arxiv_ids),
            max_results=len(canonical_arxiv_ids),
        )
        by_id: dict[str, ArxivPaperRecord] = {}
        with self._session.operation(), self._client.page_operation():
            try:
                for result in self._client.results(search):
                    try:
                        record = map_arxiv_result(result)
                    except ArxivResponseError:
                        continue
                    if record.canonical_arxiv_id not in requested_ids:
                        continue
                    by_id.setdefault(record.canonical_arxiv_id, record)
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

        return tuple(
            by_id[canonical_arxiv_id]
            for canonical_arxiv_id in canonical_arxiv_ids
            if canonical_arxiv_id in by_id
        )

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

    @contextmanager
    def page_operation(self) -> Generator[None]:
        if self._page_count is not None:
            raise RuntimeError("arXiv page operation is already active")
        self._page_count = 0
        try:
            yield
        finally:
            self._page_count = None

    def _parse_feed(self, url: str, first_page: bool = True, _try_index: int = 0) -> Any:
        if self._page_count is None:
            raise RuntimeError("arXiv feed parsing requires an active page operation")
        if self._page_count >= self._max_pages:
            raise ArxivResultLimitError(
                "arXiv page bound was reached before window exhaustion; cursor was not advanced"
            )
        self._page_count += 1
        feed = super()._parse_feed(url, first_page=first_page, _try_index=_try_index)
        if feed.malformed:
            raise ArxivResponseError("arXiv returned a malformed Atom feed")
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
        content = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            self.check_deadline()
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > self._atom_max_bytes:
                raise ArxivResponseError("arXiv Atom response exceeds the configured size bound")

        normalized_content = _normalize_atom_document(bytes(content))

        # arxiv.py consumes Response.content after this streamed response has
        # been closed. Buffering the complete body restores that requests API
        # contract while ensuring a failed attempt can never expose its prefix.
        buffered_response = cast(Any, response)
        buffered_response._content = normalized_content
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


def _normalize_atom_document(content: bytes) -> bytes:
    """Enforce XML safety and omit malformed entries before arxiv.py parsing."""

    element_stack: list[str] = []
    text_stack: list[list[str]] = []

    parser = expat.ParserCreate(namespace_separator=_XML_NAMESPACE_SEPARATOR)
    parser.buffer_text = True
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def reject_dtd_or_entity(*_args: Any) -> None:
        raise ArxivResponseError("arXiv Atom response contains a forbidden DTD or entity")

    def reject_external_entity(*_args: Any) -> int:
        raise ArxivResponseError("arXiv Atom response contains a forbidden external entity")

    def start_element(name: str, _attributes: dict[str, str]) -> None:
        if len(element_stack) >= _MAX_ATOM_DEPTH:
            raise ArxivResponseError("arXiv Atom response exceeds the XML depth bound")
        if not element_stack and name != _ATOM_FEED:
            raise ArxivResponseError("arXiv Atom response has an invalid feed root")
        element_stack.append(name)
        text_stack.append([])

    def character_data(value: str) -> None:
        if text_stack:
            text_stack[-1].append(value)

    def end_element(name: str) -> None:
        if not element_stack or element_stack[-1] != name:
            raise ArxivResponseError("arXiv returned a malformed Atom feed")
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

    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise ArxivResponseError("arXiv returned a malformed Atom feed") from error
    for entry in tuple(root.findall(_ATOM_ENTRY_TAG)):
        if not _atom_entry_has_required_metadata(entry):
            root.remove(entry)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _atom_entry_has_required_metadata(entry: ElementTree.Element) -> bool:
    values: dict[str, str] = {}
    for tag in _REQUIRED_ATOM_ENTRY_TAGS:
        element = entry.find(tag)
        if element is None or element.text is None or not element.text.strip():
            return False
        values[tag] = element.text.strip()
    for field_name in ("published", "updated"):
        value = values[f"{{{_ATOM_NAMESPACE}}}{field_name}"]
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return False
    return True


def map_arxiv_result(result: Any) -> ArxivPaperRecord:
    try:
        canonical_id, version = parse_arxiv_identifier(result.get_short_id())
        authors = _normalize_authors(result.authors)
        categories = _normalize_text_sequence(result.categories, field_name="categories")
        pdf_url = _normalize_required_text(result.pdf_url, field_name="PDF URL")
        source_url = _normalize_required_text(result.entry_id, field_name="source URL")
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
            title=_normalize_required_text(result.title, field_name="title"),
            abstract=_normalize_required_text(result.summary, field_name="abstract"),
            submitted_at=_as_utc(result.published),
            updated_at=_as_utc(result.updated),
            primary_category=_normalize_required_text(
                result.primary_category,
                field_name="primary category",
            ),
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


def _normalize_required_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise DomainInvariantError(f"arXiv {field_name} must be a string")
    normalized = " ".join(value.split())
    if not normalized:
        raise DomainInvariantError(f"arXiv {field_name} cannot be empty")
    return normalized


def _normalize_authors(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DomainInvariantError("arXiv authors must be a sequence")
    names: list[str] = []
    for author in cast(Sequence[object], value):
        author_name: object = getattr(author, "name", None)
        names.append(_normalize_required_text(author_name, field_name="author"))
    return tuple(names)


def _normalize_text_sequence(value: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DomainInvariantError(f"arXiv {field_name} must be a sequence")
    return tuple(
        _normalize_required_text(item, field_name=field_name.removesuffix("s"))
        for item in cast(Sequence[object], value)
    )


def _as_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ArxivResponseError("arXiv returned an invalid timestamp type")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ArxivResponseError("arXiv returned a timestamp without a time zone")
    return value.astimezone(UTC)
