"""Read projections exposed by application and API boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
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
from paper_harness.domain.knowledge import (
    GraphEdge,
    GraphEntity,
    GraphEntityMention,
    LineageSnapshot,
    TrendPaperRecord,
    TrendSnapshot,
    TrendWindow,
)
from paper_harness.domain.models import (
    DailyRun,
    Paper,
    PaperSourceIdentity,
    PaperVersion,
    RunItem,
    RunOperation,
    TopicConfig,
)
from paper_harness.domain.reports import Report, ReportEvidenceReference


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
class ReportDetail:
    report: Report
    evidence: tuple[ReportEvidenceReference, ...]

    def __post_init__(self) -> None:
        expected_ids = set(self.report.evidence_ids)
        actual_ids = {item.id for item in self.evidence}
        if expected_ids != actual_ids or len(actual_ids) != len(self.evidence):
            raise DomainInvariantError(
                "report detail must expose every referenced evidence record exactly once"
            )


@dataclass(frozen=True, slots=True)
class ProductRunDetail:
    run: DailyRun
    items: tuple[RunItemDetail, ...]
    report: ReportDetail | None

    def __post_init__(self) -> None:
        if self.run.operation is not RunOperation.PRODUCT_PUBLICATION:
            raise DomainInvariantError("product run projection requires PRODUCT_PUBLICATION")
        if any(item.item.run_id != self.run.id for item in self.items):
            raise DomainInvariantError("product run items must belong to the projected run")
        if self.report is not None and self.report.report.run_id != self.run.id:
            raise DomainInvariantError("product run report must belong to the projected run")


@dataclass(frozen=True, slots=True)
class PublicationTrendArtifact:
    snapshot_id: UUID
    window: TrendWindow


@dataclass(frozen=True, slots=True)
class PublicationArtifactSummary:
    """Exact run-owned product artifacts used by deployment verification."""

    publication_run_id: UUID
    pipeline_execution_id: UUID
    graph_entity_count: int
    graph_edge_count: int
    inferred_graph_edge_count: int
    trend_snapshots: tuple[PublicationTrendArtifact, ...]
    lineage_snapshot_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if (
            min(
                self.graph_entity_count,
                self.graph_edge_count,
                self.inferred_graph_edge_count,
            )
            < 0
        ):
            raise DomainInvariantError("publication artifact counts cannot be negative")
        if self.inferred_graph_edge_count > self.graph_edge_count:
            raise DomainInvariantError(
                "inferred publication edge count cannot exceed the total edge count"
            )
        trend_ids = tuple(item.snapshot_id for item in self.trend_snapshots)
        trend_windows = tuple(item.window for item in self.trend_snapshots)
        if len(set(trend_ids)) != len(trend_ids):
            raise DomainInvariantError("publication trend snapshot IDs must be unique")
        if len(set(trend_windows)) != len(trend_windows):
            raise DomainInvariantError("publication trend windows must be unique")
        if len(set(self.lineage_snapshot_ids)) != len(self.lineage_snapshot_ids):
            raise DomainInvariantError("publication lineage snapshot IDs must be unique")


@dataclass(frozen=True, slots=True)
class GraphNodeDetail:
    entity: GraphEntity
    mentions: tuple[GraphEntityMention, ...]
    total_mentions: int

    def __post_init__(self) -> None:
        if not self.mentions:
            raise DomainInvariantError("graph node projection requires a persisted mention")
        if any(item.entity_id != self.entity.id for item in self.mentions):
            raise DomainInvariantError("graph node mentions must belong to the projected entity")
        if self.total_mentions < len(self.mentions):
            raise DomainInvariantError("graph node mention total cannot be smaller than its page")


class GraphEvidenceRole(StrEnum):
    SOURCE = "SOURCE"
    TARGET = "TARGET"
    RELATION = "RELATION"


@dataclass(frozen=True, slots=True)
class GraphEdgeEvidenceReference:
    edge_id: UUID
    evidence_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    role: GraphEvidenceRole


@dataclass(frozen=True, slots=True)
class GraphEdgeDetail:
    edge: GraphEdge
    evidence: tuple[GraphEdgeEvidenceReference, ...]

    def __post_init__(self) -> None:
        expected_ids = set(self.edge.evidence_ids)
        actual_ids = {item.evidence_id for item in self.evidence}
        if expected_ids != actual_ids or len(actual_ids) != len(self.evidence):
            raise DomainInvariantError(
                "graph edge detail must expose every evidence owner exactly once"
            )
        if any(item.edge_id != self.edge.id for item in self.evidence):
            raise DomainInvariantError("graph edge evidence must belong to the projected edge")


@dataclass(frozen=True, slots=True)
class GraphView:
    topic_id: UUID
    as_of: date | None
    nodes: tuple[GraphNodeDetail, ...]
    edges: tuple[GraphEdgeDetail, ...]
    total_nodes: int
    total_edges: int
    total_mentions: int
    truncated: bool

    def __post_init__(self) -> None:
        if (
            self.total_nodes < len(self.nodes)
            or self.total_edges < len(self.edges)
            or self.total_mentions < sum(item.total_mentions for item in self.nodes)
        ):
            raise DomainInvariantError("graph projection totals cannot be smaller than its page")
        node_ids = {item.entity.id for item in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise DomainInvariantError("graph projection nodes must be unique")
        if any(
            item.edge.source_entity_id not in node_ids or item.edge.target_entity_id not in node_ids
            for item in self.edges
        ):
            raise DomainInvariantError("graph projection edges cannot reference omitted nodes")
        expected_truncated = (
            self.total_nodes > len(self.nodes)
            or self.total_edges > len(self.edges)
            or self.total_mentions > sum(len(item.mentions) for item in self.nodes)
        )
        if self.truncated is not expected_truncated:
            raise DomainInvariantError("graph projection truncation flag is inconsistent")


@dataclass(frozen=True, slots=True)
class LineageDetail:
    snapshot: LineageSnapshot
    evidence: tuple[GraphEdgeEvidenceReference, ...]

    def __post_init__(self) -> None:
        edge_by_id = {item.id: item for item in self.snapshot.edges}
        if any(item.edge_id not in edge_by_id for item in self.evidence):
            raise DomainInvariantError("lineage evidence references an omitted edge")
        expected_pairs = {
            (edge.id, evidence_id)
            for edge in self.snapshot.edges
            for evidence_id in edge.evidence_ids
        }
        actual_pairs = {(item.edge_id, item.evidence_id) for item in self.evidence}
        if expected_pairs != actual_pairs or len(actual_pairs) != len(self.evidence):
            raise DomainInvariantError(
                "lineage detail must expose every edge evidence owner exactly once"
            )


@dataclass(frozen=True, slots=True)
class TrendDetail:
    snapshot: TrendSnapshot
    representative_papers: tuple[TrendPaperRecord, ...]
    total_entities: int
    truncated: bool

    def __post_init__(self) -> None:
        expected_ids = set(self.snapshot.representative_paper_ids)
        actual_ids = {item.paper_id for item in self.representative_papers}
        if expected_ids != actual_ids or len(actual_ids) != len(self.representative_papers):
            raise DomainInvariantError(
                "trend detail must expose every representative paper exactly once"
            )
        if self.total_entities < len(self.snapshot.entity_counts):
            raise DomainInvariantError("trend entity total cannot be smaller than its page")
        if self.truncated is not (self.total_entities > len(self.snapshot.entity_counts)):
            raise DomainInvariantError("trend entity truncation flag is inconsistent")


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
