"""Port for bounded arXiv daily discovery."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import validate_canonical_arxiv_id

MAX_ARXIV_ID_LOOKUP = 200


class ArxivPortError(RuntimeError):
    """Base error raised at the arXiv boundary."""

    error_code = "ARXIV_FAILURE"
    retryable = False


class ArxivUnavailableError(ArxivPortError):
    error_code = "ARXIV_UNAVAILABLE"
    retryable = True


class ArxivResponseError(ArxivPortError):
    error_code = "ARXIV_RESPONSE_INVALID"


class ArxivResultLimitError(ArxivPortError):
    """The bounded result window was saturated and cannot advance safely."""

    error_code = "ARXIV_RESULT_LIMIT"


class ArxivPdfError(ArxivPortError):
    error_code = "ARXIV_PDF_INVALID"


@dataclass(frozen=True, slots=True)
class ArxivPdf:
    canonical_arxiv_id: str
    version: int
    source_url: str
    content: bytes

    def __post_init__(self) -> None:
        validate_canonical_arxiv_id(self.canonical_arxiv_id)
        if self.version < 1:
            raise DomainInvariantError("arXiv PDF version must be positive")
        if not self.source_url.startswith("https://arxiv.org/pdf/"):
            raise DomainInvariantError("arXiv PDF must use the approved HTTPS host and path")
        if not self.content.startswith(b"%PDF-"):
            raise DomainInvariantError("arXiv PDF content is missing its PDF signature")


@dataclass(frozen=True, slots=True)
class ArxivPaperRecord:
    canonical_arxiv_id: str
    version: int
    title: str
    abstract: str
    submitted_at: datetime
    updated_at: datetime
    primary_category: str
    categories: tuple[str, ...]
    authors: tuple[str, ...]
    pdf_url: str
    source_url: str

    def __post_init__(self) -> None:
        validate_canonical_arxiv_id(self.canonical_arxiv_id)
        if self.version < 1:
            raise DomainInvariantError("arXiv version must be positive")
        if not self.title or not self.abstract or not self.primary_category:
            raise DomainInvariantError("arXiv record metadata is incomplete")
        if not self.categories or not self.authors or not self.pdf_url or not self.source_url:
            raise DomainInvariantError("arXiv record source fields are incomplete")
        for field_name, value in (
            ("submitted_at", self.submitted_at),
            ("updated_at", self.updated_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise DomainInvariantError(f"{field_name} must be timezone-aware")


class ArxivPort(Protocol):
    def search(
        self,
        *,
        query: str,
        updated_from: datetime,
        updated_until: datetime,
        max_results: int,
    ) -> tuple[ArxivPaperRecord, ...]:
        """Return a bounded, locally normalized UTC-window result set or raise."""

        ...

    def get_papers_by_ids(
        self,
        *,
        canonical_arxiv_ids: tuple[str, ...],
    ) -> tuple[ArxivPaperRecord, ...]:
        """Return available latest explicit versions for requested canonical IDs."""

        ...

    def download_pdf(
        self,
        *,
        canonical_arxiv_id: str,
        version: int,
        pdf_url: str,
    ) -> ArxivPdf:
        """Download one bounded arXiv-hosted PDF without changing provider."""

        ...


def normalize_arxiv_records(
    records: Iterable[ArxivPaperRecord],
    *,
    updated_from: datetime | None = None,
    updated_until: datetime | None = None,
) -> tuple[ArxivPaperRecord, ...]:
    """Normalize, deduplicate, filter, and deterministically order arXiv records."""

    if (updated_from is None) is not (updated_until is None):
        raise ValueError("arXiv normalization requires both discovery window bounds")
    if updated_from is not None and updated_until is not None:
        if updated_from.tzinfo is None or updated_from.utcoffset() is None:
            raise ValueError("arXiv discovery window must be timezone-aware")
        if updated_until.tzinfo is None or updated_until.utcoffset() is None:
            raise ValueError("arXiv discovery window must be timezone-aware")
        if updated_from > updated_until:
            raise ValueError("arXiv discovery start cannot follow its end")
        normalized_from = updated_from.astimezone(UTC)
        normalized_until = updated_until.astimezone(UTC)
    else:
        normalized_from = None
        normalized_until = None

    by_identity: dict[tuple[str, int], ArxivPaperRecord] = {}
    for record in records:
        normalized = replace(
            record,
            submitted_at=record.submitted_at.astimezone(UTC),
            updated_at=record.updated_at.astimezone(UTC),
        )
        identity = (normalized.canonical_arxiv_id, normalized.version)
        existing = by_identity.get(identity)
        by_identity[identity] = (
            normalized
            if existing is None
            or _record_preference_key(normalized) > _record_preference_key(existing)
            else existing
        )

    normalized_records = tuple(by_identity.values())
    if normalized_from is not None and normalized_until is not None:
        normalized_records = tuple(
            record
            for record in normalized_records
            if normalized_from <= record.updated_at <= normalized_until
        )

    ordered = sorted(
        normalized_records,
        key=lambda record: (record.canonical_arxiv_id, record.version),
    )
    ordered.sort(key=lambda record: record.submitted_at, reverse=True)
    ordered.sort(key=lambda record: record.updated_at, reverse=True)
    return tuple(ordered)


def _record_preference_key(record: ArxivPaperRecord) -> tuple[object, ...]:
    return (
        record.updated_at,
        record.submitted_at,
        record.title,
        record.abstract,
        record.primary_category,
        record.categories,
        record.authors,
        record.pdf_url,
        record.source_url,
    )
