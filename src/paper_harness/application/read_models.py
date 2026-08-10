"""Read projections exposed by application and API boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from paper_harness.domain.analysis import (
    AnalysisClaim,
    AnalysisScope,
    Evidence,
    EvidenceType,
    PaperAnalysis,
    VerificationStatus,
)
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import (
    Comparison,
    ComparisonBundle,
    ExternalPaperStub,
    HistoricalCorpusEntry,
    PaperRelation,
    SearchAction,
    SearchCandidate,
    SearchCandidateDiscovery,
    SearchSession,
)
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


@dataclass(frozen=True, slots=True)
class HistoricalRetrievalMatch:
    external_paper: ExternalPaperStub
    corpus_entry: HistoricalCorpusEntry
    score: float

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 1:
            raise DomainInvariantError("historical retrieval score must be between zero and one")


@dataclass(frozen=True, slots=True)
class SearchSessionDetail:
    session: SearchSession
    actions: tuple[SearchAction, ...]
    candidates: tuple[SearchCandidate, ...]
    discoveries: tuple[SearchCandidateDiscovery, ...]


@dataclass(frozen=True, slots=True)
class RelatedWorkItem:
    candidate: SearchCandidate
    external_paper: ExternalPaperStub
    discoveries: tuple[SearchCandidateDiscovery, ...]
    relations: tuple[PaperRelation, ...]
    comparison_id: UUID | None


@dataclass(frozen=True, slots=True)
class RelatedWorkDetail:
    session: SearchSession
    actions: tuple[SearchAction, ...]
    items: tuple[RelatedWorkItem, ...]
    comparisons: tuple[ComparisonBundle, ...]


@dataclass(frozen=True, slots=True)
class ComparisonEvidenceReference:
    id: UUID
    analysis_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    analysis_scope: AnalysisScope
    section: str
    excerpt: str
    evidence_type: EvidenceType
    verification_status: VerificationStatus


@dataclass(frozen=True, slots=True)
class ComparisonDetail:
    comparison: Comparison
    relations: tuple[PaperRelation, ...]
    evidence: tuple[ComparisonEvidenceReference, ...]

    def __post_init__(self) -> None:
        ComparisonBundle(comparison=self.comparison, relations=self.relations)
        source_evidence_ids = {
            evidence_id
            for dimension in self.comparison.dimensions
            for evidence_id in dimension.source_evidence_ids
        }
        target_evidence_ids = {
            evidence_id
            for dimension in self.comparison.dimensions
            for evidence_id in dimension.target_evidence_ids
        }
        referenced_evidence_ids = source_evidence_ids | target_evidence_ids
        projected_evidence_ids = {item.id for item in self.evidence}
        if referenced_evidence_ids != projected_evidence_ids or len(projected_evidence_ids) != len(
            self.evidence
        ):
            raise DomainInvariantError(
                "comparison detail must expose every referenced evidence record exactly once"
            )
        evidence_by_id = {item.id: item for item in self.evidence}
        if any(
            evidence_by_id[evidence_id].analysis_id != self.comparison.source_analysis_id
            or evidence_by_id[evidence_id].analysis_scope
            is not self.comparison.source_analysis_scope
            or evidence_by_id[evidence_id].paper_id != self.comparison.source_paper_id
            or evidence_by_id[evidence_id].paper_version_id
            != self.comparison.source_paper_version_id
            for evidence_id in source_evidence_ids
        ):
            raise DomainInvariantError("source comparison evidence has the wrong owner")
        if any(
            evidence_by_id[evidence_id].analysis_id != self.comparison.target_analysis_id
            or evidence_by_id[evidence_id].analysis_scope
            is not self.comparison.target_analysis_scope
            or evidence_by_id[evidence_id].paper_id != self.comparison.target_paper_id
            or evidence_by_id[evidence_id].paper_version_id
            != self.comparison.target_paper_version_id
            for evidence_id in target_evidence_ids
        ):
            raise DomainInvariantError("target comparison evidence has the wrong owner")
