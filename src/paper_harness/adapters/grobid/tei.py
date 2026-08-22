"""Safe, deterministic mapping from GROBID TEI to parsed-paper domain objects."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from paper_harness.domain.analysis import (
    CitationContext,
    PageCoordinates,
    ParsedPaper,
    ParsedPassage,
    ParsedReference,
    ParsedSection,
)
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import (
    stable_citation_context_id,
    stable_parsed_paper_id,
    stable_parsed_passage_id,
    stable_parsed_reference_id,
    stable_parsed_section_id,
)
from paper_harness.ports.pdf_parser import PdfParserOutputError

GROBID_PARSER_NAME = "grobid"
GROBID_PARSER_VERSION = "0.9.0"
GROBID_PARSE_SOURCE = "grobid_tei"
DEFAULT_MAX_TEI_BYTES = 30 * 1024 * 1024

_TEI_NAMESPACE = "http://www.tei-c.org/ns/1.0"
_XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
_NAMESPACES = {"tei": _TEI_NAMESPACE}
_XML_ID = f"{{{_XML_NAMESPACE}}}id"
_FORBIDDEN_XML_DECLARATION = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_XML_DECLARATION_ENCODING = re.compile(
    r"^\s*<\?xml\b[^>]*\bencoding\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_FOUR_DIGIT_YEAR = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_MAX_CONTEXT_LENGTH = 2000
_MAX_XML_DEPTH = 128


def _tei(local_name: str) -> str:
    return f"{{{_TEI_NAMESPACE}}}{local_name}"


@dataclass(frozen=True, slots=True)
class _MappedPassage:
    element: ET.Element
    passage: ParsedPassage


def map_grobid_tei(
    content: bytes,
    *,
    paper_id: UUID,
    paper_version_id: UUID,
    parsed_at: datetime,
    max_tei_bytes: int = DEFAULT_MAX_TEI_BYTES,
) -> ParsedPaper:
    """Validate and map one complete GROBID TEI response without external lookups."""

    if not 1 <= max_tei_bytes <= DEFAULT_MAX_TEI_BYTES:
        raise ValueError("max_tei_bytes must be between 1 and the 30 MiB service bound")
    if not content.strip():
        raise PdfParserOutputError("GROBID returned an empty TEI document")
    if len(content) > max_tei_bytes:
        raise PdfParserOutputError("GROBID returned a TEI document above the configured limit")
    try:
        decoded_content = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise PdfParserOutputError("GROBID TEI must use UTF-8 encoding") from None
    if "\x00" in decoded_content:
        raise PdfParserOutputError("GROBID TEI must use UTF-8 encoding")
    declared_encoding = _XML_DECLARATION_ENCODING.search(decoded_content)
    if declared_encoding is not None and declared_encoding.group(1).lower() != "utf-8":
        raise PdfParserOutputError("GROBID TEI XML declaration must specify UTF-8 encoding")
    if _FORBIDDEN_XML_DECLARATION.search(decoded_content) is not None:
        raise PdfParserOutputError(
            "GROBID TEI contains a prohibited document or entity declaration"
        )

    try:
        root = ET.fromstring(content)
    except (ET.ParseError, ValueError) as error:
        raise PdfParserOutputError("GROBID returned malformed TEI XML") from error
    if root.tag != _tei("TEI"):
        raise PdfParserOutputError("GROBID response is not a namespaced TEI document")
    _validate_xml_depth(root)

    body = root.find("./tei:text/tei:body", _NAMESPACES)
    if body is None:
        raise PdfParserOutputError("GROBID TEI does not contain a body")

    parsed_paper_id = stable_parsed_paper_id(
        paper_version_id, GROBID_PARSER_NAME, GROBID_PARSER_VERSION
    )
    references = _map_references(root, parsed_paper_id)
    reference_source_ids = {reference.source_id for reference in references}
    sections, mapped_passages = _map_sections(body, parsed_paper_id)
    contexts = _map_citation_contexts(
        mapped_passages,
        parsed_paper_id=parsed_paper_id,
        reference_source_ids=reference_source_ids,
    )

    try:
        return ParsedPaper(
            id=parsed_paper_id,
            paper_id=paper_id,
            paper_version_id=paper_version_id,
            parser_name=GROBID_PARSER_NAME,
            parser_version=GROBID_PARSER_VERSION,
            parsed_at=parsed_at,
            source=GROBID_PARSE_SOURCE,
            sections=sections,
            references=references,
            citation_contexts=contexts,
            schema_version=1,
        )
    except DomainInvariantError as error:
        raise PdfParserOutputError("GROBID TEI violates parsed-paper invariants") from error


def _map_sections(
    body: ET.Element, parsed_paper_id: UUID
) -> tuple[tuple[ParsedSection, ...], tuple[_MappedPassage, ...]]:
    containers: list[ET.Element] = []
    if _owned_paragraphs(body):
        containers.append(body)
    containers.extend(element for element in body.iter(_tei("div")) if _owned_paragraphs(element))

    sections: list[ParsedSection] = []
    all_mapped_passages: list[_MappedPassage] = []
    for section_index, container in enumerate(containers):
        title = _section_title(container, section_index=section_index, is_body=container is body)
        mapped_passages = _map_passages(
            _owned_paragraphs(container),
            parsed_paper_id=parsed_paper_id,
            section_index=section_index,
        )
        if not mapped_passages:
            continue
        actual_section_index = len(sections)
        if actual_section_index != section_index:
            mapped_passages = _map_passages(
                _owned_paragraphs(container),
                parsed_paper_id=parsed_paper_id,
                section_index=actual_section_index,
            )
        try:
            section = ParsedSection(
                id=stable_parsed_section_id(parsed_paper_id, actual_section_index),
                index=actual_section_index,
                title=title,
                passages=tuple(item.passage for item in mapped_passages),
            )
        except DomainInvariantError as error:
            raise PdfParserOutputError("GROBID TEI contains an invalid body section") from error
        sections.append(section)
        all_mapped_passages.extend(mapped_passages)

    if not sections:
        raise PdfParserOutputError("GROBID TEI body has no non-empty passages")
    return tuple(sections), tuple(all_mapped_passages)


def _owned_paragraphs(container: ET.Element) -> tuple[ET.Element, ...]:
    paragraphs: list[ET.Element] = []
    remaining = list(reversed(tuple(container)))
    while remaining:
        child = remaining.pop()
        if child.tag == _tei("div"):
            continue
        if child.tag == _tei("p"):
            paragraphs.append(child)
            continue
        remaining.extend(reversed(tuple(child)))
    return tuple(paragraphs)


def _validate_xml_depth(root: ET.Element) -> None:
    remaining = [(root, 1)]
    while remaining:
        element, depth = remaining.pop()
        if depth > _MAX_XML_DEPTH:
            raise PdfParserOutputError("GROBID TEI exceeds the maximum element depth")
        remaining.extend((child, depth + 1) for child in element)


def _section_title(container: ET.Element, *, section_index: int, is_body: bool) -> str:
    if is_body:
        return "Body"
    head = container.find("./tei:head", _NAMESPACES)
    title = "" if head is None else _normalized_text(head)
    if title:
        return title
    section_type = " ".join(container.get("type", "").replace("_", " ").split())
    if section_type:
        return section_type
    return f"Untitled section {section_index + 1}"


def _map_passages(
    paragraphs: tuple[ET.Element, ...], *, parsed_paper_id: UUID, section_index: int
) -> tuple[_MappedPassage, ...]:
    mapped: list[_MappedPassage] = []
    for paragraph_index, paragraph in enumerate(paragraphs):
        sentences = tuple(paragraph.findall(".//tei:s", _NAMESPACES))
        passage_elements = sentences or (paragraph,)
        for sentence_index, element in enumerate(passage_elements):
            text = _normalized_text(element)
            if not text:
                continue
            passage_index = len(mapped)
            source_id = element.get(_XML_ID) or (
                f"section-{section_index}-paragraph-{paragraph_index}-passage-{sentence_index}"
            )
            coordinate_text = element.get("coords")
            if coordinate_text is None and element is not paragraph:
                coordinate_text = paragraph.get("coords")
            try:
                passage = ParsedPassage(
                    id=stable_parsed_passage_id(parsed_paper_id, source_id),
                    source_id=source_id,
                    section_index=section_index,
                    passage_index=passage_index,
                    text=text,
                    coordinates=_parse_coordinates(coordinate_text),
                )
            except DomainInvariantError as error:
                raise PdfParserOutputError("GROBID TEI contains an invalid passage") from error
            mapped.append(_MappedPassage(element=element, passage=passage))
    return tuple(mapped)


def _map_references(root: ET.Element, parsed_paper_id: UUID) -> tuple[ParsedReference, ...]:
    references: list[ParsedReference] = []
    source_ids: set[str] = set()
    for reference_index, element in enumerate(
        root.findall(".//tei:listBibl/tei:biblStruct", _NAMESPACES)
    ):
        source_id = element.get(_XML_ID) or f"reference-{reference_index}"
        if source_id in source_ids:
            continue
        source_ids.add(source_id)

        title_element = element.find("./tei:analytic/tei:title", _NAMESPACES)
        if title_element is None:
            title_element = element.find("./tei:monogr/tei:title", _NAMESPACES)
        title = None if title_element is None else (_normalized_text(title_element) or None)

        author_elements = element.findall("./tei:analytic/tei:author", _NAMESPACES)
        if not author_elements:
            author_elements = element.findall("./tei:monogr/tei:author", _NAMESPACES)
        authors = tuple(name for author in author_elements if (name := _normalized_text(author)))
        raw_element = element.find(".//tei:note[@type='raw_reference']", _NAMESPACES)
        raw_text = None if raw_element is None else (_normalized_text(raw_element) or None)

        try:
            reference = ParsedReference(
                id=stable_parsed_reference_id(parsed_paper_id, source_id),
                source_id=source_id,
                title=title,
                authors=authors,
                year=_reference_year(element),
                raw_text=raw_text,
            )
        except DomainInvariantError:
            continue
        references.append(reference)
    return tuple(references)


def _reference_year(element: ET.Element) -> int | None:
    for date_element in element.findall(".//tei:date", _NAMESPACES):
        candidate = date_element.get("when") or _normalized_text(date_element)
        match = _FOUR_DIGIT_YEAR.search(candidate)
        if match is not None:
            return int(match.group(1))
    return None


def _map_citation_contexts(
    mapped_passages: tuple[_MappedPassage, ...],
    *,
    parsed_paper_id: UUID,
    reference_source_ids: set[str],
) -> tuple[CitationContext, ...]:
    contexts: list[CitationContext] = []
    ordinals: defaultdict[tuple[UUID, str], int] = defaultdict(int)
    for mapped in mapped_passages:
        for reference_marker in mapped.element.findall(".//tei:ref", _NAMESPACES):
            if reference_marker.get("type") != "bibr":
                continue
            target_attribute = reference_marker.get("target")
            if target_attribute is None:
                continue
            targets = _reference_targets(target_attribute)
            if not targets:
                continue
            marker_text = _normalized_text(reference_marker)
            for reference_source_id in targets:
                if reference_source_id not in reference_source_ids:
                    continue
                ordinal_key = (mapped.passage.id, reference_source_id)
                ordinal = ordinals[ordinal_key]
                ordinals[ordinal_key] += 1
                try:
                    contexts.append(
                        CitationContext(
                            id=stable_citation_context_id(
                                parsed_paper_id,
                                mapped.passage.id,
                                reference_source_id,
                                ordinal,
                            ),
                            parsed_passage_id=mapped.passage.id,
                            reference_source_id=reference_source_id,
                            excerpt=_context_excerpt(mapped.passage.text, marker_text),
                            coordinates=_parse_coordinates(reference_marker.get("coords")),
                        )
                    )
                except DomainInvariantError:
                    continue
    return tuple(contexts)


def _reference_targets(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(target.removeprefix("#") for target in value.split() if target != "#")


def _context_excerpt(passage_text: str, marker_text: str) -> str:
    if len(passage_text) <= _MAX_CONTEXT_LENGTH:
        return passage_text
    marker_index = passage_text.find(marker_text) if marker_text else -1
    if marker_index < 0:
        return passage_text[:_MAX_CONTEXT_LENGTH]
    start = max(0, marker_index - (_MAX_CONTEXT_LENGTH // 2))
    end = min(len(passage_text), start + _MAX_CONTEXT_LENGTH)
    start = max(0, end - _MAX_CONTEXT_LENGTH)
    return passage_text[start:end]


def _parse_coordinates(value: str | None) -> tuple[PageCoordinates, ...]:
    if value is None or not value.strip():
        return ()
    coordinates: list[PageCoordinates] = []
    try:
        for box in value.split(";"):
            parts = tuple(part.strip() for part in box.split(","))
            if len(parts) != 5 or any(not part for part in parts):
                raise ValueError
            page = int(parts[0])
            numeric = tuple(float(part) for part in parts[1:])
            if not all(math.isfinite(part) for part in numeric):
                raise ValueError
            coordinates.append(
                PageCoordinates(
                    page=page,
                    x=numeric[0],
                    y=numeric[1],
                    width=numeric[2],
                    height=numeric[3],
                )
            )
    except (DomainInvariantError, ValueError):
        return ()
    return tuple(coordinates)


def _normalized_text(element: ET.Element) -> str:
    return " ".join("".join(element.itertext()).split())
