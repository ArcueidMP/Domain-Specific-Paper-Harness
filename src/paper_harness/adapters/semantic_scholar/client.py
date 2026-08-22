"""Strict authenticated client for the official Semantic Scholar HTTP APIs."""

from __future__ import annotations

import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Literal, Self, TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from paper_harness.adapters.http_retry import HttpRetryPolicy, send_with_retry
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import validate_canonical_arxiv_id
from paper_harness.ports.scholarly_search import (
    ScholarlyAuthor,
    ScholarlyExternalIds,
    ScholarlyPaper,
    ScholarlyPaperNotFoundError,
    ScholarlySearchAuthenticationError,
    ScholarlySearchConfigurationError,
    ScholarlySearchLimitError,
    ScholarlySearchRequestError,
    ScholarlySearchResponseError,
    ScholarlySearchUnavailableError,
)

SEMANTIC_SCHOLAR_GRAPH_BASE_URL = "https://api.semanticscholar.org/graph/v1"
SEMANTIC_SCHOLAR_RECOMMENDATIONS_BASE_URL = "https://api.semanticscholar.org/recommendations/v1"
MAX_SEARCH_RESULTS = 500
MAX_RELATION_RESULTS = 500
MAX_RECOMMENDATIONS = 500
MAX_POSITIVE_PAPER_IDS = 100
MAX_PAGE_SIZE = 100
MAX_PAGINATION_PAGES = 20
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_SEARCH_TOTAL = (1 << 63) - 1
PAPER_FIELDS = ",".join(
    (
        "paperId",
        "corpusId",
        "externalIds",
        "url",
        "title",
        "abstract",
        "venue",
        "year",
        "publicationDate",
        "authors",
        "citationCount",
        "influentialCitationCount",
        "referenceCount",
    )
)

_SEMANTIC_SCHOLAR_ID = re.compile(r"^[0-9a-f]{40}$")
_MAX_SEARCH_TOTAL_TEXT = str(MAX_SEARCH_TOTAL)
_SAFE_VALIDATION_LOCATIONS = frozenset(
    {
        "abstract",
        "authorId",
        "authors",
        "citationCount",
        "citedPaper",
        "citingPaper",
        "context",
        "contexts",
        "contextsWithIntent",
        "corpusId",
        "data",
        "disclaimer",
        "externalIds",
        "influentialCitationCount",
        "intents",
        "isInfluential",
        "license",
        "name",
        "next",
        "offset",
        "openAccessPdf",
        "paperId",
        "publicationDate",
        "recommendedPapers",
        "referenceCount",
        "status",
        "title",
        "total",
        "url",
        "venue",
        "year",
    }
)
_ResponseModel = TypeVar("_ResponseModel", bound=BaseModel)
_SchemaOperation = Literal[
    "search",
    "paper",
    "arxiv_lookup",
    "references",
    "citations",
    "recommendations",
]


class SemanticScholarSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: str = Field(min_length=1, max_length=1000)
    graph_base_url: Literal["https://api.semanticscholar.org/graph/v1"] = (
        SEMANTIC_SCHOLAR_GRAPH_BASE_URL
    )
    recommendations_base_url: Literal["https://api.semanticscholar.org/recommendations/v1"] = (
        SEMANTIC_SCHOLAR_RECOMMENDATIONS_BASE_URL
    )

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("SEMANTIC_SCHOLAR_API_KEY must not be blank")
        if any(not 33 <= ord(character) <= 126 for character in value):
            raise ValueError(
                "SEMANTIC_SCHOLAR_API_KEY must contain only printable ASCII without whitespace"
            )
        return value

    @classmethod
    def from_environment(cls) -> Self:
        try:
            return cls.model_validate({"api_key": os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")})
        except ValidationError as error:
            raise ScholarlySearchConfigurationError(
                "historical scholarly search requires a non-empty SEMANTIC_SCHOLAR_API_KEY"
            ) from error


class _PayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("optional text metadata must be a string or null")
    normalized = value.strip()
    return normalized or None


class _AuthorPayload(_PayloadModel):
    author_id: str | None = Field(alias="authorId", max_length=64)
    name: str = Field(min_length=1, max_length=500)

    @field_validator("author_id", mode="before")
    @classmethod
    def normalize_author_id(cls, value: object) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_author_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class _PaperPayload(_PayloadModel):
    paper_id: str = Field(alias="paperId", pattern=r"^[0-9a-f]{40}$")
    corpus_id: int = Field(alias="corpusId", strict=True, ge=1)
    external_ids: dict[str, str | int] | None = Field(
        default=None, alias="externalIds", max_length=50
    )
    # The provider URL is display metadata. The adapter derives the approved
    # canonical URL from paperId instead of making a paper depend on this field.
    url: object | None = None
    title: str = Field(min_length=1, max_length=10_000)
    abstract: str | None = Field(default=None, max_length=1_000_000)
    venue: str | None = Field(default=None, max_length=1000)
    year: int | None = Field(default=None, strict=True, ge=1000, le=3000)
    publication_date: date | None = Field(default=None, alias="publicationDate")
    authors: tuple[_AuthorPayload, ...] = Field(default=(), max_length=500)
    citation_count: int = Field(default=0, alias="citationCount", strict=True, ge=0)
    influential_citation_count: int = Field(
        default=0, alias="influentialCitationCount", strict=True, ge=0
    )
    reference_count: int = Field(default=0, alias="referenceCount", strict=True, ge=0)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_required_paper_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("abstract", "venue", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("external_ids")
    @classmethod
    def validate_external_ids(
        cls, value: dict[str, str | int] | None
    ) -> dict[str, str | int] | None:
        if value is None:
            return None
        for key, identifier in value.items():
            rendered = str(identifier)
            if (
                not key
                or key != key.strip()
                or len(key) > 40
                or not rendered
                or rendered != rendered.strip()
                or len(rendered) > 512
            ):
                raise ValueError("external paper identifier metadata is invalid")
        return value


class _SearchPaperPayload(_PaperPayload):
    open_access_pdf: object | None = Field(
        default=None,
        alias="openAccessPdf",
    )


class _SearchPage(_PayloadModel):
    total: int = Field(strict=True, ge=0, le=MAX_SEARCH_TOTAL)
    offset: int = Field(strict=True, ge=0)
    next_offset: int | None = Field(default=None, alias="next", strict=True, ge=0)
    data: tuple[object, ...] = Field(max_length=MAX_PAGE_SIZE)

    @field_validator("total", mode="before")
    @classmethod
    def normalize_total(cls, value: object) -> object:
        if type(value) is not str:
            return value
        if (
            not value
            or len(value) > len(_MAX_SEARCH_TOTAL_TEXT)
            or re.fullmatch(r"(?:0|[1-9][0-9]*)", value) is None
            or (len(value) == len(_MAX_SEARCH_TOTAL_TEXT) and value > _MAX_SEARCH_TOTAL_TEXT)
        ):
            raise ValueError("search total must be a bounded canonical decimal")
        return int(value)


class _ReferenceItem(_PayloadModel):
    paper: object = Field(alias="citedPaper")


class _CitationItem(_PayloadModel):
    paper: object = Field(alias="citingPaper")


class _ReferencePage(_PayloadModel):
    offset: int = Field(strict=True, ge=0)
    next_offset: int | None = Field(default=None, alias="next", strict=True, ge=0)
    total: int | None = Field(default=None, strict=True, ge=0)
    data: tuple[object, ...] = Field(max_length=MAX_PAGE_SIZE)


class _CitationPage(_PayloadModel):
    offset: int = Field(strict=True, ge=0)
    next_offset: int | None = Field(default=None, alias="next", strict=True, ge=0)
    total: int | None = Field(default=None, strict=True, ge=0)
    data: tuple[object, ...] = Field(max_length=MAX_PAGE_SIZE)


class _RecommendationsResponse(_PayloadModel):
    recommended_papers: tuple[object, ...] = Field(
        alias="recommendedPapers", max_length=MAX_RECOMMENDATIONS
    )


class SemanticScholarClient:
    """Synchronous bounded adapter with no ranking, anonymous mode, or fallback."""

    def __init__(
        self,
        settings: SemanticScholarSettings,
        *,
        client: httpx.Client | None = None,
        retry_policy: HttpRetryPolicy | None = None,
        page_size: int = MAX_PAGE_SIZE,
        max_relation_results: int = 100,
        recommendation_limit: int = 100,
        minimum_request_interval_seconds: float = 1.0,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ScholarlySearchConfigurationError(
                f"Semantic Scholar page_size must be between 1 and {MAX_PAGE_SIZE}"
            )
        if not 1 <= max_relation_results <= MAX_RELATION_RESULTS:
            raise ScholarlySearchConfigurationError(
                f"Semantic Scholar relation bound must be between 1 and {MAX_RELATION_RESULTS}"
            )
        if not 1 <= recommendation_limit <= MAX_RECOMMENDATIONS:
            raise ScholarlySearchConfigurationError(
                f"Semantic Scholar recommendation bound must be between 1 and {MAX_RECOMMENDATIONS}"
            )
        if not math.isfinite(minimum_request_interval_seconds) or not (
            1 <= minimum_request_interval_seconds <= 60
        ):
            raise ScholarlySearchConfigurationError(
                "Semantic Scholar request interval must be between 1 and 60 seconds"
            )
        if not 1024 <= max_response_bytes <= 20 * 1024 * 1024:
            raise ScholarlySearchConfigurationError(
                "Semantic Scholar response-size bound must be between 1 KiB and 20 MiB"
            )
        self._settings = settings
        self._client = client or httpx.Client()
        self._retry_policy = retry_policy or HttpRetryPolicy(
            max_retries=2,
            request_timeout_seconds=30,
            total_timeout_seconds=120,
            backoff_seconds=1,
            max_retry_after_seconds=30,
        )
        self._page_size = page_size
        self._max_relation_results = max_relation_results
        self._recommendation_limit = recommendation_limit
        self._minimum_request_interval_seconds = minimum_request_interval_seconds
        self._max_response_bytes = max_response_bytes
        self._monotonic = monotonic
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(UTC))
        self._last_request_started: float | None = None

    def search_papers(
        self,
        query: str,
        year_from: int,
        year_to: int,
        limit: int,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[ScholarlyPaper, ...]:
        query = _validate_query(query)
        _validate_year_range(year_from, year_to)
        if type(limit) is not int or not 1 <= limit <= MAX_SEARCH_RESULTS:
            raise ScholarlySearchRequestError(
                f"Semantic Scholar search limit must be between 1 and {MAX_SEARCH_RESULTS}"
            )

        papers: list[ScholarlyPaper] = []
        paper_ids: set[str] = set()
        offset = 0
        operation_deadline = self._operation_deadline(timeout_seconds)
        for _page_number in range(MAX_PAGINATION_PAGES):
            requested_page_size = min(self._page_size, limit - len(papers))
            page = self._request_model(
                "GET",
                f"{self._settings.graph_base_url}/paper/search",
                params={
                    "query": query,
                    "year": f"{year_from}-{year_to}",
                    "limit": requested_page_size,
                    "offset": offset,
                    "fields": PAPER_FIELDS,
                },
                response_model=_SearchPage,
                operation_deadline=operation_deadline,
                schema_operation="search",
            )
            _validate_page(
                page.offset,
                offset,
                page.next_offset,
                len(page.data),
                requested_page_size,
            )
            _append_unique(
                papers,
                paper_ids,
                page.data,
                payload_model=_SearchPaperPayload,
            )
            if len(papers) >= limit or page.next_offset is None:
                return tuple(papers[:limit])
            offset = page.next_offset
        raise ScholarlySearchLimitError(
            "Semantic Scholar search exceeded the bounded pagination depth"
        )

    def get_paper(
        self, semantic_scholar_id: str, *, timeout_seconds: float | None = None
    ) -> ScholarlyPaper:
        paper_id = _validate_paper_id(semantic_scholar_id)
        operation_deadline = self._operation_deadline(timeout_seconds)
        payload = self._request_model(
            "GET",
            f"{self._settings.graph_base_url}/paper/{paper_id}",
            params={"fields": PAPER_FIELDS},
            response_model=_PaperPayload,
            operation_deadline=operation_deadline,
            schema_operation="paper",
        )
        return _to_paper(payload)

    def get_paper_by_arxiv_id(
        self, canonical_arxiv_id: str, *, timeout_seconds: float | None = None
    ) -> ScholarlyPaper:
        arxiv_id = _validate_arxiv_id(canonical_arxiv_id)
        provider_id = quote(f"ARXIV:{arxiv_id}", safe=":")
        payload = self._request_model(
            "GET",
            f"{self._settings.graph_base_url}/paper/{provider_id}",
            params={"fields": PAPER_FIELDS},
            response_model=_PaperPayload,
            operation_deadline=self._operation_deadline(timeout_seconds),
            schema_operation="arxiv_lookup",
        )
        paper = _to_paper(payload)
        if paper.external_ids.arxiv_id != arxiv_id:
            raise ScholarlySearchResponseError(
                "Semantic Scholar arXiv lookup returned a different external identity"
            )
        return paper

    def get_references(
        self, semantic_scholar_id: str, *, timeout_seconds: float | None = None
    ) -> tuple[ScholarlyPaper, ...]:
        return self._get_relations(
            semantic_scholar_id,
            relation="references",
            timeout_seconds=timeout_seconds,
        )

    def get_citations(
        self, semantic_scholar_id: str, *, timeout_seconds: float | None = None
    ) -> tuple[ScholarlyPaper, ...]:
        return self._get_relations(
            semantic_scholar_id,
            relation="citations",
            timeout_seconds=timeout_seconds,
        )

    def get_recommendations(
        self,
        positive_paper_ids: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[ScholarlyPaper, ...]:
        if not positive_paper_ids or len(positive_paper_ids) > MAX_POSITIVE_PAPER_IDS:
            raise ScholarlySearchRequestError(
                "Semantic Scholar recommendations require between 1 and 100 positive papers"
            )
        paper_ids = tuple(dict.fromkeys(_validate_paper_id(value) for value in positive_paper_ids))
        response = self._request_model(
            "POST",
            f"{self._settings.recommendations_base_url}/papers/",
            params={"limit": self._recommendation_limit, "fields": PAPER_FIELDS},
            json_body={"positivePaperIds": list(paper_ids), "negativePaperIds": []},
            response_model=_RecommendationsResponse,
            operation_deadline=self._operation_deadline(timeout_seconds),
            schema_operation="recommendations",
        )
        return _convert_unique(
            response.recommended_papers,
            payload_model=_PaperPayload,
        )[: self._recommendation_limit]

    def _get_relations(
        self,
        semantic_scholar_id: str,
        *,
        relation: Literal["references", "citations"],
        timeout_seconds: float | None,
    ) -> tuple[ScholarlyPaper, ...]:
        paper_id = _validate_paper_id(semantic_scholar_id)
        papers: list[ScholarlyPaper] = []
        paper_ids: set[str] = set()
        offset = 0
        operation_deadline = self._operation_deadline(timeout_seconds)
        for _page_number in range(MAX_PAGINATION_PAGES):
            requested_page_size = min(self._page_size, self._max_relation_results - len(papers))
            if relation == "references":
                page = self._request_model(
                    "GET",
                    f"{self._settings.graph_base_url}/paper/{paper_id}/references",
                    params={
                        "limit": requested_page_size,
                        "offset": offset,
                        "fields": PAPER_FIELDS,
                    },
                    response_model=_ReferencePage,
                    operation_deadline=operation_deadline,
                    schema_operation="references",
                )
                payloads = _relation_paper_values(
                    page.data,
                    item_model=_ReferenceItem,
                )
            else:
                page = self._request_model(
                    "GET",
                    f"{self._settings.graph_base_url}/paper/{paper_id}/citations",
                    params={
                        "limit": requested_page_size,
                        "offset": offset,
                        "fields": PAPER_FIELDS,
                    },
                    response_model=_CitationPage,
                    operation_deadline=operation_deadline,
                    schema_operation="citations",
                )
                payloads = _relation_paper_values(
                    page.data,
                    item_model=_CitationItem,
                )
            _validate_page(
                page.offset,
                offset,
                page.next_offset,
                len(payloads),
                requested_page_size,
            )
            _append_unique(
                papers,
                paper_ids,
                payloads,
                payload_model=_PaperPayload,
            )
            if len(papers) >= self._max_relation_results or page.next_offset is None:
                return tuple(papers[: self._max_relation_results])
            offset = page.next_offset
        raise ScholarlySearchLimitError(
            f"Semantic Scholar {relation} exceeded the bounded pagination depth"
        )

    def _request_model(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        params: dict[str, str | int],
        response_model: type[_ResponseModel],
        operation_deadline: float,
        schema_operation: _SchemaOperation,
        json_body: dict[str, object] | None = None,
    ) -> _ResponseModel:
        retry_policy = self._remaining_retry_policy(operation_deadline)

        def send(timeout: float) -> httpx.Response:
            self._wait_for_rate_limit(operation_deadline)
            remaining = operation_deadline - self._monotonic()
            if remaining <= 0:
                raise httpx.TimeoutException(
                    "Semantic Scholar rate limit exhausted the operation timeout"
                )
            request_timeout = min(timeout, remaining)
            headers = {
                "x-api-key": self._settings.api_key,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            }
            if json_body is None:
                request = self._client.build_request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    timeout=request_timeout,
                )
            else:
                request = self._client.build_request(
                    method,
                    url,
                    headers={**headers, "Content-Type": "application/json"},
                    params=params,
                    json=json_body,
                    timeout=request_timeout,
                )
            response = self._client.send(request, stream=True)
            if response.status_code != 200:
                return response
            try:
                content = _bounded_json_content(
                    response,
                    max_bytes=self._max_response_bytes,
                    deadline=operation_deadline,
                    monotonic=self._monotonic,
                )
            finally:
                response.close()
            return httpx.Response(
                200,
                headers=response.headers,
                content=content,
                request=request,
            )

        try:
            response = send_with_retry(
                send,
                policy=retry_policy,
                sleep=self._sleep,
                monotonic=self._monotonic,
                now=self._utc_now,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise ScholarlySearchUnavailableError(
                f"Semantic Scholar transport failed with {type(error).__name__}"
            ) from error

        try:
            _raise_for_status(response.status_code)
            try:
                return response_model.model_validate_json(response.content)
            except ValidationError as error:
                summary = _validation_error_summary(error)
                raise ScholarlySearchResponseError(
                    "Semantic Scholar returned an invalid response schema "
                    f"for operation={schema_operation} ({summary})"
                ) from None
            except (RecursionError, ValueError):
                raise ScholarlySearchResponseError(
                    "Semantic Scholar returned an invalid response schema "
                    f"for operation={schema_operation}"
                ) from None
        finally:
            response.close()

    def _operation_deadline(self, timeout_seconds: float | None) -> float:
        started = self._monotonic()
        if not math.isfinite(started):
            raise ScholarlySearchConfigurationError(
                "Semantic Scholar monotonic clock returned a non-finite value"
            )
        operation_timeout = self._retry_policy.total_timeout_seconds
        if timeout_seconds is not None:
            if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
                raise ScholarlySearchUnavailableError(
                    "Semantic Scholar operation has no remaining timeout budget"
                )
            operation_timeout = min(operation_timeout, timeout_seconds)
        return started + operation_timeout

    def _remaining_retry_policy(self, operation_deadline: float) -> HttpRetryPolicy:
        remaining = operation_deadline - self._monotonic()
        if not math.isfinite(remaining) or remaining < 1:
            raise ScholarlySearchUnavailableError(
                "Semantic Scholar operation exceeded its total timeout"
            )
        return replace(
            self._retry_policy,
            request_timeout_seconds=min(
                self._retry_policy.request_timeout_seconds,
                remaining,
            ),
            total_timeout_seconds=remaining,
        )

    def _wait_for_rate_limit(self, operation_deadline: float) -> None:
        started = self._monotonic()
        if not math.isfinite(started):
            raise ScholarlySearchConfigurationError(
                "Semantic Scholar monotonic clock returned a non-finite value"
            )
        if self._last_request_started is not None:
            elapsed = started - self._last_request_started
            if elapsed < 0:
                raise ScholarlySearchConfigurationError(
                    "Semantic Scholar monotonic clock moved backwards"
                )
            delay = self._minimum_request_interval_seconds - elapsed
            if delay > 0:
                if delay > operation_deadline - started:
                    raise httpx.TimeoutException(
                        "Semantic Scholar rate limit would exceed the operation timeout"
                    )
                self._sleep(delay)
                started = self._monotonic()
                if not math.isfinite(started) or started < self._last_request_started:
                    raise ScholarlySearchConfigurationError(
                        "Semantic Scholar rate-limit clock did not advance safely"
                    )
        self._last_request_started = started

    def _utc_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ScholarlySearchConfigurationError(
                "Semantic Scholar clock must return a timezone-aware datetime"
            )
        return value.astimezone(UTC)


def _validate_query(query: str) -> str:
    if (
        not query
        or query != query.strip()
        or len(query) > 500
        or any(ord(character) < 32 for character in query)
    ):
        raise ScholarlySearchRequestError(
            "Semantic Scholar query must be trimmed text under 501 characters"
        )
    return query


def _validate_year_range(year_from: int, year_to: int) -> None:
    if (
        type(year_from) is not int
        or type(year_to) is not int
        or not 1000 <= year_from <= year_to <= 3000
    ):
        raise ScholarlySearchRequestError("Semantic Scholar year range is invalid")


def _validate_paper_id(value: str) -> str:
    normalized = value.lower()
    if value != value.strip() or _SEMANTIC_SCHOLAR_ID.fullmatch(normalized) is None:
        raise ScholarlySearchRequestError(
            "Semantic Scholar paper identity must be exactly 40 hexadecimal characters"
        )
    return normalized


def _validate_arxiv_id(value: str) -> str:
    try:
        normalized = validate_canonical_arxiv_id(value)
    except DomainInvariantError:
        raise ScholarlySearchRequestError(
            "Semantic Scholar arXiv lookup requires a canonical unversioned arXiv identity"
        ) from None
    if value != normalized:
        raise ScholarlySearchRequestError(
            "Semantic Scholar arXiv lookup requires a normalized canonical arXiv identity"
        )
    return normalized


def _validate_page(
    response_offset: int,
    requested_offset: int,
    next_offset: int | None,
    item_count: int,
    requested_page_size: int,
) -> None:
    del response_offset, item_count, requested_page_size
    if next_offset is not None and next_offset <= requested_offset:
        raise ScholarlySearchResponseError("Semantic Scholar returned an invalid pagination cursor")


def _append_unique(
    papers: list[ScholarlyPaper],
    paper_ids: set[str],
    values: tuple[object, ...],
    *,
    payload_model: type[_PaperPayload],
) -> None:
    for value in values:
        paper = _to_collection_paper(value, payload_model=payload_model)
        if paper is None:
            continue
        if paper.semantic_scholar_id in paper_ids:
            continue
        paper_ids.add(paper.semantic_scholar_id)
        papers.append(paper)


def _convert_unique(
    values: tuple[object, ...],
    *,
    payload_model: type[_PaperPayload],
) -> tuple[ScholarlyPaper, ...]:
    papers: list[ScholarlyPaper] = []
    paper_ids: set[str] = set()
    _append_unique(papers, paper_ids, values, payload_model=payload_model)
    return tuple(papers)


def _relation_paper_values(
    values: tuple[object, ...],
    *,
    item_model: type[_ReferenceItem] | type[_CitationItem],
) -> tuple[object, ...]:
    papers: list[object] = []
    for value in values:
        try:
            item = item_model.model_validate(value, strict=False)
        except (RecursionError, ValidationError, ValueError):
            continue
        papers.append(item.paper)
    return tuple(papers)


def _to_collection_paper(
    value: object,
    *,
    payload_model: type[_PaperPayload],
) -> ScholarlyPaper | None:
    try:
        payload = payload_model.model_validate(value, strict=False)
        return _to_paper(payload)
    except (RecursionError, ValidationError, ValueError, ScholarlySearchResponseError):
        return None


def _to_paper(payload: _PaperPayload) -> ScholarlyPaper:
    external_ids = payload.external_ids
    arxiv_id: str | None = None
    doi: str | None = None
    identifier_values: dict[str, str] = {}
    if external_ids is not None:
        identifier_values = {key: str(value) for key, value in external_ids.items()}
        raw_arxiv_id = identifier_values.get("ArXiv")
        if raw_arxiv_id is not None:
            try:
                arxiv_id = validate_canonical_arxiv_id(raw_arxiv_id)
            except DomainInvariantError:
                # Keep the Semantic Scholar metadata, but do not claim arXiv
                # full-text eligibility from an unusable optional identifier.
                identifier_values.pop("ArXiv")
            else:
                identifier_values["ArXiv"] = arxiv_id
        doi = identifier_values.get("DOI")
    try:
        return ScholarlyPaper(
            semantic_scholar_id=payload.paper_id,
            corpus_id=payload.corpus_id,
            external_ids=ScholarlyExternalIds(
                arxiv_id=arxiv_id,
                doi=doi,
                values=tuple(
                    sorted(identifier_values.items(), key=lambda item: item[0].casefold())
                ),
            ),
            url=f"https://www.semanticscholar.org/paper/{payload.paper_id}",
            title=payload.title,
            abstract=payload.abstract,
            venue=payload.venue,
            year=payload.year,
            publication_date=payload.publication_date,
            authors=tuple(
                ScholarlyAuthor(author_id=author.author_id, name=author.name)
                for author in payload.authors
            ),
            citation_count=payload.citation_count,
            influential_citation_count=min(
                payload.influential_citation_count,
                payload.citation_count,
            ),
            reference_count=payload.reference_count,
        )
    except DomainInvariantError:
        raise ScholarlySearchResponseError(
            "Semantic Scholar response violated scholarly paper invariants"
        ) from None


def _validation_error_summary(error: ValidationError) -> str:
    summaries: list[str] = []
    for detail in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[:3]:
        location_parts: list[str] = []
        for part in detail.get("loc", ()):
            if type(part) is int:
                location_parts.append(str(part))
            elif type(part) is str and part in _SAFE_VALIDATION_LOCATIONS:
                location_parts.append(part)
            else:
                location_parts.append("field")
        location = ".".join(location_parts) or "root"
        error_type = str(detail.get("type", "validation_error"))
        if re.fullmatch(r"[a-z][a-z0-9_.]{0,80}", error_type) is None:
            error_type = "validation_error"
        summaries.append(f"{location}:{error_type}")
    return ", ".join(summaries) or "root:validation_error"


def _bounded_json_content(
    response: httpx.Response,
    *,
    max_bytes: int,
    deadline: float,
    monotonic: Callable[[], float],
) -> bytes:
    content = bytearray()
    for chunk in response.iter_bytes():
        if monotonic() >= deadline:
            raise httpx.TimeoutException(
                "Semantic Scholar response exceeded the total operation timeout"
            )
        content.extend(chunk)
        if len(content) > max_bytes:
            raise ScholarlySearchResponseError(
                "Semantic Scholar response exceeds the configured size bound"
            )
    if monotonic() >= deadline:
        raise httpx.TimeoutException(
            "Semantic Scholar response exceeded the total operation timeout"
        )
    if not content:
        raise ScholarlySearchResponseError("Semantic Scholar returned an empty response")
    return bytes(content)


def _raise_for_status(status_code: int) -> None:
    if status_code in (401, 403):
        raise ScholarlySearchAuthenticationError(
            f"Semantic Scholar authentication failed with HTTP {status_code}"
        )
    if status_code in (400, 422):
        raise ScholarlySearchRequestError(
            f"Semantic Scholar rejected the request with HTTP {status_code}"
        )
    if status_code == 404:
        raise ScholarlyPaperNotFoundError("Semantic Scholar paper was not found")
    if status_code in (429, 500, 502, 503):
        raise ScholarlySearchUnavailableError(
            f"Semantic Scholar HTTP {status_code} exhausted bounded retries"
        )
    if status_code != 200:
        raise ScholarlySearchRequestError(
            f"Semantic Scholar returned unexpected HTTP {status_code}"
        )
