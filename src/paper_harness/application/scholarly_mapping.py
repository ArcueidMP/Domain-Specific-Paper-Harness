"""Mapping at the validated Semantic Scholar-to-domain boundary."""

from __future__ import annotations

from datetime import datetime

from paper_harness.domain.historical import ExternalPaperStub
from paper_harness.domain.identity import stable_external_paper_id
from paper_harness.ports.scholarly_search import ScholarlyPaper


def external_stub_from_scholarly_paper(
    paper: ScholarlyPaper, *, observed_at: datetime
) -> ExternalPaperStub:
    """Create the stable bibliographic stub allowed for historical retrieval."""

    return ExternalPaperStub(
        id=stable_external_paper_id(
            paper.semantic_scholar_id,
            arxiv_id=paper.external_ids.arxiv_id,
            doi=paper.external_ids.doi,
        ),
        semantic_scholar_id=paper.semantic_scholar_id,
        title=paper.title,
        abstract=paper.abstract,
        year=paper.year,
        publication_date=paper.publication_date,
        venue=paper.venue,
        authors=tuple(author.name for author in paper.authors),
        external_ids=paper.external_ids.values,
        arxiv_id=paper.external_ids.arxiv_id,
        doi=paper.external_ids.doi,
        citation_count=paper.citation_count,
        influential_citation_count=paper.influential_citation_count,
        full_text_available=paper.external_ids.arxiv_id is not None,
        source="semantic_scholar",
        schema_version=1,
        created_at=observed_at,
        updated_at=observed_at,
    )
