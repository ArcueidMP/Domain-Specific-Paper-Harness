"""Official arXiv OAI-PMH identifier pages, separate from Atom metadata lookup."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlencode
from xml.etree import ElementTree
from xml.parsers import expat

from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import validate_canonical_arxiv_id
from paper_harness.ports.arxiv import (
    ArxivIdentifierPage,
    ArxivResponseError,
    ArxivTokenExpiredError,
)

OAI_URL = "https://oaipmh.arxiv.org/oai"
_NS = "{http://www.openarchives.org/OAI/2.0/}"


def category_set(category: str) -> str:
    archive, separator, subject = category.partition(".")
    if not archive or (separator and not subject):
        raise ValueError("arXiv discovery category is invalid")
    group = (
        archive
        if archive in {"cs", "math", "q-bio", "q-fin", "stat", "eess", "econ"}
        else "physics"
    )
    return f"{group}:{archive}" + (f":{subject}" if separator else "")


def identifier_page_url(day: date, category: str, token: str | None) -> str:
    if token is not None:
        if not token.strip() or len(token) > 4096:
            raise ArxivResponseError("arXiv OAI resumption token is invalid")
        parameters = {"verb": "ListIdentifiers", "resumptionToken": token}
    else:
        parameters = {
            "verb": "ListIdentifiers",
            "metadataPrefix": "arXiv",
            "set": category_set(category),
            "from": day.isoformat(),
            "until": day.isoformat(),
        }
    return f"{OAI_URL}?{urlencode(parameters)}"


def parse_identifier_page(content: bytes, *, day: date, category: str) -> ArxivIdentifierPage:
    parser = expat.ParserCreate()
    depth = 0

    def reject(*_arguments: object) -> None:
        raise ArxivResponseError("arXiv OAI response contains a forbidden DTD or entity")

    def start(_name: str, _attributes: dict[str, str]) -> None:
        nonlocal depth
        depth += 1
        if depth > 128:
            raise ArxivResponseError("arXiv OAI response exceeds the XML depth bound")

    def end(_name: str) -> None:
        nonlocal depth
        depth -= 1

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        parser.Parse(content, True)
        root = ElementTree.fromstring(content)
    except (expat.ExpatError, ElementTree.ParseError) as error:
        raise ArxivResponseError("arXiv returned malformed OAI XML") from error
    if root.tag != f"{_NS}OAI-PMH":
        raise ArxivResponseError("arXiv OAI response has an invalid root")
    error_element = root.find(f"{_NS}error")
    if error_element is not None:
        code = error_element.get("code")
        if code == "noRecordsMatch":
            return ArxivIdentifierPage((), None)
        if code == "badResumptionToken":
            raise ArxivTokenExpiredError(
                "arXiv OAI continuation expired; replay the current day/category"
            )
        raise ArxivResponseError(f"arXiv OAI rejected identifier harvest: {code}")
    listing = root.find(f"{_NS}ListIdentifiers")
    if listing is None:
        raise ArxivResponseError("arXiv OAI response lacks ListIdentifiers")
    identifiers: set[str] = set()
    headers = listing.findall(f"{_NS}header")
    if not headers:
        raise ArxivResponseError("arXiv OAI identifier listing is empty without noRecordsMatch")
    for header in headers:
        identifier = header.findtext(f"{_NS}identifier", "").strip()
        if not identifier.startswith("oai:arXiv.org:"):
            raise ArxivResponseError("arXiv OAI record has an invalid required identity")
        canonical_id = identifier.removeprefix("oai:arXiv.org:")
        try:
            validate_canonical_arxiv_id(canonical_id)
            datestamp = date.fromisoformat(header.findtext(f"{_NS}datestamp", "").strip())
        except (DomainInvariantError, ValueError) as error:
            raise ArxivResponseError(
                "arXiv OAI record has invalid identity or datestamp"
            ) from error
        if datestamp != day:
            raise ArxivResponseError("arXiv OAI record is outside the requested update day")
        if header.get("status") != "deleted":
            sets = {element.text for element in header.findall(f"{_NS}setSpec")}
            if category_set(category) not in sets:
                raise ArxivResponseError("arXiv OAI record is outside the requested category")
            identifiers.add(canonical_id)
    token = listing.findtext(f"{_NS}resumptionToken", "").strip() or None
    if token is not None and len(token) > 4096:
        raise ArxivResponseError("arXiv OAI resumption token exceeds its size bound")
    if len(identifiers) > 10000:
        raise ArxivResponseError("arXiv OAI identifier page exceeds its size bound")
    return ArxivIdentifierPage(tuple(sorted(identifiers)), token)
