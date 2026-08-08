"""Strict GROBID 0.9.0 scientific PDF parser adapter."""

from paper_harness.adapters.grobid.client import GrobidClient
from paper_harness.adapters.grobid.tei import (
    GROBID_PARSER_NAME,
    GROBID_PARSER_VERSION,
    map_grobid_tei,
)

__all__ = [
    "GROBID_PARSER_NAME",
    "GROBID_PARSER_VERSION",
    "GrobidClient",
    "map_grobid_tei",
]
