"""Validated database projections used by the M4 batch publication pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit
from uuid import UUID

from paper_harness.application.read_models import ReportDetail, RunDetail
from paper_harness.domain.analysis import AnalysisBundle, VerificationStatus
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import ComparisonBundle
from paper_harness.domain.identity import validate_canonical_arxiv_id
from paper_harness.domain.knowledge import (
    GraphEdge,
    GraphEntity,
    GraphEntityMention,
    LineagePaper,
    TrendPaperRecord,
    TrendSnapshot,
)
from paper_harness.domain.models import PaperStage, RunItemStatus, RunOperation, RunStatus
from paper_harness.domain.reports import ReportEvidenceReference, ReportGraphChanges, ReportType


@dataclass(frozen=True, slots=True)
class ComparisonGraphInput:
    bundle: ComparisonBundle
    source_paper_title: str
    target_paper_title: str

    def __post_init__(self) -> None:
        if not self.source_paper_title.strip() or not self.target_paper_title.strip():
            raise DomainInvariantError("comparison graph input requires both paper titles")


@dataclass(frozen=True, slots=True)
class PublicationPaperCardInput:
    """Persisted source metadata that remains publishable without analysis enrichment."""

    paper_id: UUID
    paper_version_id: UUID
    canonical_arxiv_id: str
    title: str
    abstract: str | None
    source_url: str

    def __post_init__(self) -> None:
        validate_canonical_arxiv_id(self.canonical_arxiv_id)
        if not self.title.strip() or self.title != self.title.strip():
            raise DomainInvariantError("publication paper card requires a normalized title")
        if self.abstract is not None and (
            not self.abstract.strip() or self.abstract != self.abstract.strip()
        ):
            raise DomainInvariantError("publication paper card abstract must be normalized")
        parsed = urlsplit(self.source_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "arxiv.org"
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.path.startswith("/abs/")
            or parsed.query
            or parsed.fragment
        ):
            raise DomainInvariantError(
                "publication paper card requires an approved arXiv source URL"
            )


@dataclass(frozen=True, slots=True)
class ProductPaperInput:
    paper_id: UUID
    paper_version_id: UUID
    paper_title: str
    analysis: AnalysisBundle
    comparisons: tuple[ComparisonGraphInput, ...]
    evidence: tuple[ReportEvidenceReference, ...]
    retrieved_candidate_count: int
    related_work_available: bool | None = None
    related_work_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.paper_title.strip():
            raise DomainInvariantError("product paper input requires a title")
        if (
            self.analysis.analysis.paper_id != self.paper_id
            or self.analysis.analysis.paper_version_id != self.paper_version_id
        ):
            raise DomainInvariantError("product analysis input has the wrong paper owner")
        if self.retrieved_candidate_count < len(self.comparisons):
            raise DomainInvariantError(
                "retrieved candidate count cannot be smaller than comparisons"
            )
        if len({item.bundle.comparison.id for item in self.comparisons}) != len(self.comparisons):
            raise DomainInvariantError("product comparison inputs must be unique")
        evidence_by_id = {item.id: item for item in self.evidence}
        available_evidence_ids = set(evidence_by_id)
        if len(available_evidence_ids) != len(self.evidence):
            raise DomainInvariantError("product report evidence must be unique")
        required_comparison_evidence_ids = {
            evidence_id
            for item in self.comparisons
            for dimension in item.bundle.comparison.dimensions
            for evidence_id in dimension.source_evidence_ids + dimension.target_evidence_ids
        } | {
            evidence_id
            for item in self.comparisons
            for relation in item.bundle.relations
            for evidence_id in relation.evidence_ids
        }
        required_evidence_ids = {
            item.id
            for item in self.analysis.evidence
            if item.verification_status is not VerificationStatus.REJECTED
        } | required_comparison_evidence_ids
        if not required_evidence_ids.issubset(available_evidence_ids):
            raise DomainInvariantError("product report input omits referenced evidence")
        if any(
            evidence_by_id[evidence_id].verification_status is VerificationStatus.REJECTED
            for evidence_id in required_comparison_evidence_ids
        ):
            raise DomainInvariantError("product comparison references rejected evidence")
        if any(
            item.bundle.comparison.source_paper_id != self.paper_id
            or item.bundle.comparison.source_paper_version_id != self.paper_version_id
            or item.bundle.comparison.source_analysis_id != self.analysis.analysis.id
            for item in self.comparisons
        ):
            raise DomainInvariantError("product comparison input has the wrong source analysis")
        if self.related_work_available is True and self.related_work_reason is not None:
            raise DomainInvariantError("available related work cannot carry an unavailable reason")
        if self.related_work_available is False and (
            self.related_work_reason is None or not self.related_work_reason.strip()
        ):
            raise DomainInvariantError("unavailable related work requires a concise reason")


@dataclass(frozen=True, slots=True)
class ProductFailureInput:
    """Frozen upstream item failure that must remain visible in product publication."""

    paper_id: UUID
    paper_version_id: UUID
    stage: PaperStage
    failed_stage: PaperStage
    error_code: str
    retryable: bool
    error_detail: str

    def __post_init__(self) -> None:
        if not self.error_code.strip() or len(self.error_code) > 80:
            raise DomainInvariantError("product failure code must be concise valid text")
        if not self.error_detail.strip() or len(self.error_detail) > 1000:
            raise DomainInvariantError("product failure detail must be concise valid text")
        if "\x00" in self.error_code or "\x00" in self.error_detail:
            raise DomainInvariantError("product failure metadata contains invalid text")


@dataclass(frozen=True, slots=True)
class ProductPublicationInput:
    source_run: RunDetail
    papers: tuple[ProductPaperInput, ...]
    cards: tuple[PublicationPaperCardInput, ...]
    input_failures: tuple[ProductFailureInput, ...] = ()

    def __post_init__(self) -> None:
        if self.source_run.run.operation is not RunOperation.STRUCTURED_ANALYSIS:
            raise DomainInvariantError("product publication requires a structured-analysis run")
        if self.source_run.run.status not in (RunStatus.COMPLETE, RunStatus.PARTIAL):
            raise DomainInvariantError("product publication requires a publishable source run")
        successful_version_ids = {
            item.item.paper_version_id
            for item in self.source_run.items
            if item.item.status is RunItemStatus.COMPLETED
        }
        selected_version_ids = {item.item.paper_version_id for item in self.source_run.items}
        projected_version_ids = {item.paper_version_id for item in self.papers}
        card_version_ids = {item.paper_version_id for item in self.cards}
        failed_version_ids = {item.paper_version_id for item in self.input_failures}
        if (
            len(projected_version_ids) != len(self.papers)
            or len(card_version_ids) != len(self.cards)
            or len(failed_version_ids) != len(self.input_failures)
            or projected_version_ids & failed_version_ids
            or successful_version_ids != projected_version_ids | failed_version_ids
            or selected_version_ids != card_version_ids
        ):
            raise DomainInvariantError(
                "product input must card every selected item and project or fail each analysis"
            )
        cards_by_version = {item.paper_version_id: item for item in self.cards}
        if any(
            cards_by_version[item.paper_version_id].paper_id != item.paper_id
            or cards_by_version[item.paper_version_id].title != item.paper_title
            for item in self.papers
        ):
            raise DomainInvariantError("product paper analysis does not match its metadata card")


@dataclass(frozen=True, slots=True)
class GraphCorpusInput:
    topic_id: UUID
    papers: tuple[TrendPaperRecord, ...]
    lineage_papers: tuple[LineagePaper, ...]
    entities: tuple[GraphEntity, ...]
    mentions: tuple[GraphEntityMention, ...]
    edges: tuple[GraphEdge, ...]
    mention_activity_dates: Mapping[UUID, date]
    edge_activity_dates: Mapping[UUID, date]

    def __post_init__(self) -> None:
        if any(item.topic_id != self.topic_id for item in self.entities):
            raise DomainInvariantError("graph corpus cannot cross topic boundaries")
        if len({item.paper_version_id for item in self.papers}) != len(self.papers):
            raise DomainInvariantError("graph corpus paper versions must be unique")
        if len({item.graph_entity_id for item in self.lineage_papers}) != len(self.lineage_papers):
            raise DomainInvariantError("graph corpus lineage paper entities must be unique")
        entity_ids = {item.id for item in self.entities}
        if len(entity_ids) != len(self.entities):
            raise DomainInvariantError("graph corpus entities must be unique")
        if any(item.entity_id not in entity_ids for item in self.mentions):
            raise DomainInvariantError("graph corpus mention references an unknown entity")
        if any(
            item.source_entity_id not in entity_ids or item.target_entity_id not in entity_ids
            for item in self.edges
        ):
            raise DomainInvariantError("graph corpus edge references an unknown entity")
        mention_ids = {item.id for item in self.mentions}
        if len(mention_ids) != len(self.mentions):
            raise DomainInvariantError("graph corpus mentions must be unique")
        edge_ids = {item.id for item in self.edges}
        if len(edge_ids) != len(self.edges):
            raise DomainInvariantError("graph corpus edges must be unique")
        if not mention_ids.issubset(self.mention_activity_dates):
            raise DomainInvariantError("graph corpus is missing mention activity dates")
        if not edge_ids.issubset(self.edge_activity_dates):
            raise DomainInvariantError("graph corpus is missing edge activity dates")
        if any(type(value) is not date for value in self.mention_activity_dates.values()):
            raise DomainInvariantError("graph corpus mention activity dates must be dates")
        if any(type(value) is not date for value in self.edge_activity_dates.values()):
            raise DomainInvariantError("graph corpus edge activity dates must be dates")


@dataclass(frozen=True, slots=True)
class GraphWriteResult:
    entity_ids: tuple[UUID, ...]
    edge_ids: tuple[UUID, ...]
    new_entity_ids: tuple[UUID, ...]
    inferred_edge_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "entity_ids",
            "edge_ids",
            "new_entity_ids",
            "inferred_edge_ids",
        ):
            values = getattr(self, field_name)
            object.__setattr__(self, field_name, tuple(sorted(set(values), key=str)))
        if not set(self.new_entity_ids).issubset(self.entity_ids):
            raise DomainInvariantError("graph write new entities must belong to the write")
        if not set(self.inferred_edge_ids).issubset(self.edge_ids):
            raise DomainInvariantError("graph write inferred edges must belong to the write")

    @property
    def entity_count(self) -> int:
        return len(self.entity_ids)

    @property
    def edge_count(self) -> int:
        return len(self.edge_ids)

    @property
    def new_entity_count(self) -> int:
        return len(self.new_entity_ids)

    @property
    def inferred_edge_count(self) -> int:
        return len(self.inferred_edge_ids)


@dataclass(frozen=True, slots=True)
class PeriodicReportInput:
    topic_id: UUID
    report_type: ReportType
    period_start: date
    period_end: date
    daily_reports: tuple[ReportDetail, ...]
    included_paper_ids: tuple[UUID, ...]
    graph_changes: ReportGraphChanges
    trends: tuple[TrendSnapshot, ...]

    def __post_init__(self) -> None:
        if self.report_type not in (ReportType.WEEKLY, ReportType.MONTHLY):
            raise DomainInvariantError("periodic input requires weekly or monthly report scope")
        if self.period_start > self.period_end:
            raise DomainInvariantError("periodic report input has a reversed period")
        if len(set(self.included_paper_ids)) != len(self.included_paper_ids):
            raise DomainInvariantError("periodic included paper IDs must be unique")
        if any(
            detail.report.report_type is not ReportType.DAILY
            or detail.report.topic_id != self.topic_id
            or not self.period_start <= detail.report.logical_date <= self.period_end
            for detail in self.daily_reports
        ):
            raise DomainInvariantError("periodic source reports are outside the requested scope")
        if len({detail.report.logical_date for detail in self.daily_reports}) != len(
            self.daily_reports
        ):
            raise DomainInvariantError("periodic source daily dates must be unique")
        if any(snapshot.topic_id != self.topic_id for snapshot in self.trends):
            raise DomainInvariantError("periodic trend snapshots cannot cross topic boundaries")
