"""Typed boundary for approved historical scholarly search operations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from urllib.parse import urlsplit

from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import validate_canonical_arxiv_id

_SEMANTIC_SCHOLAR_ID = re.compile(r"^[0-9a-f]{40}$")
_DOI = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


class ScholarlySearchError(RuntimeError):
    """Base failure at the authenticated scholarly-search boundary."""

    error_code = "SCHOLARLY_SEARCH_FAILURE"
    retryable = False


class ScholarlySearchConfigurationError(ScholarlySearchError):
    """Required scholarly-search configuration is absent or invalid."""

    error_code = "SCHOLARLY_SEARCH_CONFIGURATION_INVALID"


class ScholarlySearchAuthenticationError(ScholarlySearchError):
    """The approved scholarly provider rejected authentication."""

    error_code = "SCHOLARLY_SEARCH_AUTHENTICATION_FAILED"


class ScholarlySearchRequestError(ScholarlySearchError):
    """The approved scholarly provider rejected a non-retryable request."""

    error_code = "SCHOLARLY_SEARCH_REQUEST_REJECTED"


class ScholarlyPaperNotFoundError(ScholarlySearchError):
    """The requested Semantic Scholar paper identity does not exist."""

    error_code = "SCHOLARLY_PAPER_NOT_FOUND"


class ScholarlySearchUnavailableError(ScholarlySearchError):
    """The approved scholarly provider remained transiently unavailable."""

    error_code = "SCHOLARLY_SEARCH_UNAVAILABLE"
    retryable = True


class ScholarlySearchResponseError(ScholarlySearchError):
    """The approved scholarly provider returned malformed or inconsistent data."""

    error_code = "SCHOLARLY_SEARCH_RESPONSE_INVALID"


class ScholarlySearchLimitError(ScholarlySearchError):
    """A bounded scholarly operation could not complete within its declared limits."""

    error_code = "SCHOLARLY_SEARCH_LIMIT_EXCEEDED"


@dataclass(frozen=True, slots=True)
class ScholarlyAuthor:
    author_id: str | None
    name: str

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.strip() or len(self.name) > 500:
            raise DomainInvariantError("scholarly author name must be concise non-empty text")
        if self.author_id is not None and (
            not self.author_id
            or len(self.author_id) > 64
            or any(character.isspace() for character in self.author_id)
        ):
            raise DomainInvariantError("scholarly author identity is invalid")


@dataclass(frozen=True, slots=True)
class ScholarlyExternalIds:
    arxiv_id: str | None
    doi: str | None
    values: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if self.arxiv_id is not None:
            validate_canonical_arxiv_id(self.arxiv_id)
        if self.doi is not None and (len(self.doi) > 300 or _DOI.fullmatch(self.doi) is None):
            raise DomainInvariantError("scholarly DOI is invalid")
        if len(self.values) > 50 or len({key.casefold() for key, _value in self.values}) != len(
            self.values
        ):
            raise DomainInvariantError("scholarly external identifiers must be bounded and unique")
        for key, value in self.values:
            if (
                not key
                or key != key.strip()
                or len(key) > 40
                or not value
                or value != value.strip()
                or len(value) > 512
            ):
                raise DomainInvariantError("scholarly external identifier metadata is invalid")
        identifiers = dict(self.values)
        if identifiers.get("ArXiv") != self.arxiv_id or identifiers.get("DOI") != self.doi:
            raise DomainInvariantError(
                "scholarly canonical identifiers must agree with external identifier metadata"
            )


@dataclass(frozen=True, slots=True)
class ScholarlyPaper:
    semantic_scholar_id: str
    corpus_id: int
    external_ids: ScholarlyExternalIds
    url: str
    title: str
    abstract: str | None
    venue: str | None
    year: int | None
    publication_date: date | None
    authors: tuple[ScholarlyAuthor, ...]
    citation_count: int
    influential_citation_count: int
    reference_count: int

    def __post_init__(self) -> None:
        if _SEMANTIC_SCHOLAR_ID.fullmatch(self.semantic_scholar_id) is None:
            raise DomainInvariantError("Semantic Scholar paper identity must be 40 lowercase hex")
        if self.corpus_id < 1:
            raise DomainInvariantError("Semantic Scholar corpus identity must be positive")
        if not self.title or self.title != self.title.strip() or len(self.title) > 10_000:
            raise DomainInvariantError("scholarly paper title must be concise non-empty text")
        if self.abstract is not None and (
            not self.abstract.strip() or len(self.abstract) > 1_000_000
        ):
            raise DomainInvariantError("scholarly paper abstract is invalid")
        if self.venue is not None and (not self.venue.strip() or len(self.venue) > 1000):
            raise DomainInvariantError("scholarly paper venue is invalid")
        if self.year is not None and not 1000 <= self.year <= 3000:
            raise DomainInvariantError("scholarly paper year is outside the supported range")
        if min(self.citation_count, self.influential_citation_count, self.reference_count) < 0:
            raise DomainInvariantError("scholarly citation counts cannot be negative")
        if self.influential_citation_count > self.citation_count:
            raise DomainInvariantError(
                "influential citation count cannot exceed total citation count"
            )
        parsed_url = urlsplit(self.url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != "www.semanticscholar.org"
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.port is not None
            or not parsed_url.path.startswith("/paper/")
        ):
            raise DomainInvariantError("scholarly paper URL must use the approved HTTPS host")


class ScholarlySearchPort(Protocol):
    def search_papers(
        self,
        query: str,
        year_from: int,
        year_to: int,
        limit: int,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[ScholarlyPaper, ...]:
        """Search a bounded historical year range without applying product ranking."""

        ...

    def get_paper(
        self, semantic_scholar_id: str, *, timeout_seconds: float | None = None
    ) -> ScholarlyPaper:
        """Return one paper by its canonical Semantic Scholar paper identity."""

        ...

    def get_paper_by_arxiv_id(
        self, canonical_arxiv_id: str, *, timeout_seconds: float | None = None
    ) -> ScholarlyPaper:
        """Resolve one canonical arXiv work through the provider's ARXIV identifier."""

        ...

    def get_references(
        self, semantic_scholar_id: str, *, timeout_seconds: float | None = None
    ) -> tuple[ScholarlyPaper, ...]:
        """Return a configured bounded reference expansion."""

        ...

    def get_citations(
        self, semantic_scholar_id: str, *, timeout_seconds: float | None = None
    ) -> tuple[ScholarlyPaper, ...]:
        """Return a configured bounded citation expansion."""

        ...

    def get_recommendations(
        self,
        positive_paper_ids: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[ScholarlyPaper, ...]:
        """Return a configured bounded recommendation expansion."""

        ...
