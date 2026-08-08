from __future__ import annotations

import os
from uuid import UUID

import pytest

from paper_harness.adapters.arxiv import ArxivClient
from paper_harness.adapters.grobid import GrobidClient
from paper_harness.ports.pdf_parser import PdfParseRequest

pytestmark = pytest.mark.live


def test_live_arxiv_pdf_to_grobid_parsed_paper_contract() -> None:
    if os.environ.get("RUN_LIVE_GROBID_TEST") != "1":
        pytest.skip("set RUN_LIVE_GROBID_TEST=1 for the explicit live GROBID parse smoke test")
    grobid_url = os.environ.get("GROBID_URL", "").strip()
    if not grobid_url:
        pytest.fail("GROBID_URL is required when RUN_LIVE_GROBID_TEST=1")

    canonical_arxiv_id = "1706.03762"
    version = 7
    pdf_url = f"https://arxiv.org/pdf/{canonical_arxiv_id}v{version}"
    pdf = ArxivClient(max_total_seconds=120).download_pdf(
        canonical_arxiv_id=canonical_arxiv_id,
        version=version,
        pdf_url=pdf_url,
    )
    parsed = GrobidClient(grobid_url).parse(
        PdfParseRequest(
            paper_id=UUID("91c198f8-c23a-40e3-bd86-246b92be7813"),
            paper_version_id=UUID("029b4bec-9d07-45f6-9af1-b557ec6ece03"),
            canonical_arxiv_id=canonical_arxiv_id,
            arxiv_version=version,
            content=pdf.content,
        )
    )

    assert parsed.parser_name == "grobid"
    assert parsed.parser_version == "0.9.0"
    assert parsed.paper_version_id == UUID("029b4bec-9d07-45f6-9af1-b557ec6ece03")
    assert parsed.sections
    assert any(section.passages for section in parsed.sections)
    assert any(passage.coordinates for section in parsed.sections for passage in section.passages)
    assert parsed.references
