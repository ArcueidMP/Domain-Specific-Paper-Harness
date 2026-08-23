"""PostgreSQL persistence for M4 graph, trend, lineage, and report publication."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import and_, case, delete, func, or_, select, union, update
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm import Session, aliased, sessionmaker

from paper_harness.application.product_models import (
    ComparisonGraphInput,
    GraphCorpusInput,
    GraphWriteResult,
    PeriodicReportInput,
    ProductFailureInput,
    ProductPaperInput,
    ProductPublicationInput,
    PublicationPaperCardInput,
)
from paper_harness.application.read_models import (
    GraphEdgeDetail,
    GraphEdgeEvidenceReference,
    GraphEvidenceRole,
    GraphNodeDetail,
    GraphView,
    LineageDetail,
    ProductRunDetail,
    PublicationArtifactSummary,
    PublicationTrendArtifact,
    ReportDetail,
    RunDetail,
    RunItemDetail,
    TrendAvailabilityStatus,
    TrendDetail,
)
from paper_harness.domain.analysis import (
    AnalysisBundle,
    AnalysisClaim,
    AnalysisScope,
    ClaimType,
    Evidence,
    EvidenceType,
    ModelUsage,
    PageCoordinates,
    PaperAnalysis,
    VerificationStatus,
)
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import (
    ComparabilityStatus,
    RelationProvenance,
    SearchSessionStatus,
)
from paper_harness.domain.identity import stable_lineage_snapshot_id
from paper_harness.domain.knowledge import (
    GraphEdge,
    GraphEntity,
    GraphEntityMention,
    GraphEntityType,
    GraphModelProvenance,
    GraphRelationType,
    KnowledgeGraphBundle,
    LineageCorpusScope,
    LineageNode,
    LineagePaper,
    LineageSnapshot,
    TrendChange,
    TrendDataSufficiency,
    TrendEntityCount,
    TrendGrowthStatus,
    TrendPaperRecord,
    TrendRelationCount,
    TrendSnapshot,
    TrendThresholds,
    TrendWindow,
)
from paper_harness.domain.models import (
    DailyRun,
    PaperStage,
    PipelineExecutionMode,
    RunItem,
    RunItemStatus,
    RunOperation,
    RunStatus,
)
from paper_harness.domain.reports import (
    Report,
    ReportComparisonHighlight,
    ReportCounts,
    ReportEntityHighlight,
    ReportEvidenceReference,
    ReportFailure,
    ReportGraphChanges,
    ReportLineageHighlight,
    ReportNarrativeMode,
    ReportPaperHighlight,
    ReportSection,
    ReportSectionKind,
    ReportType,
)
from paper_harness.ports.repository import (
    RepositoryError,
    RepositoryIntegrityError,
    RepositoryUnavailableError,
)

from .historical_repository import comparison_bundle_from_session
from .models import (
    AnalysisClaimRow,
    ComparisonEvidenceLinkRow,
    ComparisonRow,
    DailyRunRow,
    EvidenceClaimRow,
    EvidenceRow,
    GraphEdgeEvidenceLinkRow,
    GraphEdgeRow,
    GraphEntityMentionRow,
    GraphEntityRow,
    GraphMentionEvidenceLinkRow,
    LineageEdgeRow,
    LineageNodeRow,
    LineageSnapshotRow,
    PaperAnalysisRow,
    PaperRelationRow,
    PaperRow,
    PaperVersionRow,
    ParsedPaperRow,
    PipelineExecutionRow,
    ProductRunComparisonInputRow,
    ProductRunPaperInputRow,
    RelationEvidenceLinkRow,
    ReportComparisonHighlightRow,
    ReportEntityHighlightRow,
    ReportEvidenceLinkRow,
    ReportFailureRow,
    ReportLineageHighlightRow,
    ReportPaperHighlightRow,
    ReportRow,
    ReportSectionRow,
    ReportTrendLinkRow,
    RunItemRow,
    SearchCandidateRow,
    SearchSessionRow,
    TopicRow,
    TrendMetricRow,
    TrendRepresentativePaperRow,
    TrendSnapshotRow,
)

_GRAPH_READY_STAGES = (
    PaperStage.GRAPH_UPDATED.value,
    PaperStage.TREND_SNAPSHOTS_GENERATED.value,
    PaperStage.REPORT_GENERATED.value,
    PaperStage.PUBLISHED.value,
)

_PUBLISHED_PRODUCT_STATUSES = (
    RunStatus.COMPLETE.value,
    RunStatus.PARTIAL.value,
)

_MAX_GRAPH_MENTIONS_PER_NODE = 100

_LINEAGE_PREDECESSOR_TYPES = (
    GraphRelationType.CITES,
    GraphRelationType.EXTENDS,
    GraphRelationType.IMPROVES_ON,
)


def _sorted_ids(values: Iterable[UUID]) -> tuple[UUID, ...]:
    return tuple(sorted(set(values), key=str))


def _published_product_run_ids(*, topic_id: UUID | None = None, as_of: date | None = None) -> Any:
    newer = aliased(DailyRunRow)
    newer_published_revision = (
        select(newer.id)
        .where(
            newer.topic_id == DailyRunRow.topic_id,
            newer.logical_date == DailyRunRow.logical_date,
            newer.operation == RunOperation.PRODUCT_PUBLICATION.value,
            newer.status.in_(_PUBLISHED_PRODUCT_STATUSES),
            newer.pipeline_execution_mode != PipelineExecutionMode.SMOKE.value,
            or_(
                newer.started_at > DailyRunRow.started_at,
                and_(
                    newer.started_at == DailyRunRow.started_at,
                    newer.id > DailyRunRow.id,
                ),
            ),
        )
        .exists()
    )
    statement = select(DailyRunRow.id).where(
        DailyRunRow.operation == RunOperation.PRODUCT_PUBLICATION.value,
        DailyRunRow.status.in_(_PUBLISHED_PRODUCT_STATUSES),
        DailyRunRow.pipeline_execution_mode != PipelineExecutionMode.SMOKE.value,
        ~newer_published_revision,
    )
    if topic_id is not None:
        statement = statement.where(DailyRunRow.topic_id == topic_id)
    if as_of is not None:
        statement = statement.where(DailyRunRow.logical_date <= as_of)
    return statement


class ProductRepositoryMixin:
    """Focused M4 methods mixed into the synchronous PostgreSQL repository."""

    _sessions: sessionmaker[Session]

    def get_run(self, run_id: UUID) -> RunDetail | None:
        try:
            with self._sessions() as session:
                row = session.get(DailyRunRow, run_id)
                return None if row is None else _run_detail_from_session(session, row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL run detail query is unavailable"
            ) from error
        except DomainInvariantError as error:
            raise RepositoryIntegrityError(
                "stored run detail violates domain invariants"
            ) from error

    def _get_run_detail_by_statement(self, statement: Any) -> RunDetail | None:
        """Load a full run for the legacy latest-run endpoint."""

        try:
            with self._sessions() as session:
                row = session.scalars(statement).one_or_none()
                return None if row is None else _run_detail_from_session(session, row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL run detail query is unavailable"
            ) from error
        except DomainInvariantError as error:
            raise RepositoryIntegrityError(
                "stored run detail violates domain invariants"
            ) from error

    def get_product_run_for_date(
        self,
        topic_id: UUID,
        logical_date: date,
        *,
        pipeline_execution_id: UUID | None = None,
    ) -> DailyRun | None:
        statement = select(DailyRunRow).where(
            DailyRunRow.topic_id == topic_id,
            DailyRunRow.logical_date == logical_date,
            DailyRunRow.operation == RunOperation.PRODUCT_PUBLICATION.value,
            DailyRunRow.pipeline_execution_id == pipeline_execution_id,
        )
        try:
            with self._sessions() as session:
                row = session.scalars(statement).one_or_none()
                return None if row is None else _run_from_row(row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL product run query is unavailable"
            ) from error

    def get_product_run(
        self,
        *,
        logical_date: date | None,
        topic_slug: str | None,
        pipeline_execution_id: UUID | None = None,
    ) -> ProductRunDetail | None:
        statement = select(DailyRunRow).where(
            DailyRunRow.operation == RunOperation.PRODUCT_PUBLICATION.value
        )
        if pipeline_execution_id is None:
            statement = statement.where(
                DailyRunRow.pipeline_execution_mode != PipelineExecutionMode.SMOKE.value
            )
        else:
            statement = statement.where(DailyRunRow.pipeline_execution_id == pipeline_execution_id)
        if topic_slug is not None:
            statement = statement.join(TopicRow).where(TopicRow.slug == topic_slug)
        if logical_date is not None:
            statement = statement.where(DailyRunRow.logical_date == logical_date)
        statement = statement.order_by(
            DailyRunRow.logical_date.desc(), DailyRunRow.started_at.desc(), DailyRunRow.id
        ).limit(1)
        try:
            with self._sessions() as session:
                row = session.scalars(statement).one_or_none()
                if row is None:
                    return None
                detail = _run_detail_from_session(session, row)
                report_detail = _report_detail_for_run(session, row.id)
                return ProductRunDetail(
                    run=detail.run,
                    items=detail.items,
                    report=report_detail,
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL product run query is unavailable"
            ) from error
        except DomainInvariantError as error:
            raise RepositoryIntegrityError(
                "stored product run violates domain invariants"
            ) from error

    def get_publication_artifact_summary(
        self,
        *,
        publication_run_id: UUID,
        pipeline_execution_id: UUID,
    ) -> PublicationArtifactSummary | None:
        """Load only artifacts owned by one exact full-pipeline publication."""

        try:
            with self._sessions() as session:
                run_row = session.scalars(
                    select(DailyRunRow).where(
                        DailyRunRow.id == publication_run_id,
                        DailyRunRow.operation == RunOperation.PRODUCT_PUBLICATION.value,
                        DailyRunRow.pipeline_execution_id == pipeline_execution_id,
                    )
                ).one_or_none()
                if run_row is None:
                    return None

                mention_rows = tuple(
                    session.scalars(
                        select(GraphEntityMentionRow).where(
                            GraphEntityMentionRow.publication_run_id == publication_run_id
                        )
                    )
                )
                edge_rows = tuple(
                    session.scalars(
                        select(GraphEdgeRow).where(
                            GraphEdgeRow.publication_run_id == publication_run_id
                        )
                    )
                )
                trend_rows = tuple(
                    session.scalars(
                        select(TrendSnapshotRow)
                        .where(TrendSnapshotRow.publication_run_id == publication_run_id)
                        .order_by(TrendSnapshotRow.window_size_days, TrendSnapshotRow.id)
                    )
                )
                lineage_rows = tuple(
                    session.scalars(
                        select(LineageSnapshotRow)
                        .where(LineageSnapshotRow.publication_run_id == publication_run_id)
                        .order_by(LineageSnapshotRow.root_paper_id, LineageSnapshotRow.id)
                    )
                )
                artifacts = (*mention_rows, *edge_rows, *trend_rows, *lineage_rows)
                if any(item.pipeline_execution_id != pipeline_execution_id for item in artifacts):
                    raise RepositoryIntegrityError(
                        "publication artifacts cross pipeline-execution boundaries"
                    )
                return PublicationArtifactSummary(
                    publication_run_id=publication_run_id,
                    pipeline_execution_id=pipeline_execution_id,
                    graph_entity_count=len({item.entity_id for item in mention_rows}),
                    graph_edge_count=len({item.id for item in edge_rows}),
                    inferred_graph_edge_count=sum(
                        item.provenance == RelationProvenance.LLM_INFERRED.value
                        for item in edge_rows
                    ),
                    trend_snapshots=tuple(
                        PublicationTrendArtifact(
                            snapshot_id=item.id,
                            window=TrendWindow(item.window),
                        )
                        for item in trend_rows
                    ),
                    lineage_snapshot_ids=tuple(item.id for item in lineage_rows),
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL publication artifact query is unavailable"
            ) from error
        except (DomainInvariantError, ValueError) as error:
            raise RepositoryIntegrityError(
                "stored publication artifacts violate domain invariants"
            ) from error

    def get_graph(
        self,
        *,
        topic_slug: str | None,
        as_of: date | None,
        paper_id: UUID | None,
        entity_id: UUID | None = None,
        entity_type: GraphEntityType | None,
        relation_type: GraphRelationType | None,
        provenance: RelationProvenance | None,
        verification_status: VerificationStatus | None,
        max_nodes: int,
        max_edges: int,
    ) -> GraphView | None:
        if max_nodes < 1 or max_edges < 1:
            raise RepositoryIntegrityError("graph limits must be positive")
        try:
            with self._sessions() as session:
                topic_id = _resolve_graph_topic(session, topic_slug, as_of=as_of)
                if topic_id is None:
                    return None
                published_runs = _published_product_run_ids(topic_id=topic_id, as_of=as_of)
                visible_entity_ids = (
                    select(GraphEntityMentionRow.entity_id)
                    .where(
                        GraphEntityMentionRow.publication_run_id.in_(published_runs),
                    )
                    .distinct()
                )
                seed_statement = select(GraphEntityRow.id.label("entity_id")).where(
                    GraphEntityRow.topic_id == topic_id,
                    GraphEntityRow.id.in_(visible_entity_ids),
                )
                if entity_id is not None:
                    seed_statement = seed_statement.where(GraphEntityRow.id == entity_id)
                if entity_type is not None:
                    seed_statement = seed_statement.where(
                        GraphEntityRow.entity_type == entity_type.value
                    )
                if paper_id is not None:
                    paper_mention_entity_ids = select(GraphEntityMentionRow.entity_id).where(
                        GraphEntityMentionRow.publication_run_id.in_(published_runs),
                        GraphEntityMentionRow.paper_id == paper_id,
                    )
                    seed_statement = seed_statement.where(
                        or_(
                            GraphEntityRow.paper_id == paper_id,
                            GraphEntityRow.id.in_(paper_mention_entity_ids),
                        )
                    )
                seed_statement = seed_statement.distinct()

                edge_statement = select(
                    GraphEdgeRow.id,
                    GraphEdgeRow.source_entity_id,
                    GraphEdgeRow.target_entity_id,
                ).where(
                    GraphEdgeRow.topic_id == topic_id,
                    GraphEdgeRow.publication_run_id.in_(published_runs),
                    GraphEdgeRow.source_entity_id.in_(visible_entity_ids),
                    GraphEdgeRow.target_entity_id.in_(visible_entity_ids),
                )
                if paper_id is not None:
                    edge_statement = edge_statement.where(
                        or_(
                            GraphEdgeRow.source_paper_id == paper_id,
                            GraphEdgeRow.target_paper_id == paper_id,
                        )
                    )
                if relation_type is not None:
                    edge_statement = edge_statement.where(
                        GraphEdgeRow.relation_type == relation_type.value
                    )
                if provenance is not None:
                    edge_statement = edge_statement.where(
                        GraphEdgeRow.provenance == provenance.value
                    )
                if verification_status is not None:
                    edge_statement = edge_statement.where(
                        GraphEdgeRow.verification_status == verification_status.value
                    )
                has_edge_filter = any(
                    value is not None for value in (relation_type, provenance, verification_status)
                )
                if entity_id is not None or (entity_type is not None and has_edge_filter):
                    edge_statement = edge_statement.where(
                        or_(
                            GraphEdgeRow.source_entity_id.in_(seed_statement),
                            GraphEdgeRow.target_entity_id.in_(seed_statement),
                        )
                    )
                filtered_edges = edge_statement.cte("filtered_graph_edges")
                endpoint_selects = (
                    select(filtered_edges.c.source_entity_id.label("entity_id")),
                    select(filtered_edges.c.target_entity_id.label("entity_id")),
                )
                candidate_selects: tuple[Any, ...]
                if has_edge_filter:
                    candidate_selects = endpoint_selects
                    if entity_id is not None:
                        candidate_selects = (seed_statement, *candidate_selects)
                elif paper_id is not None or entity_id is not None:
                    candidate_selects = (seed_statement, *endpoint_selects)
                else:
                    candidate_selects = (seed_statement,)
                candidate_entities = (
                    candidate_selects[0].cte("candidate_graph_entities")
                    if len(candidate_selects) == 1
                    else union(*candidate_selects).cte("candidate_graph_entities")
                )
                candidate_entity_ids = select(candidate_entities.c.entity_id)
                total_nodes = int(
                    session.scalar(select(func.count()).select_from(candidate_entities)) or 0
                )
                if total_nodes == 0:
                    return None
                qualifying_edge_ids = (
                    select(filtered_edges.c.id)
                    .where(
                        filtered_edges.c.source_entity_id.in_(candidate_entity_ids),
                        filtered_edges.c.target_entity_id.in_(candidate_entity_ids),
                    )
                    .cte("qualifying_graph_edges")
                )
                total_edges = int(
                    session.scalar(select(func.count()).select_from(qualifying_edge_ids)) or 0
                )
                entity_order: list[Any] = []
                if entity_id is not None:
                    entity_order.append(case((GraphEntityRow.id == entity_id, 0), else_=1))
                latest_visible_label = (
                    select(GraphEntityMentionRow.observed_label)
                    .join(
                        DailyRunRow,
                        DailyRunRow.id == GraphEntityMentionRow.publication_run_id,
                    )
                    .where(
                        GraphEntityMentionRow.entity_id == GraphEntityRow.id,
                        GraphEntityMentionRow.publication_run_id.in_(published_runs),
                    )
                    .order_by(
                        DailyRunRow.logical_date.desc(),
                        GraphEntityMentionRow.generated_at.desc(),
                        GraphEntityMentionRow.id,
                    )
                    .limit(1)
                    .correlate(GraphEntityRow)
                    .scalar_subquery()
                )
                entity_order.extend(
                    (
                        GraphEntityRow.entity_type,
                        func.lower(latest_visible_label),
                        GraphEntityRow.id,
                    )
                )
                selected_entity_rows = tuple(
                    session.scalars(
                        select(GraphEntityRow)
                        .join(
                            candidate_entities,
                            candidate_entities.c.entity_id == GraphEntityRow.id,
                        )
                        .order_by(*entity_order)
                        .limit(max_nodes)
                    )
                )
                selected_ids = {row.id for row in selected_entity_rows}
                mention_counts = {
                    row_entity_id: int(row_count)
                    for row_entity_id, row_count in session.execute(
                        select(
                            GraphEntityMentionRow.entity_id,
                            func.count(GraphEntityMentionRow.id),
                        )
                        .where(
                            GraphEntityMentionRow.publication_run_id.in_(published_runs),
                            GraphEntityMentionRow.entity_id.in_(selected_ids),
                        )
                        .group_by(GraphEntityMentionRow.entity_id)
                    )
                }
                total_mentions = sum(mention_counts.values())
                ranked_mentions = (
                    select(
                        GraphEntityMentionRow.id.label("mention_id"),
                        DailyRunRow.logical_date.label("activity_date"),
                        func.row_number()
                        .over(
                            partition_by=GraphEntityMentionRow.entity_id,
                            order_by=(
                                DailyRunRow.logical_date.desc(),
                                GraphEntityMentionRow.generated_at.desc(),
                                GraphEntityMentionRow.id,
                            ),
                        )
                        .label("mention_rank"),
                    )
                    .join(
                        DailyRunRow,
                        DailyRunRow.id == GraphEntityMentionRow.publication_run_id,
                    )
                    .where(
                        GraphEntityMentionRow.publication_run_id.in_(published_runs),
                        GraphEntityMentionRow.entity_id.in_(selected_ids),
                    )
                    .cte("ranked_graph_mentions")
                )
                mention_results = tuple(
                    session.execute(
                        select(GraphEntityMentionRow, ranked_mentions.c.activity_date)
                        .join(
                            ranked_mentions,
                            ranked_mentions.c.mention_id == GraphEntityMentionRow.id,
                        )
                        .where(
                            ranked_mentions.c.mention_rank <= _MAX_GRAPH_MENTIONS_PER_NODE,
                        )
                        .order_by(
                            ranked_mentions.c.activity_date.desc(),
                            GraphEntityMentionRow.generated_at.desc(),
                            GraphEntityMentionRow.id,
                        )
                    )
                )
                selected_mentions = tuple(row for row, _logical_date in mention_results)
                mention_activity_dates = {
                    row.id: logical_date for row, logical_date in mention_results
                }
                mentions_by_entity_row: dict[UUID, list[GraphEntityMentionRow]] = {}
                for row in selected_mentions:
                    mentions_by_entity_row.setdefault(row.entity_id, []).append(row)
                projected_entity_by_id = {
                    row.id: _entity_from_row_with_mentions(
                        row,
                        tuple(mentions_by_entity_row.get(row.id, ())),
                        mention_activity_dates,
                    )
                    for row in selected_entity_rows
                }
                selected_edge_rows = tuple(
                    session.scalars(
                        select(GraphEdgeRow)
                        .join(
                            qualifying_edge_ids,
                            qualifying_edge_ids.c.id == GraphEdgeRow.id,
                        )
                        .where(
                            GraphEdgeRow.source_entity_id.in_(selected_ids),
                            GraphEdgeRow.target_entity_id.in_(selected_ids),
                        )
                        .order_by(
                            GraphEdgeRow.relation_type,
                            GraphEdgeRow.generated_at.desc(),
                            GraphEdgeRow.id,
                        )
                        .limit(max_edges)
                    )
                )
                mention_evidence = _mention_evidence_map(
                    session, tuple(row.id for row in selected_mentions)
                )
                edge_evidence = _edge_evidence_map(
                    session, tuple(row.id for row in selected_edge_rows)
                )
                edge_evidence_details = _edge_evidence_detail_map(
                    session, tuple(row.id for row in selected_edge_rows)
                )
                mentions_by_entity: dict[UUID, list[GraphEntityMention]] = {}
                for row in sorted(
                    selected_mentions,
                    key=lambda item: (
                        mention_activity_dates[item.id],
                        item.generated_at,
                        str(item.id),
                    ),
                    reverse=True,
                ):
                    mentions_by_entity.setdefault(row.entity_id, []).append(
                        _mention_from_row(row, mention_evidence.get(row.id, ()))
                    )
                nodes = tuple(
                    GraphNodeDetail(
                        entity=projected_entity_by_id[row.id],
                        mentions=tuple(mentions_by_entity[row.id]),
                        total_mentions=mention_counts[row.id],
                    )
                    for row in selected_entity_rows
                    if row.id in mentions_by_entity
                )
                node_ids = {node.entity.id for node in nodes}
                edges = tuple(
                    GraphEdgeDetail(
                        edge=_edge_from_row(row, edge_evidence.get(row.id, ())),
                        evidence=edge_evidence_details.get(row.id, ()),
                    )
                    for row in selected_edge_rows
                    if row.source_entity_id in node_ids and row.target_entity_id in node_ids
                )
                return GraphView(
                    topic_id=topic_id,
                    as_of=as_of,
                    nodes=nodes,
                    edges=edges,
                    total_nodes=total_nodes,
                    total_edges=total_edges,
                    total_mentions=total_mentions,
                    truncated=(
                        total_nodes > len(nodes)
                        or total_edges > len(edges)
                        or total_mentions > len(selected_mentions)
                    ),
                )
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL graph query is unavailable") from error
        except DomainInvariantError as error:
            raise RepositoryIntegrityError("stored graph violates domain invariants") from error

    def list_trends(
        self,
        *,
        topic_slug: str | None,
        as_of: date | None,
        windows: tuple[TrendWindow, ...],
        entity_type: GraphEntityType | None = None,
        max_entities: int = 50,
    ) -> tuple[TrendDetail, ...]:
        if len(set(windows)) != len(windows):
            raise RepositoryIntegrityError("trend windows must be unique")
        if not 1 <= max_entities <= 200:
            raise RepositoryIntegrityError("trend entity limit must be between 1 and 200")
        try:
            with self._sessions() as session:
                topic_id = _resolve_trend_topic(session, topic_slug)
                if topic_id is None:
                    return ()
                effective_date = as_of
                if effective_date is None:
                    effective_date = session.scalar(
                        select(func.max(TrendSnapshotRow.as_of_date)).where(
                            TrendSnapshotRow.topic_id == topic_id,
                            TrendSnapshotRow.publication_run_id.in_(_published_product_run_ids()),
                        )
                    )
                if effective_date is None:
                    return ()
                rows = tuple(
                    session.scalars(
                        select(TrendSnapshotRow)
                        .where(
                            TrendSnapshotRow.topic_id == topic_id,
                            TrendSnapshotRow.as_of_date == effective_date,
                            TrendSnapshotRow.window.in_(tuple(item.value for item in windows)),
                            TrendSnapshotRow.publication_run_id.in_(_published_product_run_ids()),
                        )
                        .order_by(
                            TrendSnapshotRow.window_size_days, TrendSnapshotRow.generated_at.desc()
                        )
                    )
                )
                selected: dict[TrendWindow, TrendSnapshotRow] = {}
                for row in rows:
                    selected.setdefault(TrendWindow(row.window), row)
                return tuple(
                    _trend_detail_from_session(
                        session,
                        selected[window],
                        entity_type=entity_type,
                        max_entities=max_entities,
                    )
                    for window in windows
                    if window in selected
                )
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL trend query is unavailable") from error
        except DomainInvariantError as error:
            raise RepositoryIntegrityError("stored trend violates domain invariants") from error

    def get_lineage(
        self,
        entity_or_paper_id: UUID,
        *,
        topic_slug: str | None,
        max_depth: int,
        max_nodes: int,
        max_edges: int,
    ) -> LineageDetail | None:
        if max_depth < 1 or max_nodes < 1 or max_edges < 1:
            raise RepositoryIntegrityError("lineage limits must be positive")
        entity_paper = select(GraphEntityRow.paper_id).where(
            GraphEntityRow.id == entity_or_paper_id,
            GraphEntityRow.entity_type == GraphEntityType.PAPER.value,
        )
        statement = select(LineageSnapshotRow).where(
            LineageSnapshotRow.publication_run_id.in_(_published_product_run_ids()),
            or_(
                LineageSnapshotRow.root_paper_id == entity_or_paper_id,
                LineageSnapshotRow.root_paper_id.in_(entity_paper),
            ),
        )
        if topic_slug is not None:
            statement = statement.join(TopicRow).where(TopicRow.slug == topic_slug)
        statement = statement.order_by(
            LineageSnapshotRow.as_of_date.desc(),
            LineageSnapshotRow.generated_at.desc(),
            LineageSnapshotRow.id,
        ).limit(1)
        try:
            with self._sessions() as session:
                row = session.scalars(statement).one_or_none()
                if row is None:
                    return None
                snapshot = _lineage_from_session(session, row)
                bounded = _bounded_lineage(
                    snapshot,
                    max_depth=max_depth,
                    max_nodes=max_nodes,
                    max_edges=max_edges,
                )
                evidence_by_edge = _edge_evidence_detail_map(
                    session, tuple(item.id for item in bounded.edges)
                )
                return LineageDetail(
                    snapshot=bounded,
                    evidence=tuple(
                        reference
                        for edge in bounded.edges
                        for reference in evidence_by_edge.get(edge.id, ())
                    ),
                )
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL lineage query is unavailable") from error
        except DomainInvariantError as error:
            raise RepositoryIntegrityError("stored lineage violates domain invariants") from error

    def list_reports(
        self,
        *,
        report_type: ReportType,
        topic_slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[ReportDetail, ...], int]:
        base = select(ReportRow).where(ReportRow.report_type == report_type.value)
        count = select(func.count(ReportRow.id)).where(ReportRow.report_type == report_type.value)
        canonical = or_(
            ReportRow.run_id.is_(None),
            ReportRow.run_id.in_(_published_product_run_ids()),
        )
        base = base.where(canonical)
        count = count.where(canonical)
        if topic_slug is not None:
            base = base.join(TopicRow).where(TopicRow.slug == topic_slug)
            count = count.join(TopicRow).where(TopicRow.slug == topic_slug)
        statement = (
            base.order_by(ReportRow.period_end.desc(), ReportRow.generated_at.desc(), ReportRow.id)
            .limit(limit)
            .offset(offset)
        )
        try:
            with self._sessions() as session:
                rows = tuple(session.scalars(statement))
                total = session.scalar(count) or 0
                return tuple(_report_detail_from_session(session, row) for row in rows), total
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL report query is unavailable") from error
        except DomainInvariantError as error:
            raise RepositoryIntegrityError("stored report violates domain invariants") from error

    def get_report(
        self,
        *,
        report_type: ReportType,
        period_start: date,
        period_end: date,
        topic_slug: str | None,
    ) -> ReportDetail | None:
        statement = select(ReportRow).where(
            ReportRow.report_type == report_type.value,
            ReportRow.period_start == period_start,
            ReportRow.period_end == period_end,
            or_(
                ReportRow.run_id.is_(None),
                ReportRow.run_id.in_(_published_product_run_ids()),
            ),
        )
        if topic_slug is not None:
            statement = statement.join(TopicRow).where(TopicRow.slug == topic_slug)
        statement = statement.order_by(ReportRow.generated_at.desc(), ReportRow.id).limit(1)
        try:
            with self._sessions() as session:
                row = session.scalars(statement).one_or_none()
                return None if row is None else _report_detail_from_session(session, row)
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL report query is unavailable") from error
        except DomainInvariantError as error:
            raise RepositoryIntegrityError("stored report violates domain invariants") from error

    def get_product_publication_input(
        self,
        topic_id: UUID,
        logical_date: date,
        *,
        pipeline_execution_id: UUID | None = None,
    ) -> ProductPublicationInput | None:
        statement = select(DailyRunRow).where(
            DailyRunRow.topic_id == topic_id,
            DailyRunRow.logical_date == logical_date,
            DailyRunRow.operation == RunOperation.STRUCTURED_ANALYSIS.value,
            DailyRunRow.status.in_((RunStatus.COMPLETE.value, RunStatus.PARTIAL.value)),
            DailyRunRow.pipeline_execution_id == pipeline_execution_id,
        )
        try:
            with self._sessions() as session:
                run_row = session.scalars(statement).one_or_none()
                if run_row is None:
                    return None
                source_run = _run_detail_from_session(session, run_row)
                source_version_ids = {item.item.paper_version_id for item in source_run.items}
                source_versions = {
                    row.id: row
                    for row in session.scalars(
                        select(PaperVersionRow).where(PaperVersionRow.id.in_(source_version_ids))
                    )
                }
                if set(source_versions) != source_version_ids:
                    raise RepositoryIntegrityError(
                        "product publication source references missing paper metadata"
                    )
                cards = tuple(
                    PublicationPaperCardInput(
                        paper_id=item.item.paper_id,
                        paper_version_id=item.item.paper_version_id,
                        canonical_arxiv_id=item.canonical_arxiv_id,
                        title=item.paper_title,
                        abstract=source_versions[item.item.paper_version_id].abstract or None,
                        source_url=source_versions[item.item.paper_version_id].source_url,
                    )
                    for item in source_run.items
                )
                search_sessions_by_version: dict[UUID, SearchSessionRow] = {}
                for search_row in session.scalars(
                    select(SearchSessionRow)
                    .where(
                        SearchSessionRow.topic_id == topic_id,
                        SearchSessionRow.source_paper_version_id.in_(source_version_ids),
                        SearchSessionRow.pipeline_execution_id == pipeline_execution_id,
                    )
                    .order_by(
                        SearchSessionRow.started_at.desc(),
                        SearchSessionRow.id.desc(),
                    )
                ):
                    search_sessions_by_version.setdefault(
                        search_row.source_paper_version_id,
                        search_row,
                    )
                completed_version_ids = {
                    item.item.paper_version_id
                    for item in source_run.items
                    if item.item.status is RunItemStatus.COMPLETED
                }
                product_run_row = session.scalars(
                    select(DailyRunRow).where(
                        DailyRunRow.topic_id == topic_id,
                        DailyRunRow.logical_date == logical_date,
                        DailyRunRow.operation == RunOperation.PRODUCT_PUBLICATION.value,
                        DailyRunRow.pipeline_execution_id == pipeline_execution_id,
                    )
                ).one_or_none()
                snapshot_by_version: dict[UUID, ProductRunPaperInputRow] | None = None
                snapshot_comparison_ids: dict[UUID, set[UUID]] = {}
                input_failures_by_version: dict[UUID, ProductFailureInput] = {}
                if product_run_row is not None and product_run_row.status in (
                    RunStatus.COMPLETE.value,
                    RunStatus.PARTIAL.value,
                ):
                    snapshot_by_version = {
                        row.paper_version_id: row
                        for row in session.scalars(
                            select(ProductRunPaperInputRow).where(
                                ProductRunPaperInputRow.run_id == product_run_row.id
                            )
                        )
                    }
                    for row in session.scalars(
                        select(RunItemRow).where(RunItemRow.run_id == product_run_row.id)
                    ):
                        if (
                            row.error_code == "ANALYSIS_IDENTITY_MISSING"
                            and row.failed_stage == PaperStage.ANALYZED.value
                        ):
                            input_failures_by_version[row.paper_version_id] = (
                                _product_failure_from_item_row(row)
                            )
                    if set(snapshot_by_version) != completed_version_ids - set(
                        input_failures_by_version
                    ):
                        raise RepositoryIntegrityError(
                            "product run input snapshot does not match its source items"
                        )
                    for row in session.scalars(
                        select(ProductRunComparisonInputRow).where(
                            ProductRunComparisonInputRow.run_id == product_run_row.id
                        )
                    ):
                        snapshot_comparison_ids.setdefault(row.paper_version_id, set()).add(
                            row.comparison_id
                        )
                papers: list[ProductPaperInput] = []
                for item_detail in source_run.items:
                    item = item_detail.item
                    if item.status is not RunItemStatus.COMPLETED:
                        continue
                    if item.paper_version_id in input_failures_by_version:
                        continue
                    snapshot = (
                        None
                        if snapshot_by_version is None
                        else snapshot_by_version.get(item.paper_version_id)
                    )
                    if snapshot_by_version is None:
                        analysis_row = _select_publication_analysis(
                            session,
                            run_row=run_row,
                            paper_version_id=item.paper_version_id,
                        )
                    elif snapshot is None:
                        raise RepositoryIntegrityError(
                            "product run analysis snapshot is missing a source item"
                        )
                    else:
                        analysis_row = session.get(PaperAnalysisRow, snapshot.analysis_id)
                        if (
                            analysis_row is None
                            or analysis_row.paper_id != snapshot.paper_id
                            or analysis_row.paper_version_id != snapshot.paper_version_id
                            or analysis_row.analysis_scope != snapshot.analysis_scope
                        ):
                            raise RepositoryIntegrityError(
                                "product run analysis snapshot has invalid ownership"
                            )
                    if analysis_row is None:
                        input_failures_by_version[item.paper_version_id] = ProductFailureInput(
                            paper_id=item.paper_id,
                            paper_version_id=item.paper_version_id,
                            stage=PaperStage.EVIDENCE_EXTRACTED,
                            failed_stage=PaperStage.ANALYZED,
                            error_code="ANALYSIS_IDENTITY_MISSING",
                            retryable=False,
                            error_detail=(
                                "The completed analysis item has no compatible persisted analysis "
                                "identity for product publication."
                            ),
                        )
                        continue
                    analysis = _analysis_bundle_from_session(session, analysis_row)
                    comparison_statement = select(ComparisonRow).where(
                        ComparisonRow.source_paper_version_id == item.paper_version_id,
                        ComparisonRow.source_analysis_id == analysis_row.id,
                    )
                    expected_comparison_ids: set[UUID] | None = None
                    if snapshot is not None:
                        expected_comparison_ids = snapshot_comparison_ids.get(
                            item.paper_version_id, set()
                        )
                        comparison_statement = comparison_statement.where(
                            ComparisonRow.id.in_(expected_comparison_ids)
                        )
                    comparison_rows = tuple(
                        session.scalars(
                            comparison_statement.order_by(
                                ComparisonRow.generated_at.desc(), ComparisonRow.id
                            )
                        )
                    )
                    if (
                        expected_comparison_ids is not None
                        and {row.id for row in comparison_rows} != expected_comparison_ids
                    ):
                        raise RepositoryIntegrityError(
                            "product run comparison snapshot has invalid ownership"
                        )
                    target_version_ids = {row.target_paper_version_id for row in comparison_rows}
                    target_titles = {
                        row.id: row.title
                        for row in session.scalars(
                            select(PaperVersionRow).where(
                                PaperVersionRow.id.in_(target_version_ids)
                            )
                        )
                    }
                    comparisons = tuple(
                        ComparisonGraphInput(
                            bundle=comparison_bundle_from_session(session, row),
                            source_paper_title=item_detail.paper_title,
                            target_paper_title=target_titles[row.target_paper_version_id],
                        )
                        for row in comparison_rows
                    )
                    required_evidence_ids = (
                        {evidence.id for evidence in analysis.evidence}
                        | {
                            evidence_id
                            for comparison in comparisons
                            for dimension in comparison.bundle.comparison.dimensions
                            for evidence_id in (
                                dimension.source_evidence_ids + dimension.target_evidence_ids
                            )
                        }
                        | {
                            evidence_id
                            for comparison in comparisons
                            for relation in comparison.bundle.relations
                            for evidence_id in relation.evidence_ids
                        }
                    )
                    evidence_rows = tuple(
                        session.scalars(
                            select(EvidenceRow)
                            .where(EvidenceRow.id.in_(required_evidence_ids))
                            .order_by(EvidenceRow.id)
                        )
                    )
                    if {row.id for row in evidence_rows} != required_evidence_ids:
                        raise RepositoryIntegrityError(
                            "comparison references missing persisted evidence"
                        )
                    session_ids = {row.search_session_id for row in comparison_rows}
                    retrieved_count = (
                        0
                        if not session_ids
                        else int(
                            session.scalar(
                                select(func.count(SearchCandidateRow.id)).where(
                                    SearchCandidateRow.session_id.in_(session_ids)
                                )
                            )
                            or 0
                        )
                    )
                    search_row = search_sessions_by_version.get(item.paper_version_id)
                    related_work_available = (
                        search_row is not None
                        and search_row.status == SearchSessionStatus.COMPLETE.value
                    )
                    papers.append(
                        ProductPaperInput(
                            paper_id=item.paper_id,
                            paper_version_id=item.paper_version_id,
                            paper_title=item_detail.paper_title,
                            analysis=analysis,
                            comparisons=comparisons,
                            evidence=tuple(_report_evidence_from_row(row) for row in evidence_rows),
                            retrieved_candidate_count=max(retrieved_count, len(comparisons)),
                            related_work_available=related_work_available,
                            related_work_reason=(
                                None
                                if related_work_available
                                else (
                                    "NO_RELATED_WORK_SESSION"
                                    if search_row is None
                                    else search_row.error_code or "RELATED_WORK_UNAVAILABLE"
                                )
                            ),
                        )
                    )
                return ProductPublicationInput(
                    source_run=source_run,
                    papers=tuple(papers),
                    cards=cards,
                    input_failures=tuple(
                        input_failures_by_version[item.item.paper_version_id]
                        for item in source_run.items
                        if item.item.paper_version_id in input_failures_by_version
                    ),
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL product publication input query is unavailable"
            ) from error
        except DomainInvariantError as error:
            raise RepositoryIntegrityError(
                "stored product publication input violates domain invariants"
            ) from error

    def start_product_run(
        self,
        *,
        topic_id: UUID,
        logical_date: date,
        source: ProductPublicationInput,
        upstream_failures: tuple[ProductFailureInput, ...] = (),
        started_at: datetime,
        pipeline_execution_id: UUID | None = None,
    ) -> DailyRun:
        run_id = uuid4()
        failures_by_version = _product_failures_by_version(
            (*source.input_failures, *upstream_failures)
        )
        try:
            with self._sessions.begin() as session:
                source_row = session.scalars(
                    select(DailyRunRow)
                    .where(DailyRunRow.id == source.source_run.run.id)
                    .with_for_update()
                ).one_or_none()
                if (
                    source_row is None
                    or source_row.topic_id != topic_id
                    or source_row.logical_date != logical_date
                    or source_row.operation != RunOperation.STRUCTURED_ANALYSIS.value
                    or source_row.status not in (RunStatus.COMPLETE.value, RunStatus.PARTIAL.value)
                    or source_row.pipeline_execution_id != pipeline_execution_id
                ):
                    raise RepositoryError(
                        "product publication source run is missing or not publishable"
                    )
                if source.source_run.run != _run_from_row(source_row):
                    raise RepositoryError("product publication source run changed before start")
                source_item_rows = tuple(
                    session.scalars(
                        select(RunItemRow)
                        .where(RunItemRow.run_id == source_row.id)
                        .order_by(RunItemRow.created_at, RunItemRow.id)
                    )
                )
                if {row.paper_version_id for row in source_item_rows} != {
                    item.item.paper_version_id for item in source.source_run.items
                }:
                    raise RepositoryError("product publication source items changed before start")
                source_versions = {row.paper_version_id for row in source_item_rows}
                failed_count = sum(
                    row.status == RunItemStatus.FAILED.value
                    or row.paper_version_id in failures_by_version
                    for row in source_item_rows
                ) + len(set(failures_by_version) - source_versions)
                run_row = DailyRunRow(
                    id=run_id,
                    topic_id=topic_id,
                    logical_date=logical_date,
                    operation=RunOperation.PRODUCT_PUBLICATION.value,
                    source_run_id=source_row.id,
                    pipeline_execution_id=pipeline_execution_id,
                    pipeline_execution_mode=source_row.pipeline_execution_mode,
                    pipeline_selection_limit=source_row.pipeline_selection_limit,
                    analysis_scope=None,
                    status=RunStatus.RUNNING.value,
                    started_at=started_at,
                    completed_at=None,
                    cursor_from=None,
                    cursor_to=None,
                    discovered_count=0,
                    normalized_count=0,
                    selected_count=len(source_versions | set(failures_by_version)),
                    completed_count=0,
                    failed_count=failed_count,
                    error_code=None,
                    error_detail=None,
                    schema_version=1,
                    created_at=started_at,
                )
                session.add(run_row)
                session.flush()
                for source_item in source_item_rows:
                    completed = source_item.status == RunItemStatus.COMPLETED.value
                    upstream_failure = failures_by_version.get(source_item.paper_version_id)
                    if not completed and upstream_failure is not None:
                        _require_matching_product_failure(source_item, upstream_failure)
                    failed = not completed or upstream_failure is not None
                    session.add(
                        RunItemRow(
                            id=uuid5(run_id, f"product:{source_item.paper_version_id}"),
                            run_id=run_id,
                            paper_id=source_item.paper_id,
                            paper_version_id=source_item.paper_version_id,
                            stage=(
                                upstream_failure.stage.value
                                if upstream_failure is not None
                                else (
                                    PaperStage.EVIDENCE_EXTRACTED.value
                                    if completed
                                    else source_item.stage
                                )
                            ),
                            status=(
                                RunItemStatus.FAILED.value
                                if failed
                                else RunItemStatus.IN_PROGRESS.value
                            ),
                            failed_stage=(
                                upstream_failure.failed_stage.value
                                if upstream_failure is not None
                                else source_item.failed_stage
                            ),
                            error_code=(
                                upstream_failure.error_code
                                if upstream_failure is not None
                                else source_item.error_code
                            ),
                            retryable=(
                                upstream_failure.retryable
                                if upstream_failure is not None
                                else source_item.retryable
                            ),
                            error_detail=(
                                upstream_failure.error_detail
                                if upstream_failure is not None
                                else source_item.error_detail
                            ),
                            schema_version=1,
                            created_at=started_at,
                            updated_at=started_at,
                        )
                    )
                for failure in failures_by_version.values():
                    if failure.paper_version_id in source_versions:
                        continue
                    session.add(
                        RunItemRow(
                            id=uuid5(run_id, f"product:{failure.paper_version_id}"),
                            run_id=run_id,
                            paper_id=failure.paper_id,
                            paper_version_id=failure.paper_version_id,
                            stage=failure.stage.value,
                            status=RunItemStatus.FAILED.value,
                            failed_stage=failure.failed_stage.value,
                            error_code=failure.error_code,
                            retryable=failure.retryable,
                            error_detail=failure.error_detail,
                            schema_version=1,
                            created_at=started_at,
                            updated_at=started_at,
                        )
                    )
                session.flush()
                source_papers = {paper.paper_version_id: paper for paper in source.papers}
                completed_source_versions = {
                    row.paper_version_id
                    for row in source_item_rows
                    if row.status == RunItemStatus.COMPLETED.value
                }
                if set(source_papers) != completed_source_versions - {
                    failure.paper_version_id for failure in source.input_failures
                }:
                    raise RepositoryError(
                        "product publication payload changed before input snapshot"
                    )
                for paper in source_papers.values():
                    analysis = paper.analysis.analysis
                    if (
                        analysis.paper_id != paper.paper_id
                        or analysis.paper_version_id != paper.paper_version_id
                    ):
                        raise RepositoryError("product analysis snapshot has the wrong owner")
                    session.add(
                        ProductRunPaperInputRow(
                            run_id=run_id,
                            topic_id=topic_id,
                            source_run_id=source_row.id,
                            paper_id=paper.paper_id,
                            paper_version_id=paper.paper_version_id,
                            analysis_id=analysis.id,
                            analysis_scope=analysis.analysis_scope.value,
                            schema_version=1,
                            created_at=started_at,
                        )
                    )
                session.flush()
                for paper in source_papers.values():
                    analysis_id = paper.analysis.analysis.id
                    for comparison in paper.comparisons:
                        comparison_row = comparison.bundle.comparison
                        if (
                            comparison_row.source_paper_id != paper.paper_id
                            or comparison_row.source_paper_version_id != paper.paper_version_id
                            or comparison_row.source_analysis_id != analysis_id
                        ):
                            raise RepositoryError(
                                "product comparison snapshot has the wrong source owner"
                            )
                        session.add(
                            ProductRunComparisonInputRow(
                                run_id=run_id,
                                topic_id=topic_id,
                                source_run_id=source_row.id,
                                paper_id=paper.paper_id,
                                paper_version_id=paper.paper_version_id,
                                analysis_id=analysis_id,
                                comparison_id=comparison_row.id,
                                schema_version=1,
                                created_at=started_at,
                            )
                        )
                session.flush()
                return _run_from_row(run_row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL product run creation is unavailable"
            ) from error
        except (IntegrityError, DataError) as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected product run ownership constraints"
            ) from error

    def restart_product_run(
        self,
        run_id: UUID,
        *,
        source: ProductPublicationInput,
        upstream_failures: tuple[ProductFailureInput, ...] = (),
        started_at: datetime,
    ) -> DailyRun:
        failures_by_version = _product_failures_by_version(
            (*source.input_failures, *upstream_failures)
        )
        try:
            with self._sessions.begin() as session:
                run_row = session.scalars(
                    select(DailyRunRow).where(DailyRunRow.id == run_id).with_for_update()
                ).one_or_none()
                if (
                    run_row is None
                    or run_row.operation != RunOperation.PRODUCT_PUBLICATION.value
                    or run_row.status != RunStatus.FAILED.value
                    or run_row.source_run_id != source.source_run.run.id
                ):
                    raise RepositoryError("only the matching failed product run may restart")
                if session.scalar(
                    select(func.count(ReportRow.id)).where(ReportRow.run_id == run_id)
                ):
                    raise RepositoryError("a product run with a report cannot restart")
                source_row = session.scalars(
                    select(DailyRunRow)
                    .where(DailyRunRow.id == source.source_run.run.id)
                    .with_for_update()
                ).one_or_none()
                if (
                    source_row is None
                    or source_row.topic_id != run_row.topic_id
                    or source_row.logical_date != run_row.logical_date
                    or source_row.operation != RunOperation.STRUCTURED_ANALYSIS.value
                    or source_row.status not in (RunStatus.COMPLETE.value, RunStatus.PARTIAL.value)
                    or source_row.pipeline_execution_id != run_row.pipeline_execution_id
                ):
                    raise RepositoryError("product publication source ownership is invalid")
                source_items = {
                    row.paper_version_id: row
                    for row in session.scalars(
                        select(RunItemRow).where(RunItemRow.run_id == source_row.id)
                    )
                }
                product_items = {
                    row.paper_version_id: row
                    for row in session.scalars(
                        select(RunItemRow).where(RunItemRow.run_id == run_id).with_for_update()
                    )
                }
                paper_inputs = {
                    row.paper_version_id: row
                    for row in session.scalars(
                        select(ProductRunPaperInputRow)
                        .where(ProductRunPaperInputRow.run_id == run_id)
                        .with_for_update()
                    )
                }
                comparison_inputs = {
                    (row.paper_version_id, row.comparison_id): row
                    for row in session.scalars(
                        select(ProductRunComparisonInputRow)
                        .where(ProductRunComparisonInputRow.run_id == run_id)
                        .with_for_update()
                    )
                }
                _delete_product_artifacts(session, run_row)

                source_papers = {paper.paper_version_id: paper for paper in source.papers}
                completed_source_versions = {
                    version_id
                    for version_id, item in source_items.items()
                    if item.status == RunItemStatus.COMPLETED.value
                }
                projected_source_versions = completed_source_versions - {
                    failure.paper_version_id for failure in source.input_failures
                }
                if set(source_papers) != projected_source_versions:
                    raise RepositoryError(
                        "product input papers do not match completed analysis items"
                    )
                if set(paper_inputs) != projected_source_versions:
                    raise RepositoryError(
                        "product paper input snapshot does not match completed analysis items"
                    )
                desired_comparison_inputs: dict[
                    tuple[UUID, UUID], ProductRunComparisonInputRow
                ] = {}
                for paper in source_papers.values():
                    analysis = paper.analysis.analysis
                    if (
                        analysis.paper_id != paper.paper_id
                        or analysis.paper_version_id != paper.paper_version_id
                    ):
                        raise RepositoryError("product analysis input has the wrong owner")
                    paper_input = paper_inputs[paper.paper_version_id]
                    if (
                        paper_input.topic_id != run_row.topic_id
                        or paper_input.source_run_id != source_row.id
                        or paper_input.paper_id != paper.paper_id
                        or paper_input.analysis_id != analysis.id
                        or paper_input.analysis_scope != analysis.analysis_scope.value
                    ):
                        raise RepositoryError(
                            "product paper input snapshot conflicts with its source analysis"
                        )
                    for comparison in paper.comparisons:
                        comparison_row = comparison.bundle.comparison
                        if (
                            comparison_row.source_paper_id != paper.paper_id
                            or comparison_row.source_paper_version_id != paper.paper_version_id
                            or comparison_row.source_analysis_id != analysis.id
                        ):
                            raise RepositoryError(
                                "product comparison input has the wrong source owner"
                            )
                        desired_comparison_inputs[(paper.paper_version_id, comparison_row.id)] = (
                            ProductRunComparisonInputRow(
                                run_id=run_id,
                                topic_id=run_row.topic_id,
                                source_run_id=source_row.id,
                                paper_id=paper.paper_id,
                                paper_version_id=paper.paper_version_id,
                                analysis_id=analysis.id,
                                comparison_id=comparison_row.id,
                                schema_version=1,
                                created_at=started_at,
                            )
                        )
                for key, row in comparison_inputs.items():
                    if key not in desired_comparison_inputs:
                        session.delete(row)
                session.add_all(
                    row
                    for key, row in desired_comparison_inputs.items()
                    if key not in comparison_inputs
                )

                desired_versions = set(source_items) | set(failures_by_version)
                for version_id, product_item in tuple(product_items.items()):
                    if version_id not in desired_versions:
                        session.delete(product_item)
                        product_items.pop(version_id)
                for version_id in desired_versions:
                    if version_id in product_items:
                        continue
                    source_item = source_items.get(version_id)
                    failure = failures_by_version.get(version_id)
                    if source_item is None and failure is None:
                        continue
                    if source_item is not None:
                        paper_id = source_item.paper_id
                    else:
                        assert failure is not None
                        paper_id = failure.paper_id
                    product_item = RunItemRow(
                        id=uuid5(run_id, f"product:{version_id}"),
                        run_id=run_id,
                        paper_id=paper_id,
                        paper_version_id=version_id,
                        stage=PaperStage.EVIDENCE_EXTRACTED.value,
                        status=RunItemStatus.IN_PROGRESS.value,
                        failed_stage=None,
                        error_code=None,
                        retryable=None,
                        error_detail=None,
                        schema_version=1,
                        created_at=started_at,
                        updated_at=started_at,
                    )
                    session.add(product_item)
                    product_items[version_id] = product_item
                failed_count = 0
                for version_id, product_item in product_items.items():
                    source_item = source_items.get(version_id)
                    upstream_failure = failures_by_version.get(version_id)
                    if (
                        source_item is not None
                        and source_item.status != RunItemStatus.COMPLETED.value
                    ):
                        if upstream_failure is not None:
                            _require_matching_product_failure(source_item, upstream_failure)
                        product_item.stage = source_item.stage
                        product_item.status = RunItemStatus.FAILED.value
                        product_item.failed_stage = source_item.failed_stage
                        product_item.error_code = source_item.error_code
                        product_item.retryable = source_item.retryable
                        product_item.error_detail = source_item.error_detail
                        failed_count += 1
                    elif upstream_failure is not None:
                        product_item.stage = upstream_failure.stage.value
                        product_item.status = RunItemStatus.FAILED.value
                        product_item.failed_stage = upstream_failure.failed_stage.value
                        product_item.error_code = upstream_failure.error_code
                        product_item.retryable = upstream_failure.retryable
                        product_item.error_detail = upstream_failure.error_detail
                        failed_count += 1
                    elif source_item is not None:
                        product_item.stage = PaperStage.EVIDENCE_EXTRACTED.value
                        product_item.status = RunItemStatus.IN_PROGRESS.value
                        product_item.failed_stage = None
                        product_item.error_code = None
                        product_item.retryable = None
                        product_item.error_detail = None
                    else:
                        raise RepositoryError(
                            "product publication upstream failure snapshot changed before restart"
                        )
                    product_item.updated_at = started_at
                run_row.status = RunStatus.RUNNING.value
                run_row.started_at = started_at
                run_row.completed_at = None
                run_row.selected_count = len(product_items)
                run_row.completed_count = 0
                run_row.failed_count = failed_count
                run_row.error_code = None
                run_row.error_detail = None
                session.flush()
                return _run_from_row(run_row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL product-run restart is unavailable"
            ) from error
        except (IntegrityError, DataError) as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected product-run restart constraints"
            ) from error

    def advance_product_item(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        expected_stage: PaperStage,
        next_stage: PaperStage,
        updated_at: datetime,
    ) -> None:
        if (expected_stage, next_stage) not in (
            (PaperStage.EVIDENCE_EXTRACTED, PaperStage.COMPARED),
            (PaperStage.COMPARED, PaperStage.GRAPH_UPDATED),
        ):
            raise RepositoryIntegrityError("unsupported product item transition")
        try:
            with self._sessions.begin() as session:
                item = _locked_product_item(session, run_id, paper_version_id)
                if (
                    item.status == RunItemStatus.IN_PROGRESS.value
                    and item.stage == next_stage.value
                ):
                    return
                if (
                    item.status != RunItemStatus.IN_PROGRESS.value
                    or item.stage != expected_stage.value
                ):
                    raise RepositoryError("product item is not at the expected stage")
                item.stage = next_stage.value
                item.updated_at = updated_at
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL product item transition is unavailable"
            ) from error
        except (IntegrityError, DataError) as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected product item transition constraints"
            ) from error

    def persist_product_graph(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        bundle: KnowledgeGraphBundle,
        expected_stage: PaperStage,
        updated_at: datetime,
    ) -> GraphWriteResult:
        if expected_stage is not PaperStage.COMPARED:
            raise RepositoryIntegrityError("graph persistence requires the COMPARED stage")
        try:
            with self._sessions.begin() as session:
                item = _locked_product_item(session, run_id, paper_version_id)
                run_row = session.get(DailyRunRow, run_id)
                if run_row is None or bundle.topic_id != run_row.topic_id:
                    raise RepositoryError("graph bundle has the wrong product run topic")
                if any(
                    value.pipeline_execution_id != run_row.pipeline_execution_id
                    for value in (*bundle.mentions, *bundle.edges)
                ):
                    raise RepositoryError(
                        "graph occurrence namespace does not match the product execution"
                    )
                if (
                    item.status == RunItemStatus.IN_PROGRESS.value
                    and item.stage == PaperStage.GRAPH_UPDATED.value
                ):
                    references = _validate_graph_references(session, bundle)
                    _validate_persisted_graph_bundle(
                        session,
                        bundle,
                        references,
                        publication_run_id=run_id,
                    )
                    existing_ids = {
                        row.id
                        for row in session.scalars(
                            select(GraphEntityRow).where(
                                GraphEntityRow.id.in_(tuple(value.id for value in bundle.entities))
                            )
                        )
                    }
                    return GraphWriteResult(
                        entity_ids=_sorted_ids(value.id for value in bundle.entities),
                        edge_ids=_sorted_ids(value.id for value in bundle.edges),
                        new_entity_ids=_sorted_ids(
                            value.id for value in bundle.entities if value.id not in existing_ids
                        ),
                        inferred_edge_ids=_sorted_ids(
                            edge.id
                            for edge in bundle.edges
                            if edge.provenance is RelationProvenance.LLM_INFERRED
                        ),
                    )
                if (
                    item.status != RunItemStatus.IN_PROGRESS.value
                    or item.stage != expected_stage.value
                ):
                    raise RepositoryError("product item is not ready for graph persistence")
                if not any(
                    mention.paper_version_id == paper_version_id for mention in bundle.mentions
                ):
                    raise RepositoryError("graph bundle does not include the product paper")
                references = _validate_graph_references(session, bundle)
                existing_entity_ids = {
                    row.id
                    for row in session.scalars(
                        select(GraphEntityRow).where(
                            GraphEntityRow.id.in_(tuple(value.id for value in bundle.entities))
                        )
                    )
                }
                _upsert_graph_bundle(
                    session,
                    bundle,
                    references,
                    publication_run_id=run_id,
                )
                item.stage = PaperStage.GRAPH_UPDATED.value
                item.updated_at = updated_at
                session.flush()
                return GraphWriteResult(
                    entity_ids=_sorted_ids(value.id for value in bundle.entities),
                    edge_ids=_sorted_ids(value.id for value in bundle.edges),
                    new_entity_ids=_sorted_ids(
                        value.id for value in bundle.entities if value.id not in existing_entity_ids
                    ),
                    inferred_edge_ids=_sorted_ids(
                        edge.id
                        for edge in bundle.edges
                        if edge.provenance is RelationProvenance.LLM_INFERRED
                    ),
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL graph persistence is unavailable"
            ) from error
        except (IntegrityError, DataError) as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected graph ownership constraints"
            ) from error

    def fail_product_item(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        failed_stage: PaperStage,
        error_code: str,
        retryable: bool,
        error_detail: str,
        updated_at: datetime,
    ) -> None:
        try:
            with self._sessions.begin() as session:
                item = _locked_product_item(session, run_id, paper_version_id)
                if item.status == RunItemStatus.FAILED.value:
                    return
                if item.status != RunItemStatus.IN_PROGRESS.value:
                    raise RepositoryError("only an in-progress product item may fail")
                item.status = RunItemStatus.FAILED.value
                item.failed_stage = failed_stage.value
                item.error_code = error_code[:80]
                item.retryable = retryable
                item.error_detail = error_detail[:1000]
                item.updated_at = updated_at
                run_row = session.get(DailyRunRow, run_id)
                if run_row is None:
                    raise RepositoryError("product run is missing")
                session.flush()
                run_row.failed_count = int(
                    session.scalar(
                        select(func.count(RunItemRow.id)).where(
                            RunItemRow.run_id == run_id,
                            RunItemRow.status == RunItemStatus.FAILED.value,
                        )
                    )
                    or 0
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL product item failure persistence is unavailable"
            ) from error
        except (IntegrityError, DataError) as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected product item failure constraints"
            ) from error

    def get_graph_corpus(
        self,
        topic_id: UUID,
        *,
        as_of_date: date,
        current_publication_run_id: UUID | None = None,
    ) -> GraphCorpusInput:
        try:
            with self._sessions() as session:
                visible_run_ids = select(DailyRunRow.id).where(
                    DailyRunRow.topic_id == topic_id,
                    DailyRunRow.operation == RunOperation.PRODUCT_PUBLICATION.value,
                    or_(
                        (
                            DailyRunRow.status.in_(_PUBLISHED_PRODUCT_STATUSES)
                            & (DailyRunRow.logical_date <= as_of_date)
                            & (
                                DailyRunRow.pipeline_execution_mode
                                != PipelineExecutionMode.SMOKE.value
                            )
                        ),
                        DailyRunRow.id == current_publication_run_id,
                    ),
                )
                run_rows = tuple(
                    session.execute(
                        select(RunItemRow, DailyRunRow, PaperRow, PaperVersionRow)
                        .join(DailyRunRow, DailyRunRow.id == RunItemRow.run_id)
                        .join(PaperRow, PaperRow.id == RunItemRow.paper_id)
                        .join(PaperVersionRow, PaperVersionRow.id == RunItemRow.paper_version_id)
                        .where(
                            DailyRunRow.topic_id == topic_id,
                            DailyRunRow.operation == RunOperation.PRODUCT_PUBLICATION.value,
                            DailyRunRow.logical_date <= as_of_date,
                            DailyRunRow.id.in_(visible_run_ids),
                            RunItemRow.status != RunItemStatus.FAILED.value,
                            RunItemRow.stage.in_(_GRAPH_READY_STAGES),
                        )
                        .order_by(DailyRunRow.logical_date, RunItemRow.paper_version_id)
                    )
                )
                visible_run_dates: dict[UUID, date] = {
                    run_id: logical_date
                    for run_id, logical_date in session.execute(
                        select(DailyRunRow.id, DailyRunRow.logical_date).where(
                            DailyRunRow.id.in_(visible_run_ids)
                        )
                    )
                }
                papers_by_version: dict[UUID, TrendPaperRecord] = {}
                for item_row, run_row, paper_row, version_row in run_rows:
                    papers_by_version.setdefault(
                        item_row.paper_version_id,
                        TrendPaperRecord(
                            paper_id=item_row.paper_id,
                            paper_version_id=item_row.paper_version_id,
                            activity_date=run_row.logical_date,
                            title=version_row.title or paper_row.title,
                        ),
                    )
                mention_rows = tuple(
                    session.scalars(
                        select(GraphEntityMentionRow)
                        .where(
                            GraphEntityMentionRow.topic_id == topic_id,
                            GraphEntityMentionRow.publication_run_id.in_(visible_run_ids),
                        )
                        .order_by(GraphEntityMentionRow.id)
                    )
                )
                edge_rows = tuple(
                    session.scalars(
                        select(GraphEdgeRow)
                        .where(
                            GraphEdgeRow.topic_id == topic_id,
                            GraphEdgeRow.publication_run_id.in_(visible_run_ids),
                        )
                        .order_by(GraphEdgeRow.id)
                    )
                )
                referenced_entity_ids = {row.entity_id for row in mention_rows} | {
                    entity_id
                    for row in edge_rows
                    for entity_id in (row.source_entity_id, row.target_entity_id)
                }
                entity_rows = tuple(
                    session.scalars(
                        select(GraphEntityRow)
                        .where(
                            GraphEntityRow.topic_id == topic_id,
                            GraphEntityRow.id.in_(referenced_entity_ids),
                        )
                        .order_by(GraphEntityRow.id)
                    )
                )
                if {row.id for row in entity_rows} != referenced_entity_ids:
                    raise RepositoryIntegrityError(
                        "visible graph occurrences reference missing entities"
                    )
                referenced_version_ids = (
                    {row.paper_version_id for row in mention_rows}
                    | {row.source_paper_version_id for row in edge_rows}
                    | {
                        row.target_paper_version_id
                        for row in edge_rows
                        if row.target_paper_version_id is not None
                    }
                )
                missing_version_ids = referenced_version_ids.difference(papers_by_version)
                missing_versions = {
                    row.id: row
                    for row in session.scalars(
                        select(PaperVersionRow).where(PaperVersionRow.id.in_(missing_version_ids))
                    )
                }
                if set(missing_versions) != missing_version_ids:
                    raise RepositoryIntegrityError(
                        "graph occurrences reference missing paper versions"
                    )
                occurrence_dates: dict[UUID, list[date]] = {}
                for row in mention_rows:
                    occurrence_dates.setdefault(row.paper_version_id, []).append(
                        visible_run_dates[row.publication_run_id]
                    )
                for row in edge_rows:
                    occurrence_dates.setdefault(row.source_paper_version_id, []).append(
                        visible_run_dates[row.publication_run_id]
                    )
                    if row.target_paper_version_id is not None:
                        occurrence_dates.setdefault(row.target_paper_version_id, []).append(
                            visible_run_dates[row.publication_run_id]
                        )
                for version_id, version_row in missing_versions.items():
                    papers_by_version[version_id] = TrendPaperRecord(
                        paper_id=version_row.paper_id,
                        paper_version_id=version_id,
                        activity_date=min(occurrence_dates[version_id]),
                        title=version_row.title,
                    )
                mention_evidence = _mention_evidence_map(
                    session, tuple(row.id for row in mention_rows)
                )
                edge_evidence = _edge_evidence_map(session, tuple(row.id for row in edge_rows))
                corpus_mention_dates = {
                    row.id: visible_run_dates[row.publication_run_id] for row in mention_rows
                }
                mention_rows_by_entity: dict[UUID, list[GraphEntityMentionRow]] = {}
                for row in mention_rows:
                    mention_rows_by_entity.setdefault(row.entity_id, []).append(row)
                entities = tuple(
                    _entity_from_row_with_mentions(
                        row,
                        tuple(mention_rows_by_entity.get(row.id, ())),
                        corpus_mention_dates,
                    )
                    for row in entity_rows
                )
                projected_entities = {item.id: item for item in entities}
                paper_entities = tuple(
                    LineagePaper(
                        graph_entity_id=row.id,
                        paper_id=_required(row.paper_id, "paper graph entity owner"),
                        title=projected_entities[row.id].display_label,
                        publication_date=_paper_publication_date(
                            session, _required(row.paper_id, "paper graph entity owner")
                        ),
                    )
                    for row in entity_rows
                    if row.entity_type == GraphEntityType.PAPER.value and row.paper_id is not None
                )
                return GraphCorpusInput(
                    topic_id=topic_id,
                    papers=tuple(papers_by_version.values()),
                    lineage_papers=paper_entities,
                    entities=entities,
                    mentions=tuple(
                        _mention_from_row(row, mention_evidence.get(row.id, ()))
                        for row in mention_rows
                    ),
                    edges=tuple(
                        _edge_from_row(row, edge_evidence.get(row.id, ())) for row in edge_rows
                    ),
                    mention_activity_dates=corpus_mention_dates,
                    edge_activity_dates={
                        row.id: visible_run_dates[row.publication_run_id] for row in edge_rows
                    },
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL graph corpus query is unavailable"
            ) from error
        except DomainInvariantError as error:
            raise RepositoryIntegrityError(
                "stored graph corpus violates domain invariants"
            ) from error

    def persist_product_aggregates(
        self,
        *,
        run_id: UUID,
        trends: tuple[TrendSnapshot, ...],
        lineages: tuple[LineageSnapshot, ...],
        updated_at: datetime,
    ) -> RunDetail:
        try:
            with self._sessions.begin() as session:
                run_row = session.scalars(
                    select(DailyRunRow).where(DailyRunRow.id == run_id).with_for_update()
                ).one_or_none()
                if (
                    run_row is None
                    or run_row.operation != RunOperation.PRODUCT_PUBLICATION.value
                    or run_row.status != RunStatus.RUNNING.value
                ):
                    raise RepositoryError("product run is missing or no longer running")
                item_rows = tuple(
                    session.scalars(
                        select(RunItemRow)
                        .where(RunItemRow.run_id == run_id)
                        .order_by(RunItemRow.created_at, RunItemRow.id)
                        .with_for_update()
                    )
                )
                graph_items = tuple(
                    row
                    for row in item_rows
                    if row.status == RunItemStatus.IN_PROGRESS.value
                    and row.stage == PaperStage.GRAPH_UPDATED.value
                )
                already_advanced = tuple(
                    row
                    for row in item_rows
                    if row.status == RunItemStatus.IN_PROGRESS.value
                    and row.stage == PaperStage.TREND_SNAPSHOTS_GENERATED.value
                )
                no_update = not item_rows and run_row.selected_count == 0
                metadata_only = bool(item_rows) and all(
                    row.status == RunItemStatus.FAILED.value for row in item_rows
                )
                if not graph_items and not already_advanced and not no_update and not metadata_only:
                    raise RepositoryError("product run has no graph-complete items")
                if trends and (
                    len(trends) != 3
                    or {item.window for item in trends} != set(TrendWindow)
                    or any(
                        item.topic_id != run_row.topic_id or item.as_of_date != run_row.logical_date
                        for item in trends
                    )
                ):
                    raise RepositoryError("product trend snapshots have the wrong scope")
                if any(
                    item.pipeline_execution_id != run_row.pipeline_execution_id for item in trends
                ):
                    raise RepositoryError(
                        "product trend namespace does not match the product execution"
                    )
                successful_paper_ids = {row.paper_id for row in (*graph_items, *already_advanced)}
                if len({item.root_paper_id for item in lineages}) != len(lineages) or any(
                    item.topic_id != run_row.topic_id
                    or item.as_of_date != run_row.logical_date
                    or item.root_paper_id not in successful_paper_ids
                    for item in lineages
                ):
                    raise RepositoryError("product lineage snapshots have the wrong scope")
                if any(
                    item.pipeline_execution_id != run_row.pipeline_execution_id for item in lineages
                ):
                    raise RepositoryError(
                        "product lineage namespace does not match the product execution"
                    )
                for trend in trends:
                    _upsert_trend_snapshot(
                        session,
                        trend,
                        publication_run_id=run_id,
                        updated_at=updated_at,
                    )
                for lineage in lineages:
                    _upsert_lineage_snapshot(
                        session,
                        lineage,
                        publication_run_id=run_id,
                    )
                for item in graph_items:
                    item.stage = PaperStage.TREND_SNAPSHOTS_GENERATED.value
                    item.updated_at = updated_at
                session.flush()
                return _run_detail_from_session(session, run_row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL product aggregate persistence is unavailable"
            ) from error
        except (IntegrityError, DataError) as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected trend or lineage ownership constraints"
            ) from error
        except DomainInvariantError as error:
            raise RepositoryIntegrityError(
                "product aggregate persistence violates domain invariants"
            ) from error

    def finalize_product_publication(
        self,
        *,
        run_id: UUID,
        report: Report,
        completed_at: datetime,
    ) -> DailyRun:
        try:
            with self._sessions.begin() as session:
                run_row = session.scalars(
                    select(DailyRunRow).where(DailyRunRow.id == run_id).with_for_update()
                ).one_or_none()
                if run_row is None or run_row.operation != RunOperation.PRODUCT_PUBLICATION.value:
                    raise RepositoryError("product run is missing")
                if run_row.status in (RunStatus.COMPLETE.value, RunStatus.PARTIAL.value):
                    existing = session.get(ReportRow, report.id)
                    if existing is None or existing.run_id != run_id:
                        raise RepositoryError("terminal product run has no matching report")
                    return _run_from_row(run_row)
                if run_row.status != RunStatus.RUNNING.value:
                    raise RepositoryError("failed product run cannot publish a report")
                if (
                    report.run_id != run_id
                    or report.topic_id != run_row.topic_id
                    or report.logical_date != run_row.logical_date
                    or report.period_start not in (None, run_row.logical_date)
                    or report.period_end not in (None, run_row.logical_date)
                    or report.report_type is not ReportType.DAILY
                ):
                    raise RepositoryError("daily report has the wrong product run ownership")
                item_rows = tuple(
                    session.scalars(
                        select(RunItemRow)
                        .where(RunItemRow.run_id == run_id)
                        .order_by(RunItemRow.created_at, RunItemRow.id)
                        .with_for_update()
                    )
                )
                ready = tuple(
                    row
                    for row in item_rows
                    if row.status == RunItemStatus.IN_PROGRESS.value
                    and row.stage == PaperStage.TREND_SNAPSHOTS_GENERATED.value
                )
                failed = tuple(row for row in item_rows if row.status == RunItemStatus.FAILED.value)
                no_update = (
                    not item_rows
                    and run_row.selected_count == 0
                    and report.counts == ReportCounts(0, 0, 0, 0, 0)
                )
                metadata_only = not ready and bool(failed) and len(failed) == len(item_rows)
                if (not ready and not no_update and not metadata_only) or len(ready) + len(
                    failed
                ) != len(item_rows):
                    raise RepositoryError("product items are not ready for atomic publication")
                expected_status = RunStatus.PARTIAL if failed else RunStatus.COMPLETE
                if report.status is not expected_status:
                    raise RepositoryError("daily report status does not match product item results")
                if {item.paper_version_id for item in report.failures} != {
                    item.paper_version_id for item in failed
                }:
                    raise RepositoryError("daily report failures do not match failed product items")
                if {item.paper_version_id for item in report.highlighted_papers} != {
                    item.paper_version_id for item in item_rows
                }:
                    raise RepositoryError("daily report cards do not match selected product items")
                for item in ready:
                    item.stage = PaperStage.REPORT_GENERATED.value
                    item.updated_at = completed_at
                _insert_normalized_report(session, report)
                for item in ready:
                    item.stage = PaperStage.PUBLISHED.value
                    item.status = RunItemStatus.COMPLETED.value
                    item.updated_at = completed_at
                run_row.status = expected_status.value
                run_row.completed_at = completed_at
                run_row.completed_count = len(ready)
                run_row.failed_count = len(failed)
                run_row.error_code = None if not failed else "ITEM_STAGE_FAILURES"
                run_row.error_detail = (
                    None
                    if not failed
                    else f"{len(failed)} of {len(item_rows)} selected papers failed."
                )
                session.flush()
                return _run_from_row(run_row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL product publication is unavailable"
            ) from error
        except (IntegrityError, DataError) as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected atomic product publication constraints"
            ) from error

    def fail_product_run(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        failed_stage: PaperStage,
        error_code: str,
        retryable: bool,
        error_detail: str,
    ) -> DailyRun:
        try:
            with self._sessions.begin() as session:
                run_row = session.scalars(
                    select(DailyRunRow).where(DailyRunRow.id == run_id).with_for_update()
                ).one_or_none()
                if run_row is None or run_row.operation != RunOperation.PRODUCT_PUBLICATION.value:
                    raise RepositoryError("product run is missing")
                if run_row.status == RunStatus.FAILED.value:
                    _delete_product_artifacts(session, run_row)
                    return _run_from_row(run_row)
                if run_row.status != RunStatus.RUNNING.value:
                    raise RepositoryError("terminal product run cannot be failed")
                _delete_product_artifacts(session, run_row)
                session.execute(
                    update(RunItemRow)
                    .where(
                        RunItemRow.run_id == run_id,
                        RunItemRow.status == RunItemStatus.IN_PROGRESS.value,
                    )
                    .values(
                        status=RunItemStatus.FAILED.value,
                        failed_stage=failed_stage.value,
                        error_code=error_code[:80],
                        retryable=retryable,
                        error_detail=error_detail[:1000],
                        updated_at=completed_at,
                    )
                )
                completed_count, failed_count = session.execute(
                    select(
                        func.count().filter(RunItemRow.status == RunItemStatus.COMPLETED.value),
                        func.count().filter(RunItemRow.status == RunItemStatus.FAILED.value),
                    ).where(RunItemRow.run_id == run_id)
                ).one()
                run_row.status = RunStatus.FAILED.value
                run_row.completed_at = completed_at
                run_row.completed_count = completed_count
                run_row.failed_count = failed_count
                run_row.error_code = error_code[:80]
                run_row.error_detail = error_detail[:1000]
                session.flush()
                return _run_from_row(run_row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL product-run failure persistence is unavailable"
            ) from error
        except (IntegrityError, DataError) as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected product-run failure constraints"
            ) from error

    def get_periodic_report_input(
        self,
        topic_id: UUID,
        *,
        report_type: ReportType,
        period_start: date,
        period_end: date,
    ) -> PeriodicReportInput | None:
        if report_type not in (ReportType.WEEKLY, ReportType.MONTHLY):
            raise RepositoryIntegrityError("periodic input requires weekly or monthly scope")
        try:
            with self._sessions() as session:
                report_rows = tuple(
                    session.scalars(
                        select(ReportRow)
                        .where(
                            ReportRow.topic_id == topic_id,
                            ReportRow.report_type == ReportType.DAILY.value,
                            ReportRow.logical_date >= period_start,
                            ReportRow.logical_date <= period_end,
                            ReportRow.run_id.in_(_published_product_run_ids(topic_id=topic_id)),
                        )
                        .order_by(ReportRow.logical_date, ReportRow.id)
                    )
                )
                if not report_rows:
                    return None
                details = tuple(_report_detail_from_session(session, row) for row in report_rows)
                run_ids = tuple(row.run_id for row in report_rows if row.run_id is not None)
                included_paper_ids = tuple(
                    sorted(
                        set(
                            session.scalars(
                                select(RunItemRow.paper_id).where(
                                    RunItemRow.run_id.in_(run_ids),
                                    RunItemRow.status == RunItemStatus.COMPLETED.value,
                                    RunItemRow.stage == PaperStage.PUBLISHED.value,
                                )
                            )
                        ),
                        key=str,
                    )
                )
                period_entity_ids = set(
                    session.scalars(
                        select(GraphEntityMentionRow.entity_id)
                        .where(GraphEntityMentionRow.publication_run_id.in_(run_ids))
                        .distinct()
                    )
                )
                period_edges = tuple(
                    session.execute(
                        select(GraphEdgeRow.id, GraphEdgeRow.provenance)
                        .where(GraphEdgeRow.publication_run_id.in_(run_ids))
                        .distinct()
                    )
                )
                first_publication_dates = {
                    entity_id: first_date
                    for entity_id, first_date in session.execute(
                        select(
                            GraphEntityMentionRow.entity_id,
                            func.min(DailyRunRow.logical_date),
                        )
                        .join(
                            DailyRunRow,
                            DailyRunRow.id == GraphEntityMentionRow.publication_run_id,
                        )
                        .where(
                            GraphEntityMentionRow.entity_id.in_(period_entity_ids),
                            GraphEntityMentionRow.publication_run_id.in_(
                                _published_product_run_ids(topic_id=topic_id)
                            ),
                        )
                        .group_by(GraphEntityMentionRow.entity_id)
                    )
                }
                graph_changes = ReportGraphChanges(
                    entity_count=len(period_entity_ids),
                    edge_count=len(period_edges),
                    new_entity_count=sum(
                        period_start <= first_publication_dates[entity_id] <= period_end
                        for entity_id in period_entity_ids
                    ),
                    inferred_edge_count=sum(
                        provenance == RelationProvenance.LLM_INFERRED.value
                        for _edge_id, provenance in period_edges
                    ),
                )
                candidate_trends = tuple(
                    session.scalars(
                        select(TrendSnapshotRow)
                        .where(
                            TrendSnapshotRow.topic_id == topic_id,
                            TrendSnapshotRow.as_of_date <= period_end,
                            TrendSnapshotRow.window.in_(tuple(item.value for item in TrendWindow)),
                            TrendSnapshotRow.publication_run_id.in_(_published_product_run_ids()),
                        )
                        .order_by(
                            TrendSnapshotRow.as_of_date.desc(),
                            TrendSnapshotRow.generated_at.desc(),
                            TrendSnapshotRow.window_size_days,
                        )
                    )
                )
                selected_trends: dict[TrendWindow, TrendSnapshotRow] = {}
                for row in candidate_trends:
                    selected_trends.setdefault(TrendWindow(row.window), row)
                trends = tuple(
                    _trend_from_session(session, selected_trends[window])
                    for window in TrendWindow
                    if window in selected_trends
                )
                return PeriodicReportInput(
                    topic_id=topic_id,
                    report_type=report_type,
                    period_start=period_start,
                    period_end=period_end,
                    daily_reports=details,
                    included_paper_ids=included_paper_ids,
                    graph_changes=graph_changes,
                    trends=trends,
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL periodic report input query is unavailable"
            ) from error
        except DomainInvariantError as error:
            raise RepositoryIntegrityError(
                "stored periodic report input violates domain invariants"
            ) from error

    def persist_periodic_report(self, report: Report) -> Report:
        if report.report_type not in (ReportType.WEEKLY, ReportType.MONTHLY):
            raise RepositoryIntegrityError("periodic persistence requires weekly or monthly scope")
        try:
            with self._sessions.begin() as session:
                existing = session.get(ReportRow, report.id)
                if existing is not None:
                    if (
                        existing.topic_id != report.topic_id
                        or existing.report_type != report.report_type.value
                        or existing.period_start != report.period_start
                        or existing.period_end != report.period_end
                    ):
                        raise RepositoryError("periodic report stable identity is already owned")
                    return _report_detail_from_session(session, existing).report
                _insert_normalized_report(session, report)
                session.flush()
                return report
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL periodic report persistence is unavailable"
            ) from error
        except (IntegrityError, DataError) as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected periodic report ownership constraints"
            ) from error


def _run_from_row(row: DailyRunRow) -> DailyRun:
    return DailyRun(
        id=row.id,
        topic_id=row.topic_id,
        logical_date=row.logical_date,
        operation=RunOperation(row.operation),
        analysis_scope=(None if row.analysis_scope is None else AnalysisScope(row.analysis_scope)),
        status=RunStatus(row.status),
        started_at=row.started_at,
        completed_at=row.completed_at,
        cursor_from=row.cursor_from,
        cursor_to=row.cursor_to,
        discovered_count=row.discovered_count,
        normalized_count=row.normalized_count,
        selected_count=row.selected_count,
        completed_count=row.completed_count,
        failed_count=row.failed_count,
        error_code=row.error_code,
        error_detail=row.error_detail,
        schema_version=row.schema_version,
        created_at=row.created_at,
        source_run_id=row.source_run_id,
        pipeline_execution_mode=PipelineExecutionMode(row.pipeline_execution_mode),
        pipeline_selection_limit=row.pipeline_selection_limit,
        pipeline_execution_id=row.pipeline_execution_id,
    )


def _item_from_row(row: RunItemRow) -> RunItem:
    return RunItem(
        id=row.id,
        run_id=row.run_id,
        paper_id=row.paper_id,
        paper_version_id=row.paper_version_id,
        stage=PaperStage(row.stage),
        status=RunItemStatus(row.status),
        failed_stage=None if row.failed_stage is None else PaperStage(row.failed_stage),
        error_code=row.error_code,
        retryable=row.retryable,
        error_detail=row.error_detail,
        schema_version=row.schema_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _run_detail_from_session(session: Session, row: DailyRunRow) -> RunDetail:
    item_rows = tuple(
        session.execute(
            select(RunItemRow, PaperRow, PaperVersionRow)
            .join(PaperRow, PaperRow.id == RunItemRow.paper_id)
            .join(PaperVersionRow, PaperVersionRow.id == RunItemRow.paper_version_id)
            .where(RunItemRow.run_id == row.id)
            .order_by(RunItemRow.created_at, RunItemRow.id)
        )
    )
    analysis_versions: set[UUID] = set()
    search_sessions_by_version: dict[UUID, SearchSessionRow] = {}
    comparisons_by_version: dict[UUID, list[ComparisonRow]] = {}
    trend_status: TrendAvailabilityStatus | None = None
    if row.operation == RunOperation.PRODUCT_PUBLICATION.value:
        analysis_versions = set(
            session.scalars(
                select(ProductRunPaperInputRow.paper_version_id).where(
                    ProductRunPaperInputRow.run_id == row.id
                )
            )
        )
        item_version_ids = {item_row.paper_version_id for item_row, _paper, _version in item_rows}
        for search_row in session.scalars(
            select(SearchSessionRow)
            .where(
                SearchSessionRow.topic_id == row.topic_id,
                SearchSessionRow.source_paper_version_id.in_(item_version_ids),
                SearchSessionRow.pipeline_execution_id == row.pipeline_execution_id,
            )
            .order_by(SearchSessionRow.started_at.desc(), SearchSessionRow.id.desc())
        ):
            search_sessions_by_version.setdefault(search_row.source_paper_version_id, search_row)
        comparison_input_rows = tuple(
            session.scalars(
                select(ProductRunComparisonInputRow).where(
                    ProductRunComparisonInputRow.run_id == row.id
                )
            )
        )
        comparison_rows = {
            comparison.id: comparison
            for comparison in session.scalars(
                select(ComparisonRow).where(
                    ComparisonRow.id.in_(
                        tuple(item.comparison_id for item in comparison_input_rows)
                    )
                )
            )
        }
        for item in comparison_input_rows:
            comparison = comparison_rows.get(item.comparison_id)
            if comparison is None:
                raise RepositoryError("product comparison availability references missing data")
            comparisons_by_version.setdefault(item.paper_version_id, []).append(comparison)
        trend_rows = tuple(
            session.scalars(
                select(TrendSnapshotRow).where(TrendSnapshotRow.publication_run_id == row.id)
            )
        )
        trend_status = (
            "AVAILABLE"
            if len(trend_rows) == len(TrendWindow)
            and all(
                item.data_sufficiency == TrendDataSufficiency.SUFFICIENT.value
                for item in trend_rows
            )
            else "INSUFFICIENT_DATA"
        )
    report_detail = _report_detail_for_run(session, row.id)

    def item_detail(
        item_row: RunItemRow,
        paper_row: PaperRow,
        version_row: PaperVersionRow,
    ) -> RunItemDetail:
        if row.operation != RunOperation.PRODUCT_PUBLICATION.value:
            return RunItemDetail(
                item=_item_from_row(item_row),
                canonical_arxiv_id=paper_row.canonical_arxiv_id,
                paper_title=version_row.title,
                paper_abstract=version_row.abstract or None,
                source_url=version_row.source_url,
            )
        analysis_available = item_row.paper_version_id in analysis_versions
        search_row = search_sessions_by_version.get(item_row.paper_version_id)
        related_work_available = (
            analysis_available
            and search_row is not None
            and search_row.status == SearchSessionStatus.COMPLETE.value
        )
        comparisons = comparisons_by_version.get(item_row.paper_version_id, [])
        limited_comparison = any(
            item.comparability_status != ComparabilityStatus.DIRECTLY_COMPARABLE.value
            for item in comparisons
        )
        comparison_status = (
            "COMPARISON_UNAVAILABLE"
            if not comparisons
            else "LIMITED_COMPARABILITY"
            if limited_comparison
            else "AVAILABLE"
        )
        comparison_reason = (
            "NO_COMPATIBLE_HISTORICAL_ANALYSIS"
            if not comparisons
            else next(
                (
                    item.comparability_reason
                    for item in comparisons
                    if item.comparability_status != ComparabilityStatus.DIRECTLY_COMPARABLE.value
                ),
                None,
            )
        )
        return RunItemDetail(
            item=_item_from_row(item_row),
            canonical_arxiv_id=paper_row.canonical_arxiv_id,
            paper_title=version_row.title,
            paper_abstract=version_row.abstract or None,
            source_url=version_row.source_url,
            analysis_status=("AVAILABLE" if analysis_available else "ANALYSIS_UNAVAILABLE"),
            related_work_status=(
                "AVAILABLE" if related_work_available else "RELATED_WORK_UNAVAILABLE"
            ),
            related_work_reason=(
                None
                if related_work_available
                else "ANALYSIS_UNAVAILABLE"
                if not analysis_available
                else (
                    "NO_RELATED_WORK_RESULT"
                    if search_row is None
                    else search_row.error_code or "RELATED_WORK_UNAVAILABLE"
                )
            ),
            comparison_status=comparison_status,
            comparison_reason=comparison_reason,
            trend_status=trend_status,
        )

    return RunDetail(
        run=_run_from_row(row),
        items=tuple(
            item_detail(item_row, paper_row, version_row)
            for item_row, paper_row, version_row in item_rows
        ),
        report=None if report_detail is None else report_detail.report,
    )


def _report_detail_for_run(session: Session, run_id: UUID) -> ReportDetail | None:
    row = session.scalars(
        select(ReportRow)
        .where(ReportRow.run_id == run_id)
        .order_by(ReportRow.generated_at.desc(), ReportRow.id)
        .limit(1)
    ).one_or_none()
    return None if row is None else _report_detail_from_session(session, row)


def _report_detail_from_session(session: Session, row: ReportRow) -> ReportDetail:
    failure_rows = tuple(
        session.scalars(
            select(ReportFailureRow)
            .where(ReportFailureRow.report_id == row.id)
            .order_by(ReportFailureRow.created_at, ReportFailureRow.id)
        )
    )
    section_rows = tuple(
        session.scalars(
            select(ReportSectionRow)
            .where(ReportSectionRow.report_id == row.id)
            .order_by(ReportSectionRow.position, ReportSectionRow.id)
        )
    )
    paper_rows = tuple(
        session.scalars(
            select(ReportPaperHighlightRow)
            .where(ReportPaperHighlightRow.report_id == row.id)
            .order_by(ReportPaperHighlightRow.position, ReportPaperHighlightRow.id)
        )
    )
    entity_rows = tuple(
        session.scalars(
            select(ReportEntityHighlightRow)
            .where(ReportEntityHighlightRow.report_id == row.id)
            .order_by(ReportEntityHighlightRow.position, ReportEntityHighlightRow.id)
        )
    )
    comparison_rows = tuple(
        session.scalars(
            select(ReportComparisonHighlightRow)
            .where(ReportComparisonHighlightRow.report_id == row.id)
            .order_by(ReportComparisonHighlightRow.position, ReportComparisonHighlightRow.id)
        )
    )
    comparison_owner_rows = {
        item.id: item
        for item in session.scalars(
            select(ComparisonRow).where(
                ComparisonRow.id.in_(tuple(item.comparison_id for item in comparison_rows))
            )
        )
    }
    if set(comparison_owner_rows) != {item.comparison_id for item in comparison_rows}:
        raise RepositoryError("report comparison highlight references a missing comparison")
    trend_rows = tuple(
        session.scalars(
            select(ReportTrendLinkRow)
            .where(ReportTrendLinkRow.report_id == row.id)
            .order_by(ReportTrendLinkRow.position, ReportTrendLinkRow.id)
        )
    )
    lineage_rows = tuple(
        session.scalars(
            select(ReportLineageHighlightRow)
            .where(ReportLineageHighlightRow.report_id == row.id)
            .order_by(ReportLineageHighlightRow.position, ReportLineageHighlightRow.id)
        )
    )
    link_rows = tuple(
        session.scalars(
            select(ReportEvidenceLinkRow)
            .where(ReportEvidenceLinkRow.report_id == row.id)
            .order_by(ReportEvidenceLinkRow.context_type, ReportEvidenceLinkRow.id)
        )
    )
    report_evidence_ids = tuple(
        link.evidence_id for link in link_rows if link.context_type == "REPORT"
    )
    section_evidence: dict[UUID, list[UUID]] = {}
    paper_evidence: dict[UUID, list[UUID]] = {}
    comparison_evidence: dict[UUID, list[UUID]] = {}
    for link in link_rows:
        if link.context_type == "SECTION" and link.report_section_id is not None:
            section_evidence.setdefault(link.report_section_id, []).append(link.evidence_id)
        elif link.context_type == "PAPER_HIGHLIGHT" and link.paper_highlight_id is not None:
            paper_evidence.setdefault(link.paper_highlight_id, []).append(link.evidence_id)
        elif (
            link.context_type == "COMPARISON_HIGHLIGHT" and link.comparison_highlight_id is not None
        ):
            comparison_evidence.setdefault(link.comparison_highlight_id, []).append(
                link.evidence_id
            )
    evidence_rows = tuple(
        session.scalars(
            select(EvidenceRow)
            .where(EvidenceRow.id.in_(report_evidence_ids))
            .order_by(EvidenceRow.id)
        )
    )
    usage = (
        None
        if row.prompt_tokens is None
        else ModelUsage(
            prompt_tokens=row.prompt_tokens,
            completion_tokens=_required(row.completion_tokens, "completion_tokens"),
            total_tokens=_required(row.total_tokens, "total_tokens"),
            call_count=_required(row.call_count, "call_count"),
            duration_ms=_required(row.duration_ms, "duration_ms"),
            estimated_cost_usd=row.estimated_cost_usd,
        )
    )
    report = Report(
        id=row.id,
        run_id=row.run_id,
        topic_id=row.topic_id,
        logical_date=row.logical_date,
        status=RunStatus(row.status),
        title=row.title,
        summary=row.summary,
        source=row.source,
        generated_at=row.generated_at,
        schema_version=row.schema_version,
        created_at=row.created_at,
        failures=tuple(
            ReportFailure(
                id=item.id,
                report_id=item.report_id,
                paper_id=item.paper_id,
                paper_version_id=item.paper_version_id,
                failed_stage=PaperStage(item.failed_stage),
                error_code=item.error_code,
                retryable=item.retryable,
                error_detail=item.error_detail,
                schema_version=item.schema_version,
                created_at=item.created_at,
            )
            for item in failure_rows
        ),
        sections=tuple(
            ReportSection(
                id=item.id,
                report_id=item.report_id,
                kind=ReportSectionKind(item.kind),
                narrative=item.narrative,
                evidence_ids=tuple(section_evidence.get(item.id, ())),
                schema_version=item.schema_version,
                created_at=item.created_at,
            )
            for item in section_rows
        ),
        report_type=ReportType(row.report_type),
        period_start=row.period_start,
        period_end=row.period_end,
        counts=ReportCounts(
            retrieved=row.retrieved_count,
            selected=row.selected_count,
            processed=row.processed_count,
            completed=row.completed_count,
            failed=row.failed_count,
        ),
        highlighted_papers=tuple(
            ReportPaperHighlight(
                paper_id=item.paper_id,
                paper_version_id=item.paper_version_id,
                title=item.title,
                reason=item.reason,
                evidence_ids=tuple(paper_evidence.get(item.id, ())),
            )
            for item in paper_rows
        ),
        major_entities=tuple(
            ReportEntityHighlight(
                graph_entity_id=item.graph_entity_id,
                entity_type=item.entity_type,
                label=item.label,
                distinct_paper_count=item.distinct_paper_count,
            )
            for item in entity_rows
        ),
        notable_comparisons=tuple(
            ReportComparisonHighlight(
                comparison_id=item.comparison_id,
                source_paper_id=comparison_owner_rows[item.comparison_id].source_paper_id,
                source_paper_version_id=(
                    comparison_owner_rows[item.comparison_id].source_paper_version_id
                ),
                target_paper_id=comparison_owner_rows[item.comparison_id].target_paper_id,
                target_paper_version_id=(
                    comparison_owner_rows[item.comparison_id].target_paper_version_id
                ),
                summary=item.summary,
                comparability_status=item.comparability_status,
                evidence_ids=tuple(comparison_evidence.get(item.id, ())),
            )
            for item in comparison_rows
        ),
        graph_changes=ReportGraphChanges(
            entity_count=row.graph_entity_count,
            edge_count=row.graph_edge_count,
            new_entity_count=row.new_graph_entity_count,
            inferred_edge_count=row.inferred_graph_edge_count,
        ),
        trend_snapshot_ids=tuple(item.snapshot_id for item in trend_rows),
        lineage_highlights=tuple(
            ReportLineageHighlight(
                lineage_snapshot_id=item.lineage_snapshot_id,
                root_paper_id=item.root_paper_id,
                summary=item.summary,
                uncertain=item.uncertain,
            )
            for item in lineage_rows
        ),
        evidence_ids=report_evidence_ids,
        limitations=tuple(row.limitations),
        missing_sections=tuple(row.missing_sections),
        narrative_mode=ReportNarrativeMode(row.narrative_mode),
        provider=row.provider,
        configured_model=row.configured_model,
        model_version=row.model_version,
        prompt_version=row.prompt_version,
        usage=usage,
        verification_status=VerificationStatus(row.verification_status),
    )
    return ReportDetail(
        report=report,
        evidence=tuple(_report_evidence_from_row(item) for item in evidence_rows),
    )


def _required[T](value: T | None, name: str) -> T:
    if value is None:
        raise RepositoryError(f"stored report is missing {name}")
    return value


def _paper_publication_date(session: Session, paper_id: UUID) -> date | None:
    value = session.scalar(
        select(func.min(PaperVersionRow.submitted_at)).where(PaperVersionRow.paper_id == paper_id)
    )
    return None if value is None else value.date()


def _analysis_bundle_from_session(session: Session, row: PaperAnalysisRow) -> AnalysisBundle:
    claim_rows = tuple(
        session.scalars(
            select(AnalysisClaimRow)
            .where(AnalysisClaimRow.analysis_id == row.id)
            .order_by(AnalysisClaimRow.claim_key, AnalysisClaimRow.id)
        )
    )
    evidence_rows = tuple(
        session.scalars(
            select(EvidenceRow)
            .where(EvidenceRow.analysis_id == row.id)
            .order_by(EvidenceRow.evidence_key, EvidenceRow.id)
        )
    )
    link_rows = tuple(
        session.execute(
            select(EvidenceClaimRow.evidence_id, EvidenceClaimRow.claim_id)
            .where(EvidenceClaimRow.analysis_id == row.id)
            .order_by(EvidenceClaimRow.evidence_id, EvidenceClaimRow.claim_id)
        )
    )
    supported: dict[UUID, list[UUID]] = {}
    for evidence_id, claim_id in link_rows:
        supported.setdefault(evidence_id, []).append(claim_id)
    return AnalysisBundle(
        analysis=_analysis_from_row(row),
        claims=tuple(_claim_from_row(item) for item in claim_rows),
        evidence=tuple(
            _evidence_from_row(item, tuple(supported.get(item.id, ()))) for item in evidence_rows
        ),
    )


def _analysis_from_row(row: PaperAnalysisRow) -> PaperAnalysis:
    return PaperAnalysis(
        id=row.id,
        paper_id=row.paper_id,
        paper_version_id=row.paper_version_id,
        parsed_paper_id=row.parsed_paper_id,
        analysis_scope=AnalysisScope(row.analysis_scope),
        summary=row.summary,
        research_problem=row.research_problem,
        method_summary=row.method_summary,
        key_contributions=tuple(row.key_contributions),
        limitations=tuple(row.limitations),
        provider=row.provider,
        configured_model=row.configured_model,
        model_version=row.model_version,
        prompt_version=row.prompt_version,
        generated_at=row.generated_at,
        source=row.source,
        verification_status=VerificationStatus(row.verification_status),
        usage=ModelUsage(
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            total_tokens=row.total_tokens,
            call_count=row.call_count,
            duration_ms=row.duration_ms,
            estimated_cost_usd=row.estimated_cost_usd,
        ),
        schema_version=row.schema_version,
        created_at=row.created_at,
        revision_id=row.revision_id,
    )


def _claim_from_row(row: AnalysisClaimRow) -> AnalysisClaim:
    return AnalysisClaim(
        id=row.id,
        analysis_id=row.analysis_id,
        paper_id=row.paper_id,
        paper_version_id=row.paper_version_id,
        key=row.claim_key,
        claim_type=ClaimType(row.claim_type),
        text=row.text,
        provider=row.provider,
        model_version=row.model_version,
        prompt_version=row.prompt_version,
        generated_at=row.generated_at,
        source=row.source,
        verification_status=VerificationStatus(row.verification_status),
        schema_version=row.schema_version,
        created_at=row.created_at,
    )


def _evidence_from_row(row: EvidenceRow, supported_claim_ids: tuple[UUID, ...]) -> Evidence:
    return Evidence(
        id=row.id,
        analysis_id=row.analysis_id,
        paper_id=row.paper_id,
        paper_version_id=row.paper_version_id,
        key=row.evidence_key,
        section=row.section,
        passage_id=row.passage_id,
        coordinates=_coordinates_from_json(row.coordinates),
        excerpt=row.excerpt,
        evidence_type=EvidenceType(row.evidence_type),
        supported_claim_ids=supported_claim_ids,
        extraction_source=row.extraction_source,
        provider=row.provider,
        model_version=row.model_version,
        prompt_version=row.prompt_version,
        generated_at=row.generated_at,
        verification_status=VerificationStatus(row.verification_status),
        schema_version=row.schema_version,
        created_at=row.created_at,
    )


def _coordinates_from_json(values: list[dict[str, Any]]) -> tuple[PageCoordinates, ...]:
    try:
        return tuple(
            PageCoordinates(
                page=int(value["page"]),
                x=float(value["x"]),
                y=float(value["y"]),
                width=float(value["width"]),
                height=float(value["height"]),
            )
            for value in values
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RepositoryError("stored evidence coordinates are invalid") from error


def _report_evidence_from_row(row: EvidenceRow) -> ReportEvidenceReference:
    return ReportEvidenceReference(
        id=row.id,
        paper_id=row.paper_id,
        paper_version_id=row.paper_version_id,
        section=row.section,
        excerpt=row.excerpt,
        evidence_type=row.evidence_type,
        verification_status=VerificationStatus(row.verification_status),
    )


def _locked_product_item(session: Session, run_id: UUID, paper_version_id: UUID) -> RunItemRow:
    run_row = session.scalars(
        select(DailyRunRow).where(DailyRunRow.id == run_id).with_for_update()
    ).one_or_none()
    if (
        run_row is None
        or run_row.operation != RunOperation.PRODUCT_PUBLICATION.value
        or run_row.status != RunStatus.RUNNING.value
    ):
        raise RepositoryError("product run is missing or no longer running")
    item = session.scalars(
        select(RunItemRow)
        .where(
            RunItemRow.run_id == run_id,
            RunItemRow.paper_version_id == paper_version_id,
        )
        .with_for_update()
    ).one_or_none()
    if item is None:
        raise RepositoryError("product run item is missing")
    return item


@dataclass(frozen=True, slots=True)
class _GraphReferences:
    versions: dict[UUID, PaperVersionRow]
    analyses: dict[UUID, PaperAnalysisRow]
    comparisons: dict[UUID, ComparisonRow]
    relations: dict[UUID, PaperRelationRow]
    evidence: dict[UUID, EvidenceRow]


def _validate_graph_references(session: Session, bundle: KnowledgeGraphBundle) -> _GraphReferences:
    version_ids = set(bundle.references.paper_version_ids)
    analysis_ids = set(bundle.references.analysis_ids)
    comparison_ids = set(bundle.references.comparison_ids)
    relation_ids = set(bundle.references.paper_relation_ids)
    evidence_ids = set(bundle.references.evidence_ids)
    versions = {
        row.id: row
        for row in session.scalars(
            select(PaperVersionRow).where(PaperVersionRow.id.in_(version_ids))
        )
    }
    analyses = {
        row.id: row
        for row in session.scalars(
            select(PaperAnalysisRow).where(PaperAnalysisRow.id.in_(analysis_ids))
        )
    }
    comparisons = {
        row.id: row
        for row in session.scalars(
            select(ComparisonRow).where(ComparisonRow.id.in_(comparison_ids))
        )
    }
    relations = {
        row.id: row
        for row in session.scalars(
            select(PaperRelationRow).where(PaperRelationRow.id.in_(relation_ids))
        )
    }
    evidence = {
        row.id: row
        for row in session.scalars(select(EvidenceRow).where(EvidenceRow.id.in_(evidence_ids)))
    }
    comparison_evidence: dict[UUID, set[UUID]] = {}
    for comparison_id, evidence_id in session.execute(
        select(
            ComparisonEvidenceLinkRow.comparison_id,
            ComparisonEvidenceLinkRow.evidence_id,
        ).where(ComparisonEvidenceLinkRow.comparison_id.in_(comparison_ids))
    ):
        comparison_evidence.setdefault(comparison_id, set()).add(evidence_id)
    relation_evidence: dict[UUID, set[UUID]] = {}
    for relation_id, comparison_id, evidence_id in session.execute(
        select(
            RelationEvidenceLinkRow.relation_id,
            RelationEvidenceLinkRow.comparison_id,
            RelationEvidenceLinkRow.evidence_id,
        ).where(RelationEvidenceLinkRow.comparison_id.in_(comparison_ids))
    ):
        comparison_evidence.setdefault(comparison_id, set()).add(evidence_id)
        relation_evidence.setdefault(relation_id, set()).add(evidence_id)
    if (
        set(versions) != version_ids
        or set(analyses) != analysis_ids
        or set(comparisons) != comparison_ids
        or set(relations) != relation_ids
        or set(evidence) != evidence_ids
    ):
        raise RepositoryError("graph bundle references missing persisted records")
    grounding_evidence_ids = {
        evidence_id
        for occurrence in (*bundle.mentions, *bundle.edges)
        for evidence_id in occurrence.evidence_ids
    }
    if any(
        evidence[evidence_id].verification_status == VerificationStatus.REJECTED.value
        for evidence_id in grounding_evidence_ids
    ):
        raise RepositoryError("rejected evidence cannot ground a graph occurrence")
    paper_ids = {entity.paper_id for entity in bundle.entities if entity.paper_id is not None}
    if set(session.scalars(select(PaperRow.id).where(PaperRow.id.in_(paper_ids)))) != paper_ids:
        raise RepositoryError("graph bundle references a missing paper entity")
    for mention in bundle.mentions:
        version = versions.get(mention.paper_version_id)
        if version is None or version.paper_id != mention.paper_id:
            raise RepositoryError("graph mention has invalid paper-version ownership")
        if mention.analysis_id is not None:
            analysis = analyses.get(mention.analysis_id)
            if (
                analysis is None
                or analysis.paper_id != mention.paper_id
                or analysis.paper_version_id != mention.paper_version_id
            ):
                raise RepositoryError("graph mention has invalid analysis ownership")
        if mention.comparison_id is not None:
            comparison = comparisons.get(mention.comparison_id)
            if comparison is None or (
                mention.paper_version_id
                not in (
                    comparison.source_paper_version_id,
                    comparison.target_paper_version_id,
                )
            ):
                raise RepositoryError("graph mention has invalid comparison ownership")
        for evidence_id in mention.evidence_ids:
            item = evidence[evidence_id]
            if (
                item.paper_id != mention.paper_id
                or item.paper_version_id != mention.paper_version_id
            ):
                raise RepositoryError("graph mention evidence has the wrong paper owner")
            if mention.analysis_id is not None and item.analysis_id != mention.analysis_id:
                raise RepositoryError("graph mention evidence has the wrong analysis owner")
            if mention.comparison_id is not None and evidence_id not in comparison_evidence.get(
                mention.comparison_id, set()
            ):
                raise RepositoryError("graph mention evidence is outside its persisted comparison")
    for edge in bundle.edges:
        source_version = versions.get(edge.source_paper_version_id)
        if source_version is None:
            raise RepositoryError("graph edge source paper version is missing")
        target_version = (
            None
            if edge.target_paper_version_id is None
            else versions.get(edge.target_paper_version_id)
        )
        if edge.target_paper_version_id is not None and target_version is None:
            raise RepositoryError("graph edge target paper version is missing")
        if edge.analysis_id is not None:
            analysis = analyses.get(edge.analysis_id)
            if (
                analysis is None
                or analysis.paper_version_id != edge.source_paper_version_id
                or analysis.paper_id != source_version.paper_id
            ):
                raise RepositoryError("graph edge has invalid analysis ownership")
        if edge.comparison_id is not None:
            comparison = comparisons.get(edge.comparison_id)
            if comparison is None or (
                edge.source_paper_version_id
                not in (
                    comparison.source_paper_version_id,
                    comparison.target_paper_version_id,
                )
            ):
                raise RepositoryError("graph edge has invalid comparison ownership")
            if target_version is not None and (
                comparison.source_paper_version_id != edge.source_paper_version_id
                or comparison.target_paper_version_id != target_version.id
            ):
                raise RepositoryError("paper graph edge has the wrong comparison target")
        if edge.paper_relation_id is not None:
            relation = relations.get(edge.paper_relation_id)
            if (
                relation is None
                or relation.comparison_id != edge.comparison_id
                or relation.source_paper_version_id != edge.source_paper_version_id
                or relation.target_paper_version_id != edge.target_paper_version_id
            ):
                raise RepositoryError("graph edge has invalid paper-relation ownership")
        allowed_evidence_versions = {edge.source_paper_version_id}
        if edge.target_paper_version_id is not None:
            allowed_evidence_versions.add(edge.target_paper_version_id)
        if any(
            evidence[evidence_id].paper_version_id not in allowed_evidence_versions
            for evidence_id in edge.evidence_ids
        ):
            raise RepositoryError("graph edge evidence has an unrelated paper owner")
        if edge.analysis_id is not None and any(
            evidence[evidence_id].analysis_id != edge.analysis_id
            for evidence_id in edge.evidence_ids
        ):
            raise RepositoryError("graph edge evidence has the wrong analysis owner")
        if edge.comparison_id is not None:
            allowed_comparison_evidence = (
                relation_evidence.get(edge.paper_relation_id, set())
                if edge.paper_relation_id is not None
                else comparison_evidence.get(edge.comparison_id, set())
            )
            if any(
                evidence_id not in allowed_comparison_evidence for evidence_id in edge.evidence_ids
            ):
                raise RepositoryError(
                    "graph edge evidence is outside its persisted comparison owner"
                )
    return _GraphReferences(
        versions=versions,
        analyses=analyses,
        comparisons=comparisons,
        relations=relations,
        evidence=evidence,
    )


def _upsert_graph_bundle(
    session: Session,
    bundle: KnowledgeGraphBundle,
    references: _GraphReferences,
    *,
    publication_run_id: UUID,
) -> None:
    for entity in bundle.entities:
        row = session.get(GraphEntityRow, entity.id)
        if row is None:
            session.add(
                GraphEntityRow(
                    id=entity.id,
                    topic_id=entity.topic_id,
                    entity_type=entity.entity_type.value,
                    paper_id=entity.paper_id,
                    canonical_label=entity.canonical_label,
                    normalized_key=entity.normalized_key,
                    display_label=entity.display_label,
                    aliases=list(entity.aliases),
                    provenance=entity.provenance.value,
                    source=entity.source,
                    schema_version=entity.schema_version,
                    created_at=entity.created_at,
                    updated_at=entity.updated_at,
                )
            )
        else:
            if (
                row.topic_id != entity.topic_id
                or row.entity_type != entity.entity_type.value
                or row.paper_id != entity.paper_id
                or row.normalized_key != entity.normalized_key
            ):
                raise RepositoryError("graph entity stable ID has conflicting ownership")
            # Shared entity attributes remain immutable while this publication is staged.
            # The run-owned mention records preserve each later observation without making
            # a failed publication visible through an already-published entity.
    session.flush()
    for mention in bundle.mentions:
        model = mention.model_provenance
        row = session.get(GraphEntityMentionRow, mention.id)
        values = {
            "publication_run_id": publication_run_id,
            "pipeline_execution_id": mention.pipeline_execution_id,
            "topic_id": bundle.topic_id,
            "entity_id": mention.entity_id,
            "paper_id": mention.paper_id,
            "paper_version_id": mention.paper_version_id,
            "analysis_id": mention.analysis_id,
            "comparison_id": mention.comparison_id,
            "observed_label": mention.observed_label,
            "provenance": mention.provenance.value,
            "provider": None if model is None else model.provider,
            "configured_model": None if model is None else model.configured_model,
            "model_version": None if model is None else model.model_version,
            "prompt_version": None if model is None else model.prompt_version,
            "confidence": mention.confidence,
            "verification_status": mention.verification_status.value,
            "generated_at": mention.generated_at,
            "schema_version": mention.schema_version,
            "created_at": mention.created_at,
        }
        if row is None:
            session.add(GraphEntityMentionRow(id=mention.id, **values))
            session.flush()
            for evidence_id in mention.evidence_ids:
                evidence = references.evidence[evidence_id]
                session.add(
                    GraphMentionEvidenceLinkRow(
                        mention_id=mention.id,
                        evidence_id=evidence.id,
                        mention_paper_id=mention.paper_id,
                        mention_paper_version_id=mention.paper_version_id,
                        evidence_paper_id=evidence.paper_id,
                        evidence_paper_version_id=evidence.paper_version_id,
                        evidence_analysis_id=evidence.analysis_id,
                    )
                )
        else:
            _validate_existing_graph_mention(
                session,
                row,
                values,
                mention.evidence_ids,
                publication_run_id=publication_run_id,
            )
    session.flush()
    for edge in bundle.edges:
        source = references.versions[edge.source_paper_version_id]
        target = (
            None
            if edge.target_paper_version_id is None
            else references.versions[edge.target_paper_version_id]
        )
        model = edge.model_provenance
        row = session.get(GraphEdgeRow, edge.id)
        values = {
            "publication_run_id": publication_run_id,
            "pipeline_execution_id": edge.pipeline_execution_id,
            "topic_id": bundle.topic_id,
            "source_entity_id": edge.source_entity_id,
            "target_entity_id": edge.target_entity_id,
            "relation_type": edge.relation_type.value,
            "source_paper_id": source.paper_id,
            "source_paper_version_id": edge.source_paper_version_id,
            "target_paper_id": None if target is None else target.paper_id,
            "target_paper_version_id": edge.target_paper_version_id,
            "analysis_id": edge.analysis_id,
            "comparison_id": edge.comparison_id,
            "paper_relation_id": edge.paper_relation_id,
            "provenance": edge.provenance.value,
            "justification": edge.justification,
            "provider": None if model is None else model.provider,
            "configured_model": None if model is None else model.configured_model,
            "model_version": None if model is None else model.model_version,
            "prompt_version": None if model is None else model.prompt_version,
            "confidence": edge.confidence,
            "verification_status": edge.verification_status.value,
            "generated_at": edge.generated_at,
            "schema_version": edge.schema_version,
            "created_at": edge.created_at,
        }
        if row is None:
            session.add(GraphEdgeRow(id=edge.id, **values))
            session.flush()
            for evidence_id in edge.evidence_ids:
                evidence = references.evidence[evidence_id]
                role = (
                    "SOURCE"
                    if evidence.paper_version_id == edge.source_paper_version_id
                    else (
                        "TARGET"
                        if evidence.paper_version_id == edge.target_paper_version_id
                        else "RELATION"
                    )
                )
                session.add(
                    GraphEdgeEvidenceLinkRow(
                        graph_edge_id=edge.id,
                        evidence_id=evidence.id,
                        evidence_role=role,
                        topic_id=bundle.topic_id,
                        evidence_paper_id=evidence.paper_id,
                        evidence_paper_version_id=evidence.paper_version_id,
                        evidence_analysis_id=evidence.analysis_id,
                    )
                )
        else:
            _validate_existing_graph_edge(
                session,
                row,
                values,
                edge.evidence_ids,
                publication_run_id=publication_run_id,
            )


def _validate_persisted_graph_bundle(
    session: Session,
    bundle: KnowledgeGraphBundle,
    references: _GraphReferences,
    *,
    publication_run_id: UUID,
) -> None:
    for mention in bundle.mentions:
        row = session.get(GraphEntityMentionRow, mention.id)
        if row is None:
            raise RepositoryError("graph-complete item is missing a persisted mention")
        model = mention.model_provenance
        _validate_existing_graph_mention(
            session,
            row,
            {
                "publication_run_id": publication_run_id,
                "topic_id": bundle.topic_id,
                "entity_id": mention.entity_id,
                "paper_id": mention.paper_id,
                "paper_version_id": mention.paper_version_id,
                "analysis_id": mention.analysis_id,
                "comparison_id": mention.comparison_id,
                "observed_label": mention.observed_label,
                "provenance": mention.provenance.value,
                "provider": None if model is None else model.provider,
                "configured_model": None if model is None else model.configured_model,
                "model_version": None if model is None else model.model_version,
                "prompt_version": None if model is None else model.prompt_version,
                "confidence": mention.confidence,
                "verification_status": mention.verification_status.value,
                "generated_at": mention.generated_at,
                "schema_version": mention.schema_version,
                "created_at": mention.created_at,
            },
            mention.evidence_ids,
            publication_run_id=publication_run_id,
        )
    for edge in bundle.edges:
        row = session.get(GraphEdgeRow, edge.id)
        if row is None:
            raise RepositoryError("graph-complete item is missing a persisted edge")
        source = references.versions[edge.source_paper_version_id]
        target = (
            None
            if edge.target_paper_version_id is None
            else references.versions[edge.target_paper_version_id]
        )
        model = edge.model_provenance
        _validate_existing_graph_edge(
            session,
            row,
            {
                "publication_run_id": publication_run_id,
                "topic_id": bundle.topic_id,
                "source_entity_id": edge.source_entity_id,
                "target_entity_id": edge.target_entity_id,
                "relation_type": edge.relation_type.value,
                "source_paper_id": source.paper_id,
                "source_paper_version_id": edge.source_paper_version_id,
                "target_paper_id": None if target is None else target.paper_id,
                "target_paper_version_id": edge.target_paper_version_id,
                "analysis_id": edge.analysis_id,
                "comparison_id": edge.comparison_id,
                "paper_relation_id": edge.paper_relation_id,
                "provenance": edge.provenance.value,
                "justification": edge.justification,
                "provider": None if model is None else model.provider,
                "configured_model": None if model is None else model.configured_model,
                "model_version": None if model is None else model.model_version,
                "prompt_version": None if model is None else model.prompt_version,
                "confidence": edge.confidence,
                "verification_status": edge.verification_status.value,
                "generated_at": edge.generated_at,
                "schema_version": edge.schema_version,
                "created_at": edge.created_at,
            },
            edge.evidence_ids,
            publication_run_id=publication_run_id,
        )


def _validate_existing_graph_mention(
    session: Session,
    row: GraphEntityMentionRow,
    values: Mapping[str, object],
    evidence_ids: tuple[UUID, ...],
    *,
    publication_run_id: UUID,
) -> None:
    _validate_reusable_occurrence_owner(
        session,
        row.publication_run_id,
        publication_run_id,
        kind="mention",
    )
    if any(
        getattr(row, key) != value for key, value in values.items() if key != "publication_run_id"
    ):
        raise RepositoryError("graph mention stable ID has conflicting content")
    stored_evidence_ids = set(
        session.scalars(
            select(GraphMentionEvidenceLinkRow.evidence_id).where(
                GraphMentionEvidenceLinkRow.mention_id == row.id
            )
        )
    )
    if stored_evidence_ids != set(evidence_ids):
        raise RepositoryError("graph mention stable ID has conflicting evidence")


def _validate_existing_graph_edge(
    session: Session,
    row: GraphEdgeRow,
    values: Mapping[str, object],
    evidence_ids: tuple[UUID, ...],
    *,
    publication_run_id: UUID,
) -> None:
    _validate_reusable_occurrence_owner(
        session,
        row.publication_run_id,
        publication_run_id,
        kind="edge",
    )
    if any(
        getattr(row, key) != value for key, value in values.items() if key != "publication_run_id"
    ):
        raise RepositoryError("graph edge stable ID has conflicting content")
    stored_evidence_ids = set(
        session.scalars(
            select(GraphEdgeEvidenceLinkRow.evidence_id).where(
                GraphEdgeEvidenceLinkRow.graph_edge_id == row.id
            )
        )
    )
    if stored_evidence_ids != set(evidence_ids):
        raise RepositoryError("graph edge stable ID has conflicting evidence")


def _validate_reusable_occurrence_owner(
    session: Session,
    owner_run_id: UUID,
    publication_run_id: UUID,
    *,
    kind: str,
) -> None:
    if owner_run_id == publication_run_id:
        return
    owner = session.get(DailyRunRow, owner_run_id)
    publication = session.get(DailyRunRow, publication_run_id)
    if (
        owner is None
        or publication is None
        or owner.operation != RunOperation.PRODUCT_PUBLICATION.value
        or owner.status not in _PUBLISHED_PRODUCT_STATUSES
        or owner.topic_id != publication.topic_id
        or owner.logical_date > publication.logical_date
    ):
        raise RepositoryError(f"graph {kind} stable ID has conflicting run ownership")


def _resolve_graph_topic(
    session: Session,
    topic_slug: str | None,
    *,
    as_of: date | None,
) -> UUID | None:
    statement = (
        select(GraphEntityRow.topic_id)
        .join(
            GraphEntityMentionRow,
            GraphEntityMentionRow.entity_id == GraphEntityRow.id,
        )
        .join(TopicRow, TopicRow.id == GraphEntityRow.topic_id)
        .where(
            GraphEntityMentionRow.publication_run_id.in_(_published_product_run_ids(as_of=as_of))
        )
        .order_by(
            GraphEntityMentionRow.generated_at.desc(),
            TopicRow.slug,
            GraphEntityRow.topic_id,
        )
        .limit(1)
    )
    if topic_slug is not None:
        statement = statement.where(TopicRow.slug == topic_slug)
    return session.scalar(statement)


def _resolve_trend_topic(session: Session, topic_slug: str | None) -> UUID | None:
    statement = (
        select(TrendSnapshotRow.topic_id)
        .join(TopicRow, TopicRow.id == TrendSnapshotRow.topic_id)
        .where(TrendSnapshotRow.publication_run_id.in_(_published_product_run_ids()))
        .order_by(
            TrendSnapshotRow.as_of_date.desc(),
            TrendSnapshotRow.generated_at.desc(),
            TopicRow.slug,
        )
        .limit(1)
    )
    if topic_slug is not None:
        statement = statement.where(TopicRow.slug == topic_slug)
    return session.scalar(statement)


def _mention_evidence_map(
    session: Session, mention_ids: tuple[UUID, ...]
) -> dict[UUID, tuple[UUID, ...]]:
    if not mention_ids:
        return {}
    rows = tuple(
        session.execute(
            select(
                GraphMentionEvidenceLinkRow.mention_id,
                GraphMentionEvidenceLinkRow.evidence_id,
            )
            .where(GraphMentionEvidenceLinkRow.mention_id.in_(mention_ids))
            .order_by(
                GraphMentionEvidenceLinkRow.mention_id,
                GraphMentionEvidenceLinkRow.evidence_id,
            )
        )
    )
    values: dict[UUID, list[UUID]] = {}
    for mention_id, evidence_id in rows:
        values.setdefault(mention_id, []).append(evidence_id)
    return {key: tuple(value) for key, value in values.items()}


def _edge_evidence_map(
    session: Session, edge_ids: tuple[UUID, ...]
) -> dict[UUID, tuple[UUID, ...]]:
    if not edge_ids:
        return {}
    rows = tuple(
        session.execute(
            select(
                GraphEdgeEvidenceLinkRow.graph_edge_id,
                GraphEdgeEvidenceLinkRow.evidence_id,
            )
            .where(GraphEdgeEvidenceLinkRow.graph_edge_id.in_(edge_ids))
            .order_by(
                GraphEdgeEvidenceLinkRow.graph_edge_id,
                GraphEdgeEvidenceLinkRow.evidence_id,
            )
        )
    )
    values: dict[UUID, list[UUID]] = {}
    for edge_id, evidence_id in rows:
        if evidence_id not in values.setdefault(edge_id, []):
            values[edge_id].append(evidence_id)
    return {key: tuple(value) for key, value in values.items()}


def _edge_evidence_detail_map(
    session: Session, edge_ids: tuple[UUID, ...]
) -> dict[UUID, tuple[GraphEdgeEvidenceReference, ...]]:
    if not edge_ids:
        return {}
    rows = tuple(
        session.execute(
            select(
                GraphEdgeEvidenceLinkRow.graph_edge_id,
                EvidenceRow.id,
                EvidenceRow.paper_id,
                EvidenceRow.paper_version_id,
                GraphEdgeEvidenceLinkRow.evidence_role,
            )
            .join(EvidenceRow, EvidenceRow.id == GraphEdgeEvidenceLinkRow.evidence_id)
            .where(GraphEdgeEvidenceLinkRow.graph_edge_id.in_(edge_ids))
            .order_by(
                GraphEdgeEvidenceLinkRow.graph_edge_id,
                EvidenceRow.id,
                GraphEdgeEvidenceLinkRow.evidence_role,
            )
        )
    )
    values: dict[UUID, list[GraphEdgeEvidenceReference]] = {}
    for edge_id, evidence_id, paper_id, paper_version_id, role in rows:
        values.setdefault(edge_id, []).append(
            GraphEdgeEvidenceReference(
                edge_id=edge_id,
                evidence_id=evidence_id,
                paper_id=paper_id,
                paper_version_id=paper_version_id,
                role=GraphEvidenceRole(role),
            )
        )
    return {key: tuple(value) for key, value in values.items()}


def _model_provenance_from_values(
    provider: str | None,
    configured_model: str | None,
    model_version: str | None,
    prompt_version: str | None,
) -> GraphModelProvenance | None:
    values = (provider, configured_model, model_version, prompt_version)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise RepositoryError("stored graph model provenance is incomplete")
    return GraphModelProvenance(
        provider=_required(provider, "graph provider"),
        configured_model=_required(configured_model, "configured graph model"),
        model_version=_required(model_version, "graph model version"),
        prompt_version=_required(prompt_version, "graph prompt version"),
    )


def _entity_from_row(row: GraphEntityRow) -> GraphEntity:
    return GraphEntity(
        id=row.id,
        topic_id=row.topic_id,
        entity_type=GraphEntityType(row.entity_type),
        paper_id=row.paper_id,
        canonical_label=row.canonical_label,
        normalized_key=row.normalized_key,
        display_label=row.display_label,
        aliases=tuple(row.aliases),
        provenance=RelationProvenance(row.provenance),
        source=row.source,
        schema_version=row.schema_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _entity_from_row_with_mentions(
    row: GraphEntityRow,
    mentions: tuple[GraphEntityMentionRow, ...],
    activity_dates: Mapping[UUID, date],
) -> GraphEntity:
    if not mentions:
        return _entity_from_row(row)
    ordered = tuple(
        sorted(
            mentions,
            key=lambda item: (activity_dates[item.id], item.generated_at, str(item.id)),
        )
    )
    latest = ordered[-1]
    aliases: list[str] = []
    for alias in (item.observed_label for item in ordered):
        if alias not in aliases:
            aliases.append(alias)
    entity_type = GraphEntityType(row.entity_type)
    created_at = min(item.created_at for item in ordered)
    updated_at = max(item.created_at for item in ordered)
    return GraphEntity(
        id=row.id,
        topic_id=row.topic_id,
        entity_type=entity_type,
        paper_id=row.paper_id,
        canonical_label=latest.observed_label,
        normalized_key=row.normalized_key,
        display_label=latest.observed_label,
        aliases=tuple(aliases),
        provenance=RelationProvenance(latest.provenance),
        source=(
            "paper_metadata" if entity_type is GraphEntityType.PAPER else "canonical_entity_key_v1"
        ),
        schema_version=row.schema_version,
        created_at=created_at,
        updated_at=updated_at,
    )


def _mention_from_row(
    row: GraphEntityMentionRow, evidence_ids: tuple[UUID, ...]
) -> GraphEntityMention:
    return GraphEntityMention(
        id=row.id,
        entity_id=row.entity_id,
        paper_id=row.paper_id,
        paper_version_id=row.paper_version_id,
        analysis_id=row.analysis_id,
        comparison_id=row.comparison_id,
        observed_label=row.observed_label,
        provenance=RelationProvenance(row.provenance),
        evidence_ids=evidence_ids,
        model_provenance=_model_provenance_from_values(
            row.provider,
            row.configured_model,
            row.model_version,
            row.prompt_version,
        ),
        confidence=row.confidence,
        verification_status=VerificationStatus(row.verification_status),
        generated_at=row.generated_at,
        schema_version=row.schema_version,
        created_at=row.created_at,
        pipeline_execution_id=row.pipeline_execution_id,
    )


def _edge_from_row(row: GraphEdgeRow, evidence_ids: tuple[UUID, ...]) -> GraphEdge:
    return GraphEdge(
        id=row.id,
        source_entity_id=row.source_entity_id,
        target_entity_id=row.target_entity_id,
        relation_type=GraphRelationType(row.relation_type),
        source_paper_version_id=row.source_paper_version_id,
        target_paper_version_id=row.target_paper_version_id,
        analysis_id=row.analysis_id,
        comparison_id=row.comparison_id,
        paper_relation_id=row.paper_relation_id,
        provenance=RelationProvenance(row.provenance),
        evidence_ids=evidence_ids,
        justification=row.justification,
        model_provenance=_model_provenance_from_values(
            row.provider,
            row.configured_model,
            row.model_version,
            row.prompt_version,
        ),
        confidence=row.confidence,
        verification_status=VerificationStatus(row.verification_status),
        generated_at=row.generated_at,
        schema_version=row.schema_version,
        created_at=row.created_at,
        pipeline_execution_id=row.pipeline_execution_id,
    )


def _trend_change_from_metric(row: TrendMetricRow) -> TrendChange:
    return TrendChange(
        current_count=row.current_count,
        preceding_count=row.preceding_count,
        absolute_change=row.absolute_change,
        denominator_count=row.denominator_count,
        relative_change=row.relative_change,
        growth_status=TrendGrowthStatus(row.growth_status),
    )


def _trend_from_session(
    session: Session,
    row: TrendSnapshotRow,
    *,
    entity_type: GraphEntityType | None = None,
    max_entities: int | None = None,
) -> TrendSnapshot:
    volume = tuple(
        session.scalars(
            select(TrendMetricRow).where(
                TrendMetricRow.snapshot_id == row.id,
                TrendMetricRow.metric_kind == "PAPER_VOLUME",
            )
        )
    )
    if len(volume) != 1:
        raise RepositoryError("stored trend snapshot requires one paper-volume metric")
    entity_statement = select(TrendMetricRow).where(
        TrendMetricRow.snapshot_id == row.id,
        TrendMetricRow.metric_kind == "ENTITY",
    )
    if entity_type is not None:
        entity_statement = entity_statement.where(TrendMetricRow.entity_type == entity_type.value)
    entity_statement = entity_statement.order_by(
        TrendMetricRow.current_count.desc(),
        TrendMetricRow.label,
        TrendMetricRow.id,
    )
    if max_entities is not None:
        entity_statement = entity_statement.limit(max_entities)
    entity_metrics = tuple(session.scalars(entity_statement))
    relation_metrics = tuple(
        session.scalars(
            select(TrendMetricRow)
            .where(
                TrendMetricRow.snapshot_id == row.id,
                TrendMetricRow.metric_kind == "RELATION",
            )
            .order_by(TrendMetricRow.relation_type, TrendMetricRow.id)
        )
    )
    entity_counts = tuple(
        TrendEntityCount(
            entity_id=_required(item.entity_id, "trend entity ID"),
            entity_type=GraphEntityType(_required(item.entity_type, "trend entity type")),
            label=item.label,
            change=_trend_change_from_metric(item),
            newly_appearing=item.newly_appearing,
            recurring=item.recurring,
        )
        for item in entity_metrics
    )
    relation_counts = tuple(
        TrendRelationCount(
            relation_type=GraphRelationType(_required(item.relation_type, "trend relation type")),
            change=_trend_change_from_metric(item),
        )
        for item in relation_metrics
    )
    representative_rows = tuple(
        session.scalars(
            select(TrendRepresentativePaperRow)
            .where(TrendRepresentativePaperRow.snapshot_id == row.id)
            .order_by(
                TrendRepresentativePaperRow.position,
                TrendRepresentativePaperRow.paper_id,
            )
        )
    )
    representative_ids = tuple(item.paper_id for item in representative_rows)
    return TrendSnapshot(
        id=row.id,
        topic_id=row.topic_id,
        as_of_date=row.as_of_date,
        window=TrendWindow(row.window),
        window_start=row.window_start,
        window_end=row.window_end,
        preceding_window_start=row.preceding_start,
        preceding_window_end=row.preceding_end,
        included_paper_count=row.included_paper_count,
        preceding_paper_count=row.preceding_paper_count,
        paper_count_change=_trend_change_from_metric(volume[0]),
        entity_counts=entity_counts,
        relation_counts=relation_counts,
        new_entity_ids=tuple(
            sorted((item.entity_id for item in entity_counts if item.newly_appearing), key=str)
        ),
        recurring_entity_ids=tuple(
            sorted((item.entity_id for item in entity_counts if item.recurring), key=str)
        ),
        representative_paper_ids=representative_ids,
        data_sufficiency=TrendDataSufficiency(row.data_sufficiency),
        preceding_data_sufficiency=TrendDataSufficiency(row.preceding_data_sufficiency),
        thresholds=TrendThresholds(
            limited_paper_count=row.limited_paper_count,
            sufficient_paper_count=row.sufficient_paper_count,
            minimum_growth_denominator=row.minimum_growth_denominator,
        ),
        aggregation_version=row.aggregation_version,
        generated_at=row.generated_at,
        schema_version=row.schema_version,
        pipeline_execution_id=row.pipeline_execution_id,
    )


def _trend_detail_from_session(
    session: Session,
    row: TrendSnapshotRow,
    *,
    entity_type: GraphEntityType | None,
    max_entities: int,
) -> TrendDetail:
    total_statement = select(func.count(TrendMetricRow.id)).where(
        TrendMetricRow.snapshot_id == row.id,
        TrendMetricRow.metric_kind == "ENTITY",
    )
    if entity_type is not None:
        total_statement = total_statement.where(TrendMetricRow.entity_type == entity_type.value)
    total_entities = int(session.scalar(total_statement) or 0)
    snapshot = _trend_from_session(
        session,
        row,
        entity_type=entity_type,
        max_entities=max_entities,
    )
    representatives = tuple(
        _trend_paper_from_session(session, row, paper_id)
        for paper_id in snapshot.representative_paper_ids
    )
    return TrendDetail(
        snapshot=snapshot,
        representative_papers=representatives,
        total_entities=total_entities,
        truncated=total_entities > len(snapshot.entity_counts),
    )


def _trend_paper_from_session(
    session: Session, snapshot: TrendSnapshotRow, paper_id: UUID
) -> TrendPaperRecord:
    selected = session.execute(
        select(RunItemRow, DailyRunRow, PaperVersionRow)
        .join(DailyRunRow, DailyRunRow.id == RunItemRow.run_id)
        .join(PaperVersionRow, PaperVersionRow.id == RunItemRow.paper_version_id)
        .where(
            DailyRunRow.topic_id == snapshot.topic_id,
            DailyRunRow.operation == RunOperation.PRODUCT_PUBLICATION.value,
            DailyRunRow.status.in_(_PUBLISHED_PRODUCT_STATUSES),
            DailyRunRow.logical_date >= snapshot.window_start,
            DailyRunRow.logical_date <= snapshot.window_end,
            RunItemRow.paper_id == paper_id,
            RunItemRow.status != RunItemStatus.FAILED.value,
            RunItemRow.stage.in_(_GRAPH_READY_STAGES),
        )
        .order_by(DailyRunRow.logical_date.desc(), PaperVersionRow.version.desc())
        .limit(1)
    ).one_or_none()
    if selected is None:
        mention = session.execute(
            select(GraphEntityMentionRow, PaperVersionRow, DailyRunRow)
            .join(
                PaperVersionRow,
                PaperVersionRow.id == GraphEntityMentionRow.paper_version_id,
            )
            .join(
                DailyRunRow,
                DailyRunRow.id == GraphEntityMentionRow.publication_run_id,
            )
            .where(
                GraphEntityMentionRow.topic_id == snapshot.topic_id,
                DailyRunRow.status.in_(_PUBLISHED_PRODUCT_STATUSES),
                GraphEntityMentionRow.paper_id == paper_id,
                DailyRunRow.logical_date >= snapshot.window_start,
                DailyRunRow.logical_date <= snapshot.window_end,
            )
            .order_by(
                DailyRunRow.logical_date.desc(),
                PaperVersionRow.version.desc(),
            )
            .limit(1)
        ).one_or_none()
        if mention is None:
            raise RepositoryError("trend representative paper is outside its persisted window")
        mention_row, version_row, run_row = mention
        return TrendPaperRecord(
            paper_id=mention_row.paper_id,
            paper_version_id=mention_row.paper_version_id,
            activity_date=run_row.logical_date,
            title=version_row.title,
        )
    item_row, run_row, version_row = selected
    return TrendPaperRecord(
        paper_id=item_row.paper_id,
        paper_version_id=item_row.paper_version_id,
        activity_date=run_row.logical_date,
        title=version_row.title,
    )


def _upsert_trend_snapshot(
    session: Session,
    snapshot: TrendSnapshot,
    *,
    publication_run_id: UUID,
    updated_at: datetime,
) -> None:
    entity_ids = {item.entity_id for item in snapshot.entity_counts}
    owned_entity_ids = set(
        session.scalars(
            select(GraphEntityRow.id).where(
                GraphEntityRow.topic_id == snapshot.topic_id,
                GraphEntityRow.id.in_(entity_ids),
            )
        )
    )
    if owned_entity_ids != entity_ids:
        raise RepositoryError("trend snapshot references graph entities from another topic")
    representative_ids = set(snapshot.representative_paper_ids)
    if (
        set(session.scalars(select(PaperRow.id).where(PaperRow.id.in_(representative_ids))))
        != representative_ids
    ):
        raise RepositoryError("trend snapshot references a missing representative paper")
    row = session.get(TrendSnapshotRow, snapshot.id)
    values = {
        "publication_run_id": publication_run_id,
        "pipeline_execution_id": snapshot.pipeline_execution_id,
        "topic_id": snapshot.topic_id,
        "as_of_date": snapshot.as_of_date,
        "window": snapshot.window.value,
        "window_size_days": snapshot.window.days,
        "window_start": snapshot.window_start,
        "window_end": snapshot.window_end,
        "preceding_start": snapshot.preceding_window_start,
        "preceding_end": snapshot.preceding_window_end,
        "included_paper_count": snapshot.included_paper_count,
        "preceding_paper_count": snapshot.preceding_paper_count,
        "paper_count_change": snapshot.paper_count_change.absolute_change,
        "paper_count_denominator": snapshot.paper_count_change.denominator_count,
        "paper_growth_rate": snapshot.paper_count_change.relative_change,
        "growth_status": snapshot.paper_count_change.growth_status.value,
        "entity_count": len(snapshot.entity_counts),
        "relation_count": len(snapshot.relation_counts),
        "new_entity_count": len(snapshot.new_entity_ids),
        "recurring_entity_count": len(snapshot.recurring_entity_ids),
        "limited_paper_count": snapshot.thresholds.limited_paper_count,
        "sufficient_paper_count": snapshot.thresholds.sufficient_paper_count,
        "minimum_growth_denominator": snapshot.thresholds.minimum_growth_denominator,
        "data_sufficiency": snapshot.data_sufficiency.value,
        "preceding_data_sufficiency": snapshot.preceding_data_sufficiency.value,
        "aggregation_version": snapshot.aggregation_version,
        "generated_at": snapshot.generated_at,
        "source": "deterministic_graph_aggregation",
        "schema_version": snapshot.schema_version,
        "created_at": snapshot.generated_at,
        "updated_at": updated_at,
    }
    if row is None:
        session.add(TrendSnapshotRow(id=snapshot.id, **values))
    else:
        if (
            row.topic_id != snapshot.topic_id
            or row.as_of_date != snapshot.as_of_date
            or row.window != snapshot.window.value
            or row.aggregation_version != snapshot.aggregation_version
            or row.publication_run_id != publication_run_id
            or row.pipeline_execution_id != snapshot.pipeline_execution_id
        ):
            raise RepositoryError("trend stable ID has conflicting ownership")
        for key, value in values.items():
            setattr(row, key, value)
    session.flush()
    session.execute(delete(TrendMetricRow).where(TrendMetricRow.snapshot_id == snapshot.id))
    session.execute(
        delete(TrendRepresentativePaperRow).where(
            TrendRepresentativePaperRow.snapshot_id == snapshot.id
        )
    )
    _add_trend_metric(
        session,
        snapshot,
        key="paper-volume",
        metric_kind="PAPER_VOLUME",
        label="Paper volume",
        change=snapshot.paper_count_change,
    )
    for item in snapshot.entity_counts:
        _add_trend_metric(
            session,
            snapshot,
            key=f"entity:{item.entity_id}",
            metric_kind="ENTITY",
            label=item.label,
            change=item.change,
            entity_id=item.entity_id,
            entity_type=item.entity_type.value,
            newly_appearing=item.newly_appearing,
            recurring=item.recurring,
        )
    for item in snapshot.relation_counts:
        _add_trend_metric(
            session,
            snapshot,
            key=f"relation:{item.relation_type.value}",
            metric_kind="RELATION",
            label=item.relation_type.value,
            change=item.change,
            relation_type=item.relation_type.value,
        )
    for position, paper_id in enumerate(snapshot.representative_paper_ids):
        session.add(
            TrendRepresentativePaperRow(
                snapshot_id=snapshot.id,
                topic_id=snapshot.topic_id,
                paper_id=paper_id,
                position=position,
                schema_version=1,
                created_at=snapshot.generated_at,
            )
        )


def _add_trend_metric(
    session: Session,
    snapshot: TrendSnapshot,
    *,
    key: str,
    metric_kind: str,
    label: str,
    change: TrendChange,
    entity_id: UUID | None = None,
    entity_type: str | None = None,
    relation_type: str | None = None,
    newly_appearing: bool = False,
    recurring: bool = False,
) -> None:
    session.add(
        TrendMetricRow(
            id=uuid5(snapshot.id, key),
            snapshot_id=snapshot.id,
            topic_id=snapshot.topic_id,
            metric_kind=metric_kind,
            entity_type=entity_type,
            entity_id=entity_id,
            relation_type=relation_type,
            label=label,
            current_count=change.current_count,
            preceding_count=change.preceding_count,
            absolute_change=change.absolute_change,
            denominator_count=change.denominator_count,
            relative_change=change.relative_change,
            growth_status=change.growth_status.value,
            newly_appearing=newly_appearing,
            recurring=recurring,
            schema_version=1,
            created_at=snapshot.generated_at,
        )
    )


def _lineage_from_session(session: Session, row: LineageSnapshotRow) -> LineageSnapshot:
    node_rows = tuple(
        session.scalars(
            select(LineageNodeRow)
            .where(LineageNodeRow.snapshot_id == row.id)
            .order_by(LineageNodeRow.position, LineageNodeRow.graph_entity_id)
        )
    )
    lineage_edge_rows = tuple(
        session.scalars(
            select(LineageEdgeRow)
            .where(LineageEdgeRow.snapshot_id == row.id)
            .order_by(LineageEdgeRow.position, LineageEdgeRow.graph_edge_id)
        )
    )
    edge_ids = tuple(item.graph_edge_id for item in lineage_edge_rows)
    graph_rows = {
        item.id: item
        for item in session.scalars(select(GraphEdgeRow).where(GraphEdgeRow.id.in_(edge_ids)))
    }
    if set(graph_rows) != set(edge_ids):
        raise RepositoryError("lineage references missing graph edges")
    edge_evidence = _edge_evidence_map(session, edge_ids)
    return LineageSnapshot(
        id=row.id,
        topic_id=row.topic_id,
        root_paper_id=row.root_paper_id,
        as_of_date=row.as_of_date,
        nodes=tuple(
            LineageNode(
                graph_entity_id=item.graph_entity_id,
                paper_id=item.paper_id,
                title=item.title,
                publication_date=item.publication_date,
                depth=item.depth,
            )
            for item in node_rows
        ),
        edges=tuple(
            _edge_from_row(
                graph_rows[item.graph_edge_id], edge_evidence.get(item.graph_edge_id, ())
            )
            for item in lineage_edge_rows
        ),
        permitted_relation_types=tuple(
            GraphRelationType(value) for value in row.permitted_relation_types
        ),
        max_depth=row.max_depth,
        max_nodes=row.max_nodes,
        max_edges=row.max_edges,
        truncated=row.truncated,
        explicit_predecessor_available=row.explicit_predecessor_available,
        verified_predecessor_available=row.verified_predecessor_available,
        corpus_scope=LineageCorpusScope(row.corpus_scope),
        limitations=tuple(row.limitations),
        lineage_version=row.lineage_version,
        generated_at=row.generated_at,
        schema_version=row.schema_version,
        pipeline_execution_id=row.pipeline_execution_id,
    )


def _bounded_lineage(
    snapshot: LineageSnapshot,
    *,
    max_depth: int,
    max_nodes: int,
    max_edges: int,
) -> LineageSnapshot:
    effective_depth = min(max_depth, snapshot.max_depth)
    effective_nodes = min(max_nodes, snapshot.max_nodes)
    effective_edges = min(max_edges, snapshot.max_edges)
    nodes_by_entity = {item.graph_entity_id: item for item in snapshot.nodes}
    root = next(item for item in snapshot.nodes if item.paper_id == snapshot.root_paper_id)
    root_entity_id = root.graph_entity_id
    permitted = tuple(
        item for item in snapshot.permitted_relation_types if item in _LINEAGE_PREDECESSOR_TYPES
    )
    eligible_edges = tuple(
        sorted(
            (
                item
                for item in snapshot.edges
                if item.relation_type in permitted
                and item.source_entity_id in nodes_by_entity
                and item.target_entity_id in nodes_by_entity
            ),
            key=lambda item: (
                nodes_by_entity[item.target_entity_id].publication_date is None,
                nodes_by_entity[item.target_entity_id].publication_date or date.max,
                str(nodes_by_entity[item.target_entity_id].paper_id),
                item.relation_type.value,
                str(item.id),
            ),
        )
    )
    outgoing: dict[UUID, list[GraphEdge]] = {}
    for edge in eligible_edges:
        outgoing.setdefault(edge.source_entity_id, []).append(edge)
    selected_depths = {root_entity_id: 0}
    selected_edges: list[GraphEdge] = []
    selected_edge_ids: set[UUID] = set()
    queue: deque[UUID] = deque((root_entity_id,))
    projection_truncated = False
    while queue:
        source_id = queue.popleft()
        depth = selected_depths[source_id]
        source_edges = outgoing.get(source_id, ())
        if depth >= effective_depth:
            if any(edge.target_entity_id not in selected_depths for edge in source_edges):
                projection_truncated = True
            continue
        for edge in source_edges:
            target_id = edge.target_entity_id
            if target_id in selected_depths:
                continue
            if len(selected_depths) >= effective_nodes or len(selected_edges) >= effective_edges:
                projection_truncated = True
                continue
            selected_depths[target_id] = depth + 1
            selected_edges.append(edge)
            selected_edge_ids.add(edge.id)
            queue.append(target_id)
    for edge in eligible_edges:
        if edge.id in selected_edge_ids:
            continue
        if (
            edge.source_entity_id not in selected_depths
            or edge.target_entity_id not in selected_depths
            or selected_depths[edge.target_entity_id] != selected_depths[edge.source_entity_id] + 1
        ):
            continue
        if len(selected_edges) >= effective_edges:
            projection_truncated = True
            break
        selected_edges.append(edge)
        selected_edge_ids.add(edge.id)
    selected_nodes = tuple(
        sorted(
            (
                LineageNode(
                    graph_entity_id=entity_id,
                    paper_id=nodes_by_entity[entity_id].paper_id,
                    title=nodes_by_entity[entity_id].title,
                    publication_date=nodes_by_entity[entity_id].publication_date,
                    depth=depth,
                )
                for entity_id, depth in selected_depths.items()
            ),
            key=lambda item: (
                item.publication_date is None,
                item.publication_date or date.max,
                str(item.paper_id),
            ),
        )
    )
    selected_edge_values = tuple(selected_edges)
    predecessor_edges = tuple(
        item
        for item in selected_edge_values
        if item.source_entity_id == root_entity_id
        and item.relation_type in _LINEAGE_PREDECESSOR_TYPES
    )
    explicit = any(
        item.provenance
        in (
            RelationProvenance.METADATA_EXPLICIT,
            RelationProvenance.TEXT_EXPLICIT,
            RelationProvenance.HUMAN_VERIFIED,
        )
        for item in predecessor_edges
    )
    verified = any(
        item.verification_status is VerificationStatus.HUMAN_VERIFIED
        or item.provenance is RelationProvenance.HUMAN_VERIFIED
        for item in predecessor_edges
    )
    requested_smaller = (
        effective_depth < snapshot.max_depth
        or effective_nodes < snapshot.max_nodes
        or effective_edges < snapshot.max_edges
    )
    limitations = tuple(
        dict.fromkeys(
            (
                *snapshot.limitations,
                *(
                    (
                        "This API projection was bounded below the persisted "
                        "lineage snapshot limits.",
                    )
                    if requested_smaller
                    else ()
                ),
            )
        )
    )
    return LineageSnapshot(
        id=stable_lineage_snapshot_id(
            snapshot.topic_id,
            snapshot.root_paper_id,
            snapshot.as_of_date,
            permitted_relation_types=tuple(item.value for item in permitted),
            max_depth=effective_depth,
            max_nodes=effective_nodes,
            max_edges=effective_edges,
            lineage_version=snapshot.lineage_version,
            pipeline_execution_id=snapshot.pipeline_execution_id,
        ),
        topic_id=snapshot.topic_id,
        root_paper_id=snapshot.root_paper_id,
        as_of_date=snapshot.as_of_date,
        nodes=selected_nodes,
        edges=selected_edge_values,
        permitted_relation_types=permitted,
        max_depth=effective_depth,
        max_nodes=effective_nodes,
        max_edges=effective_edges,
        truncated=(snapshot.truncated or projection_truncated),
        explicit_predecessor_available=explicit,
        verified_predecessor_available=verified,
        corpus_scope=snapshot.corpus_scope,
        limitations=limitations,
        lineage_version=snapshot.lineage_version,
        generated_at=snapshot.generated_at,
        schema_version=snapshot.schema_version,
        pipeline_execution_id=snapshot.pipeline_execution_id,
    )


def _upsert_lineage_snapshot(
    session: Session,
    snapshot: LineageSnapshot,
    *,
    publication_run_id: UUID,
) -> None:
    eligible_run_ids = select(DailyRunRow.id).where(
        DailyRunRow.topic_id == snapshot.topic_id,
        DailyRunRow.operation == RunOperation.PRODUCT_PUBLICATION.value,
        DailyRunRow.logical_date <= snapshot.as_of_date,
        or_(
            DailyRunRow.id == publication_run_id,
            (
                DailyRunRow.status.in_(_PUBLISHED_PRODUCT_STATUSES)
                & (DailyRunRow.pipeline_execution_mode != PipelineExecutionMode.SMOKE.value)
            ),
        ),
    )
    node_entity_ids = {item.graph_entity_id for item in snapshot.nodes}
    entity_rows = {
        row.id: row
        for row in session.scalars(
            select(GraphEntityRow).where(
                GraphEntityRow.topic_id == snapshot.topic_id,
                GraphEntityRow.id.in_(node_entity_ids),
                GraphEntityRow.entity_type == GraphEntityType.PAPER.value,
            )
        )
    }
    if set(entity_rows) != node_entity_ids or any(
        entity_rows[item.graph_entity_id].paper_id != item.paper_id for item in snapshot.nodes
    ):
        raise RepositoryError("lineage nodes do not match topic-owned paper entities")
    visible_node_ids = set(
        session.scalars(
            select(GraphEntityMentionRow.entity_id).where(
                GraphEntityMentionRow.entity_id.in_(node_entity_ids),
                GraphEntityMentionRow.publication_run_id.in_(eligible_run_ids),
            )
        )
    )
    if visible_node_ids != node_entity_ids:
        raise RepositoryError("lineage references an unpublished paper entity")
    edge_ids = {item.id for item in snapshot.edges}
    owned_edge_ids = set(
        session.scalars(
            select(GraphEdgeRow.id).where(
                GraphEdgeRow.topic_id == snapshot.topic_id,
                GraphEdgeRow.id.in_(edge_ids),
                GraphEdgeRow.publication_run_id.in_(eligible_run_ids),
            )
        )
    )
    if owned_edge_ids != edge_ids:
        raise RepositoryError("lineage references an ineligible graph edge")
    row = session.get(LineageSnapshotRow, snapshot.id)
    values = {
        "publication_run_id": publication_run_id,
        "pipeline_execution_id": snapshot.pipeline_execution_id,
        "topic_id": snapshot.topic_id,
        "root_paper_id": snapshot.root_paper_id,
        "as_of_date": snapshot.as_of_date,
        "permitted_relation_types": [item.value for item in snapshot.permitted_relation_types],
        "max_depth": snapshot.max_depth,
        "max_nodes": snapshot.max_nodes,
        "max_edges": snapshot.max_edges,
        "node_count": len(snapshot.nodes),
        "edge_count": len(snapshot.edges),
        "truncated": snapshot.truncated,
        "corpus_scope": snapshot.corpus_scope.value,
        "explicit_predecessor_available": snapshot.explicit_predecessor_available,
        "verified_predecessor_available": snapshot.verified_predecessor_available,
        "limitations": list(snapshot.limitations),
        "lineage_version": snapshot.lineage_version,
        "generated_at": snapshot.generated_at,
        "source": "deterministic_lineage",
        "schema_version": snapshot.schema_version,
        "created_at": snapshot.generated_at,
    }
    if row is None:
        session.add(LineageSnapshotRow(id=snapshot.id, **values))
    else:
        if (
            row.topic_id != snapshot.topic_id
            or row.root_paper_id != snapshot.root_paper_id
            or row.as_of_date != snapshot.as_of_date
            or row.publication_run_id != publication_run_id
            or row.pipeline_execution_id != snapshot.pipeline_execution_id
        ):
            raise RepositoryError("lineage stable ID has conflicting ownership")
        for key, value in values.items():
            setattr(row, key, value)
    session.flush()
    session.execute(delete(LineageEdgeRow).where(LineageEdgeRow.snapshot_id == snapshot.id))
    session.execute(delete(LineageNodeRow).where(LineageNodeRow.snapshot_id == snapshot.id))
    for position, node in enumerate(snapshot.nodes):
        session.add(
            LineageNodeRow(
                snapshot_id=snapshot.id,
                topic_id=snapshot.topic_id,
                graph_entity_id=node.graph_entity_id,
                paper_id=node.paper_id,
                title=node.title,
                depth=node.depth,
                position=position,
                publication_date=node.publication_date,
                schema_version=1,
                created_at=snapshot.generated_at,
            )
        )
    session.flush()
    for position, edge in enumerate(snapshot.edges):
        session.add(
            LineageEdgeRow(
                snapshot_id=snapshot.id,
                topic_id=snapshot.topic_id,
                graph_edge_id=edge.id,
                source_entity_id=edge.source_entity_id,
                target_entity_id=edge.target_entity_id,
                relation_type=edge.relation_type.value,
                provenance=edge.provenance.value,
                verification_status=edge.verification_status.value,
                position=position,
                uncertain=(
                    edge.provenance is RelationProvenance.LLM_INFERRED
                    and edge.verification_status is not VerificationStatus.HUMAN_VERIFIED
                ),
                schema_version=1,
                created_at=snapshot.generated_at,
            )
        )


def _select_publication_analysis(
    session: Session,
    *,
    run_row: DailyRunRow,
    paper_version_id: UUID,
) -> PaperAnalysisRow | None:
    if run_row.completed_at is None:
        raise RepositoryIntegrityError("publishable source run has no completion boundary")
    statement = select(PaperAnalysisRow).where(
        PaperAnalysisRow.paper_version_id == paper_version_id,
        PaperAnalysisRow.analysis_scope == run_row.analysis_scope,
        PaperAnalysisRow.generated_at <= run_row.completed_at,
        PaperAnalysisRow.created_at <= run_row.completed_at,
    )
    if run_row.pipeline_execution_id is not None:
        execution = session.get(PipelineExecutionRow, run_row.pipeline_execution_id)
        if execution is None:
            raise RepositoryIntegrityError("analysis run has no owning pipeline execution")
        contract = execution.execution_contract
        try:
            provider = contract["llm_provider"]
            configured_model = contract["llm_configured_model"]
            prompt_version = contract["analysis_prompt_version"]
            parser_name = contract["parser_name"]
            parser_version = contract["parser_version"]
        except KeyError as error:
            raise RepositoryIntegrityError(
                "pipeline execution is missing its analysis selection contract"
            ) from error
        statement = statement.where(
            PaperAnalysisRow.provider == provider,
            PaperAnalysisRow.configured_model == configured_model,
            PaperAnalysisRow.prompt_version == prompt_version,
            (
                PaperAnalysisRow.revision_id == run_row.pipeline_execution_id
                if run_row.pipeline_execution_mode == PipelineExecutionMode.REPROCESS.value
                else PaperAnalysisRow.revision_id.is_(None)
            ),
        )
        if run_row.analysis_scope == AnalysisScope.FULL_TEXT.value:
            statement = statement.join(
                ParsedPaperRow,
                ParsedPaperRow.id == PaperAnalysisRow.parsed_paper_id,
            ).where(
                ParsedPaperRow.parser_name == parser_name,
                ParsedPaperRow.parser_version == parser_version,
            )
    return session.scalars(
        statement.order_by(PaperAnalysisRow.generated_at.desc(), PaperAnalysisRow.id.desc()).limit(
            1
        )
    ).one_or_none()


def _product_failure_from_item_row(row: RunItemRow) -> ProductFailureInput:
    if (
        row.failed_stage is None
        or row.error_code is None
        or row.retryable is None
        or row.error_detail is None
    ):
        raise RepositoryIntegrityError("failed product item lacks failure metadata")
    return ProductFailureInput(
        paper_id=row.paper_id,
        paper_version_id=row.paper_version_id,
        stage=PaperStage(row.stage),
        failed_stage=PaperStage(row.failed_stage),
        error_code=row.error_code,
        retryable=row.retryable,
        error_detail=row.error_detail,
    )


def _product_failures_by_version(
    failures: tuple[ProductFailureInput, ...],
) -> dict[UUID, ProductFailureInput]:
    by_version = {failure.paper_version_id: failure for failure in failures}
    if len(by_version) != len(failures):
        raise RepositoryError("product upstream failures must identify unique paper versions")
    return by_version


def _require_matching_product_failure(
    row: RunItemRow,
    failure: ProductFailureInput,
) -> None:
    if (
        row.paper_id != failure.paper_id
        or row.paper_version_id != failure.paper_version_id
        or row.stage != failure.stage.value
        or row.failed_stage != failure.failed_stage.value
        or row.error_code != failure.error_code
        or row.retryable != failure.retryable
        or row.error_detail != failure.error_detail
    ):
        raise RepositoryError("product upstream failure conflicts with its source run item")


def _delete_product_artifacts(session: Session, run_row: DailyRunRow) -> None:
    mention_entity_ids = set(
        session.scalars(
            select(GraphEntityMentionRow.entity_id).where(
                GraphEntityMentionRow.publication_run_id == run_row.id
            )
        )
    )
    edge_entity_ids = {
        entity_id
        for source_id, target_id in session.execute(
            select(
                GraphEdgeRow.source_entity_id,
                GraphEdgeRow.target_entity_id,
            ).where(GraphEdgeRow.publication_run_id == run_row.id)
        )
        for entity_id in (source_id, target_id)
    }
    candidate_entity_ids = mention_entity_ids | edge_entity_ids
    session.execute(
        delete(TrendSnapshotRow).where(TrendSnapshotRow.publication_run_id == run_row.id)
    )
    session.execute(
        delete(LineageSnapshotRow).where(LineageSnapshotRow.publication_run_id == run_row.id)
    )
    session.execute(delete(GraphEdgeRow).where(GraphEdgeRow.publication_run_id == run_row.id))
    session.execute(
        delete(GraphEntityMentionRow).where(GraphEntityMentionRow.publication_run_id == run_row.id)
    )
    if candidate_entity_ids:
        remaining_mention = (
            select(GraphEntityMentionRow.id)
            .where(GraphEntityMentionRow.entity_id == GraphEntityRow.id)
            .exists()
        )
        remaining_edge = (
            select(GraphEdgeRow.id)
            .where(
                or_(
                    GraphEdgeRow.source_entity_id == GraphEntityRow.id,
                    GraphEdgeRow.target_entity_id == GraphEntityRow.id,
                )
            )
            .exists()
        )
        session.execute(
            delete(GraphEntityRow).where(
                GraphEntityRow.topic_id == run_row.topic_id,
                GraphEntityRow.id.in_(candidate_entity_ids),
                ~remaining_mention,
                ~remaining_edge,
            )
        )


def _insert_normalized_report(session: Session, report: Report) -> None:
    if session.get(ReportRow, report.id) is not None:
        raise RepositoryError("report stable identity is already persisted")
    if session.get(TopicRow, report.topic_id) is None:
        raise RepositoryError("report topic is missing")
    if report.run_id is not None:
        run_row = session.get(DailyRunRow, report.run_id)
        if run_row is None or run_row.topic_id != report.topic_id:
            raise RepositoryError("report run has the wrong topic owner")
    version_ids = {item.paper_version_id for item in (*report.failures, *report.highlighted_papers)}
    versions = {
        row.id: row
        for row in session.scalars(
            select(PaperVersionRow).where(PaperVersionRow.id.in_(version_ids))
        )
    }
    if set(versions) != version_ids or any(
        versions[item.paper_version_id].paper_id != item.paper_id
        for item in (*report.failures, *report.highlighted_papers)
    ):
        raise RepositoryError("report paper-version ownership is invalid")
    entity_ids = {item.graph_entity_id for item in report.major_entities}
    if (
        set(
            session.scalars(
                select(GraphEntityRow.id).where(
                    GraphEntityRow.topic_id == report.topic_id,
                    GraphEntityRow.id.in_(entity_ids),
                )
            )
        )
        != entity_ids
    ):
        raise RepositoryError("report graph-entity ownership is invalid")
    comparison_ids = {item.comparison_id for item in report.notable_comparisons}
    comparison_rows = {
        row.id: row
        for row in session.scalars(
            select(ComparisonRow).where(ComparisonRow.id.in_(comparison_ids))
        )
    }
    if set(comparison_rows) != comparison_ids:
        raise RepositoryError("report references a missing comparison")
    if any(
        (
            item.source_paper_id,
            item.source_paper_version_id,
            item.target_paper_id,
            item.target_paper_version_id,
        )
        != (
            comparison_rows[item.comparison_id].source_paper_id,
            comparison_rows[item.comparison_id].source_paper_version_id,
            comparison_rows[item.comparison_id].target_paper_id,
            comparison_rows[item.comparison_id].target_paper_version_id,
        )
        for item in report.notable_comparisons
    ):
        raise RepositoryError("report comparison highlight has the wrong paper ownership")
    trend_ids = set(report.trend_snapshot_ids)
    if (
        set(
            session.scalars(
                select(TrendSnapshotRow.id).where(
                    TrendSnapshotRow.topic_id == report.topic_id,
                    TrendSnapshotRow.id.in_(trend_ids),
                )
            )
        )
        != trend_ids
    ):
        raise RepositoryError("report trend-snapshot ownership is invalid")
    trend_rows = {
        row.id: row
        for row in session.scalars(
            select(TrendSnapshotRow).where(TrendSnapshotRow.id.in_(trend_ids))
        )
    }
    if report.run_id is not None and any(
        row.publication_run_id != report.run_id for row in trend_rows.values()
    ):
        raise RepositoryError("daily report trend snapshots belong to another product run")
    published_run_ids: set[UUID] | None = None
    if report.run_id is None:
        published_run_ids = set(session.scalars(_published_product_run_ids()))
        if any(row.publication_run_id not in published_run_ids for row in trend_rows.values()):
            raise RepositoryError("periodic report references an unpublished trend snapshot")
    if report.major_entities:
        if not trend_rows:
            raise RepositoryError("report entity highlights require a linked trend snapshot")
        primary_trend = min(
            trend_rows.values(),
            key=lambda row: (-row.as_of_date.toordinal(), row.window_size_days, str(row.id)),
        )
        highlighted_entity_ids = {item.graph_entity_id for item in report.major_entities}
        entity_metrics = {
            row.entity_id: row
            for row in session.scalars(
                select(TrendMetricRow).where(
                    TrendMetricRow.snapshot_id == primary_trend.id,
                    TrendMetricRow.metric_kind == "ENTITY",
                    TrendMetricRow.entity_id.in_(highlighted_entity_ids),
                )
            )
            if row.entity_id is not None
        }
        if set(entity_metrics) != highlighted_entity_ids or any(
            (
                entity_metrics[item.graph_entity_id].entity_type,
                entity_metrics[item.graph_entity_id].label,
                entity_metrics[item.graph_entity_id].current_count,
            )
            != (item.entity_type, item.label, item.distinct_paper_count)
            for item in report.major_entities
        ):
            raise RepositoryError("report entity highlights do not match the linked primary trend")
    lineage_ids = {item.lineage_snapshot_id for item in report.lineage_highlights}
    lineage_rows = {
        row.id: row
        for row in session.scalars(
            select(LineageSnapshotRow).where(
                LineageSnapshotRow.topic_id == report.topic_id,
                LineageSnapshotRow.id.in_(lineage_ids),
            )
        )
    }
    if set(lineage_rows) != lineage_ids or any(
        lineage_rows[item.lineage_snapshot_id].root_paper_id != item.root_paper_id
        for item in report.lineage_highlights
    ):
        raise RepositoryError("report lineage-snapshot ownership is invalid")
    if report.run_id is not None and any(
        row.publication_run_id != report.run_id for row in lineage_rows.values()
    ):
        raise RepositoryError("daily report lineage snapshots belong to another product run")
    if report.run_id is None:
        assert published_run_ids is not None
        if any(row.publication_run_id not in published_run_ids for row in lineage_rows.values()):
            raise RepositoryError("periodic report references an unpublished lineage snapshot")
    evidence_ids = set(report.evidence_ids)
    nested_evidence_ids = (
        {evidence_id for section in report.sections for evidence_id in section.evidence_ids}
        | {evidence_id for item in report.highlighted_papers for evidence_id in item.evidence_ids}
        | {evidence_id for item in report.notable_comparisons for evidence_id in item.evidence_ids}
    )
    if not nested_evidence_ids.issubset(evidence_ids):
        raise RepositoryError("report context evidence is outside the report evidence closure")
    evidence_rows = {
        row.id: row
        for row in session.scalars(select(EvidenceRow).where(EvidenceRow.id.in_(evidence_ids)))
    }
    if set(evidence_rows) != evidence_ids:
        raise RepositoryError("report references missing evidence")
    if any(
        row.verification_status == VerificationStatus.REJECTED.value
        for row in evidence_rows.values()
    ):
        raise RepositoryError("rejected evidence cannot ground a report")
    for item in report.highlighted_papers:
        if any(
            (
                evidence_rows[evidence_id].paper_id,
                evidence_rows[evidence_id].paper_version_id,
            )
            != (item.paper_id, item.paper_version_id)
            for evidence_id in item.evidence_ids
        ):
            raise RepositoryError(
                "report paper-highlight evidence has the wrong paper-version owner"
            )
    comparison_evidence_rows = tuple(
        session.execute(
            select(
                ComparisonEvidenceLinkRow.comparison_id,
                ComparisonEvidenceLinkRow.evidence_id,
            ).where(ComparisonEvidenceLinkRow.comparison_id.in_(comparison_ids))
        )
    )
    relation_evidence_rows = tuple(
        session.execute(
            select(
                RelationEvidenceLinkRow.comparison_id,
                RelationEvidenceLinkRow.evidence_id,
            ).where(RelationEvidenceLinkRow.comparison_id.in_(comparison_ids))
        )
    )
    allowed_comparison_evidence: dict[UUID, set[UUID]] = {}
    for comparison_id, evidence_id in (*comparison_evidence_rows, *relation_evidence_rows):
        allowed_comparison_evidence.setdefault(comparison_id, set()).add(evidence_id)
    for item in report.notable_comparisons:
        allowed_owners = {
            (item.source_paper_id, item.source_paper_version_id),
            (item.target_paper_id, item.target_paper_version_id),
        }
        if any(
            evidence_id not in allowed_comparison_evidence.get(item.comparison_id, set())
            or (
                evidence_rows[evidence_id].paper_id,
                evidence_rows[evidence_id].paper_version_id,
            )
            not in allowed_owners
            for evidence_id in item.evidence_ids
        ):
            raise RepositoryError(
                "report comparison-highlight evidence is outside its persisted comparison"
            )
    usage = report.usage
    session.add(
        ReportRow(
            id=report.id,
            run_id=report.run_id,
            topic_id=report.topic_id,
            logical_date=report.logical_date,
            report_type=report.report_type.value,
            period_start=report.period_start or report.logical_date,
            period_end=report.period_end or report.logical_date,
            status=report.status.value,
            title=report.title,
            summary=report.summary,
            source=report.source,
            generated_at=report.generated_at,
            retrieved_count=report.counts.retrieved,
            selected_count=report.counts.selected,
            processed_count=report.counts.processed,
            completed_count=report.counts.completed,
            failed_count=report.counts.failed,
            graph_entity_count=report.graph_changes.entity_count,
            graph_edge_count=report.graph_changes.edge_count,
            new_graph_entity_count=report.graph_changes.new_entity_count,
            inferred_graph_edge_count=report.graph_changes.inferred_edge_count,
            limitations=list(report.limitations),
            missing_sections=list(report.missing_sections),
            narrative_mode=report.narrative_mode.value,
            provider=report.provider,
            configured_model=report.configured_model,
            model_version=report.model_version,
            prompt_version=report.prompt_version,
            prompt_tokens=None if usage is None else usage.prompt_tokens,
            completion_tokens=None if usage is None else usage.completion_tokens,
            total_tokens=None if usage is None else usage.total_tokens,
            call_count=None if usage is None else usage.call_count,
            duration_ms=None if usage is None else usage.duration_ms,
            estimated_cost_usd=None if usage is None else usage.estimated_cost_usd,
            verification_status=report.verification_status.value,
            schema_version=report.schema_version,
            created_at=report.created_at,
        )
    )
    session.flush()
    for item in report.failures:
        session.add(
            ReportFailureRow(
                id=item.id,
                report_id=report.id,
                paper_id=item.paper_id,
                paper_version_id=item.paper_version_id,
                failed_stage=item.failed_stage.value,
                error_code=item.error_code,
                retryable=item.retryable,
                error_detail=item.error_detail,
                schema_version=item.schema_version,
                created_at=item.created_at,
            )
        )
    for position, item in enumerate(report.sections):
        session.add(
            ReportSectionRow(
                id=item.id,
                report_id=report.id,
                kind=item.kind.value,
                position=position,
                narrative=item.narrative,
                schema_version=item.schema_version,
                created_at=item.created_at,
            )
        )
    paper_row_ids: dict[UUID, UUID] = {}
    for position, item in enumerate(report.highlighted_papers):
        row_id = uuid5(report.id, f"paper-highlight:{item.paper_version_id}")
        paper_row_ids[item.paper_version_id] = row_id
        session.add(
            ReportPaperHighlightRow(
                id=row_id,
                report_id=report.id,
                paper_id=item.paper_id,
                paper_version_id=item.paper_version_id,
                position=position,
                title=item.title,
                reason=item.reason,
                schema_version=1,
                created_at=report.created_at,
            )
        )
    for position, item in enumerate(report.major_entities):
        session.add(
            ReportEntityHighlightRow(
                id=uuid5(report.id, f"entity-highlight:{item.graph_entity_id}"),
                report_id=report.id,
                topic_id=report.topic_id,
                graph_entity_id=item.graph_entity_id,
                position=position,
                entity_type=item.entity_type,
                label=item.label,
                distinct_paper_count=item.distinct_paper_count,
                schema_version=1,
                created_at=report.created_at,
            )
        )
    comparison_row_ids: dict[UUID, UUID] = {}
    for position, item in enumerate(report.notable_comparisons):
        row_id = uuid5(report.id, f"comparison-highlight:{item.comparison_id}")
        comparison_row_ids[item.comparison_id] = row_id
        session.add(
            ReportComparisonHighlightRow(
                id=row_id,
                report_id=report.id,
                comparison_id=item.comparison_id,
                position=position,
                summary=item.summary,
                comparability_status=item.comparability_status,
                schema_version=1,
                created_at=report.created_at,
            )
        )
    for position, snapshot_id in enumerate(report.trend_snapshot_ids):
        session.add(
            ReportTrendLinkRow(
                id=uuid5(report.id, f"trend:{snapshot_id}"),
                report_id=report.id,
                snapshot_id=snapshot_id,
                topic_id=report.topic_id,
                position=position,
                schema_version=1,
                created_at=report.created_at,
            )
        )
    for position, item in enumerate(report.lineage_highlights):
        session.add(
            ReportLineageHighlightRow(
                id=uuid5(report.id, f"lineage:{item.lineage_snapshot_id}"),
                report_id=report.id,
                topic_id=report.topic_id,
                lineage_snapshot_id=item.lineage_snapshot_id,
                root_paper_id=item.root_paper_id,
                position=position,
                summary=item.summary,
                uncertain=item.uncertain,
                schema_version=1,
                created_at=report.created_at,
            )
        )
    session.flush()
    for evidence_id in report.evidence_ids:
        _add_report_evidence_link(
            session,
            report,
            evidence_rows[evidence_id],
            context_type="REPORT",
        )
    for section in report.sections:
        for evidence_id in section.evidence_ids:
            _add_report_evidence_link(
                session,
                report,
                evidence_rows[evidence_id],
                context_type="SECTION",
                report_section_id=section.id,
            )
    for item in report.highlighted_papers:
        for evidence_id in item.evidence_ids:
            _add_report_evidence_link(
                session,
                report,
                evidence_rows[evidence_id],
                context_type="PAPER_HIGHLIGHT",
                paper_highlight_id=paper_row_ids[item.paper_version_id],
            )
    for item in report.notable_comparisons:
        for evidence_id in item.evidence_ids:
            _add_report_evidence_link(
                session,
                report,
                evidence_rows[evidence_id],
                context_type="COMPARISON_HIGHLIGHT",
                comparison_highlight_id=comparison_row_ids[item.comparison_id],
            )


def _add_report_evidence_link(
    session: Session,
    report: Report,
    evidence: EvidenceRow,
    *,
    context_type: str,
    report_section_id: UUID | None = None,
    paper_highlight_id: UUID | None = None,
    comparison_highlight_id: UUID | None = None,
) -> None:
    context_id = report_section_id or paper_highlight_id or comparison_highlight_id or report.id
    session.add(
        ReportEvidenceLinkRow(
            id=uuid5(report.id, f"evidence:{context_type}:{context_id}:{evidence.id}"),
            report_id=report.id,
            evidence_id=evidence.id,
            evidence_paper_id=evidence.paper_id,
            evidence_paper_version_id=evidence.paper_version_id,
            evidence_analysis_id=evidence.analysis_id,
            context_type=context_type,
            report_section_id=report_section_id,
            paper_highlight_id=paper_highlight_id,
            comparison_highlight_id=comparison_highlight_id,
            schema_version=1,
            created_at=report.created_at,
        )
    )
