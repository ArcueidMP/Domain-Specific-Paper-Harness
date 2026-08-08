"""Stable identifiers for persisted domain objects."""

from __future__ import annotations

import re
from uuid import UUID, uuid5

from paper_harness.domain.errors import DomainInvariantError

PAPER_NAMESPACE = UUID("cbd3c150-b93e-4af8-95ba-e17e77ccb12a")
PAPER_VERSION_NAMESPACE = UUID("3e7688e7-0ca6-46d9-a4d2-e3b416a82cf3")
SOURCE_IDENTITY_NAMESPACE = UUID("34bd2824-7bd2-4df8-a09e-784db57d20cb")
AUTHOR_NAMESPACE = UUID("b741f3e4-28ce-48b5-b2c7-9080e3da6690")
PARSED_PAPER_NAMESPACE = UUID("ce05af6a-1963-43f2-980d-1a165f2a3a50")
PARSED_SECTION_NAMESPACE = UUID("30ea24b3-4d3e-4ef4-a092-bb13982f6d39")
PARSED_PASSAGE_NAMESPACE = UUID("36387192-ea62-4216-8981-f9d4311ef581")
PARSED_REFERENCE_NAMESPACE = UUID("fbef25cb-8d31-46a5-bce5-bec9bfd3746e")
CITATION_CONTEXT_NAMESPACE = UUID("cd7f287b-b2d8-4e6b-b77e-8f77e8b4b151")
ANALYSIS_NAMESPACE = UUID("5b37c831-4524-41e4-9e69-4a7316699ae6")
CLAIM_NAMESPACE = UUID("42b72c7b-665a-4d11-9f45-d2154fb79257")
EVIDENCE_NAMESPACE = UUID("09955308-cb9d-4b0a-85dd-82235fa8080d")
REPORT_NAMESPACE = UUID("ff9abc00-438e-42ec-999e-3e7f8c213a98")

_ARXIV_VERSIONED_ID = re.compile(
    r"^(?P<canonical>(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7}))v(?P<version>[1-9]\d*)$",
    re.IGNORECASE,
)
_ARXIV_CANONICAL_ID = re.compile(
    r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})$",
    re.IGNORECASE,
)


def parse_arxiv_identifier(value: str) -> tuple[str, int]:
    """Return a canonical arXiv work identifier and explicit positive version."""

    cleaned = value.strip()
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        cleaned = cleaned.rstrip("/").rsplit("/", maxsplit=1)[-1]
    match = _ARXIV_VERSIONED_ID.fullmatch(cleaned)
    if match is None:
        raise DomainInvariantError(f"arXiv identifier must include an explicit version: {value!r}")
    canonical = match.group("canonical")
    return canonical.lower(), int(match.group("version"))


def validate_canonical_arxiv_id(value: str) -> str:
    """Validate and normalize an unversioned canonical arXiv work identifier."""

    cleaned = value.strip().lower()
    if _ARXIV_CANONICAL_ID.fullmatch(cleaned) is None:
        raise DomainInvariantError(f"invalid canonical arXiv identifier: {value!r}")
    return cleaned


def stable_paper_id(canonical_arxiv_id: str) -> UUID:
    return uuid5(PAPER_NAMESPACE, validate_canonical_arxiv_id(canonical_arxiv_id))


def stable_paper_version_id(canonical_arxiv_id: str, version: int) -> UUID:
    if version < 1:
        raise DomainInvariantError("paper version must be positive")
    canonical = validate_canonical_arxiv_id(canonical_arxiv_id)
    return uuid5(PAPER_VERSION_NAMESPACE, f"arxiv:{canonical}:v{version}")


def stable_source_identity_id(source: str, external_id: str, source_version: str) -> UUID:
    return uuid5(SOURCE_IDENTITY_NAMESPACE, f"{source}:{external_id}:{source_version}")


def normalize_author_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise DomainInvariantError("author name must not be empty")
    return normalized


def stable_author_id(name: str) -> UUID:
    return uuid5(AUTHOR_NAMESPACE, normalize_author_name(name).casefold())


def stable_parsed_paper_id(paper_version_id: UUID, parser_name: str, parser_version: str) -> UUID:
    return uuid5(PARSED_PAPER_NAMESPACE, f"{paper_version_id}:{parser_name}:{parser_version}")


def stable_parsed_section_id(parsed_paper_id: UUID, index: int) -> UUID:
    return uuid5(PARSED_SECTION_NAMESPACE, f"{parsed_paper_id}:{index}")


def stable_parsed_passage_id(parsed_paper_id: UUID, source_id: str) -> UUID:
    return uuid5(PARSED_PASSAGE_NAMESPACE, f"{parsed_paper_id}:{source_id}")


def stable_parsed_reference_id(parsed_paper_id: UUID, source_id: str) -> UUID:
    return uuid5(PARSED_REFERENCE_NAMESPACE, f"{parsed_paper_id}:{source_id}")


def stable_citation_context_id(
    parsed_paper_id: UUID, passage_id: UUID, reference_source_id: str, ordinal: int
) -> UUID:
    return uuid5(
        CITATION_CONTEXT_NAMESPACE,
        f"{parsed_paper_id}:{passage_id}:{reference_source_id}:{ordinal}",
    )


def stable_analysis_id(
    paper_version_id: UUID,
    analysis_scope: str,
    parsed_paper_id: UUID | None,
    provider: str,
    configured_model: str,
    model_version: str,
    prompt_version: str,
) -> UUID:
    parsed_identity = "abstract" if parsed_paper_id is None else str(parsed_paper_id)
    return uuid5(
        ANALYSIS_NAMESPACE,
        f"{paper_version_id}:{analysis_scope}:{parsed_identity}:{provider}:"
        f"{configured_model}:{model_version}:{prompt_version}",
    )


def stable_claim_id(analysis_id: UUID, claim_key: str) -> UUID:
    return uuid5(CLAIM_NAMESPACE, f"{analysis_id}:{claim_key}")


def stable_evidence_id(analysis_id: UUID, evidence_key: str) -> UUID:
    return uuid5(EVIDENCE_NAMESPACE, f"{analysis_id}:{evidence_key}")


def stable_report_id(run_id: UUID) -> UUID:
    return uuid5(REPORT_NAMESPACE, str(run_id))
