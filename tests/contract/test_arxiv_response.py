from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from paper_harness.adapters.arxiv.client import map_arxiv_result
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
