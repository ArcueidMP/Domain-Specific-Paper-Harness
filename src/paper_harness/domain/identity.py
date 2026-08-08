"""Stable identifiers for persisted domain objects."""

from __future__ import annotations

import re
from uuid import UUID, uuid5

from paper_harness.domain.errors import DomainInvariantError

PAPER_NAMESPACE = UUID("cbd3c150-b93e-4af8-95ba-e17e77ccb12a")
PAPER_VERSION_NAMESPACE = UUID("3e7688e7-0ca6-46d9-a4d2-e3b416a82cf3")
SOURCE_IDENTITY_NAMESPACE = UUID("34bd2824-7bd2-4df8-a09e-784db57d20cb")
AUTHOR_NAMESPACE = UUID("b741f3e4-28ce-48b5-b2c7-9080e3da6690")

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
