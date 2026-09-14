from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from paper_harness.adapters.arxiv.client import map_arxiv_result
from paper_harness.domain.identity import stable_author_id
from paper_harness.ports.arxiv import ArxivResponseError


def _result(raw: dict[str, Any]) -> SimpleNamespace:
    authors = [SimpleNamespace(name=name) for name in raw["authors"]]
    return SimpleNamespace(
        **{
            **raw,
            "authors": authors,
            "published": datetime.fromisoformat(raw["published"]),
            "updated": datetime.fromisoformat(raw["updated"]),
            "get_short_id": lambda: raw["short_id"],
        }
    )


def test_stored_arxiv_fixture_maps_to_explicit_version() -> None:
    raw = json.loads(Path("tests/contract/fixtures/arxiv_result.json").read_text(encoding="utf-8"))
    record = map_arxiv_result(_result(raw))
    assert record.canonical_arxiv_id == "2601.01234"
    assert record.version == 2
    assert record.authors == ("Ada Lovelace", "Alan Turing")


def test_arxiv_response_without_version_is_rejected() -> None:
    raw = json.loads(Path("tests/contract/fixtures/arxiv_result.json").read_text(encoding="utf-8"))
    raw["short_id"] = "2601.01234"
    with pytest.raises(ArxivResponseError, match="invalid paper metadata"):
        map_arxiv_result(_result(raw))


@pytest.mark.parametrize(
    ("provider_names", "expected_names"),
    (
        (("Ada Lovelace", "Ada Lovelace", "Alan Turing"), ("Ada Lovelace", "Alan Turing")),
        (
            (
                " Grace Hopper ",
                "Ada\t\nLovelace",
                "GRACE  HOPPER",
                "ada lovelace",
                "Alan Turing",
            ),
            ("Grace Hopper", "Ada Lovelace", "Alan Turing"),
        ),
        (
            ("Stra\u00dfe Researcher", "STRASSE RESEARCHER", "\u03a3igma", "\u03c2igma"),
            ("Stra\u00dfe Researcher", "\u03a3igma"),
        ),
        (
            ("Jos\u00e9 Garc\u00eda", "Jose Garcia", "J. Garc\u00eda"),
            ("Jos\u00e9 Garc\u00eda", "Jose Garcia", "J. Garc\u00eda"),
        ),
    ),
)
def test_arxiv_authors_keep_first_distinct_normalized_name_in_provider_order(
    provider_names: tuple[str, ...],
    expected_names: tuple[str, ...],
) -> None:
    raw = json.loads(Path("tests/contract/fixtures/arxiv_result.json").read_text(encoding="utf-8"))
    raw["authors"] = provider_names

    record = map_arxiv_result(_result(raw))

    assert record.authors == expected_names
    assert tuple(stable_author_id(name) for name in record.authors) == tuple(
        dict.fromkeys(stable_author_id(name) for name in provider_names)
    )
    raw["authors"] = record.authors
    assert map_arxiv_result(_result(raw)) == record


@pytest.mark.parametrize("invalid_name", (None, 42, False, b"Ada Lovelace", {}, " \t\n"))
def test_arxiv_author_deduplication_does_not_hide_malformed_names(invalid_name: object) -> None:
    raw = json.loads(Path("tests/contract/fixtures/arxiv_result.json").read_text(encoding="utf-8"))
    raw["authors"] = ["Ada Lovelace", "ada lovelace", invalid_name]

    with pytest.raises(ArxivResponseError, match="arXiv author"):
        map_arxiv_result(_result(raw))
