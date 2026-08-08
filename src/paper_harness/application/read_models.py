"""Read projections exposed by the M1 API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from paper_harness.domain.analysis import AnalysisClaim, AnalysisScope, Evidence, PaperAnalysis
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.models import (
    DailyRun,
    Paper,
    PaperSourceIdentity,
    PaperVersion,
    RunItem,
    TopicConfig,
)
from paper_harness.domain.reports import Report


@dataclass(frozen=True, slots=True)
class StoredTopic:
    config: TopicConfig
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PaperDetail:
    paper: Paper
    versions: tuple[PaperVersion, ...]
    source_identities: tuple[PaperSourceIdentity, ...]
    topic_slugs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalysisTarget:
    paper: Paper
    version: PaperVersion


@dataclass(frozen=True, slots=True)
class AnalysisDetail:
    analysis: PaperAnalysis
    arxiv_version: int
    claims: tuple[AnalysisClaim, ...]
    evidence: tuple[Evidence, ...]
    parser_name: str | None = None
    parser_version: str | None = None

    def __post_init__(self) -> None:
        parser_values = (self.parser_name, self.parser_version)
        if self.analysis.analysis_scope is AnalysisScope.FULL_TEXT:
            if any(value is None or not value.strip() for value in parser_values):
                raise DomainInvariantError(
                    "full-text analysis detail requires parser name and version"
                )
        elif any(value is not None for value in parser_values):
            raise DomainInvariantError(
                "abstract-only analysis detail cannot expose parser provenance"
            )


@dataclass(frozen=True, slots=True)
class RunItemDetail:
    item: RunItem
    canonical_arxiv_id: str
    paper_title: str


@dataclass(frozen=True, slots=True)
class RunDetail:
    run: DailyRun
    items: tuple[RunItemDetail, ...]
    report: Report | None = None
