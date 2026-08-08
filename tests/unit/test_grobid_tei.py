from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from paper_harness.adapters.grobid.tei import map_grobid_tei
from paper_harness.domain.analysis import ParsedPaper
from paper_harness.ports.pdf_parser import PdfParserOutputError

_PAPER_ID = UUID("8f018024-3b47-54ab-a248-326c3e2b96ae")
_PAPER_VERSION_ID = UUID("a844bcec-145d-5f9a-96e8-82f06d8b58b5")
_PARSED_AT = datetime(2026, 8, 8, 12, tzinfo=UTC)
_FIXTURE = Path("tests/contract/fixtures/grobid_fulltext_0_9_0.tei.xml")


def test_namespaced_grobid_tei_maps_ordered_structure_and_coordinates() -> None:
    parsed = _map_fixture()

    assert parsed.parser_name == "grobid"
    assert parsed.parser_version == "0.9.0"
    assert parsed.source == "grobid_tei"
    assert [section.title for section in parsed.sections] == ["Introduction", "Evaluation"]
    assert [passage.source_id for passage in parsed.sections[0].passages] == [
        "s-introduction-1",
        "s-introduction-2",
    ]
    assert parsed.sections[0].passages[0].text == "LLM agents use tools [1] to solve tasks."
    assert parsed.sections[0].passages[0].coordinates[0].page == 1
    assert parsed.sections[0].passages[1].coordinates[0].width == 468.0
    assert parsed.sections[1].passages[0].source_id == "p-evaluation"
    assert parsed.sections[1].passages[0].passage_index == 0

    assert [reference.source_id for reference in parsed.references] == ["b0", "b1"]
    assert parsed.references[0].title == "Tool Use by Language Model Agents"
    assert parsed.references[0].authors == ("Ada Lovelace", "Alan Turing")
    assert parsed.references[0].year == 2024
    assert parsed.references[0].raw_text is not None

    assert [context.reference_source_id for context in parsed.citation_contexts] == ["b0", "b1"]
    assert parsed.citation_contexts[0].excerpt == "LLM agents use tools [1] to solve tasks."
    assert parsed.citation_contexts[0].coordinates[0].x == 201.0
    assert parsed.citation_contexts[0].parsed_passage_id == parsed.sections[0].passages[0].id


@pytest.mark.parametrize(
    "document",
    [
        b"<?xml version='1.0'?><!DOCTYPE TEI><TEI />",
        b"<?xml version='1.0'?><!ENTITY paper 'untrusted'><TEI />",
    ],
)
def test_tei_rejects_document_type_and_entity_declarations(document: bytes) -> None:
    with pytest.raises(PdfParserOutputError, match="prohibited"):
        _map(document)


def test_tei_rejects_utf16_that_would_hide_an_entity_declaration_from_byte_scans() -> None:
    document = """<?xml version="1.0" encoding="UTF-16"?>
<!DOCTYPE TEI [<!ENTITY injected "untrusted">]>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text><body><p>&injected;</p></body></text>
</TEI>
""".encode("utf-16")

    with pytest.raises(PdfParserOutputError, match="UTF-8"):
        _map(document)


def test_tei_rejects_a_non_utf8_xml_encoding_declaration() -> None:
    document = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text><body><p>ASCII content with a false encoding claim.</p></body></text>
</TEI>
"""

    with pytest.raises(PdfParserOutputError, match="must specify UTF-8"):
        _map(document)


def test_tei_rejects_excessive_element_depth_without_recursive_mapping() -> None:
    wrapper_count = 140
    document = (
        '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
        + ("<note>" * wrapper_count)
        + "<p>bounded text</p>"
        + ("</note>" * wrapper_count)
        + "</body></text></TEI>"
    ).encode()

    with pytest.raises(PdfParserOutputError, match="maximum element depth"):
        _map(document)


def test_tei_rejects_empty_malformed_oversized_and_non_namespaced_documents() -> None:
    invalid_documents = (
        b"",
        b"<TEI>",
        b"<TEI><text><body><p>text</p></body></text></TEI>",
    )
    for document in invalid_documents:
        with pytest.raises(PdfParserOutputError):
            _map(document)

    with pytest.raises(PdfParserOutputError, match="configured limit"):
        map_grobid_tei(
            b"<TEI />",
            paper_id=_PAPER_ID,
            paper_version_id=_PAPER_VERSION_ID,
            parsed_at=_PARSED_AT,
            max_tei_bytes=2,
        )


def test_tei_rejects_invalid_coordinates_and_unresolved_citations() -> None:
    fixture = _FIXTURE.read_bytes()
    invalid_coordinates = fixture.replace(
        b'coords="1,201.0,115.0,16.0,12.0"', b'coords="not-coordinates"', 1
    )
    with pytest.raises(PdfParserOutputError, match="coordinates"):
        _map(invalid_coordinates)

    unresolved = fixture.replace(b'target="#b0"', b'target="#missing"', 1)
    with pytest.raises(PdfParserOutputError, match="does not resolve"):
        _map(unresolved)


def test_mapping_is_deterministic_for_the_same_version_and_parser_identity() -> None:
    first = _map_fixture()
    second = _map_fixture()

    assert first.id == second.id
    assert [section.id for section in first.sections] == [section.id for section in second.sections]
    assert [passage.id for section in first.sections for passage in section.passages] == [
        passage.id for section in second.sections for passage in section.passages
    ]
    assert [context.id for context in first.citation_contexts] == [
        context.id for context in second.citation_contexts
    ]


def _map_fixture() -> ParsedPaper:
    return _map(_FIXTURE.read_bytes())


def _map(document: bytes) -> ParsedPaper:
    return map_grobid_tei(
        document,
        paper_id=_PAPER_ID,
        paper_version_id=_PAPER_VERSION_ID,
        parsed_at=_PARSED_AT,
    )
