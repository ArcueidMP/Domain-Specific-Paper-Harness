"""PostgreSQL persistence for M3 historical search, retrieval, and comparison."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import String, and_, case, delete, exists, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.base import Executable
from sqlalchemy.sql.elements import ColumnElement

from paper_harness.application.read_models import (
    ComparisonDetail,
    ComparisonEvidenceReference,
    HistoricalRetrievalMatch,
    RelatedWorkDetail,
    RelatedWorkItem,
    SearchSessionDetail,
)
from paper_harness.domain.analysis import (
    AnalysisScope,
    EvidenceType,
    ModelUsage,
    VerificationStatus,
)
from paper_harness.domain.historical import (
    BackfillStatus,
    CandidateOrigin,
    CandidateScoreComponents,
    ComparabilityStatus,
    Comparison,
    ComparisonBundle,
    ComparisonDimension,
    ComparisonDimensionName,
    ComparisonEvidenceInput,
    ComparisonPaperInput,
    ComparisonTargetDecision,
    ExternalPaperStub,
    GeneratedCrawlerPlan,
    HistoricalBackfillRun,
    HistoricalCorpusEntry,
    PaperRelation,
    PaperRelationType,
    RelationProvenance,
    ScientificEmbedding,
    SearchAction,
    SearchActionStatus,
    SearchCandidate,
    SearchCandidateDiscovery,
    SearchLimits,
    SearchModelProvenance,
    SearchSession,
    SearchSessionStatus,
    SearchStopReason,
    SearchTool,
    SelectionDecision,
)
from paper_harness.domain.identity import (
    stable_candidate_discovery_id,
    stable_embedding_id,
    stable_historical_corpus_entry_id,
    stable_search_candidate_id,
)
from paper_harness.domain.models import (
    PaperStage,
    PipelineExecutionMode,
    RunItemStatus,
    RunOperation,
    RunStatus,
)
from paper_harness.ports.repository import (
    ExternalPaperIdentifierConflictError,
    RepositoryIntegrityError,
    RepositoryUnavailableError,
)

from .models import (
    ComparisonDimensionRow,
    ComparisonEvidenceLinkRow,
    ComparisonRow,
    DailyRunRow,
    EvidenceRow,
    ExternalPaperIdentifierRow,
    ExternalPaperStubRow,
    HistoricalBackfillRunRow,
    HistoricalCorpusEntryRow,
    PaperAnalysisRow,
    PaperRelationRow,
    PaperRow,
    PaperVersionRow,
    ParsedPaperRow,
    ProductRunComparisonInputRow,
    RelationEvidenceLinkRow,
    RunItemRow,
    ScientificEmbeddingRow,
    SearchActionRow,
    SearchCandidateDiscoveryRow,
    SearchCandidateRow,
    SearchSessionRow,
    TopicRow,
)


class HistoricalRepositoryMixin:
    """Focused M3 methods mixed into the synchronous PostgreSQL repository."""

    _sessions: sessionmaker[Session]

    def start_historical_backfill(self, run: HistoricalBackfillRun) -> HistoricalBackfillRun:
        if run.status is not BackfillStatus.RUNNING:
            raise RepositoryIntegrityError("a historical backfill must start in RUNNING state")
        try:
            with self._sessions.begin() as session:
                statement = (
                    insert(HistoricalBackfillRunRow)
                    .values(**_backfill_values(run))
                    .on_conflict_do_nothing(
                        index_elements=[
                            HistoricalBackfillRunRow.topic_id,
                            HistoricalBackfillRunRow.window_from,
                            HistoricalBackfillRunRow.window_to,
                        ]
                    )
                )
                session.execute(statement)
                row = session.scalars(
                    select(HistoricalBackfillRunRow)
                    .where(
                        HistoricalBackfillRunRow.topic_id == run.topic_id,
                        HistoricalBackfillRunRow.window_from == run.window_from,
                        HistoricalBackfillRunRow.window_to == run.window_to,
                    )
                    .with_for_update()
                ).one()
                stored = _backfill_from_row(row)
                if stored.status is BackfillStatus.FAILED:
                    for key, value in _backfill_values(run).items():
                        setattr(row, key, value)
                    session.flush()
                return _backfill_from_row(row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL historical backfill persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError("PostgreSQL rejected the historical backfill") from error

    def get_historical_backfill(
        self, topic_id: UUID, window_from: date, window_to: date
    ) -> HistoricalBackfillRun | None:
        try:
            with self._sessions() as session:
                row = session.scalars(
                    select(HistoricalBackfillRunRow).where(
                        HistoricalBackfillRunRow.topic_id == topic_id,
                        HistoricalBackfillRunRow.window_from == window_from,
                        HistoricalBackfillRunRow.window_to == window_to,
                    )
                ).one_or_none()
                return None if row is None else _backfill_from_row(row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL historical backfill read is unavailable"
            ) from error

    def persist_historical_backfill_page(
        self,
        run_id: UUID,
        *,
        expected_query_index: int,
        next_query_index: int,
        papers: tuple[ExternalPaperStub, ...],
        entries: tuple[HistoricalCorpusEntry, ...],
        embeddings: tuple[ScientificEmbedding, ...],
        discovered_count: int,
        persisted_count: int,
        persisted_at: datetime,
    ) -> HistoricalBackfillRun:
        if next_query_index <= expected_query_index:
            raise RepositoryIntegrityError("historical backfill cursor must advance")
        paper_ids = {paper.id for paper in papers}
        entry_paper_ids = {entry.external_paper_id for entry in entries}
        if (
            len(paper_ids) != len(papers)
            or len(entry_paper_ids) != len(entries)
            or paper_ids != entry_paper_ids
        ):
            raise RepositoryIntegrityError(
                "a historical page must pair each paper with one corpus entry"
            )
        actual_embedding_owners = {embedding.external_paper_id for embedding in embeddings}
        if len(actual_embedding_owners) != len(embeddings) or any(
            embedding.external_paper_id not in paper_ids or embedding.paper_version_id is not None
            for embedding in embeddings
        ):
            raise RepositoryIntegrityError(
                "historical page embeddings must uniquely belong to papers in the page"
            )
        try:
            with self._sessions.begin() as session:
                run_row = session.scalars(
                    select(HistoricalBackfillRunRow)
                    .where(HistoricalBackfillRunRow.id == run_id)
                    .with_for_update()
                ).one_or_none()
                if run_row is None:
                    raise RepositoryIntegrityError("historical backfill does not exist")
                if run_row.status != BackfillStatus.RUNNING.value:
                    raise RepositoryIntegrityError("historical backfill is no longer running")
                if any(
                    embedding.model_identifier != run_row.embedding_model_identifier
                    or embedding.model_revision != run_row.embedding_model_revision
                    or embedding.tokenizer_identifier != run_row.embedding_tokenizer_identifier
                    or embedding.tokenizer_revision != run_row.embedding_tokenizer_revision
                    or embedding.dimension != run_row.embedding_dimension
                    or embedding.preprocessing_contract != run_row.embedding_preprocessing_contract
                    or embedding.model_provenance != run_row.embedding_model_provenance
                    or embedding.source != run_row.embedding_source
                    for embedding in embeddings
                ):
                    raise RepositoryIntegrityError(
                        "historical page embeddings do not match the backfill model provenance"
                    )
                if run_row.next_query_index == next_query_index:
                    if (
                        run_row.discovered_count != discovered_count
                        or run_row.persisted_count != persisted_count
                    ):
                        raise RepositoryIntegrityError(
                            "retried historical page conflicts with stored counts"
                        )
                    return _backfill_from_row(run_row)
                if run_row.next_query_index != expected_query_index:
                    raise RepositoryIntegrityError("historical backfill cursor changed")
                if (
                    discovered_count < run_row.discovered_count
                    or persisted_count < run_row.persisted_count
                ):
                    raise RepositoryIntegrityError(
                        "historical backfill counts cannot move backwards"
                    )
                paper_by_id = {paper.id: paper for paper in papers}
                for paper in papers:
                    _upsert_external_paper(session, paper)
                for entry in entries:
                    if entry.topic_id != run_row.topic_id:
                        raise RepositoryIntegrityError(
                            "historical corpus entry belongs to another topic"
                        )
                    paper = paper_by_id[entry.external_paper_id]
                    local_paper_id, local_version_id = _resolve_local_identity(
                        session,
                        arxiv_id=paper.arxiv_id,
                        paper_id=entry.local_paper_id,
                        paper_version_id=entry.local_paper_version_id,
                    )
                    _upsert_corpus_entry(
                        session,
                        entry,
                        local_paper_id=local_paper_id,
                        local_paper_version_id=local_version_id,
                        persisted_at=persisted_at,
                    )
                _upsert_embeddings(session, embeddings)
                run_row.next_query_index = next_query_index
                run_row.discovered_count = discovered_count
                run_row.persisted_count = persisted_count
                session.flush()
                return _backfill_from_row(run_row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL historical page persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected the historical page, embedding, or cursor"
            ) from error

    def finalize_historical_backfill(
        self,
        run_id: UUID,
        *,
        representatives: tuple[tuple[UUID, int], ...],
        completed_at: datetime,
    ) -> HistoricalBackfillRun:
        ranks = [rank for _paper_id, rank in representatives]
        if ranks != list(range(1, len(ranks) + 1)) or len(
            {paper_id for paper_id, _rank in representatives}
        ) != len(representatives):
            raise RepositoryIntegrityError(
                "historical representatives must be uniquely and consecutively ranked"
            )
        try:
            with self._sessions.begin() as session:
                run_row = session.scalars(
                    select(HistoricalBackfillRunRow)
                    .where(HistoricalBackfillRunRow.id == run_id)
                    .with_for_update()
                ).one_or_none()
                if run_row is None or run_row.status != BackfillStatus.RUNNING.value:
                    raise RepositoryIntegrityError("historical backfill is not running")
                if run_row.next_query_index != len(run_row.query_plan):
                    raise RepositoryIntegrityError(
                        "historical backfill cannot complete before its query plan is exhausted"
                    )
                representative_limit = session.scalar(
                    select(TopicRow.representative_full_text_count).where(
                        TopicRow.id == run_row.topic_id
                    )
                )
                if representative_limit is None or len(representatives) > representative_limit:
                    raise RepositoryIntegrityError(
                        "historical representative count exceeds the topic limit"
                    )
                corpus_rows = tuple(
                    session.scalars(
                        select(HistoricalCorpusEntryRow)
                        .where(HistoricalCorpusEntryRow.topic_id == run_row.topic_id)
                        .with_for_update()
                    )
                )
                by_external_id = {row.external_paper_id: row for row in corpus_rows}
                if any(paper_id not in by_external_id for paper_id, _rank in representatives):
                    raise RepositoryIntegrityError(
                        "historical representative is not in the topic corpus"
                    )
                representative_ids = {paper_id for paper_id, _rank in representatives}
                full_text_ids = set(
                    session.scalars(
                        select(ExternalPaperStubRow.id).where(
                            ExternalPaperStubRow.id.in_(representative_ids),
                            ExternalPaperStubRow.full_text_available.is_(True),
                        )
                    )
                )
                if full_text_ids != representative_ids:
                    raise RepositoryIntegrityError(
                        "historical representatives must have arXiv full text"
                    )
                for row in corpus_rows:
                    row.representative_rank = None
                session.flush()
                for paper_id, rank in representatives:
                    by_external_id[paper_id].representative_rank = rank
                run_row.status = BackfillStatus.COMPLETE.value
                run_row.representative_count = len(representatives)
                run_row.completed_at = completed_at
                session.flush()
                return _backfill_from_row(run_row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL historical finalization is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected historical representatives"
            ) from error

    def fail_historical_backfill(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> HistoricalBackfillRun:
        try:
            with self._sessions.begin() as session:
                row = session.scalars(
                    select(HistoricalBackfillRunRow)
                    .where(HistoricalBackfillRunRow.id == run_id)
                    .with_for_update()
                ).one_or_none()
                if row is None or row.status != BackfillStatus.RUNNING.value:
                    raise RepositoryIntegrityError("historical backfill is not running")
                row.status = BackfillStatus.FAILED.value
                row.completed_at = completed_at
                row.error_code = error_code
                row.error_detail = error_detail
                session.flush()
                return _backfill_from_row(row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL historical failure persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected historical failure metadata"
            ) from error

    def start_search_session(self, session: SearchSession) -> SearchSession:
        if session.status is not SearchSessionStatus.RUNNING:
            raise RepositoryIntegrityError("a search session must start in RUNNING state")
        try:
            with self._sessions.begin() as database_session:
                existing = database_session.get(SearchSessionRow, session.id)
                if existing is None:
                    row = SearchSessionRow(**_search_session_values(session))
                    database_session.add(row)
                    database_session.flush()
                else:
                    row = existing
                    if _search_session_from_row(row) != session:
                        raise RepositoryIntegrityError(
                            "stable search session ID conflicts with stored data"
                        )
                return _search_session_from_row(row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL search session persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError("PostgreSQL rejected the search session") from error

    def persist_search_crawler_plan(
        self, session_id: UUID, plan: GeneratedCrawlerPlan
    ) -> SearchSession:
        try:
            with self._sessions.begin() as session:
                row = _require_running_search_session(session, session_id, for_update=True)
                if len(plan.queries) > row.max_queries:
                    raise RepositoryIntegrityError(
                        "crawler plan exceeds the persisted session query limit"
                    )
                planned = replace(
                    _search_session_from_row(row),
                    provider=plan.provider,
                    configured_model=plan.configured_model,
                    model_version=plan.model_version,
                    prompt_version=plan.prompt_version,
                    usage=plan.usage,
                    crawler_queries=plan.queries,
                    crawler_use_recommendations=plan.use_recommendations,
                    crawler_expand_references=plan.expand_references,
                    crawler_expand_citations=plan.expand_citations,
                    crawler_decision_reason=plan.decision_reason,
                    crawler_generated_at=plan.generated_at,
                )
                current = _search_session_from_row(row)
                if current.crawler_queries is not None:
                    if current != planned:
                        raise RepositoryIntegrityError(
                            "crawler plan conflicts with persisted search provenance"
                        )
                    return current
                for name, value in _search_session_values(planned).items():
                    setattr(row, name, value)
                session.flush()
                return _search_session_from_row(row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL crawler plan persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError("PostgreSQL rejected the crawler plan") from error

    def get_search_session(self, session_id: UUID) -> SearchSessionDetail | None:
        try:
            with self._sessions() as session:
                session_row = session.get(SearchSessionRow, session_id)
                if session_row is None:
                    return None
                action_rows = tuple(
                    session.scalars(
                        select(SearchActionRow)
                        .where(SearchActionRow.session_id == session_id)
                        .order_by(SearchActionRow.step, SearchActionRow.id)
                    )
                )
                candidate_rows = tuple(
                    session.scalars(
                        select(SearchCandidateRow)
                        .where(SearchCandidateRow.session_id == session_id)
                        .order_by(SearchCandidateRow.rank, SearchCandidateRow.id)
                    )
                )
                candidate_ids = [row.id for row in candidate_rows]
                discovery_rows = (
                    tuple(
                        session.scalars(
                            select(SearchCandidateDiscoveryRow)
                            .where(SearchCandidateDiscoveryRow.candidate_id.in_(candidate_ids))
                            .order_by(
                                SearchCandidateDiscoveryRow.discovered_at,
                                SearchCandidateDiscoveryRow.id,
                            )
                        )
                    )
                    if candidate_ids
                    else ()
                )
                return SearchSessionDetail(
                    session=_search_session_from_row(session_row),
                    actions=tuple(_search_action_from_row(row) for row in action_rows),
                    candidates=tuple(_candidate_from_row(row) for row in candidate_rows),
                    discoveries=tuple(_discovery_from_row(row) for row in discovery_rows),
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL search session read is unavailable"
            ) from error

    def restart_search_session(self, session_id: UUID, *, restarted_at: datetime) -> SearchSession:
        try:
            with self._sessions.begin() as session:
                row = session.scalars(
                    select(SearchSessionRow)
                    .where(SearchSessionRow.id == session_id)
                    .with_for_update()
                ).one_or_none()
                if row is None or row.status not in (
                    SearchSessionStatus.RUNNING.value,
                    SearchSessionStatus.FAILED.value,
                ):
                    raise RepositoryIntegrityError("only an incomplete search session may restart")
                comparison_exists = session.scalar(
                    select(exists().where(ComparisonRow.search_session_id == session_id))
                )
                if comparison_exists:
                    raise RepositoryIntegrityError("a referenced search session cannot restart")
                session.execute(
                    delete(SearchCandidateDiscoveryRow).where(
                        SearchCandidateDiscoveryRow.session_id == session_id
                    )
                )
                session.execute(
                    delete(SearchCandidateRow).where(SearchCandidateRow.session_id == session_id)
                )
                session.execute(
                    delete(SearchActionRow).where(SearchActionRow.session_id == session_id)
                )
                row.status = SearchSessionStatus.RUNNING.value
                row.started_at = restarted_at
                row.completed_at = None
                row.stop_reason = None
                row.error_code = None
                row.error_detail = None
                row.provider = None
                row.configured_model = None
                row.model_version = None
                row.prompt_version = None
                row.prompt_tokens = None
                row.completion_tokens = None
                row.total_tokens = None
                row.call_count = None
                row.model_duration_ms = None
                row.estimated_cost_usd = None
                row.crawler_queries = None
                row.crawler_use_recommendations = None
                row.crawler_expand_references = None
                row.crawler_expand_citations = None
                row.crawler_decision_reason = None
                row.crawler_generated_at = None
                session.flush()
                return _search_session_from_row(row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL search-session restart is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError("PostgreSQL rejected search-session restart") from error

    def start_search_action(self, action: SearchAction) -> SearchAction:
        if action.status is not SearchActionStatus.RUNNING:
            raise RepositoryIntegrityError("a search action must start in RUNNING state")
        try:
            with self._sessions.begin() as session:
                session_row = _require_running_search_session(
                    session, action.session_id, for_update=True
                )
                if (
                    action.step > session_row.max_steps
                    or action.relation_depth > session_row.max_citation_depth
                ):
                    raise RepositoryIntegrityError(
                        "search action exceeds its persisted session limits"
                    )
                if action.tool in {
                    SearchTool.SEARCH_PAPERS,
                    SearchTool.GET_REFERENCES,
                    SearchTool.GET_CITATIONS,
                    SearchTool.GET_RECOMMENDATIONS,
                } and (
                    action.year_from != session_row.requested_year_from
                    or action.year_to != session_row.effective_year_to
                ):
                    raise RepositoryIntegrityError(
                        "search action must use the persisted session year scope"
                    )
                existing = session.get(SearchActionRow, action.id)
                if existing is None:
                    if action.tool is SearchTool.SEARCH_PAPERS:
                        query_count = session.scalar(
                            select(func.count())
                            .select_from(SearchActionRow)
                            .where(
                                SearchActionRow.session_id == action.session_id,
                                SearchActionRow.tool == SearchTool.SEARCH_PAPERS.value,
                            )
                        )
                        if query_count is None or query_count >= session_row.max_queries:
                            raise RepositoryIntegrityError(
                                "search action exceeds the persisted query limit"
                            )
                    row = SearchActionRow(**_search_action_values(action))
                    session.add(row)
                    session.flush()
                else:
                    row = existing
                    if _search_action_from_row(row) != action:
                        raise RepositoryIntegrityError(
                            "stable search action ID conflicts with stored data"
                        )
                return _search_action_from_row(row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL search action persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError("PostgreSQL rejected the search action") from error

    def persist_search_action_result(
        self,
        action: SearchAction,
        *,
        papers: tuple[ExternalPaperStub, ...],
        candidates: tuple[SearchCandidate, ...],
        discoveries: tuple[SearchCandidateDiscovery, ...],
    ) -> None:
        if action.status not in {SearchActionStatus.COMPLETED, SearchActionStatus.FAILED}:
            raise RepositoryIntegrityError("search action result must be terminal")
        if (
            action.status is SearchActionStatus.COMPLETED
            and action.tool is not SearchTool.READ_ARXIV_PAPER
            and action.result_count != len(papers)
        ):
            raise RepositoryIntegrityError(
                "completed search action result count must match its paper page"
            )
        if action.status is SearchActionStatus.FAILED and any((papers, candidates, discoveries)):
            raise RepositoryIntegrityError(
                "failed search action cannot persist untrusted partial results"
            )
        try:
            with self._sessions.begin() as session:
                session_row = session.scalars(
                    select(SearchSessionRow)
                    .where(SearchSessionRow.id == action.session_id)
                    .with_for_update()
                ).one_or_none()
                if session_row is None:
                    raise RepositoryIntegrityError("search session is missing")
                row = session.scalars(
                    select(SearchActionRow).where(SearchActionRow.id == action.id).with_for_update()
                ).one_or_none()
                if row is None:
                    raise RepositoryIntegrityError("search action is missing")
                if row.status != SearchActionStatus.RUNNING.value:
                    if _search_action_from_row(row) == action and _action_payload_matches(
                        session,
                        action_id=action.id,
                        papers=papers,
                        candidates=candidates,
                        discoveries=discoveries,
                    ):
                        return
                    raise RepositoryIntegrityError(
                        "terminal search action conflicts with stored data"
                    )
                if session_row.status != SearchSessionStatus.RUNNING.value:
                    raise RepositoryIntegrityError("search session is not running")
                if not _same_action_identity(row, action):
                    raise RepositoryIntegrityError(
                        "terminal search action does not match its running action"
                    )
                _persist_candidates(
                    session,
                    session_id=action.session_id,
                    papers=papers,
                    candidates=candidates,
                    discoveries=discoveries,
                    expected_action_id=action.id,
                )
                for name, value in _search_action_values(action).items():
                    setattr(row, name, value)
                session.flush()
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL search action result persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected the search action result"
            ) from error

    def persist_local_search_candidates(
        self,
        session_id: UUID,
        *,
        papers: tuple[ExternalPaperStub, ...],
        candidates: tuple[SearchCandidate, ...],
        discoveries: tuple[SearchCandidateDiscovery, ...],
    ) -> None:
        if any(item.action_id is not None for item in discoveries):
            raise RepositoryIntegrityError(
                "local search discoveries cannot reference a remote action"
            )
        try:
            with self._sessions.begin() as session:
                _require_running_search_session(session, session_id)
                _persist_candidates(
                    session,
                    session_id=session_id,
                    papers=papers,
                    candidates=candidates,
                    discoveries=discoveries,
                    expected_action_id=None,
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL local candidate persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError("PostgreSQL rejected local search candidates") from error

    def update_search_candidate_decisions(
        self,
        session_id: UUID,
        candidates: tuple[SearchCandidate, ...],
    ) -> None:
        try:
            with self._sessions.begin() as session:
                session_row = _require_running_search_session(session, session_id, for_update=True)
                for candidate in candidates:
                    if candidate.session_id != session_id:
                        raise RepositoryIntegrityError(
                            "candidate decision belongs to another session"
                        )
                    row = session.get(SearchCandidateRow, candidate.id)
                    if row is None or row.session_id != session_id:
                        raise RepositoryIntegrityError("search candidate does not exist")
                    for name, value in _candidate_values(candidate).items():
                        if name not in {
                            "id",
                            "session_id",
                            "external_paper_id",
                            "semantic_scholar_id",
                            "local_paper_id",
                            "local_paper_version_id",
                            "discovered_by_action_id",
                            "created_at",
                        }:
                            setattr(row, name, value)
                session.flush()
                selected_count = session.scalar(
                    select(func.count())
                    .select_from(SearchCandidateRow)
                    .where(
                        SearchCandidateRow.session_id == session_id,
                        SearchCandidateRow.decision == SelectionDecision.SELECTED.value,
                    )
                )
                if selected_count is None or selected_count > session_row.max_selected_candidates:
                    raise RepositoryIntegrityError(
                        "selected candidates exceed the persisted session limit"
                    )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL candidate decision persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError("PostgreSQL rejected candidate decisions") from error

    def update_search_comparison_targets(
        self,
        session_id: UUID,
        candidates: tuple[SearchCandidate, ...],
    ) -> None:
        try:
            with self._sessions.begin() as session:
                session_row = session.scalars(
                    select(SearchSessionRow)
                    .where(SearchSessionRow.id == session_id)
                    .with_for_update()
                ).one_or_none()
                if session_row is None or session_row.status != SearchSessionStatus.COMPLETE.value:
                    raise RepositoryIntegrityError(
                        "comparison targets require a completed search session"
                    )
                rows = tuple(
                    session.scalars(
                        select(SearchCandidateRow)
                        .where(SearchCandidateRow.session_id == session_id)
                        .order_by(SearchCandidateRow.rank, SearchCandidateRow.id)
                        .with_for_update()
                    )
                )
                updates = {candidate.id: candidate for candidate in candidates}
                if len(updates) != len(candidates) or set(updates) != {row.id for row in rows}:
                    raise RepositoryIntegrityError(
                        "comparison-target decisions must cover the exact candidate set"
                    )
                for row in rows:
                    candidate = updates[row.id]
                    current = _candidate_from_row(row)
                    if candidate.session_id != session_id or replace(
                        candidate,
                        comparison_target_decision=current.comparison_target_decision,
                        comparison_target_reason=current.comparison_target_reason,
                    ) != replace(
                        current,
                        comparison_target_decision=current.comparison_target_decision,
                        comparison_target_reason=current.comparison_target_reason,
                    ):
                        raise RepositoryIntegrityError(
                            "comparison-target update changed candidate provenance"
                        )
                    if (
                        candidate.comparison_target_decision is None
                        or candidate.comparison_target_reason is None
                    ):
                        raise RepositoryIntegrityError(
                            "comparison-target decision cannot remain pending"
                        )
                target_count = sum(
                    candidate.comparison_target_decision is ComparisonTargetDecision.TARGET
                    for candidate in candidates
                )
                if target_count > session_row.max_selected_candidates:
                    raise RepositoryIntegrityError(
                        "comparison targets exceed the persisted session limit"
                    )
                for row in rows:
                    candidate = updates[row.id]
                    target_decision = candidate.comparison_target_decision
                    if target_decision is None:
                        raise RepositoryIntegrityError(
                            "comparison-target decision cannot remain pending"
                        )
                    row.comparison_target_decision = target_decision.value
                    row.comparison_target_reason = candidate.comparison_target_reason
                session.flush()
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL comparison-target persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected comparison-target decisions"
            ) from error

    def complete_search_session(
        self,
        session_id: UUID,
        *,
        completed_at: datetime,
        stop_reason: SearchStopReason,
        provenance: SearchModelProvenance | None,
    ) -> SearchSession:
        if stop_reason is SearchStopReason.FAILED:
            raise RepositoryIntegrityError("successful search completion cannot use FAILED")
        return self._finish_search_session(
            session_id,
            status=SearchSessionStatus.COMPLETE,
            completed_at=completed_at,
            stop_reason=stop_reason,
            error_code=None,
            error_detail=None,
            provenance=provenance,
        )

    def fail_search_session(
        self,
        session_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
        provenance: SearchModelProvenance | None,
    ) -> SearchSession:
        return self._finish_search_session(
            session_id,
            status=SearchSessionStatus.FAILED,
            completed_at=completed_at,
            stop_reason=SearchStopReason.FAILED,
            error_code=error_code,
            error_detail=error_detail,
            provenance=provenance,
        )

    def _finish_search_session(
        self,
        session_id: UUID,
        *,
        status: SearchSessionStatus,
        completed_at: datetime,
        stop_reason: SearchStopReason,
        error_code: str | None,
        error_detail: str | None,
        provenance: SearchModelProvenance | None,
    ) -> SearchSession:
        try:
            with self._sessions.begin() as session:
                row = _require_running_search_session(session, session_id, for_update=True)
                running_action_count = session.scalar(
                    select(func.count())
                    .select_from(SearchActionRow)
                    .where(
                        SearchActionRow.session_id == session_id,
                        SearchActionRow.status == SearchActionStatus.RUNNING.value,
                    )
                )
                if running_action_count:
                    raise RepositoryIntegrityError(
                        "search session cannot finish with a running action"
                    )
                row.status = status.value
                row.completed_at = completed_at
                row.stop_reason = stop_reason.value
                row.error_code = error_code
                row.error_detail = error_detail
                if provenance is not None:
                    row.provider = provenance.provider
                    row.configured_model = provenance.configured_model
                    row.model_version = provenance.model_version
                    row.prompt_version = provenance.prompt_version
                    row.prompt_tokens = provenance.usage.prompt_tokens
                    row.completion_tokens = provenance.usage.completion_tokens
                    row.total_tokens = provenance.usage.total_tokens
                    row.call_count = provenance.usage.call_count
                    row.model_duration_ms = provenance.usage.duration_ms
                    row.estimated_cost_usd = provenance.usage.estimated_cost_usd
                session.flush()
                return _search_session_from_row(row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL search session finalization is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected search session finalization"
            ) from error

    def upsert_scientific_embeddings(self, embeddings: tuple[ScientificEmbedding, ...]) -> None:
        try:
            with self._sessions.begin() as session:
                _upsert_embeddings(session, embeddings)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL embedding persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError("PostgreSQL rejected scientific embeddings") from error

    def search_historical_lexically(
        self,
        topic_id: UUID,
        *,
        query: str,
        limit: int,
    ) -> tuple[HistoricalRetrievalMatch, ...]:
        if not query.strip() or not 1 <= limit <= 1000:
            raise RepositoryIntegrityError("lexical retrieval query or limit is invalid")
        empty_text: ColumnElement[str] = literal_column("''", String())
        document = func.to_tsvector(
            literal_column("'english'::regconfig"),
            func.coalesce(ExternalPaperStubRow.title, empty_text)
            + literal_column("' '")
            + func.coalesce(ExternalPaperStubRow.abstract, empty_text),
        )
        ts_query = func.websearch_to_tsquery(literal_column("'english'::regconfig"), query)
        score = func.least(
            1.0,
            func.greatest(0.0, func.ts_rank_cd(document, ts_query, 32)),
        ).label("score")
        statement = (
            select(ExternalPaperStubRow, HistoricalCorpusEntryRow, score)
            .join(
                HistoricalCorpusEntryRow,
                HistoricalCorpusEntryRow.external_paper_id == ExternalPaperStubRow.id,
            )
            .where(
                HistoricalCorpusEntryRow.topic_id == topic_id,
                document.op("@@")(ts_query),
            )
            .order_by(score.desc(), ExternalPaperStubRow.semantic_scholar_id)
            .limit(limit)
        )
        return self._historical_matches(statement)

    def search_historical_by_vector(
        self,
        topic_id: UUID,
        *,
        vector: tuple[float, ...],
        model_identifier: str,
        model_revision: str,
        tokenizer_identifier: str,
        tokenizer_revision: str,
        dimension: int,
        preprocessing_contract: str,
        model_provenance: str,
        source: str,
        limit: int,
    ) -> tuple[HistoricalRetrievalMatch, ...]:
        if (
            dimension != 768
            or len(vector) != dimension
            or any(not math.isfinite(value) for value in vector)
            or not any(value != 0.0 for value in vector)
            or any(
                not value.strip()
                for value in (
                    model_identifier,
                    model_revision,
                    tokenizer_identifier,
                    tokenizer_revision,
                    preprocessing_contract,
                    model_provenance,
                    source,
                )
            )
            or not 1 <= limit <= 1000
        ):
            raise RepositoryIntegrityError("vector retrieval dimension or limit is invalid")
        distance = ScientificEmbeddingRow.vector.cosine_distance(list(vector))
        score = func.least(1.0, func.greatest(0.0, 1.0 - distance / 2.0)).label("score")
        statement = (
            select(ExternalPaperStubRow, HistoricalCorpusEntryRow, score)
            .join(
                HistoricalCorpusEntryRow,
                HistoricalCorpusEntryRow.external_paper_id == ExternalPaperStubRow.id,
            )
            .join(
                ScientificEmbeddingRow,
                ScientificEmbeddingRow.external_paper_id == ExternalPaperStubRow.id,
            )
            .where(
                HistoricalCorpusEntryRow.topic_id == topic_id,
                ScientificEmbeddingRow.paper_version_id.is_(None),
                ScientificEmbeddingRow.model_identifier == model_identifier,
                ScientificEmbeddingRow.model_revision == model_revision,
                ScientificEmbeddingRow.tokenizer_identifier == tokenizer_identifier,
                ScientificEmbeddingRow.tokenizer_revision == tokenizer_revision,
                ScientificEmbeddingRow.dimension == dimension,
                ScientificEmbeddingRow.preprocessing_contract == preprocessing_contract,
                ScientificEmbeddingRow.model_provenance == model_provenance,
                ScientificEmbeddingRow.source == source,
            )
            .order_by(distance, ExternalPaperStubRow.semantic_scholar_id)
            .limit(limit)
        )
        return self._historical_matches(statement)

    def _historical_matches(self, statement: Executable) -> tuple[HistoricalRetrievalMatch, ...]:
        try:
            with self._sessions() as session:
                rows = cast(
                    tuple[
                        tuple[
                            ExternalPaperStubRow,
                            HistoricalCorpusEntryRow,
                            float,
                        ],
                        ...,
                    ],
                    tuple(session.execute(statement)),
                )
                paper_ids = {row[0].id for row in rows}
                identifiers = _identifiers_by_paper(session, paper_ids)
                return tuple(
                    HistoricalRetrievalMatch(
                        external_paper=_external_paper_from_row(
                            row[0], identifiers.get(row[0].id, ())
                        ),
                        corpus_entry=_corpus_entry_from_row(row[1]),
                        score=float(row[2]),
                    )
                    for row in rows
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL historical retrieval is unavailable"
            ) from error

    def get_comparison_paper_input(
        self,
        paper_version_id: UUID,
        *,
        analysis_id: UUID | None = None,
        analysis_scope: AnalysisScope | None = None,
        provider: str | None = None,
        configured_model: str | None = None,
        prompt_version: str | None = None,
        parser_name: str | None = None,
        parser_version: str | None = None,
    ) -> ComparisonPaperInput | None:
        statement = (
            select(PaperAnalysisRow, PaperVersionRow)
            .join(PaperVersionRow, PaperVersionRow.id == PaperAnalysisRow.paper_version_id)
            .where(PaperAnalysisRow.paper_version_id == paper_version_id)
        )
        if analysis_id is not None:
            statement = statement.where(PaperAnalysisRow.id == analysis_id)
        if analysis_scope is not None:
            statement = statement.where(PaperAnalysisRow.analysis_scope == analysis_scope.value)
        model_contract = (provider, configured_model, prompt_version)
        if any(value is not None for value in model_contract):
            if any(value is None for value in model_contract):
                raise RepositoryIntegrityError(
                    "comparison analysis model provenance must be supplied together"
                )
            statement = statement.where(
                PaperAnalysisRow.provider == provider,
                PaperAnalysisRow.configured_model == configured_model,
                PaperAnalysisRow.prompt_version == prompt_version,
            )
            if analysis_id is None:
                statement = statement.where(PaperAnalysisRow.revision_id.is_(None))
        if (parser_name is None) != (parser_version is None):
            raise RepositoryIntegrityError(
                "comparison analysis parser provenance must be supplied together"
            )
        if parser_name is not None:
            statement = statement.where(
                exists(
                    select(ParsedPaperRow.id).where(
                        ParsedPaperRow.id == PaperAnalysisRow.parsed_paper_id,
                        ParsedPaperRow.parser_name == parser_name,
                        ParsedPaperRow.parser_version == parser_version,
                    )
                )
            )
        if analysis_id is None and analysis_scope is None:
            statement = statement.order_by(
                case((PaperAnalysisRow.analysis_scope == "FULL_TEXT", 1), else_=0).desc(),
                PaperAnalysisRow.generated_at.desc(),
                PaperAnalysisRow.id.desc(),
            )
        else:
            statement = statement.order_by(
                PaperAnalysisRow.generated_at.desc(),
                PaperAnalysisRow.id.desc(),
            )
        statement = statement.limit(1)
        try:
            with self._sessions() as session:
                result = session.execute(statement).one_or_none()
                if result is None:
                    return None
                analysis, version = result
                evidence_rows = tuple(
                    session.scalars(
                        select(EvidenceRow)
                        .where(
                            EvidenceRow.analysis_id == analysis.id,
                            EvidenceRow.verification_status != VerificationStatus.REJECTED.value,
                        )
                        .order_by(EvidenceRow.evidence_key, EvidenceRow.id)
                    )
                )
                return ComparisonPaperInput(
                    paper_id=analysis.paper_id,
                    paper_version_id=analysis.paper_version_id,
                    analysis_id=analysis.id,
                    analysis_scope=AnalysisScope(analysis.analysis_scope),
                    title=version.title,
                    summary=analysis.summary,
                    research_problem=analysis.research_problem,
                    method_summary=analysis.method_summary,
                    limitations=tuple(analysis.limitations),
                    evidence=tuple(
                        ComparisonEvidenceInput(
                            id=row.id,
                            analysis_id=row.analysis_id,
                            paper_id=row.paper_id,
                            paper_version_id=row.paper_version_id,
                            section=row.section,
                            excerpt=row.excerpt,
                        )
                        for row in evidence_rows
                    ),
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL comparison context read is unavailable"
            ) from error

    def persist_comparison_bundle(self, bundle: ComparisonBundle) -> None:
        comparison = bundle.comparison
        evidence_ids = {
            evidence_id
            for dimension in comparison.dimensions
            for evidence_id in dimension.source_evidence_ids + dimension.target_evidence_ids
        }
        try:
            with self._sessions.begin() as session:
                existing_comparison = session.get(ComparisonRow, comparison.id)
                if existing_comparison is not None:
                    if _canonical_bundle(
                        _comparison_bundle_from_session(session, existing_comparison)
                    ) != _canonical_bundle(bundle):
                        raise RepositoryIntegrityError(
                            "stable comparison ID conflicts with stored data"
                        )
                    return
                search_row = session.get(SearchSessionRow, comparison.search_session_id)
                if (
                    search_row is None
                    or search_row.status != SearchSessionStatus.COMPLETE.value
                    or search_row.source_paper_id != comparison.source_paper_id
                    or search_row.source_paper_version_id != comparison.source_paper_version_id
                    or search_row.source_analysis_id != comparison.source_analysis_id
                    or search_row.source_analysis_scope != comparison.source_analysis_scope.value
                ):
                    raise RepositoryIntegrityError(
                        "comparison source requires its completed search session"
                    )
                target_predicate = (
                    SearchCandidateRow.comparison_target_decision
                    == ComparisonTargetDecision.TARGET.value
                )
                if search_row.pipeline_execution_id is None:
                    target_predicate = or_(
                        target_predicate,
                        and_(
                            SearchCandidateRow.comparison_target_decision.is_(None),
                            SearchCandidateRow.decision == SelectionDecision.SELECTED.value,
                        ),
                    )
                selected_candidate_exists = session.execute(
                    select(SearchCandidateRow.id).where(
                        SearchCandidateRow.session_id == comparison.search_session_id,
                        SearchCandidateRow.local_paper_id == comparison.target_paper_id,
                        SearchCandidateRow.local_paper_version_id
                        == comparison.target_paper_version_id,
                        target_predicate,
                    )
                ).scalar_one_or_none()
                if selected_candidate_exists is None:
                    raise RepositoryIntegrityError(
                        "comparison target is not a bounded local search candidate"
                    )
                _require_paper_version_owner(
                    session,
                    comparison.source_paper_id,
                    comparison.source_paper_version_id,
                )
                _require_paper_version_owner(
                    session,
                    comparison.target_paper_id,
                    comparison.target_paper_version_id,
                )
                evidence_rows = (
                    tuple(
                        session.scalars(select(EvidenceRow).where(EvidenceRow.id.in_(evidence_ids)))
                    )
                    if evidence_ids
                    else ()
                )
                evidence_by_id = {row.id: row for row in evidence_rows}
                if set(evidence_by_id) != evidence_ids:
                    raise RepositoryIntegrityError("comparison references missing evidence")
                if any(
                    row.verification_status == VerificationStatus.REJECTED.value
                    for row in evidence_rows
                ):
                    raise RepositoryIntegrityError("comparison references rejected evidence")
                session.add(ComparisonRow(**_comparison_values(comparison)))
                session.flush()
                for dimension in comparison.dimensions:
                    session.add(ComparisonDimensionRow(**_comparison_dimension_values(dimension)))
                session.flush()
                for dimension in comparison.dimensions:
                    for evidence_id in dimension.source_evidence_ids:
                        evidence = evidence_by_id[evidence_id]
                        if (
                            evidence.analysis_id != comparison.source_analysis_id
                            or evidence.paper_id != comparison.source_paper_id
                            or evidence.paper_version_id != comparison.source_paper_version_id
                        ):
                            raise RepositoryIntegrityError(
                                "source comparison evidence has the wrong owner"
                            )
                        session.add(
                            _comparison_evidence_link(
                                dimension,
                                evidence,
                                role="SOURCE",
                            )
                        )
                    for evidence_id in dimension.target_evidence_ids:
                        evidence = evidence_by_id[evidence_id]
                        if (
                            evidence.analysis_id != comparison.target_analysis_id
                            or evidence.paper_id != comparison.target_paper_id
                            or evidence.paper_version_id != comparison.target_paper_version_id
                        ):
                            raise RepositoryIntegrityError(
                                "target comparison evidence has the wrong owner"
                            )
                        session.add(
                            _comparison_evidence_link(
                                dimension,
                                evidence,
                                role="TARGET",
                            )
                        )
                for relation in bundle.relations:
                    session.add(
                        PaperRelationRow(
                            comparison_id=comparison.id,
                            **_paper_relation_values(relation),
                        )
                    )
                session.flush()
                for relation in bundle.relations:
                    for evidence_id in relation.evidence_ids:
                        evidence = evidence_by_id[evidence_id]
                        session.add(
                            RelationEvidenceLinkRow(
                                relation_id=relation.id,
                                evidence_id=evidence.id,
                                comparison_id=comparison.id,
                                evidence_paper_id=evidence.paper_id,
                                evidence_paper_version_id=evidence.paper_version_id,
                            )
                        )
                session.flush()
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL comparison persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError("PostgreSQL rejected the comparison bundle") from error

    def get_comparison(
        self,
        comparison_id: UUID,
        *,
        canonical_only: bool = False,
    ) -> ComparisonDetail | None:
        try:
            with self._sessions() as session:
                if canonical_only and not session.scalar(
                    exists(
                        select(ProductRunComparisonInputRow.comparison_id)
                        .join(
                            DailyRunRow,
                            DailyRunRow.id == ProductRunComparisonInputRow.run_id,
                        )
                        .join(
                            RunItemRow,
                            and_(
                                RunItemRow.run_id == DailyRunRow.id,
                                RunItemRow.paper_version_id
                                == ProductRunComparisonInputRow.paper_version_id,
                            ),
                        )
                        .where(
                            ProductRunComparisonInputRow.comparison_id == comparison_id,
                            DailyRunRow.operation == RunOperation.PRODUCT_PUBLICATION.value,
                            DailyRunRow.status.in_(
                                (RunStatus.COMPLETE.value, RunStatus.PARTIAL.value)
                            ),
                            DailyRunRow.pipeline_execution_mode
                            != PipelineExecutionMode.SMOKE.value,
                            RunItemRow.status == RunItemStatus.COMPLETED.value,
                            RunItemRow.stage == PaperStage.PUBLISHED.value,
                        )
                    ).select()
                ):
                    return None
                row = session.get(ComparisonRow, comparison_id)
                return None if row is None else _comparison_detail_from_session(session, row)
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL comparison read is unavailable") from error

    def get_related_work(
        self,
        paper_id: UUID,
        *,
        paper_version_id: UUID | None = None,
        search_session_id: UUID | None = None,
    ) -> RelatedWorkDetail | None:
        try:
            with self._sessions() as session:
                statement = select(SearchSessionRow).where(
                    SearchSessionRow.source_paper_id == paper_id
                )
                if paper_version_id is not None:
                    statement = statement.where(
                        SearchSessionRow.source_paper_version_id == paper_version_id
                    )
                if search_session_id is not None:
                    statement = statement.where(SearchSessionRow.id == search_session_id)
                else:
                    canonical_publication = exists(
                        select(ProductRunComparisonInputRow.comparison_id)
                        .join(
                            ComparisonRow,
                            ComparisonRow.id == ProductRunComparisonInputRow.comparison_id,
                        )
                        .join(
                            DailyRunRow,
                            DailyRunRow.id == ProductRunComparisonInputRow.run_id,
                        )
                        .join(
                            RunItemRow,
                            and_(
                                RunItemRow.run_id == DailyRunRow.id,
                                RunItemRow.paper_id == ProductRunComparisonInputRow.paper_id,
                                RunItemRow.paper_version_id
                                == ProductRunComparisonInputRow.paper_version_id,
                            ),
                        )
                        .where(
                            ComparisonRow.search_session_id == SearchSessionRow.id,
                            ProductRunComparisonInputRow.paper_id
                            == SearchSessionRow.source_paper_id,
                            ProductRunComparisonInputRow.paper_version_id
                            == SearchSessionRow.source_paper_version_id,
                            ProductRunComparisonInputRow.analysis_id
                            == SearchSessionRow.source_analysis_id,
                            RunItemRow.status == RunItemStatus.COMPLETED.value,
                            RunItemRow.stage == PaperStage.PUBLISHED.value,
                            DailyRunRow.operation == RunOperation.PRODUCT_PUBLICATION.value,
                            DailyRunRow.status.in_(
                                (RunStatus.COMPLETE.value, RunStatus.PARTIAL.value)
                            ),
                            DailyRunRow.pipeline_execution_mode
                            != PipelineExecutionMode.SMOKE.value,
                        )
                    )
                    statement = statement.where(
                        SearchSessionRow.status == SearchSessionStatus.COMPLETE.value,
                        canonical_publication,
                    )
                session_row = session.scalars(
                    statement.order_by(
                        SearchSessionRow.started_at.desc(), SearchSessionRow.id.desc()
                    ).limit(1)
                ).one_or_none()
                if session_row is None:
                    return None
                action_rows = tuple(
                    session.scalars(
                        select(SearchActionRow)
                        .where(SearchActionRow.session_id == session_row.id)
                        .order_by(SearchActionRow.step, SearchActionRow.id)
                    )
                )
                candidate_rows = tuple(
                    session.scalars(
                        select(SearchCandidateRow)
                        .where(SearchCandidateRow.session_id == session_row.id)
                        .order_by(SearchCandidateRow.rank, SearchCandidateRow.id)
                    )
                )
                candidate_ids = {row.id for row in candidate_rows}
                discovery_rows = (
                    tuple(
                        session.scalars(
                            select(SearchCandidateDiscoveryRow)
                            .where(SearchCandidateDiscoveryRow.candidate_id.in_(candidate_ids))
                            .order_by(
                                SearchCandidateDiscoveryRow.discovered_at,
                                SearchCandidateDiscoveryRow.id,
                            )
                        )
                    )
                    if candidate_ids
                    else ()
                )
                external_ids = {row.external_paper_id for row in candidate_rows}
                external_rows = (
                    tuple(
                        session.scalars(
                            select(ExternalPaperStubRow).where(
                                ExternalPaperStubRow.id.in_(external_ids)
                            )
                        )
                    )
                    if external_ids
                    else ()
                )
                external_by_id = {row.id: row for row in external_rows}
                identifiers = _identifiers_by_paper(session, external_ids)
                comparison_rows = tuple(
                    session.scalars(
                        select(ComparisonRow)
                        .where(ComparisonRow.search_session_id == session_row.id)
                        .order_by(ComparisonRow.generated_at, ComparisonRow.id)
                    )
                )
                comparisons = tuple(
                    _comparison_bundle_from_session(session, row) for row in comparison_rows
                )
                comparison_by_target = {
                    item.comparison.target_paper_version_id: item.comparison.id
                    for item in comparisons
                }
                discoveries_by_candidate: dict[UUID, list[SearchCandidateDiscovery]] = {}
                for row in discovery_rows:
                    discoveries_by_candidate.setdefault(row.candidate_id, []).append(
                        _discovery_from_row(row)
                    )
                relations_by_target: dict[UUID, list[PaperRelation]] = {}
                for item in comparisons:
                    for relation in item.relations:
                        relations_by_target.setdefault(relation.target_paper_version_id, []).append(
                            relation
                        )
                items: list[RelatedWorkItem] = []
                for candidate_row in candidate_rows:
                    external_row = external_by_id.get(candidate_row.external_paper_id)
                    if external_row is None:
                        raise RepositoryIntegrityError(
                            "related-work candidate has no external paper"
                        )
                    target_version_id = candidate_row.local_paper_version_id
                    items.append(
                        RelatedWorkItem(
                            candidate=_candidate_from_row(candidate_row),
                            external_paper=_external_paper_from_row(
                                external_row,
                                identifiers.get(external_row.id, ()),
                            ),
                            discoveries=tuple(discoveries_by_candidate.get(candidate_row.id, ())),
                            relations=()
                            if target_version_id is None
                            else tuple(relations_by_target.get(target_version_id, ())),
                            comparison_id=None
                            if target_version_id is None
                            else comparison_by_target.get(target_version_id),
                        )
                    )
                return RelatedWorkDetail(
                    session=_search_session_from_row(session_row),
                    actions=tuple(_search_action_from_row(row) for row in action_rows),
                    items=tuple(items),
                    comparisons=comparisons,
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL related-work read is unavailable"
            ) from error


def _backfill_values(run: HistoricalBackfillRun) -> dict[str, object]:
    return {
        "id": run.id,
        "topic_id": run.topic_id,
        "window_from": run.window_from,
        "window_to": run.window_to,
        "query_plan": list(run.query_plan),
        "max_results_per_query": run.max_results_per_query,
        "overall_timeout_seconds": run.overall_timeout_seconds,
        "embedding_model_identifier": run.embedding_model_identifier,
        "embedding_model_revision": run.embedding_model_revision,
        "embedding_tokenizer_identifier": run.embedding_tokenizer_identifier,
        "embedding_tokenizer_revision": run.embedding_tokenizer_revision,
        "embedding_dimension": run.embedding_dimension,
        "embedding_preprocessing_contract": run.embedding_preprocessing_contract,
        "embedding_model_provenance": run.embedding_model_provenance,
        "embedding_source": run.embedding_source,
        "status": run.status.value,
        "next_query_index": run.next_query_index,
        "discovered_count": run.discovered_count,
        "persisted_count": run.persisted_count,
        "representative_count": run.representative_count,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "error_code": run.error_code,
        "error_detail": run.error_detail,
        "schema_version": run.schema_version,
        "created_at": run.created_at,
    }


def _backfill_from_row(row: HistoricalBackfillRunRow) -> HistoricalBackfillRun:
    return HistoricalBackfillRun(
        id=row.id,
        topic_id=row.topic_id,
        window_from=row.window_from,
        window_to=row.window_to,
        query_plan=tuple(row.query_plan),
        max_results_per_query=row.max_results_per_query,
        overall_timeout_seconds=row.overall_timeout_seconds,
        embedding_model_identifier=row.embedding_model_identifier,
        embedding_model_revision=row.embedding_model_revision,
        embedding_tokenizer_identifier=row.embedding_tokenizer_identifier,
        embedding_tokenizer_revision=row.embedding_tokenizer_revision,
        embedding_dimension=row.embedding_dimension,
        embedding_preprocessing_contract=row.embedding_preprocessing_contract,
        embedding_model_provenance=row.embedding_model_provenance,
        embedding_source=row.embedding_source,
        status=BackfillStatus(row.status),
        next_query_index=row.next_query_index,
        discovered_count=row.discovered_count,
        persisted_count=row.persisted_count,
        representative_count=row.representative_count,
        started_at=row.started_at,
        completed_at=row.completed_at,
        error_code=row.error_code,
        error_detail=row.error_detail,
        schema_version=row.schema_version,
        created_at=row.created_at,
    )


def _search_session_values(value: SearchSession) -> dict[str, object]:
    return {
        "id": value.id,
        "pipeline_execution_id": value.pipeline_execution_id,
        "topic_id": value.topic_id,
        "source_paper_id": value.source_paper_id,
        "source_paper_version_id": value.source_paper_version_id,
        "source_analysis_id": value.source_analysis_id,
        "source_analysis_scope": value.source_analysis_scope.value,
        "requested_year_from": value.requested_year_from,
        "effective_year_to": value.effective_year_to,
        "objective": value.objective,
        "crawler_queries": None if value.crawler_queries is None else list(value.crawler_queries),
        "crawler_use_recommendations": value.crawler_use_recommendations,
        "crawler_expand_references": value.crawler_expand_references,
        "crawler_expand_citations": value.crawler_expand_citations,
        "crawler_decision_reason": value.crawler_decision_reason,
        "crawler_generated_at": value.crawler_generated_at,
        "status": value.status.value,
        "max_steps": value.limits.max_steps,
        "max_queries": value.limits.max_queries,
        "max_queue_size": value.limits.max_queue_size,
        "max_citation_depth": value.limits.max_citation_depth,
        "max_candidates": value.limits.max_candidates,
        "max_selected_candidates": value.limits.max_selected_candidates,
        "per_operation_timeout_seconds": value.limits.per_operation_timeout_seconds,
        "overall_timeout_seconds": value.limits.overall_timeout_seconds,
        "started_at": value.started_at,
        "completed_at": value.completed_at,
        "stop_reason": None if value.stop_reason is None else value.stop_reason.value,
        "error_code": value.error_code,
        "error_detail": value.error_detail,
        "provider": value.provider,
        "configured_model": value.configured_model,
        "model_version": value.model_version,
        "prompt_version": value.prompt_version,
        "prompt_tokens": None if value.usage is None else value.usage.prompt_tokens,
        "completion_tokens": None if value.usage is None else value.usage.completion_tokens,
        "total_tokens": None if value.usage is None else value.usage.total_tokens,
        "call_count": None if value.usage is None else value.usage.call_count,
        "model_duration_ms": None if value.usage is None else value.usage.duration_ms,
        "estimated_cost_usd": None if value.usage is None else value.usage.estimated_cost_usd,
        "schema_version": value.schema_version,
        "created_at": value.created_at,
    }


def _search_session_from_row(row: SearchSessionRow) -> SearchSession:
    return SearchSession(
        id=row.id,
        pipeline_execution_id=row.pipeline_execution_id,
        topic_id=row.topic_id,
        source_paper_id=row.source_paper_id,
        source_paper_version_id=row.source_paper_version_id,
        source_analysis_id=row.source_analysis_id,
        source_analysis_scope=AnalysisScope(row.source_analysis_scope),
        requested_year_from=row.requested_year_from,
        effective_year_to=row.effective_year_to,
        objective=row.objective,
        status=SearchSessionStatus(row.status),
        limits=SearchLimits(
            max_steps=row.max_steps,
            max_queries=row.max_queries,
            max_queue_size=row.max_queue_size,
            max_citation_depth=row.max_citation_depth,
            max_candidates=row.max_candidates,
            max_selected_candidates=row.max_selected_candidates,
            per_operation_timeout_seconds=row.per_operation_timeout_seconds,
            overall_timeout_seconds=row.overall_timeout_seconds,
        ),
        started_at=row.started_at,
        completed_at=row.completed_at,
        stop_reason=None if row.stop_reason is None else SearchStopReason(row.stop_reason),
        error_code=row.error_code,
        error_detail=row.error_detail,
        provider=row.provider,
        configured_model=row.configured_model,
        model_version=row.model_version,
        prompt_version=row.prompt_version,
        usage=(
            None
            if row.prompt_tokens is None
            else ModelUsage(
                prompt_tokens=row.prompt_tokens,
                completion_tokens=cast(int, row.completion_tokens),
                total_tokens=cast(int, row.total_tokens),
                call_count=cast(int, row.call_count),
                duration_ms=cast(int, row.model_duration_ms),
                estimated_cost_usd=row.estimated_cost_usd,
            )
        ),
        schema_version=row.schema_version,
        created_at=row.created_at,
        crawler_queries=None if row.crawler_queries is None else tuple(row.crawler_queries),
        crawler_use_recommendations=row.crawler_use_recommendations,
        crawler_expand_references=row.crawler_expand_references,
        crawler_expand_citations=row.crawler_expand_citations,
        crawler_decision_reason=row.crawler_decision_reason,
        crawler_generated_at=row.crawler_generated_at,
    )


def _search_action_values(value: SearchAction) -> dict[str, object]:
    return {
        "id": value.id,
        "session_id": value.session_id,
        "step": value.step,
        "tool": value.tool.value,
        "status": value.status.value,
        "query": value.query,
        "target_semantic_scholar_id": value.target_semantic_scholar_id,
        "target_arxiv_id": value.target_arxiv_id,
        "positive_paper_ids": list(value.positive_paper_ids),
        "year_from": value.year_from,
        "year_to": value.year_to,
        "requested_limit": value.requested_limit,
        "result_count": value.result_count,
        "relation_depth": value.relation_depth,
        "decision_reason": value.decision_reason,
        "error_code": value.error_code,
        "retryable": value.retryable,
        "error_detail": value.error_detail,
        "duration_ms": value.duration_ms,
        "created_at": value.created_at,
        "completed_at": value.completed_at,
        "schema_version": value.schema_version,
    }


def _search_action_from_row(row: SearchActionRow) -> SearchAction:
    return SearchAction(
        id=row.id,
        session_id=row.session_id,
        step=row.step,
        tool=SearchTool(row.tool),
        status=SearchActionStatus(row.status),
        query=row.query,
        target_semantic_scholar_id=row.target_semantic_scholar_id,
        target_arxiv_id=row.target_arxiv_id,
        positive_paper_ids=tuple(row.positive_paper_ids),
        year_from=row.year_from,
        year_to=row.year_to,
        requested_limit=row.requested_limit,
        result_count=row.result_count,
        relation_depth=row.relation_depth,
        decision_reason=row.decision_reason,
        error_code=row.error_code,
        retryable=row.retryable,
        error_detail=row.error_detail,
        duration_ms=row.duration_ms,
        created_at=row.created_at,
        completed_at=row.completed_at,
        schema_version=row.schema_version,
    )


def _candidate_values(value: SearchCandidate) -> dict[str, object]:
    return {
        "id": value.id,
        "session_id": value.session_id,
        "external_paper_id": value.external_paper_id,
        "semantic_scholar_id": value.semantic_scholar_id,
        "local_paper_id": value.local_paper_id,
        "local_paper_version_id": value.local_paper_version_id,
        "discovered_by_action_id": value.discovered_by_action_id,
        "origins": [item.value for item in value.origins],
        "relation_depth": value.relation_depth,
        "semantic_scholar_score": value.scores.semantic_scholar,
        "lexical_score": value.scores.lexical,
        "vector_score": value.scores.vector,
        "entity_overlap_score": value.scores.entity_overlap,
        "citation_score": value.scores.citation,
        "recommendation_score": value.scores.recommendation,
        "final_score": value.scores.final,
        "rank": value.rank,
        "decision": value.decision.value,
        "decision_reason": value.decision_reason,
        "comparison_target_decision": (
            None
            if value.comparison_target_decision is None
            else value.comparison_target_decision.value
        ),
        "comparison_target_reason": value.comparison_target_reason,
        "provider": value.provider,
        "configured_model": value.configured_model,
        "model_version": value.model_version,
        "prompt_version": value.prompt_version,
        "generated_at": value.generated_at,
        "verification_status": value.verification_status.value,
        "schema_version": value.schema_version,
        "created_at": value.created_at,
    }


def _candidate_from_row(row: SearchCandidateRow) -> SearchCandidate:
    return SearchCandidate(
        id=row.id,
        session_id=row.session_id,
        external_paper_id=row.external_paper_id,
        semantic_scholar_id=row.semantic_scholar_id,
        local_paper_id=row.local_paper_id,
        local_paper_version_id=row.local_paper_version_id,
        discovered_by_action_id=row.discovered_by_action_id,
        origins=tuple(CandidateOrigin(item) for item in row.origins),
        relation_depth=row.relation_depth,
        scores=CandidateScoreComponents(
            semantic_scholar=row.semantic_scholar_score,
            lexical=row.lexical_score,
            vector=row.vector_score,
            entity_overlap=row.entity_overlap_score,
            citation=row.citation_score,
            recommendation=row.recommendation_score,
            final=row.final_score,
        ),
        rank=row.rank,
        decision=SelectionDecision(row.decision),
        decision_reason=row.decision_reason,
        provider=row.provider,
        configured_model=row.configured_model,
        model_version=row.model_version,
        prompt_version=row.prompt_version,
        generated_at=row.generated_at,
        verification_status=VerificationStatus(row.verification_status),
        schema_version=row.schema_version,
        created_at=row.created_at,
        comparison_target_decision=(
            None
            if row.comparison_target_decision is None
            else ComparisonTargetDecision(row.comparison_target_decision)
        ),
        comparison_target_reason=row.comparison_target_reason,
    )


def _discovery_from_row(row: SearchCandidateDiscoveryRow) -> SearchCandidateDiscovery:
    return SearchCandidateDiscovery(
        id=row.id,
        candidate_id=row.candidate_id,
        action_id=row.action_id,
        origin=CandidateOrigin(row.origin),
        relation_depth=row.relation_depth,
        discovered_at=row.discovered_at,
    )


def _upsert_external_paper(session: Session, paper: ExternalPaperStub) -> None:
    identity_matches = [
        ExternalPaperStubRow.id == paper.id,
        ExternalPaperStubRow.semantic_scholar_id == paper.semantic_scholar_id,
    ]
    if paper.arxiv_id is not None:
        identity_matches.append(ExternalPaperStubRow.arxiv_id == paper.arxiv_id)
    if paper.doi is not None:
        identity_matches.append(ExternalPaperStubRow.doi == paper.doi)
    matching_rows = tuple(
        session.scalars(
            select(ExternalPaperStubRow)
            .where(or_(*identity_matches))
            .order_by(ExternalPaperStubRow.id)
            .with_for_update()
        )
    )
    identifiers = _merge_external_identifier_metadata(session, matching_rows, paper)
    identifier_values = {key.casefold(): value for key, value in identifiers}
    effective_arxiv_id = identifier_values.get("arxiv")
    effective_doi = identifier_values.get("doi")
    values = {
        "id": paper.id,
        "semantic_scholar_id": paper.semantic_scholar_id,
        "title": paper.title,
        "abstract": paper.abstract,
        "publication_year": paper.year,
        "publication_date": paper.publication_date,
        "venue": paper.venue,
        "authors": list(paper.authors),
        "arxiv_id": effective_arxiv_id,
        "doi": effective_doi,
        "citation_count": paper.citation_count,
        "influential_citation_count": paper.influential_citation_count,
        "full_text_available": effective_arxiv_id is not None,
        "source": paper.source,
        "schema_version": paper.schema_version,
        "created_at": paper.created_at,
        "updated_at": paper.updated_at,
    }
    if matching_rows:
        survivor = next(
            (row for row in matching_rows if row.semantic_scholar_id == paper.semantic_scholar_id),
            None,
        )
        if survivor is None:
            survivor = next((row for row in matching_rows if row.id == paper.id), matching_rows[0])
        for duplicate in matching_rows:
            if duplicate is not survivor:
                _merge_external_paper_rows(session, survivor=survivor, duplicate=duplicate)
        if survivor.id != paper.id:
            survivor.id = paper.id
            session.flush()
        created_at = min(survivor.created_at, paper.created_at)
        updated_at = max(survivor.updated_at, paper.updated_at)
        for name, value in values.items():
            if name not in {"id", "created_at", "updated_at"}:
                setattr(survivor, name, value)
        survivor.created_at = created_at
        survivor.updated_at = updated_at
        session.flush()
        _rekey_external_dependents(session, survivor)
    else:
        survivor = ExternalPaperStubRow(**values)
        session.add(survivor)
        session.flush()
    session.execute(
        delete(ExternalPaperIdentifierRow).where(
            ExternalPaperIdentifierRow.external_paper_id == survivor.id
        )
    )
    for identifier_type, identifier_value in identifiers:
        session.add(
            ExternalPaperIdentifierRow(
                external_paper_id=survivor.id,
                identifier_type=identifier_type,
                identifier_value=identifier_value,
            )
        )


def _merge_external_identifier_metadata(
    session: Session,
    matching_rows: tuple[ExternalPaperStubRow, ...],
    paper: ExternalPaperStub,
) -> tuple[tuple[str, str], ...]:
    """Return the lossless identifier union or reject ambiguous identity changes."""

    matching_ids = {row.id for row in matching_rows}
    persisted_identifiers = (
        tuple(
            session.scalars(
                select(ExternalPaperIdentifierRow)
                .where(ExternalPaperIdentifierRow.external_paper_id.in_(matching_ids))
                .order_by(
                    ExternalPaperIdentifierRow.external_paper_id,
                    ExternalPaperIdentifierRow.identifier_type,
                )
                .with_for_update()
            )
        )
        if matching_ids
        else ()
    )
    merged: dict[str, tuple[str, str]] = {}

    def add(identifier_type: str, identifier_value: str) -> None:
        normalized_type = identifier_type.casefold()
        existing = merged.get(normalized_type)
        if existing is not None and not _same_external_identifier_value(
            normalized_type, existing[1], identifier_value
        ):
            raise ExternalPaperIdentifierConflictError(
                f"external paper identifier conflict for {identifier_type}"
            )
        if existing is None:
            merged[normalized_type] = (identifier_type, identifier_value)

    for identifier in persisted_identifiers:
        add(identifier.identifier_type, identifier.identifier_value)
    for row in matching_rows:
        if row.arxiv_id is not None:
            add("ArXiv", row.arxiv_id)
        if row.doi is not None:
            add("DOI", row.doi)
    for identifier_type, identifier_value in paper.external_ids:
        add(identifier_type, identifier_value)

    if merged:
        possible_conflicts = tuple(
            session.scalars(
                select(ExternalPaperIdentifierRow)
                .where(func.lower(ExternalPaperIdentifierRow.identifier_type).in_(set(merged)))
                .with_for_update()
            )
        )
        for identifier in possible_conflicts:
            expected = merged[identifier.identifier_type.casefold()]
            if identifier.external_paper_id not in matching_ids and _same_external_identifier_value(
                identifier.identifier_type.casefold(),
                identifier.identifier_value,
                expected[1],
            ):
                raise ExternalPaperIdentifierConflictError(
                    f"external paper identifier conflict for {identifier.identifier_type}"
                )

    return tuple(merged[key] for key in sorted(merged))


def _same_external_identifier_value(normalized_type: str, left: str, right: str) -> bool:
    if normalized_type in {"arxiv", "doi"}:
        return left.casefold() == right.casefold()
    return left == right


def _merge_external_paper_rows(
    session: Session,
    *,
    survivor: ExternalPaperStubRow,
    duplicate: ExternalPaperStubRow,
) -> None:
    """Merge one approved-identity alias without leaving dependent rows behind."""

    if survivor.id == duplicate.id:
        return
    survivor.created_at = min(survivor.created_at, duplicate.created_at)
    survivor.updated_at = max(survivor.updated_at, duplicate.updated_at)
    session.execute(
        delete(ExternalPaperIdentifierRow).where(
            ExternalPaperIdentifierRow.external_paper_id == duplicate.id
        )
    )
    _merge_corpus_entries(session, survivor_id=survivor.id, duplicate_id=duplicate.id)
    _merge_external_embeddings(session, survivor_id=survivor.id, duplicate_id=duplicate.id)
    _merge_external_candidates(
        session,
        survivor_id=survivor.id,
        survivor_semantic_scholar_id=survivor.semantic_scholar_id,
        duplicate_id=duplicate.id,
    )
    session.flush()
    session.delete(duplicate)
    session.flush()


def _merge_corpus_entries(
    session: Session,
    *,
    survivor_id: UUID,
    duplicate_id: UUID,
) -> None:
    duplicate_rows = tuple(
        session.scalars(
            select(HistoricalCorpusEntryRow)
            .where(HistoricalCorpusEntryRow.external_paper_id == duplicate_id)
            .with_for_update()
        )
    )
    for duplicate in duplicate_rows:
        survivor = session.scalars(
            select(HistoricalCorpusEntryRow)
            .where(
                HistoricalCorpusEntryRow.topic_id == duplicate.topic_id,
                HistoricalCorpusEntryRow.external_paper_id == survivor_id,
            )
            .with_for_update()
        ).one_or_none()
        if survivor is None:
            duplicate.external_paper_id = survivor_id
            duplicate.id = stable_historical_corpus_entry_id(duplicate.topic_id, survivor_id)
            continue
        survivor_local = (survivor.local_paper_id, survivor.local_paper_version_id)
        duplicate_local = (duplicate.local_paper_id, duplicate.local_paper_version_id)
        if all(survivor_local) and all(duplicate_local) and survivor_local != duplicate_local:
            raise RepositoryIntegrityError(
                "external identity aliases resolve to different local paper versions"
            )
        representative_rank = (
            survivor.representative_rank
            if survivor.representative_rank is not None
            else duplicate.representative_rank
        )
        duplicate.representative_rank = None
        session.flush()
        survivor.local_paper_id = survivor.local_paper_id or duplicate.local_paper_id
        survivor.local_paper_version_id = (
            survivor.local_paper_version_id or duplicate.local_paper_version_id
        )
        survivor.representative_rank = representative_rank
        survivor.first_seen_at = min(survivor.first_seen_at, duplicate.first_seen_at)
        survivor.last_seen_at = max(survivor.last_seen_at, duplicate.last_seen_at)
        survivor.schema_version = max(survivor.schema_version, duplicate.schema_version)
        session.delete(duplicate)
    session.flush()


def _merge_external_embeddings(
    session: Session,
    *,
    survivor_id: UUID,
    duplicate_id: UUID,
) -> None:
    duplicate_rows = tuple(
        session.scalars(
            select(ScientificEmbeddingRow)
            .where(ScientificEmbeddingRow.external_paper_id == duplicate_id)
            .with_for_update()
        )
    )
    for duplicate in duplicate_rows:
        survivor = session.scalars(
            select(ScientificEmbeddingRow)
            .where(
                ScientificEmbeddingRow.external_paper_id == survivor_id,
                ScientificEmbeddingRow.model_identifier == duplicate.model_identifier,
                ScientificEmbeddingRow.model_revision == duplicate.model_revision,
                ScientificEmbeddingRow.tokenizer_identifier == duplicate.tokenizer_identifier,
                ScientificEmbeddingRow.tokenizer_revision == duplicate.tokenizer_revision,
                ScientificEmbeddingRow.dimension == duplicate.dimension,
                ScientificEmbeddingRow.preprocessing_contract == duplicate.preprocessing_contract,
                ScientificEmbeddingRow.model_provenance == duplicate.model_provenance,
                ScientificEmbeddingRow.source == duplicate.source,
            )
            .with_for_update()
        ).one_or_none()
        if survivor is None:
            duplicate.external_paper_id = survivor_id
            duplicate.id = stable_embedding_id(
                survivor_id,
                model_identifier=duplicate.model_identifier,
                model_revision=duplicate.model_revision,
                tokenizer_identifier=duplicate.tokenizer_identifier,
                tokenizer_revision=duplicate.tokenizer_revision,
                dimension=duplicate.dimension,
                preprocessing_contract=duplicate.preprocessing_contract,
                model_provenance=duplicate.model_provenance,
                source=duplicate.source,
            )
            continue
        if duplicate.generated_at > survivor.generated_at:
            survivor.dimension = duplicate.dimension
            survivor.vector = duplicate.vector
            survivor.generated_at = duplicate.generated_at
            survivor.source = duplicate.source
            survivor.schema_version = duplicate.schema_version
        survivor.created_at = min(survivor.created_at, duplicate.created_at)
        session.delete(duplicate)
    session.flush()


def _merge_external_candidates(
    session: Session,
    *,
    survivor_id: UUID,
    survivor_semantic_scholar_id: str,
    duplicate_id: UUID,
) -> None:
    duplicate_rows = tuple(
        session.scalars(
            select(SearchCandidateRow)
            .where(SearchCandidateRow.external_paper_id == duplicate_id)
            .with_for_update()
        )
    )
    for duplicate in duplicate_rows:
        survivor = session.scalars(
            select(SearchCandidateRow)
            .where(
                SearchCandidateRow.session_id == duplicate.session_id,
                SearchCandidateRow.external_paper_id == survivor_id,
            )
            .with_for_update()
        ).one_or_none()
        if survivor is None:
            duplicate.external_paper_id = survivor_id
            duplicate.semantic_scholar_id = survivor_semantic_scholar_id
            duplicate.id = stable_search_candidate_id(
                duplicate.session_id,
                survivor_semantic_scholar_id,
            )
            continue
        _merge_candidate_rows(session, survivor=survivor, duplicate=duplicate)
    session.flush()


def _merge_candidate_rows(
    session: Session,
    *,
    survivor: SearchCandidateRow,
    duplicate: SearchCandidateRow,
) -> None:
    discoveries = tuple(
        session.scalars(
            select(SearchCandidateDiscoveryRow)
            .where(SearchCandidateDiscoveryRow.candidate_id == duplicate.id)
            .with_for_update()
        )
    )
    for duplicate_discovery in discoveries:
        survivor_discovery = session.scalars(
            select(SearchCandidateDiscoveryRow)
            .where(
                SearchCandidateDiscoveryRow.candidate_id == survivor.id,
                SearchCandidateDiscoveryRow.action_id == duplicate_discovery.action_id,
                SearchCandidateDiscoveryRow.origin == duplicate_discovery.origin,
            )
            .with_for_update()
        ).one_or_none()
        if survivor_discovery is None:
            duplicate_discovery.candidate_id = survivor.id
            duplicate_discovery.id = stable_candidate_discovery_id(
                survivor.id,
                duplicate_discovery.origin,
                duplicate_discovery.action_id,
                duplicate_discovery.relation_depth,
            )
            continue
        survivor_discovery.relation_depth = min(
            survivor_discovery.relation_depth,
            duplicate_discovery.relation_depth,
        )
        survivor_discovery.discovered_at = min(
            survivor_discovery.discovered_at,
            duplicate_discovery.discovered_at,
        )
        session.delete(duplicate_discovery)
    decision_priority = {
        SelectionDecision.PENDING.value: 0,
        SelectionDecision.REJECTED.value: 1,
        SelectionDecision.SELECTED.value: 2,
    }
    preferred = survivor
    if decision_priority[duplicate.decision] > decision_priority[survivor.decision]:
        preferred = duplicate
    if preferred is duplicate:
        survivor.decision = duplicate.decision
        survivor.decision_reason = duplicate.decision_reason
        survivor.provider = duplicate.provider
        survivor.configured_model = duplicate.configured_model
        survivor.model_version = duplicate.model_version
        survivor.prompt_version = duplicate.prompt_version
        survivor.generated_at = duplicate.generated_at
        survivor.verification_status = duplicate.verification_status
    origin_order = tuple(origin.value for origin in CandidateOrigin)
    combined_origins = set(survivor.origins) | set(duplicate.origins)
    survivor.origins = [origin for origin in origin_order if origin in combined_origins]
    survivor.relation_depth = min(survivor.relation_depth, duplicate.relation_depth)
    survivor.rank = min(survivor.rank, duplicate.rank)
    survivor.semantic_scholar_score = max(
        survivor.semantic_scholar_score, duplicate.semantic_scholar_score
    )
    survivor.lexical_score = max(survivor.lexical_score, duplicate.lexical_score)
    survivor.vector_score = max(survivor.vector_score, duplicate.vector_score)
    survivor.entity_overlap_score = max(
        survivor.entity_overlap_score, duplicate.entity_overlap_score
    )
    survivor.citation_score = max(survivor.citation_score, duplicate.citation_score)
    survivor.recommendation_score = max(
        survivor.recommendation_score, duplicate.recommendation_score
    )
    survivor.final_score = max(survivor.final_score, duplicate.final_score)
    survivor.discovered_by_action_id = (
        survivor.discovered_by_action_id or duplicate.discovered_by_action_id
    )
    survivor.schema_version = max(survivor.schema_version, duplicate.schema_version)
    survivor.created_at = min(survivor.created_at, duplicate.created_at)
    session.delete(duplicate)


def _rekey_external_dependents(session: Session, paper: ExternalPaperStubRow) -> None:
    for entry in session.scalars(
        select(HistoricalCorpusEntryRow)
        .where(HistoricalCorpusEntryRow.external_paper_id == paper.id)
        .with_for_update()
    ):
        entry.id = stable_historical_corpus_entry_id(entry.topic_id, paper.id)
    for embedding in session.scalars(
        select(ScientificEmbeddingRow)
        .where(ScientificEmbeddingRow.external_paper_id == paper.id)
        .with_for_update()
    ):
        embedding.id = stable_embedding_id(
            paper.id,
            model_identifier=embedding.model_identifier,
            model_revision=embedding.model_revision,
            tokenizer_identifier=embedding.tokenizer_identifier,
            tokenizer_revision=embedding.tokenizer_revision,
            dimension=embedding.dimension,
            preprocessing_contract=embedding.preprocessing_contract,
            model_provenance=embedding.model_provenance,
            source=embedding.source,
        )
    candidates = tuple(
        session.scalars(
            select(SearchCandidateRow)
            .where(SearchCandidateRow.external_paper_id == paper.id)
            .with_for_update()
        )
    )
    for candidate in candidates:
        candidate.semantic_scholar_id = paper.semantic_scholar_id
        candidate.id = stable_search_candidate_id(
            candidate.session_id,
            paper.semantic_scholar_id,
        )
    session.flush()
    for candidate in candidates:
        for discovery in session.scalars(
            select(SearchCandidateDiscoveryRow)
            .where(SearchCandidateDiscoveryRow.candidate_id == candidate.id)
            .with_for_update()
        ):
            discovery.id = stable_candidate_discovery_id(
                candidate.id,
                discovery.origin,
                discovery.action_id,
                discovery.relation_depth,
            )
    session.flush()


def _external_paper_from_row(
    row: ExternalPaperStubRow,
    identifiers: tuple[tuple[str, str], ...],
) -> ExternalPaperStub:
    return ExternalPaperStub(
        id=row.id,
        semantic_scholar_id=row.semantic_scholar_id,
        title=row.title,
        abstract=row.abstract,
        year=row.publication_year,
        publication_date=row.publication_date,
        venue=row.venue,
        authors=tuple(row.authors),
        external_ids=identifiers,
        arxiv_id=row.arxiv_id,
        doi=row.doi,
        citation_count=row.citation_count,
        influential_citation_count=row.influential_citation_count,
        full_text_available=row.full_text_available,
        source=row.source,
        schema_version=row.schema_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _identifiers_by_paper(
    session: Session, paper_ids: set[UUID]
) -> dict[UUID, tuple[tuple[str, str], ...]]:
    if not paper_ids:
        return {}
    rows = tuple(
        session.scalars(
            select(ExternalPaperIdentifierRow)
            .where(ExternalPaperIdentifierRow.external_paper_id.in_(paper_ids))
            .order_by(
                ExternalPaperIdentifierRow.external_paper_id,
                ExternalPaperIdentifierRow.identifier_type,
            )
        )
    )
    grouped: dict[UUID, list[tuple[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row.external_paper_id, []).append(
            (row.identifier_type, row.identifier_value)
        )
    return {paper_id: tuple(values) for paper_id, values in grouped.items()}


def _resolve_local_identity(
    session: Session,
    *,
    arxiv_id: str | None,
    paper_id: UUID | None,
    paper_version_id: UUID | None,
) -> tuple[UUID | None, UUID | None]:
    if (paper_id is None) != (paper_version_id is None):
        raise RepositoryIntegrityError("local paper identity must include its version")
    if paper_id is not None and paper_version_id is not None:
        result = session.execute(
            select(PaperRow.canonical_arxiv_id)
            .join(PaperVersionRow, PaperVersionRow.paper_id == PaperRow.id)
            .where(
                PaperRow.id == paper_id,
                PaperVersionRow.id == paper_version_id,
            )
        ).scalar_one_or_none()
        if result is None or (arxiv_id is not None and result != arxiv_id):
            raise RepositoryIntegrityError(
                "local paper identity does not match the external arXiv identity"
            )
        return paper_id, paper_version_id
    if arxiv_id is None:
        return None, None
    resolved = session.execute(
        select(PaperRow.id, PaperVersionRow.id)
        .join(
            PaperVersionRow,
            (PaperVersionRow.paper_id == PaperRow.id)
            & (PaperVersionRow.version == PaperRow.current_version),
        )
        .where(PaperRow.canonical_arxiv_id == arxiv_id)
    ).one_or_none()
    if resolved is None:
        return None, None
    return cast(UUID, resolved[0]), cast(UUID, resolved[1])


def _upsert_corpus_entry(
    session: Session,
    entry: HistoricalCorpusEntry,
    *,
    local_paper_id: UUID | None,
    local_paper_version_id: UUID | None,
    persisted_at: datetime,
) -> None:
    values = {
        "id": entry.id,
        "topic_id": entry.topic_id,
        "external_paper_id": entry.external_paper_id,
        "local_paper_id": local_paper_id,
        "local_paper_version_id": local_paper_version_id,
        "representative_rank": entry.representative_rank,
        "first_seen_at": entry.first_seen_at,
        "last_seen_at": max(entry.last_seen_at, persisted_at),
        "schema_version": entry.schema_version,
    }
    statement = insert(HistoricalCorpusEntryRow).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[
            HistoricalCorpusEntryRow.topic_id,
            HistoricalCorpusEntryRow.external_paper_id,
        ],
        set_={
            "local_paper_id": statement.excluded.local_paper_id,
            "local_paper_version_id": statement.excluded.local_paper_version_id,
            "last_seen_at": func.greatest(
                HistoricalCorpusEntryRow.last_seen_at, statement.excluded.last_seen_at
            ),
            "schema_version": statement.excluded.schema_version,
        },
    ).returning(HistoricalCorpusEntryRow.id)
    stored_id = session.execute(statement).scalar_one()
    if stored_id != entry.id:
        raise RepositoryIntegrityError("historical corpus identity maps to a different stable ID")


def _corpus_entry_from_row(row: HistoricalCorpusEntryRow) -> HistoricalCorpusEntry:
    return HistoricalCorpusEntry(
        id=row.id,
        topic_id=row.topic_id,
        external_paper_id=row.external_paper_id,
        local_paper_id=row.local_paper_id,
        local_paper_version_id=row.local_paper_version_id,
        representative_rank=row.representative_rank,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        schema_version=row.schema_version,
    )


def _upsert_embeddings(session: Session, embeddings: tuple[ScientificEmbedding, ...]) -> None:
    for embedding in embeddings:
        if embedding.dimension != 768 or not any(value != 0.0 for value in embedding.vector):
            raise RepositoryIntegrityError(
                "scientific embeddings must be non-zero vectors with dimension 768"
            )
        values = {
            "id": embedding.id,
            "paper_version_id": embedding.paper_version_id,
            "external_paper_id": embedding.external_paper_id,
            "model_identifier": embedding.model_identifier,
            "model_revision": embedding.model_revision,
            "tokenizer_identifier": embedding.tokenizer_identifier,
            "tokenizer_revision": embedding.tokenizer_revision,
            "dimension": embedding.dimension,
            "preprocessing_contract": embedding.preprocessing_contract,
            "model_provenance": embedding.model_provenance,
            "vector": list(embedding.vector),
            "generated_at": embedding.generated_at,
            "source": embedding.source,
            "schema_version": embedding.schema_version,
            "created_at": embedding.created_at,
        }
        statement = insert(ScientificEmbeddingRow).values(**values)
        statement = statement.on_conflict_do_update(
            constraint="uq_scientific_embeddings_owner_model",
            set_={
                "vector": statement.excluded.vector,
                "generated_at": statement.excluded.generated_at,
                "schema_version": statement.excluded.schema_version,
            },
        ).returning(ScientificEmbeddingRow.id)
        stored_id = session.execute(statement).scalar_one()
        if stored_id != embedding.id:
            raise RepositoryIntegrityError("embedding owner and model map to a different stable ID")


def _require_running_search_session(
    session: Session, session_id: UUID, *, for_update: bool = False
) -> SearchSessionRow:
    statement = select(SearchSessionRow).where(SearchSessionRow.id == session_id)
    if for_update:
        statement = statement.with_for_update()
    row = session.scalars(statement).one_or_none()
    if row is None or row.status != SearchSessionStatus.RUNNING.value:
        raise RepositoryIntegrityError("search session is missing or not running")
    return row


def _same_action_identity(row: SearchActionRow, action: SearchAction) -> bool:
    return (
        row.id == action.id
        and row.session_id == action.session_id
        and row.step == action.step
        and row.tool == action.tool.value
        and row.query == action.query
        and row.target_semantic_scholar_id == action.target_semantic_scholar_id
        and row.target_arxiv_id == action.target_arxiv_id
        and tuple(row.positive_paper_ids) == action.positive_paper_ids
        and row.year_from == action.year_from
        and row.year_to == action.year_to
        and row.requested_limit == action.requested_limit
        and row.relation_depth == action.relation_depth
        and row.decision_reason == action.decision_reason
        and row.created_at == action.created_at
        and row.schema_version == action.schema_version
    )


def _action_payload_matches(
    session: Session,
    *,
    action_id: UUID,
    papers: tuple[ExternalPaperStub, ...],
    candidates: tuple[SearchCandidate, ...],
    discoveries: tuple[SearchCandidateDiscovery, ...],
) -> bool:
    persisted_discoveries = tuple(
        session.scalars(
            select(SearchCandidateDiscoveryRow).where(
                SearchCandidateDiscoveryRow.action_id == action_id
            )
        )
    )
    passed_candidate_by_id = {candidate.id: candidate for candidate in candidates}
    return (
        {item.id for item in persisted_discoveries} == {item.id for item in discoveries}
        and {item.candidate_id for item in persisted_discoveries} == set(passed_candidate_by_id)
        and {candidate.external_paper_id for candidate in candidates}.issubset(
            {paper.id for paper in papers}
        )
    )


def _persist_candidates(
    session: Session,
    *,
    session_id: UUID,
    papers: tuple[ExternalPaperStub, ...],
    candidates: tuple[SearchCandidate, ...],
    discoveries: tuple[SearchCandidateDiscovery, ...],
    expected_action_id: UUID | None,
) -> None:
    session_row = _require_running_search_session(session, session_id, for_update=True)
    if any(discovery.relation_depth > session_row.max_citation_depth for discovery in discoveries):
        raise RepositoryIntegrityError("candidate discovery exceeds its persisted session limits")
    if any(
        candidate.relation_depth > session_row.max_citation_depth
        or candidate.rank > session_row.max_candidates
        for candidate in candidates
    ):
        raise RepositoryIntegrityError("candidate exceeds its persisted session limits")
    if any(
        paper.year is None
        or not session_row.requested_year_from <= paper.year <= session_row.effective_year_to
        for paper in papers
    ):
        raise RepositoryIntegrityError(
            "candidate paper is outside the persisted session year scope"
        )
    for paper in papers:
        _upsert_external_paper(session, paper)
    existing_external_ids = set(
        session.scalars(
            select(SearchCandidateRow.external_paper_id).where(
                SearchCandidateRow.session_id == session_id
            )
        )
    )
    candidate_external_ids = {candidate.external_paper_id for candidate in candidates}
    if len(existing_external_ids | candidate_external_ids) > min(
        session_row.max_queue_size, session_row.max_candidates
    ):
        raise RepositoryIntegrityError("candidates exceed the persisted session limit")
    for candidate in candidates:
        if candidate.session_id != session_id:
            raise RepositoryIntegrityError("candidate belongs to another search session")
        external_row = session.get(ExternalPaperStubRow, candidate.external_paper_id)
        if (
            external_row is None
            or external_row.semantic_scholar_id != candidate.semantic_scholar_id
        ):
            raise RepositoryIntegrityError("candidate external identity does not match its stub")
        local_paper_id, local_version_id = _resolve_local_identity(
            session,
            arxiv_id=external_row.arxiv_id,
            paper_id=candidate.local_paper_id,
            paper_version_id=candidate.local_paper_version_id,
        )
        values = _candidate_values(candidate)
        values["local_paper_id"] = local_paper_id
        values["local_paper_version_id"] = local_version_id
        statement = insert(SearchCandidateRow).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[
                SearchCandidateRow.session_id,
                SearchCandidateRow.external_paper_id,
            ],
            set_={
                name: statement.excluded[name]
                for name in values
                if name
                not in {
                    "id",
                    "session_id",
                    "external_paper_id",
                    "semantic_scholar_id",
                    "created_at",
                }
            },
        ).returning(SearchCandidateRow.id)
        stored_id = session.execute(statement).scalar_one()
        if stored_id != candidate.id:
            raise RepositoryIntegrityError("candidate identity maps to a different stable ID")
    session.flush()
    selected_count = session.scalar(
        select(func.count())
        .select_from(SearchCandidateRow)
        .where(
            SearchCandidateRow.session_id == session_id,
            SearchCandidateRow.decision == SelectionDecision.SELECTED.value,
        )
    )
    if selected_count is None or selected_count > session_row.max_selected_candidates:
        raise RepositoryIntegrityError("selected candidates exceed the persisted session limit")
    candidate_rows = {
        row.id: row
        for row in session.scalars(
            select(SearchCandidateRow).where(
                SearchCandidateRow.id.in_({item.candidate_id for item in discoveries})
            )
        )
    }
    for discovery in discoveries:
        candidate_row = candidate_rows.get(discovery.candidate_id)
        if candidate_row is None or candidate_row.session_id != session_id:
            raise RepositoryIntegrityError(
                "candidate discovery references a missing session candidate"
            )
        if expected_action_id is not None and discovery.action_id != expected_action_id:
            raise RepositoryIntegrityError(
                "remote candidate discovery references another search action"
            )
        values = {
            "id": discovery.id,
            "session_id": session_id,
            "candidate_id": discovery.candidate_id,
            "action_id": discovery.action_id,
            "origin": discovery.origin.value,
            "relation_depth": discovery.relation_depth,
            "discovered_at": discovery.discovered_at,
        }
        statement = insert(SearchCandidateDiscoveryRow).values(**values)
        session.execute(
            statement.on_conflict_do_nothing(constraint="uq_search_candidate_discoveries_origin")
        )


def _require_paper_version_owner(session: Session, paper_id: UUID, paper_version_id: UUID) -> None:
    exists = session.execute(
        select(PaperVersionRow.id).where(
            PaperVersionRow.id == paper_version_id,
            PaperVersionRow.paper_id == paper_id,
        )
    ).scalar_one_or_none()
    if exists is None:
        raise RepositoryIntegrityError("paper version does not belong to the paper")


def _comparison_values(value: Comparison) -> dict[str, object]:
    return {
        "id": value.id,
        "search_session_id": value.search_session_id,
        "source_paper_id": value.source_paper_id,
        "source_paper_version_id": value.source_paper_version_id,
        "source_analysis_id": value.source_analysis_id,
        "source_analysis_scope": value.source_analysis_scope.value,
        "target_paper_id": value.target_paper_id,
        "target_paper_version_id": value.target_paper_version_id,
        "target_analysis_id": value.target_analysis_id,
        "target_analysis_scope": value.target_analysis_scope.value,
        "comparability_status": value.comparability_status.value,
        "comparability_reason": value.comparability_reason,
        "summary": value.summary,
        "provider": value.provider,
        "configured_model": value.configured_model,
        "model_version": value.model_version,
        "prompt_version": value.prompt_version,
        "generated_at": value.generated_at,
        "source": value.source,
        "verification_status": value.verification_status.value,
        "prompt_tokens": value.usage.prompt_tokens,
        "completion_tokens": value.usage.completion_tokens,
        "total_tokens": value.usage.total_tokens,
        "call_count": value.usage.call_count,
        "duration_ms": value.usage.duration_ms,
        "estimated_cost_usd": value.usage.estimated_cost_usd,
        "schema_version": value.schema_version,
        "created_at": value.created_at,
    }


def _comparison_dimension_values(value: ComparisonDimension) -> dict[str, object]:
    return {
        "id": value.id,
        "comparison_id": value.comparison_id,
        "name": value.name.value,
        "position": value.position,
        "source_value": value.source_value,
        "target_value": value.target_value,
        "assessment": value.assessment,
        "schema_version": value.schema_version,
        "created_at": value.created_at,
    }


def _comparison_evidence_link(
    dimension: ComparisonDimension,
    evidence: EvidenceRow,
    *,
    role: str,
) -> ComparisonEvidenceLinkRow:
    return ComparisonEvidenceLinkRow(
        comparison_dimension_id=dimension.id,
        evidence_id=evidence.id,
        evidence_analysis_id=evidence.analysis_id,
        evidence_role=role,
        comparison_id=dimension.comparison_id,
        evidence_paper_id=evidence.paper_id,
        evidence_paper_version_id=evidence.paper_version_id,
    )


def _paper_relation_values(value: PaperRelation) -> dict[str, object]:
    return {
        "id": value.id,
        "source_paper_id": value.source_paper_id,
        "source_paper_version_id": value.source_paper_version_id,
        "target_paper_id": value.target_paper_id,
        "target_paper_version_id": value.target_paper_version_id,
        "relation_type": value.relation_type.value,
        "provenance": value.provenance.value,
        "justification": value.justification,
        "provider": value.provider,
        "model_version": value.model_version,
        "prompt_version": value.prompt_version,
        "confidence": value.confidence,
        "verification_status": value.verification_status.value,
        "generated_at": value.generated_at,
        "schema_version": value.schema_version,
        "created_at": value.created_at,
    }


def _comparison_bundle_from_session(session: Session, row: ComparisonRow) -> ComparisonBundle:
    dimension_rows = tuple(
        session.scalars(
            select(ComparisonDimensionRow)
            .where(ComparisonDimensionRow.comparison_id == row.id)
            .order_by(ComparisonDimensionRow.position, ComparisonDimensionRow.id)
        )
    )
    link_rows = tuple(
        session.scalars(
            select(ComparisonEvidenceLinkRow).where(
                ComparisonEvidenceLinkRow.comparison_id == row.id
            )
        )
    )
    source_evidence: dict[UUID, list[UUID]] = {}
    target_evidence: dict[UUID, list[UUID]] = {}
    for link in link_rows:
        target = source_evidence if link.evidence_role == "SOURCE" else target_evidence
        target.setdefault(link.comparison_dimension_id, []).append(link.evidence_id)
    dimensions = tuple(
        ComparisonDimension(
            id=dimension.id,
            comparison_id=dimension.comparison_id,
            name=ComparisonDimensionName(dimension.name),
            position=dimension.position,
            source_value=dimension.source_value,
            target_value=dimension.target_value,
            assessment=dimension.assessment,
            source_evidence_ids=tuple(sorted(source_evidence.get(dimension.id, ()), key=str)),
            target_evidence_ids=tuple(sorted(target_evidence.get(dimension.id, ()), key=str)),
            schema_version=dimension.schema_version,
            created_at=dimension.created_at,
        )
        for dimension in dimension_rows
    )
    comparison = Comparison(
        id=row.id,
        search_session_id=row.search_session_id,
        source_paper_id=row.source_paper_id,
        source_paper_version_id=row.source_paper_version_id,
        source_analysis_id=row.source_analysis_id,
        source_analysis_scope=AnalysisScope(row.source_analysis_scope),
        target_paper_id=row.target_paper_id,
        target_paper_version_id=row.target_paper_version_id,
        target_analysis_id=row.target_analysis_id,
        target_analysis_scope=AnalysisScope(row.target_analysis_scope),
        comparability_status=ComparabilityStatus(row.comparability_status),
        comparability_reason=row.comparability_reason,
        summary=row.summary,
        dimensions=dimensions,
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
    )
    relation_rows = tuple(
        session.scalars(
            select(PaperRelationRow)
            .where(PaperRelationRow.comparison_id == row.id)
            .order_by(PaperRelationRow.relation_type, PaperRelationRow.id)
        )
    )
    relation_link_rows = tuple(
        session.scalars(
            select(RelationEvidenceLinkRow).where(RelationEvidenceLinkRow.comparison_id == row.id)
        )
    )
    relation_evidence: dict[UUID, list[UUID]] = {}
    for link in relation_link_rows:
        relation_evidence.setdefault(link.relation_id, []).append(link.evidence_id)
    relations = tuple(
        PaperRelation(
            id=relation.id,
            source_paper_id=relation.source_paper_id,
            source_paper_version_id=relation.source_paper_version_id,
            target_paper_id=relation.target_paper_id,
            target_paper_version_id=relation.target_paper_version_id,
            relation_type=PaperRelationType(relation.relation_type),
            provenance=RelationProvenance(relation.provenance),
            evidence_ids=tuple(sorted(relation_evidence.get(relation.id, ()), key=str)),
            justification=relation.justification,
            provider=relation.provider,
            model_version=relation.model_version,
            prompt_version=relation.prompt_version,
            confidence=relation.confidence,
            verification_status=VerificationStatus(relation.verification_status),
            generated_at=relation.generated_at,
            schema_version=relation.schema_version,
            created_at=relation.created_at,
        )
        for relation in relation_rows
    )
    return ComparisonBundle(comparison=comparison, relations=relations)


def comparison_bundle_from_session(session: Session, row: ComparisonRow) -> ComparisonBundle:
    """Load a comparison bundle for another PostgreSQL adapter concern."""

    return _comparison_bundle_from_session(session, row)


def _comparison_detail_from_session(session: Session, row: ComparisonRow) -> ComparisonDetail:
    bundle = _comparison_bundle_from_session(session, row)
    evidence_ids = {
        evidence_id
        for dimension in bundle.comparison.dimensions
        for evidence_id in dimension.source_evidence_ids + dimension.target_evidence_ids
    }
    if not evidence_ids:
        return ComparisonDetail(
            comparison=bundle.comparison,
            relations=bundle.relations,
            evidence=(),
        )
    evidence_rows = tuple(
        session.scalars(
            select(EvidenceRow).where(EvidenceRow.id.in_(evidence_ids)).order_by(EvidenceRow.id)
        )
    )
    analysis_ids = {evidence.analysis_id for evidence in evidence_rows}
    analysis_scopes = {
        analysis.id: AnalysisScope(analysis.analysis_scope)
        for analysis in session.scalars(
            select(PaperAnalysisRow).where(PaperAnalysisRow.id.in_(analysis_ids))
        )
    }
    evidence = tuple(
        ComparisonEvidenceReference(
            id=item.id,
            analysis_id=item.analysis_id,
            paper_id=item.paper_id,
            paper_version_id=item.paper_version_id,
            analysis_scope=analysis_scopes[item.analysis_id],
            section=item.section,
            excerpt=item.excerpt,
            evidence_type=EvidenceType(item.evidence_type),
            verification_status=VerificationStatus(item.verification_status),
        )
        for item in evidence_rows
    )
    return ComparisonDetail(
        comparison=bundle.comparison,
        relations=bundle.relations,
        evidence=evidence,
    )


def _canonical_bundle(bundle: ComparisonBundle) -> ComparisonBundle:
    dimensions = tuple(
        replace(
            dimension,
            source_evidence_ids=tuple(sorted(dimension.source_evidence_ids, key=str)),
            target_evidence_ids=tuple(sorted(dimension.target_evidence_ids, key=str)),
        )
        for dimension in bundle.comparison.dimensions
    )
    relations = tuple(
        sorted(
            (
                replace(
                    relation,
                    evidence_ids=tuple(sorted(relation.evidence_ids, key=str)),
                )
                for relation in bundle.relations
            ),
            key=lambda relation: str(relation.id),
        )
    )
    return ComparisonBundle(
        comparison=replace(bundle.comparison, dimensions=dimensions),
        relations=relations,
    )
