"""Port for bounded arXiv daily discovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import validate_canonical_arxiv_id


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
        """Return a complete bounded UTC window or raise without partial results."""

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
