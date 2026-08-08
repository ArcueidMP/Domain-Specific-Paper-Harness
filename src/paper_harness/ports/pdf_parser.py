"""Boundary for the sole configured scientific PDF parser."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from paper_harness.domain.analysis import ParsedPaper
from paper_harness.domain.errors import DomainInvariantError


class PdfParserPortError(RuntimeError):
    error_code = "PDF_PARSER_FAILURE"
    retryable = False


class PdfParserConfigurationError(PdfParserPortError):
    error_code = "PDF_PARSER_CONFIGURATION_INVALID"


class PdfParserAuthenticationError(PdfParserPortError):
    error_code = "PDF_PARSER_AUTHENTICATION_FAILED"


class PdfParserRequestError(PdfParserPortError):
    error_code = "PDF_PARSER_REQUEST_INVALID"


class PdfParserUnavailableError(PdfParserPortError):
    error_code = "PDF_PARSER_UNAVAILABLE"
    retryable = True


class PdfParserOutputError(PdfParserPortError):
    error_code = "PDF_PARSER_OUTPUT_INVALID"


@dataclass(frozen=True, slots=True)
class PdfParseRequest:
    paper_id: UUID
    paper_version_id: UUID
    canonical_arxiv_id: str
    arxiv_version: int
    content: bytes

    def __post_init__(self) -> None:
        if self.arxiv_version < 1:
            raise DomainInvariantError("arXiv version must be positive")
        if not self.content.startswith(b"%PDF-"):
            raise DomainInvariantError("parser input must have a PDF signature")


class PdfParserPort(Protocol):
    def parse(self, request: PdfParseRequest) -> ParsedPaper: ...
