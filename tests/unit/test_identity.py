from __future__ import annotations

import pytest

from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import (
    parse_arxiv_identifier,
    stable_paper_id,
    stable_paper_version_id,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2601.01234v2", ("2601.01234", 2)),
        ("https://arxiv.org/abs/2601.01234v12", ("2601.01234", 12)),
        ("hep-th/9901001v3", ("hep-th/9901001", 3)),
    ],
)
def test_parse_arxiv_identifier_requires_and_preserves_version(
    raw: str, expected: tuple[str, int]
) -> None:
    assert parse_arxiv_identifier(raw) == expected


def test_unversioned_identifier_is_rejected() -> None:
    with pytest.raises(DomainInvariantError, match="explicit version"):
        parse_arxiv_identifier("2601.01234")


def test_stable_ids_distinguish_versions_but_not_repeated_ingestion() -> None:
    assert stable_paper_id("2601.01234") == stable_paper_id("2601.01234")
    assert stable_paper_version_id("2601.01234", 1) != stable_paper_version_id("2601.01234", 2)
