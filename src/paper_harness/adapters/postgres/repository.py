"""Synchronous SQLAlchemy/PostgreSQL repository for the application persistence port."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Engine, case, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DataError, IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from paper_harness.application.read_models import (
    AnalysisDetail,
    AnalysisTarget,
    PaperDetail,
    RunDetail,
    StoredTopic,
)
from paper_harness.domain.analysis import (
    AnalysisBundle,
    AnalysisClaim,
    AnalysisScope,
    CitationContext,
    ClaimType,
    Evidence,
    EvidenceType,
    ModelUsage,
    PageCoordinates,
    PaperAnalysis,
    ParsedPaper,
    ParsedPassage,
    ParsedReference,
    ParsedSection,
    VerificationStatus,
)
from paper_harness.domain.errors import DuplicateDailyRunError
from paper_harness.domain.historical import (
    ComparisonTargetDecision,
    SearchSessionStatus,
)
from paper_harness.domain.identity import (
    normalize_author_name,
    stable_author_id,
    stable_paper_id,
    stable_paper_version_id,
    stable_report_id,
    stable_source_identity_id,
)
from paper_harness.domain.models import (
    MAX_REPRESENTATIVE_FULL_TEXT_COUNT,
    DailyRun,
    IngestionCursor,
    Paper,
    PaperSourceIdentity,
    PaperStage,
    PaperVersion,
    PipelineExecution,
    PipelineExecutionContract,
    PipelineExecutionMode,
    RunItem,
    RunItemStatus,
    RunOperation,
    RunStatus,
    TopicConfig,
)
from paper_harness.ports.arxiv import ArxivPaperRecord
from paper_harness.ports.repository import (
    MigrationIncompatibleError,
    RepositoryError,
    RepositoryIntegrityError,
    RepositoryUnavailableError,
)

from .historical_repository import HistoricalRepositoryMixin
from .models import (
    AnalysisClaimRow,
    AuthorRow,
    CitationContextRow,
    DailyRunRow,
    EvidenceClaimRow,
    EvidenceRow,
    ExternalPaperStubRow,
    HistoricalCorpusEntryRow,
    IngestionCursorRow,
    PaperAnalysisRow,
    PaperRow,
    PaperSourceIdentityRow,
    PaperVersionAuthorRow,
    PaperVersionRow,
    ParsedPaperRow,
    ParsedPassageRow,
    ParsedReferenceRow,
    ParsedSectionRow,
    PipelineExecutionRow,
    ProductRunPaperInputRow,
    ReportFailureRow,
    ReportRow,
    RunItemRow,
    SearchCandidateRow,
    SearchSessionRow,
    TopicPaperRow,
    TopicRow,
)
from .product_repository import ProductRepositoryMixin

EXPECTED_DATABASE_REVISION = "0005_m5_pipeline_provenance"


class PostgresRepository(ProductRepositoryMixin, HistoricalRepositoryMixin):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def daily_pipeline_lock(self, execution_id: UUID) -> Generator[None]:
        key = _pipeline_advisory_key(execution_id)
        with self._advisory_lock(
            key,
            duplicate_message=f"another daily pipeline holds execution {execution_id}",
        ):
            yield

    def get_pipeline_execution(self, execution_id: UUID) -> PipelineExecution | None:
        try:
            with self._sessions() as session:
                row = session.get(PipelineExecutionRow, execution_id)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL pipeline-execution read is unavailable"
            ) from error
        return None if row is None else _pipeline_execution_from_row(row)

    def start_pipeline_execution(self, execution: PipelineExecution) -> PipelineExecution:
        values = {
            "id": execution.id,
            "topic_id": execution.topic_id,
            "logical_date": execution.logical_date,
            "execution_mode": execution.execution_mode.value,
            "execution_key": "canonical",
            "analysis_scope": execution.analysis_scope.value,
            "selection_limit": execution.selection_limit,
            "execution_contract": _pipeline_execution_contract_values(execution.contract),
            "status": execution.status.value,
            "deadline_at": execution.deadline_at,
            "started_at": execution.started_at,
            "completed_at": execution.completed_at,
            "error_code": execution.error_code,
            "error_detail": execution.error_detail,
            "schema_version": execution.schema_version,
            "created_at": execution.created_at,
            "updated_at": execution.started_at,
        }
        statement = (
            insert(PipelineExecutionRow)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[PipelineExecutionRow.id])
        )
        try:
            with self._sessions.begin() as session:
                session.execute(statement)
                row = session.get(PipelineExecutionRow, execution.id)
                if row is None:
                    raise RepositoryError("pipeline execution disappeared after creation")
                if row.status in (RunStatus.RUNNING.value, RunStatus.FAILED.value):
                    row.analysis_scope = execution.analysis_scope.value
                    row.selection_limit = execution.selection_limit
                    row.execution_contract = _pipeline_execution_contract_values(execution.contract)
                    row.updated_at = execution.started_at
                    session.flush()
                persisted = _pipeline_execution_from_row(row)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL pipeline-execution creation is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected pipeline-execution ownership constraints"
            ) from error
        if (
            persisted.topic_id != execution.topic_id
            or persisted.logical_date != execution.logical_date
            or persisted.execution_mode is not execution.execution_mode
        ):
            raise RepositoryIntegrityError(
                "persisted pipeline execution conflicts with its stable owner"
            )
        return persisted

    def complete_pipeline_execution(
        self,
        execution_id: UUID,
        *,
        status: RunStatus,
        completed_at: datetime,
    ) -> PipelineExecution:
        if status not in (RunStatus.COMPLETE, RunStatus.PARTIAL):
            raise RepositoryError("pipeline completion requires COMPLETE or PARTIAL status")
        try:
            with self._sessions.begin() as session:
                row = session.scalars(
                    update(PipelineExecutionRow)
                    .where(
                        PipelineExecutionRow.id == execution_id,
                        PipelineExecutionRow.status == RunStatus.RUNNING.value,
                    )
                    .values(
                        status=status.value,
                        completed_at=completed_at,
                        error_code=None,
                        error_detail=None,
                        updated_at=completed_at,
                    )
                    .returning(PipelineExecutionRow)
                ).one_or_none()
                if row is None:
                    row = session.get(PipelineExecutionRow, execution_id)
                if row is None or row.status != status.value:
                    raise RepositoryError("pipeline execution is missing or already terminal")
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL pipeline-execution completion is unavailable"
            ) from error
        return _pipeline_execution_from_row(row)

    def restart_pipeline_execution(
        self,
        execution_id: UUID,
        *,
        started_at: datetime,
        deadline_at: datetime,
        contract: PipelineExecutionContract,
    ) -> PipelineExecution:
        try:
            with self._sessions.begin() as session:
                row = session.scalars(
                    select(PipelineExecutionRow)
                    .where(PipelineExecutionRow.id == execution_id)
                    .with_for_update()
                ).one_or_none()
                if row is None or row.status != RunStatus.FAILED.value:
                    raise RepositoryError("only a failed pipeline execution may restart")
                row.status = RunStatus.RUNNING.value
                row.execution_contract = _pipeline_execution_contract_values(contract)
                row.started_at = started_at
                row.deadline_at = deadline_at
                row.completed_at = None
                row.error_code = None
                row.error_detail = None
                row.updated_at = started_at
                session.flush()
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL pipeline-execution restart is unavailable"
            ) from error
        return _pipeline_execution_from_row(row)

    def fail_pipeline_execution(
        self,
        execution_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> PipelineExecution:
        normalized_code = error_code.strip()[:80]
        normalized_detail = error_detail.strip()[:1000]
        if not normalized_code or not normalized_detail:
            raise RepositoryError("pipeline execution failure requires a stable code and detail")
        try:
            with self._sessions.begin() as session:
                row = session.scalars(
                    update(PipelineExecutionRow)
                    .where(
                        PipelineExecutionRow.id == execution_id,
                        PipelineExecutionRow.status == RunStatus.RUNNING.value,
                    )
                    .values(
                        status=RunStatus.FAILED.value,
                        completed_at=completed_at,
                        error_code=normalized_code,
                        error_detail=normalized_detail,
                        updated_at=completed_at,
                    )
                    .returning(PipelineExecutionRow)
                ).one_or_none()
                if row is None:
                    row = session.get(PipelineExecutionRow, execution_id)
                if row is None or row.status != RunStatus.FAILED.value:
                    raise RepositoryError("pipeline execution is missing or already terminal")
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL pipeline-execution failure persistence is unavailable"
            ) from error
        return _pipeline_execution_from_row(row)

    @contextmanager
    def daily_run_lock(self, topic_id: UUID, logical_date: date) -> Generator[None]:
        key = _child_run_advisory_key(topic_id, logical_date)
        with self._advisory_lock(
            key,
            duplicate_message=(
                f"another daily run holds the lock for {topic_id} on {logical_date}"
            ),
        ):
            yield

    @contextmanager
    def _advisory_lock(self, key: int, *, duplicate_message: str) -> Generator[None]:
        try:
            with self._engine.connect() as connection:
                acquired = connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_key)"), {"lock_key": key}
                ).scalar_one()
                if not acquired:
                    raise DuplicateDailyRunError(duplicate_message)
                # pg_advisory_lock is session-scoped. End the implicit SQLAlchemy
                # transaction immediately so external PDF/parser/model calls do
                # not hold an idle database transaction while the connection
                # continues to own the lock.
                connection.commit()
                try:
                    yield
                finally:
                    released = connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": key}
                    ).scalar_one()
                    connection.commit()
                    if not released:
                        raise RepositoryError("PostgreSQL advisory lock ownership was lost")
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL advisory lock is unavailable") from error

    def upsert_topic(self, topic: TopicConfig) -> StoredTopic:
        values = {
            "id": topic.id,
            "slug": topic.slug,
            "name": topic.name,
            "description": topic.description,
            "categories": list(topic.categories),
            "include_terms": list(topic.include_terms),
            "exclude_terms": list(topic.exclude_terms),
            "overlap_hours": topic.overlap_hours,
            "initial_lookback_days": topic.initial_lookback_days,
            "max_results": topic.max_results,
            "representative_full_text_count": topic.representative_full_text_count,
            "schema_version": topic.schema_version,
        }
        statement = insert(TopicRow).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[TopicRow.id],
            set_={
                **{key: statement.excluded[key] for key in values if key != "id"},
                "updated_at": func.now(),
            },
        ).returning(TopicRow.created_at)
        try:
            with self._sessions.begin() as session:
                created_at = session.execute(statement).scalar_one()
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL topic persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected topic identity constraints"
            ) from error
        return StoredTopic(config=topic, created_at=created_at)

    def get_ingestion_cursor(self, topic_id: UUID) -> IngestionCursor | None:
        try:
            with self._sessions() as session:
                row = session.get(IngestionCursorRow, topic_id)
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL cursor read is unavailable") from error
        return None if row is None else _cursor_from_row(row)

    def get_run_for_date(
        self,
        topic_id: UUID,
        logical_date: date,
        *,
        pipeline_execution_id: UUID | None = None,
    ) -> DailyRun | None:
        statement = select(DailyRunRow).where(
            DailyRunRow.topic_id == topic_id,
            DailyRunRow.logical_date == logical_date,
            DailyRunRow.operation == RunOperation.ARXIV_INGESTION.value,
            DailyRunRow.pipeline_execution_id == pipeline_execution_id,
        )
        try:
            with self._sessions() as session:
                row = session.scalars(statement).one_or_none()
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL run read is unavailable") from error
        return None if row is None else _run_from_row(row)

    def start_ingestion_run(
        self,
        *,
        topic_id: UUID,
        logical_date: date,
        started_at: datetime,
        cursor_from: datetime,
        cursor_to: datetime,
        pipeline_execution_mode: PipelineExecutionMode = PipelineExecutionMode.STANDALONE,
        pipeline_selection_limit: int | None = None,
        pipeline_execution_id: UUID | None = None,
    ) -> DailyRun:
        run_id = uuid4()
        statement = (
            insert(DailyRunRow)
            .values(
                id=run_id,
                topic_id=topic_id,
                logical_date=logical_date,
                operation=RunOperation.ARXIV_INGESTION.value,
                pipeline_execution_id=pipeline_execution_id,
                pipeline_execution_mode=pipeline_execution_mode.value,
                pipeline_selection_limit=pipeline_selection_limit,
                analysis_scope=None,
                status=RunStatus.RUNNING.value,
                started_at=started_at,
                completed_at=None,
                cursor_from=cursor_from,
                cursor_to=cursor_to,
                discovered_count=0,
                normalized_count=0,
                selected_count=0,
                completed_count=0,
                failed_count=0,
                error_code=None,
                error_detail=None,
                schema_version=1,
                created_at=started_at,
            )
            .returning(DailyRunRow)
        )
        try:
            with self._sessions.begin() as session:
                row = session.scalars(statement).one()
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL run creation is unavailable") from error
        return _run_from_row(row)

    def restart_ingestion_run(
        self,
        run_id: UUID,
        *,
        started_at: datetime,
        cursor_from: datetime,
        cursor_to: datetime,
        pipeline_selection_limit: int | None,
    ) -> DailyRun:
        try:
            with self._sessions.begin() as session:
                row = session.scalars(
                    update(DailyRunRow)
                    .where(
                        DailyRunRow.id == run_id,
                        DailyRunRow.operation == RunOperation.ARXIV_INGESTION.value,
                        DailyRunRow.status.in_((RunStatus.RUNNING.value, RunStatus.FAILED.value)),
                    )
                    .values(
                        status=RunStatus.RUNNING.value,
                        started_at=started_at,
                        completed_at=None,
                        cursor_from=cursor_from,
                        cursor_to=cursor_to,
                        pipeline_selection_limit=pipeline_selection_limit,
                        discovered_count=0,
                        normalized_count=0,
                        selected_count=0,
                        completed_count=0,
                        failed_count=0,
                        error_code=None,
                        error_detail=None,
                    )
                    .returning(DailyRunRow)
                ).one_or_none()
                if row is None:
                    raise RepositoryError("ingestion run is missing or cannot resume")
                if session.scalar(
                    select(func.count(RunItemRow.id)).where(RunItemRow.run_id == run_id)
                ):
                    raise RepositoryError(
                        "ingestion run with a persisted batch cannot be restarted"
                    )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL ingestion-run restart is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError("PostgreSQL rejected ingestion-run restart") from error
        return _run_from_row(row)

    def persist_ingestion_selection(
        self,
        run_id: UUID,
        *,
        selected_paper_version_ids: tuple[UUID, ...],
        updated_at: datetime,
    ) -> None:
        if len(set(selected_paper_version_ids)) != len(selected_paper_version_ids):
            raise RepositoryError("pipeline paper selection contains duplicate identities")
        try:
            with self._sessions.begin() as session:
                run_row = session.scalars(
                    select(DailyRunRow).where(DailyRunRow.id == run_id).with_for_update()
                ).one_or_none()
                if (
                    run_row is None
                    or run_row.operation != RunOperation.ARXIV_INGESTION.value
                    or run_row.status != RunStatus.COMPLETE.value
                    or run_row.pipeline_execution_mode == PipelineExecutionMode.STANDALONE.value
                    or run_row.pipeline_selection_limit is None
                ):
                    raise RepositoryIntegrityError(
                        "pipeline selection requires a completed pipeline ingestion run"
                    )
                if len(selected_paper_version_ids) > run_row.pipeline_selection_limit:
                    raise RepositoryIntegrityError(
                        "pipeline selection exceeds its persisted paper limit"
                    )
                item_rows = tuple(
                    session.scalars(
                        select(RunItemRow)
                        .where(RunItemRow.run_id == run_id)
                        .order_by(RunItemRow.paper_id)
                        .with_for_update()
                    )
                )
                available_ids = {row.paper_version_id for row in item_rows}
                selected_ids = set(selected_paper_version_ids)
                if not selected_ids.issubset(available_ids):
                    raise RepositoryIntegrityError(
                        "pipeline selection references a paper outside its ingestion run"
                    )
                normalized = all(
                    row.status == RunItemStatus.COMPLETED.value
                    and row.stage == PaperStage.NORMALIZED.value
                    for row in item_rows
                )
                decided = all(
                    row.status == RunItemStatus.COMPLETED.value
                    and row.stage in (PaperStage.SELECTED.value, PaperStage.RELEVANCE_SCORED.value)
                    for row in item_rows
                )
                if decided:
                    persisted_selected = {
                        row.paper_version_id
                        for row in item_rows
                        if row.stage == PaperStage.SELECTED.value
                    }
                    if persisted_selected != selected_ids:
                        raise RepositoryIntegrityError(
                            "persisted pipeline selection conflicts with the requested versions"
                        )
                    return
                if not normalized:
                    raise RepositoryIntegrityError(
                        "pipeline selection requires normalized or consistently decided items"
                    )
                for row in item_rows:
                    row.stage = (
                        PaperStage.SELECTED.value
                        if row.paper_version_id in selected_ids
                        else PaperStage.RELEVANCE_SCORED.value
                    )
                    row.updated_at = updated_at
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL pipeline selection persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected pipeline selection persistence"
            ) from error

    def persist_arxiv_batch_and_complete(
        self,
        *,
        topic: TopicConfig,
        run_id: UUID,
        records: tuple[ArxivPaperRecord, ...],
        watermark: datetime,
        advance_shared_cursor: bool,
        persisted_at: datetime,
        completed_at: datetime,
    ) -> DailyRun:
        items: list[RunItem] = []
        try:
            with self._sessions.begin() as session:
                locked_run = session.scalars(
                    select(DailyRunRow)
                    .where(
                        DailyRunRow.id == run_id,
                        DailyRunRow.operation == RunOperation.ARXIV_INGESTION.value,
                        DailyRunRow.status == RunStatus.RUNNING.value,
                    )
                    .with_for_update()
                ).one_or_none()
                if locked_run is None:
                    raise RepositoryError(f"run {run_id} is missing or no longer running")
                expected_cursor_policy = (
                    locked_run.pipeline_execution_mode != PipelineExecutionMode.SMOKE.value
                )
                if advance_shared_cursor is not expected_cursor_policy:
                    raise RepositoryIntegrityError(
                        "ingestion cursor policy does not match the persisted execution mode"
                    )
                for record in records:
                    items.append(
                        self._persist_record(
                            session,
                            topic=topic,
                            run_id=run_id,
                            record=record,
                            persisted_at=persisted_at,
                        )
                    )
                if advance_shared_cursor:
                    self._advance_cursor(
                        session,
                        topic_id=topic.id,
                        watermark=watermark,
                        persisted_at=persisted_at,
                    )
                row = session.scalars(
                    update(DailyRunRow)
                    .where(
                        DailyRunRow.id == run_id,
                        DailyRunRow.status == RunStatus.RUNNING.value,
                    )
                    .values(
                        status=RunStatus.COMPLETE.value,
                        completed_at=completed_at,
                        discovered_count=len(records),
                        normalized_count=len(items),
                        selected_count=0,
                        completed_count=0,
                        failed_count=0,
                        error_code=None,
                        error_detail=None,
                    )
                    .returning(DailyRunRow)
                ).one_or_none()
                if row is None:
                    raise RepositoryError(f"run {run_id} is missing or no longer running")
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL arXiv batch persistence is unavailable"
            ) from error
        except (IntegrityError, DataError) as error:
            raise RepositoryIntegrityError("PostgreSQL rejected arXiv batch persistence") from error
        return _run_from_row(row)

    def list_historical_representative_arxiv_ids(
        self,
        topic_id: UUID,
        *,
        limit: int,
    ) -> tuple[str, ...]:
        if not 1 <= limit <= MAX_REPRESENTATIVE_FULL_TEXT_COUNT:
            raise RepositoryIntegrityError(
                "historical representative lookup exceeds the configured run bound"
            )
        statement = (
            select(
                ExternalPaperStubRow.arxiv_id,
                ExternalPaperStubRow.full_text_available,
                HistoricalCorpusEntryRow.representative_rank,
            )
            .join(
                HistoricalCorpusEntryRow,
                HistoricalCorpusEntryRow.external_paper_id == ExternalPaperStubRow.id,
            )
            .where(
                HistoricalCorpusEntryRow.topic_id == topic_id,
                HistoricalCorpusEntryRow.representative_rank.is_not(None),
            )
            .order_by(HistoricalCorpusEntryRow.representative_rank)
            .limit(limit)
        )
        try:
            with self._sessions() as session:
                rows = tuple(session.execute(statement))
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL historical representative lookup is unavailable"
            ) from error
        ranks = [row.representative_rank for row in rows]
        if ranks != list(range(1, len(ranks) + 1)) or any(
            row.arxiv_id is None or not row.full_text_available for row in rows
        ):
            raise RepositoryIntegrityError(
                "historical representatives do not have consecutive arXiv identities"
            )
        return tuple(str(row.arxiv_id) for row in rows)

    def list_historical_representative_version_ids(
        self,
        topic_id: UUID,
        *,
        limit: int,
    ) -> tuple[UUID, ...]:
        if not 1 <= limit <= MAX_REPRESENTATIVE_FULL_TEXT_COUNT:
            raise RepositoryIntegrityError(
                "historical representative version lookup exceeds the configured run bound"
            )
        statement = (
            select(
                HistoricalCorpusEntryRow.representative_rank,
                HistoricalCorpusEntryRow.local_paper_version_id,
            )
            .where(
                HistoricalCorpusEntryRow.topic_id == topic_id,
                HistoricalCorpusEntryRow.representative_rank.is_not(None),
            )
            .order_by(HistoricalCorpusEntryRow.representative_rank)
            .limit(limit)
        )
        try:
            with self._sessions() as session:
                rows = tuple(session.execute(statement))
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL historical representative version lookup is unavailable"
            ) from error
        ranks = [row.representative_rank for row in rows]
        if ranks != list(range(1, len(ranks) + 1)):
            raise RepositoryIntegrityError("historical representative ranks are not consecutive")
        if any(row.local_paper_version_id is None for row in rows):
            return ()
        return tuple(row.local_paper_version_id for row in rows)

    def persist_historical_arxiv_records(
        self,
        *,
        topic: TopicConfig,
        records: tuple[ArxivPaperRecord, ...],
        persisted_at: datetime,
    ) -> tuple[UUID, ...]:
        if not records:
            return ()
        if len(records) > topic.representative_full_text_count:
            raise RepositoryIntegrityError(
                "historical arXiv records exceed the topic representative limit"
            )
        canonical_ids = tuple(record.canonical_arxiv_id for record in records)
        if len(set(canonical_ids)) != len(canonical_ids):
            raise RepositoryIntegrityError(
                "historical arXiv records contain duplicate canonical identities"
            )
        try:
            with self._sessions.begin() as session:
                representative_rows = tuple(
                    session.execute(
                        select(
                            HistoricalCorpusEntryRow,
                            ExternalPaperStubRow.arxiv_id,
                        )
                        .join(
                            ExternalPaperStubRow,
                            ExternalPaperStubRow.id == HistoricalCorpusEntryRow.external_paper_id,
                        )
                        .where(
                            HistoricalCorpusEntryRow.topic_id == topic.id,
                            HistoricalCorpusEntryRow.representative_rank.is_not(None),
                            ExternalPaperStubRow.arxiv_id.in_(canonical_ids),
                        )
                        .with_for_update()
                    )
                )
                by_arxiv_id = {
                    str(arxiv_id): entry_row
                    for entry_row, arxiv_id in representative_rows
                    if arxiv_id is not None
                }
                if set(by_arxiv_id) != set(canonical_ids):
                    raise RepositoryIntegrityError(
                        "historical arXiv records are not ranked representatives for the topic"
                    )

                persisted_by_rank: list[tuple[int, UUID]] = []
                for record in records:
                    paper_id, version_id = self._persist_arxiv_metadata(
                        session,
                        topic=topic,
                        record=record,
                        persisted_at=persisted_at,
                    )
                    current_version = session.scalar(
                        select(PaperRow.current_version).where(PaperRow.id == paper_id)
                    )
                    if current_version != record.version:
                        raise RepositoryIntegrityError(
                            "historical arXiv metadata is older than the current local version"
                        )
                    entry_row = by_arxiv_id[record.canonical_arxiv_id]
                    entry_row.local_paper_id = paper_id
                    entry_row.local_paper_version_id = version_id
                    if entry_row.representative_rank is None:
                        raise RepositoryIntegrityError(
                            "historical representative rank disappeared during materialization"
                        )
                    persisted_by_rank.append((entry_row.representative_rank, version_id))
                session.flush()
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL historical arXiv materialization is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected historical arXiv materialization"
            ) from error
        persisted_by_rank.sort(key=lambda value: value[0])
        return tuple(version_id for _rank, version_id in persisted_by_rank)

    def materialize_search_candidate_arxiv_records(
        self,
        *,
        topic: TopicConfig,
        candidates: tuple[tuple[UUID, ArxivPaperRecord], ...],
        persisted_at: datetime,
    ) -> tuple[tuple[UUID, UUID, UUID], ...]:
        if not candidates:
            return ()
        candidate_ids = tuple(candidate_id for candidate_id, _record in candidates)
        if len(set(candidate_ids)) != len(candidate_ids):
            raise RepositoryIntegrityError(
                "search-candidate materialization identities must be unique"
            )
        try:
            with self._sessions.begin() as session:
                rows = tuple(
                    session.execute(
                        select(
                            SearchCandidateRow,
                            ExternalPaperStubRow,
                            SearchSessionRow,
                        )
                        .join(
                            ExternalPaperStubRow,
                            ExternalPaperStubRow.id == SearchCandidateRow.external_paper_id,
                        )
                        .join(
                            SearchSessionRow,
                            SearchSessionRow.id == SearchCandidateRow.session_id,
                        )
                        .where(SearchCandidateRow.id.in_(candidate_ids))
                        .with_for_update()
                    )
                )
                by_candidate_id = {
                    candidate_row.id: (candidate_row, external_row, search_row)
                    for candidate_row, external_row, search_row in rows
                }
                if set(by_candidate_id) != set(candidate_ids):
                    raise RepositoryIntegrityError(
                        "search candidate is missing during arXiv materialization"
                    )
                materialized: list[tuple[UUID, UUID, UUID]] = []
                for candidate_id, record in candidates:
                    candidate_row, external_row, search_row = by_candidate_id[candidate_id]
                    if (
                        search_row.topic_id != topic.id
                        or search_row.status != SearchSessionStatus.COMPLETE.value
                        or candidate_row.comparison_target_decision
                        != ComparisonTargetDecision.TARGET.value
                        or external_row.arxiv_id != record.canonical_arxiv_id
                    ):
                        raise RepositoryIntegrityError(
                            "search-candidate arXiv materialization conflicts with "
                            "target provenance"
                        )
                    paper_id, version_id = self._persist_arxiv_metadata(
                        session,
                        topic=topic,
                        record=record,
                        persisted_at=persisted_at,
                    )
                    if candidate_row.local_paper_id is not None and (
                        candidate_row.local_paper_id != paper_id
                        or candidate_row.local_paper_version_id != version_id
                    ):
                        raise RepositoryIntegrityError(
                            "search candidate changed local arXiv ownership"
                        )
                    candidate_row.local_paper_id = paper_id
                    candidate_row.local_paper_version_id = version_id
                    materialized.append((candidate_id, paper_id, version_id))
                session.flush()
                return tuple(materialized)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL search-candidate arXiv materialization is unavailable"
            ) from error
        except (IntegrityError, DataError) as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected search-candidate arXiv materialization"
            ) from error

    def _persist_record(
        self,
        session: Session,
        *,
        topic: TopicConfig,
        run_id: UUID,
        record: ArxivPaperRecord,
        persisted_at: datetime,
    ) -> RunItem:
        paper_id, version_id = self._persist_arxiv_metadata(
            session,
            topic=topic,
            record=record,
            persisted_at=persisted_at,
        )

        item_id = uuid5(run_id, str(version_id))
        session.execute(
            insert(RunItemRow)
            .values(
                id=item_id,
                run_id=run_id,
                paper_id=paper_id,
                paper_version_id=version_id,
                stage=PaperStage.NORMALIZED.value,
                status=RunItemStatus.COMPLETED.value,
                failed_stage=None,
                error_code=None,
                retryable=None,
                error_detail=None,
                schema_version=1,
                created_at=persisted_at,
                updated_at=persisted_at,
            )
            .on_conflict_do_nothing(index_elements=[RunItemRow.run_id, RunItemRow.paper_version_id])
        )
        return RunItem(
            id=item_id,
            run_id=run_id,
            paper_id=paper_id,
            paper_version_id=version_id,
            stage=PaperStage.NORMALIZED,
            status=RunItemStatus.COMPLETED,
            failed_stage=None,
            error_code=None,
            retryable=None,
            error_detail=None,
            schema_version=1,
            created_at=persisted_at,
            updated_at=persisted_at,
        )

    def _persist_arxiv_metadata(
        self,
        session: Session,
        *,
        topic: TopicConfig,
        record: ArxivPaperRecord,
        persisted_at: datetime,
    ) -> tuple[UUID, UUID]:
        paper_id = stable_paper_id(record.canonical_arxiv_id)
        version_id = stable_paper_version_id(record.canonical_arxiv_id, record.version)
        paper_statement = insert(PaperRow).values(
            id=paper_id,
            canonical_arxiv_id=record.canonical_arxiv_id,
            title=record.title,
            abstract=record.abstract,
            current_version=record.version,
            first_submitted_at=record.submitted_at,
            latest_updated_at=record.updated_at,
            primary_category=record.primary_category,
            categories=list(record.categories),
            authors=list(record.authors),
            pdf_url=record.pdf_url,
            schema_version=1,
            created_at=persisted_at,
            updated_at=persisted_at,
        )
        is_newer = paper_statement.excluded.current_version > PaperRow.current_version
        session.execute(
            paper_statement.on_conflict_do_update(
                index_elements=[PaperRow.canonical_arxiv_id],
                set_={
                    "current_version": func.greatest(
                        PaperRow.current_version, paper_statement.excluded.current_version
                    ),
                    "title": case((is_newer, paper_statement.excluded.title), else_=PaperRow.title),
                    "abstract": case(
                        (is_newer, paper_statement.excluded.abstract), else_=PaperRow.abstract
                    ),
                    "first_submitted_at": func.least(
                        PaperRow.first_submitted_at, paper_statement.excluded.first_submitted_at
                    ),
                    "latest_updated_at": case(
                        (is_newer, paper_statement.excluded.latest_updated_at),
                        else_=PaperRow.latest_updated_at,
                    ),
                    "primary_category": case(
                        (is_newer, paper_statement.excluded.primary_category),
                        else_=PaperRow.primary_category,
                    ),
                    "categories": case(
                        (is_newer, paper_statement.excluded.categories), else_=PaperRow.categories
                    ),
                    "authors": case(
                        (is_newer, paper_statement.excluded.authors), else_=PaperRow.authors
                    ),
                    "pdf_url": case(
                        (is_newer, paper_statement.excluded.pdf_url), else_=PaperRow.pdf_url
                    ),
                    "updated_at": persisted_at,
                },
            )
        )

        inserted_version_id = session.scalar(
            insert(PaperVersionRow)
            .values(
                id=version_id,
                paper_id=paper_id,
                version=record.version,
                title=record.title,
                abstract=record.abstract,
                submitted_at=record.submitted_at,
                updated_at=record.updated_at,
                primary_category=record.primary_category,
                categories=list(record.categories),
                authors=list(record.authors),
                pdf_url=record.pdf_url,
                source_url=record.source_url,
                schema_version=1,
                created_at=persisted_at,
            )
            .on_conflict_do_nothing(index_elements=[PaperVersionRow.id])
            .returning(PaperVersionRow.id)
        )

        identity_id = stable_source_identity_id(
            "arxiv", record.canonical_arxiv_id, f"v{record.version}"
        )
        session.execute(
            insert(PaperSourceIdentityRow)
            .values(
                id=identity_id,
                paper_id=paper_id,
                paper_version_id=version_id,
                source="arxiv",
                external_id=record.canonical_arxiv_id,
                source_version=f"v{record.version}",
                source_url=record.source_url,
                schema_version=1,
                created_at=persisted_at,
            )
            .on_conflict_do_nothing(index_elements=[PaperSourceIdentityRow.id])
        )

        if inserted_version_id is not None:
            display_names = tuple(normalize_author_name(name) for name in record.authors)
            if len({name.casefold() for name in display_names}) != len(display_names):
                raise RepositoryIntegrityError(
                    "arXiv paper version contains duplicate normalized authors"
                )

            for position, display_name in enumerate(display_names):
                author_id = stable_author_id(display_name)
                session.execute(
                    insert(AuthorRow)
                    .values(
                        id=author_id,
                        normalized_name=display_name.casefold(),
                        display_name=display_name,
                        schema_version=1,
                        created_at=persisted_at,
                    )
                    .on_conflict_do_nothing(index_elements=[AuthorRow.id])
                )
                session.execute(
                    insert(PaperVersionAuthorRow).values(
                        paper_version_id=version_id,
                        author_id=author_id,
                        position=position,
                    )
                )

        topic_paper_statement = insert(TopicPaperRow).values(
            topic_id=topic.id,
            paper_id=paper_id,
            first_discovered_at=persisted_at,
            last_discovered_at=persisted_at,
        )
        session.execute(
            topic_paper_statement.on_conflict_do_update(
                index_elements=[TopicPaperRow.topic_id, TopicPaperRow.paper_id],
                set_={
                    "last_discovered_at": func.greatest(
                        TopicPaperRow.last_discovered_at,
                        topic_paper_statement.excluded.last_discovered_at,
                    )
                },
            )
        )
        return paper_id, version_id

    def _advance_cursor(
        self,
        session: Session,
        *,
        topic_id: UUID,
        watermark: datetime,
        persisted_at: datetime,
    ) -> None:
        statement = insert(IngestionCursorRow).values(
            topic_id=topic_id,
            watermark=watermark,
            schema_version=1,
            created_at=persisted_at,
            updated_at=persisted_at,
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[IngestionCursorRow.topic_id],
                set_={
                    "watermark": func.greatest(
                        IngestionCursorRow.watermark, statement.excluded.watermark
                    ),
                    "updated_at": persisted_at,
                },
            )
        )

    def fail_ingestion_run(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> DailyRun:
        statement = (
            update(DailyRunRow)
            .where(DailyRunRow.id == run_id, DailyRunRow.status == RunStatus.RUNNING.value)
            .values(
                status=RunStatus.FAILED.value,
                completed_at=completed_at,
                failed_count=0,
                error_code=error_code,
                error_detail=error_detail[:1000],
            )
            .returning(DailyRunRow)
        )
        try:
            with self._sessions.begin() as session:
                row = session.scalars(statement).one_or_none()
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL run failure recording is unavailable"
            ) from error
        if row is None:
            raise RepositoryError(f"run {run_id} is missing or no longer running")
        return _run_from_row(row)

    def check_ready(self) -> None:
        try:
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1")).scalar_one()
                try:
                    revision = connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one_or_none()
                except ProgrammingError as error:
                    raise MigrationIncompatibleError(
                        "database migration is not at required revision "
                        f"{EXPECTED_DATABASE_REVISION}"
                    ) from error
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL readiness check failed") from error
        if revision != EXPECTED_DATABASE_REVISION:
            raise MigrationIncompatibleError(
                f"database revision {revision!r} does not match {EXPECTED_DATABASE_REVISION!r}"
            )

    def list_topics(self) -> tuple[StoredTopic, ...]:
        statement = select(TopicRow).order_by(TopicRow.name, TopicRow.slug)
        try:
            with self._sessions() as session:
                rows = tuple(session.scalars(statement))
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL topic query is unavailable") from error
        return tuple(_topic_from_row(row) for row in rows)

    def list_papers(
        self, *, topic_slug: str | None, limit: int, offset: int
    ) -> tuple[tuple[Paper, ...], int]:
        base = select(PaperRow)
        count = select(func.count(PaperRow.id))
        if topic_slug is not None:
            base = base.join(TopicPaperRow).join(TopicRow).where(TopicRow.slug == topic_slug)
            count = count.join(TopicPaperRow).join(TopicRow).where(TopicRow.slug == topic_slug)
        statement = (
            base.order_by(PaperRow.latest_updated_at.desc(), PaperRow.canonical_arxiv_id)
            .limit(limit)
            .offset(offset)
        )
        try:
            with self._sessions() as session:
                rows = tuple(session.scalars(statement))
                total = session.execute(count).scalar_one()
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL paper query is unavailable") from error
        return tuple(_paper_from_row(row) for row in rows), total

    def list_published_papers(
        self, *, topic_slug: str | None, limit: int, offset: int
    ) -> tuple[tuple[Paper, ...], int]:
        base = (
            select(PaperRow.id)
            .join(RunItemRow, RunItemRow.paper_id == PaperRow.id)
            .join(DailyRunRow, DailyRunRow.id == RunItemRow.run_id)
            .where(*_canonical_publication_item_predicates())
        )
        count = (
            select(func.count(func.distinct(PaperRow.id)))
            .join(RunItemRow, RunItemRow.paper_id == PaperRow.id)
            .join(DailyRunRow, DailyRunRow.id == RunItemRow.run_id)
            .where(*_canonical_publication_item_predicates())
        )
        if topic_slug is not None:
            base = base.join(TopicRow, TopicRow.id == DailyRunRow.topic_id).where(
                TopicRow.slug == topic_slug
            )
            count = count.join(TopicRow, TopicRow.id == DailyRunRow.topic_id).where(
                TopicRow.slug == topic_slug
            )
        statement = (
            base.group_by(PaperRow.id)
            .order_by(func.max(DailyRunRow.logical_date).desc(), PaperRow.id)
            .limit(limit)
            .offset(offset)
        )
        try:
            with self._sessions() as session:
                paper_ids = tuple(session.scalars(statement))
                total = int(session.scalar(count) or 0)
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL published-paper query is unavailable"
            ) from error
        details = tuple(self.get_published_paper(paper_id) for paper_id in paper_ids)
        if any(detail is None for detail in details):
            raise RepositoryIntegrityError("published paper disappeared during projection")
        return tuple(detail.paper for detail in details if detail is not None), total

    def get_paper(self, paper_id: UUID) -> PaperDetail | None:
        versions_statement = (
            select(PaperVersionRow)
            .where(PaperVersionRow.paper_id == paper_id)
            .order_by(PaperVersionRow.version.desc())
        )
        identities_statement = (
            select(PaperSourceIdentityRow)
            .where(PaperSourceIdentityRow.paper_id == paper_id)
            .order_by(PaperSourceIdentityRow.source, PaperSourceIdentityRow.source_version.desc())
        )
        topics_statement = (
            select(TopicRow.slug)
            .join(TopicPaperRow, TopicPaperRow.topic_id == TopicRow.id)
            .where(TopicPaperRow.paper_id == paper_id)
            .order_by(TopicRow.slug)
        )
        try:
            with self._sessions() as session:
                paper_row = session.get(PaperRow, paper_id)
                if paper_row is None:
                    return None
                version_rows = tuple(session.scalars(versions_statement))
                identity_rows = tuple(session.scalars(identities_statement))
                topic_slugs = tuple(session.scalars(topics_statement))
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL paper detail query is unavailable"
            ) from error
        return PaperDetail(
            paper=_paper_from_row(paper_row),
            versions=tuple(
                _version_from_row(row, paper_row.canonical_arxiv_id) for row in version_rows
            ),
            source_identities=tuple(_identity_from_row(row) for row in identity_rows),
            topic_slugs=topic_slugs,
        )

    def get_published_paper(self, paper_id: UUID) -> PaperDetail | None:
        version_statement = (
            select(PaperVersionRow)
            .join(RunItemRow, RunItemRow.paper_version_id == PaperVersionRow.id)
            .join(DailyRunRow, DailyRunRow.id == RunItemRow.run_id)
            .where(
                PaperVersionRow.paper_id == paper_id,
                *_canonical_publication_item_predicates(),
            )
            .distinct()
            .order_by(PaperVersionRow.version.desc())
        )
        topic_statement = (
            select(TopicRow.slug)
            .join(DailyRunRow, DailyRunRow.topic_id == TopicRow.id)
            .join(RunItemRow, RunItemRow.run_id == DailyRunRow.id)
            .where(
                RunItemRow.paper_id == paper_id,
                *_canonical_publication_item_predicates(),
            )
            .distinct()
            .order_by(TopicRow.slug)
        )
        try:
            with self._sessions() as session:
                paper_row = session.get(PaperRow, paper_id)
                version_rows = tuple(session.scalars(version_statement))
                if paper_row is None or not version_rows:
                    return None
                version_ids = tuple(row.id for row in version_rows)
                identity_rows = tuple(
                    session.scalars(
                        select(PaperSourceIdentityRow)
                        .where(PaperSourceIdentityRow.paper_version_id.in_(version_ids))
                        .order_by(
                            PaperSourceIdentityRow.source,
                            PaperSourceIdentityRow.source_version.desc(),
                        )
                    )
                )
                topic_slugs = tuple(session.scalars(topic_statement))
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL published-paper detail query is unavailable"
            ) from error
        latest = version_rows[0]
        paper = replace(
            _paper_from_row(paper_row),
            title=latest.title,
            abstract=latest.abstract,
            current_version=latest.version,
            latest_updated_at=latest.updated_at,
            primary_category=latest.primary_category,
            categories=tuple(latest.categories),
            authors=tuple(latest.authors),
            pdf_url=latest.pdf_url,
        )
        return PaperDetail(
            paper=paper,
            versions=tuple(
                _version_from_row(row, paper_row.canonical_arxiv_id) for row in version_rows
            ),
            source_identities=tuple(_identity_from_row(row) for row in identity_rows),
            topic_slugs=topic_slugs,
        )

    def get_analysis_targets(
        self, topic_id: UUID, paper_ids: tuple[UUID, ...]
    ) -> tuple[AnalysisTarget, ...]:
        if not paper_ids:
            return ()
        statement = (
            select(PaperRow, PaperVersionRow)
            .join(TopicPaperRow, TopicPaperRow.paper_id == PaperRow.id)
            .join(
                PaperVersionRow,
                (PaperVersionRow.paper_id == PaperRow.id)
                & (PaperVersionRow.version == PaperRow.current_version),
            )
            .where(TopicPaperRow.topic_id == topic_id, PaperRow.id.in_(paper_ids))
        )
        try:
            with self._sessions() as session:
                rows = tuple(session.execute(statement))
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL analysis-target query is unavailable"
            ) from error
        by_paper_id = {
            paper_row.id: AnalysisTarget(
                paper=_paper_from_row(paper_row),
                version=_version_from_row(version_row, paper_row.canonical_arxiv_id),
            )
            for paper_row, version_row in rows
        }
        return tuple(by_paper_id[paper_id] for paper_id in paper_ids if paper_id in by_paper_id)

    def get_analysis_targets_by_version_ids(
        self,
        topic_id: UUID,
        paper_version_ids: tuple[UUID, ...],
    ) -> tuple[AnalysisTarget, ...]:
        if not paper_version_ids:
            return ()
        statement = (
            select(PaperRow, PaperVersionRow)
            .join(TopicPaperRow, TopicPaperRow.paper_id == PaperRow.id)
            .join(PaperVersionRow, PaperVersionRow.paper_id == PaperRow.id)
            .where(
                TopicPaperRow.topic_id == topic_id,
                PaperVersionRow.id.in_(paper_version_ids),
            )
        )
        try:
            with self._sessions() as session:
                rows = tuple(session.execute(statement))
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL exact analysis-target query is unavailable"
            ) from error
        by_version_id = {
            version_row.id: AnalysisTarget(
                paper=_paper_from_row(paper_row),
                version=_version_from_row(version_row, paper_row.canonical_arxiv_id),
            )
            for paper_row, version_row in rows
        }
        return tuple(
            by_version_id[version_id]
            for version_id in paper_version_ids
            if version_id in by_version_id
        )

    def get_analyzed_paper_version_ids(
        self,
        paper_version_ids: tuple[UUID, ...],
        *,
        analysis_scope: AnalysisScope,
    ) -> frozenset[UUID]:
        if not paper_version_ids:
            return frozenset()
        statement = select(PaperAnalysisRow.paper_version_id).where(
            PaperAnalysisRow.paper_version_id.in_(paper_version_ids),
            PaperAnalysisRow.analysis_scope == analysis_scope.value,
        )
        try:
            with self._sessions() as session:
                return frozenset(session.scalars(statement))
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL analyzed-version lookup is unavailable"
            ) from error

    def get_reusable_analyzed_paper_version_ids(
        self,
        paper_version_ids: tuple[UUID, ...],
        *,
        analysis_scope: AnalysisScope,
        provider: str,
        configured_model: str,
        prompt_version: str,
        parser_name: str | None,
        parser_version: str | None,
    ) -> frozenset[UUID]:
        if not paper_version_ids:
            return frozenset()
        statement = select(PaperAnalysisRow.paper_version_id).where(
            PaperAnalysisRow.paper_version_id.in_(paper_version_ids),
            PaperAnalysisRow.analysis_scope == analysis_scope.value,
            PaperAnalysisRow.provider == provider,
            PaperAnalysisRow.configured_model == configured_model,
            PaperAnalysisRow.prompt_version == prompt_version,
        )
        if analysis_scope is AnalysisScope.FULL_TEXT:
            if parser_name is None or parser_version is None:
                raise RepositoryIntegrityError(
                    "full-text reusable-analysis lookup requires parser provenance"
                )
            statement = statement.join(
                ParsedPaperRow,
                ParsedPaperRow.id == PaperAnalysisRow.parsed_paper_id,
            ).where(
                ParsedPaperRow.parser_name == parser_name,
                ParsedPaperRow.parser_version == parser_version,
            )
        elif parser_name is not None or parser_version is not None:
            raise RepositoryIntegrityError(
                "abstract reusable-analysis lookup cannot carry parser provenance"
            )
        try:
            with self._sessions() as session:
                return frozenset(session.scalars(statement))
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL reusable analyzed-version lookup is unavailable"
            ) from error

    def get_canonically_published_paper_version_ids(
        self,
        paper_version_ids: tuple[UUID, ...],
    ) -> frozenset[UUID]:
        if not paper_version_ids:
            return frozenset()
        statement = (
            select(RunItemRow.paper_version_id)
            .join(DailyRunRow, DailyRunRow.id == RunItemRow.run_id)
            .where(
                RunItemRow.paper_version_id.in_(paper_version_ids),
                RunItemRow.status == RunItemStatus.COMPLETED.value,
                RunItemRow.stage == PaperStage.PUBLISHED.value,
                DailyRunRow.operation == RunOperation.PRODUCT_PUBLICATION.value,
                DailyRunRow.status.in_((RunStatus.COMPLETE.value, RunStatus.PARTIAL.value)),
                DailyRunRow.pipeline_execution_mode != PipelineExecutionMode.SMOKE.value,
            )
        )
        try:
            with self._sessions() as session:
                return frozenset(session.scalars(statement))
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL canonical publication lookup is unavailable"
            ) from error

    def attach_existing_analysis_to_run(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        analysis_scope: AnalysisScope,
        provider: str,
        configured_model: str,
        prompt_version: str,
        parser_name: str | None,
        parser_version: str | None,
        updated_at: datetime,
    ) -> bool:
        try:
            with self._sessions.begin() as session:
                run_row = session.scalars(
                    select(DailyRunRow)
                    .where(
                        DailyRunRow.id == run_id,
                        DailyRunRow.operation.in_(
                            (
                                RunOperation.STRUCTURED_ANALYSIS.value,
                                RunOperation.HISTORICAL_ANALYSIS.value,
                            )
                        ),
                        DailyRunRow.status == RunStatus.RUNNING.value,
                        DailyRunRow.analysis_scope == analysis_scope.value,
                    )
                    .with_for_update()
                ).one_or_none()
                if run_row is None:
                    raise RepositoryError("analysis run is missing or not reusable")
                item = session.scalars(
                    select(RunItemRow)
                    .where(
                        RunItemRow.run_id == run_id,
                        RunItemRow.paper_version_id == paper_version_id,
                    )
                    .with_for_update()
                ).one_or_none()
                if item is None:
                    raise RepositoryError("analysis run item is missing")
                if item.status == RunItemStatus.COMPLETED.value:
                    return item.stage == PaperStage.EVIDENCE_EXTRACTED.value
                if (
                    item.status != RunItemStatus.IN_PROGRESS.value
                    or item.stage != PaperStage.SELECTED.value
                ):
                    return False
                statement = select(PaperAnalysisRow.id)
                if analysis_scope is AnalysisScope.FULL_TEXT:
                    if parser_name is None or parser_version is None:
                        raise RepositoryError("full-text analysis reuse requires parser provenance")
                    statement = statement.join(
                        ParsedPaperRow,
                        ParsedPaperRow.id == PaperAnalysisRow.parsed_paper_id,
                    ).where(
                        ParsedPaperRow.parser_name == parser_name,
                        ParsedPaperRow.parser_version == parser_version,
                    )
                elif parser_name is not None or parser_version is not None:
                    raise RepositoryError(
                        "abstract-only analysis reuse cannot carry parser provenance"
                    )
                statement = (
                    statement.where(
                        PaperAnalysisRow.paper_version_id == paper_version_id,
                        PaperAnalysisRow.analysis_scope == analysis_scope.value,
                        PaperAnalysisRow.provider == provider,
                        PaperAnalysisRow.configured_model == configured_model,
                        PaperAnalysisRow.prompt_version == prompt_version,
                    )
                    .order_by(PaperAnalysisRow.generated_at.desc(), PaperAnalysisRow.id.desc())
                    .limit(1)
                )
                existing_id = session.scalar(statement)
                if existing_id is None:
                    return False
                item.stage = PaperStage.EVIDENCE_EXTRACTED.value
                item.status = RunItemStatus.COMPLETED.value
                item.failed_stage = None
                item.error_code = None
                item.retryable = None
                item.error_detail = None
                item.updated_at = updated_at
                return True
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL analysis-reuse transition is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected analysis-reuse ownership"
            ) from error

    def get_analysis_run_for_date(
        self,
        topic_id: UUID,
        logical_date: date,
        *,
        pipeline_execution_id: UUID | None = None,
        operation: RunOperation = RunOperation.STRUCTURED_ANALYSIS,
    ) -> DailyRun | None:
        if operation not in (
            RunOperation.STRUCTURED_ANALYSIS,
            RunOperation.HISTORICAL_ANALYSIS,
        ):
            raise RepositoryError("analysis-run lookup operation is unsupported")
        statement = select(DailyRunRow).where(
            DailyRunRow.topic_id == topic_id,
            DailyRunRow.logical_date == logical_date,
            DailyRunRow.operation == operation.value,
            DailyRunRow.pipeline_execution_id == pipeline_execution_id,
        )
        try:
            with self._sessions() as session:
                row = session.scalars(statement).one_or_none()
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL analysis-run query is unavailable"
            ) from error
        return None if row is None else _run_from_row(row)

    def start_analysis_run(
        self,
        *,
        topic_id: UUID,
        logical_date: date,
        analysis_scope: AnalysisScope,
        started_at: datetime,
        targets: tuple[AnalysisTarget, ...],
        pipeline_execution_mode: PipelineExecutionMode = PipelineExecutionMode.STANDALONE,
        pipeline_selection_limit: int | None = None,
        pipeline_execution_id: UUID | None = None,
        operation: RunOperation = RunOperation.STRUCTURED_ANALYSIS,
    ) -> DailyRun:
        if not targets:
            raise RepositoryError("analysis run requires selected targets")
        if operation not in (
            RunOperation.STRUCTURED_ANALYSIS,
            RunOperation.HISTORICAL_ANALYSIS,
        ):
            raise RepositoryError("analysis run operation is unsupported")
        run_id = uuid4()
        try:
            with self._sessions.begin() as session:
                row = session.scalars(
                    insert(DailyRunRow)
                    .values(
                        id=run_id,
                        topic_id=topic_id,
                        logical_date=logical_date,
                        operation=operation.value,
                        pipeline_execution_id=pipeline_execution_id,
                        pipeline_execution_mode=pipeline_execution_mode.value,
                        pipeline_selection_limit=pipeline_selection_limit,
                        analysis_scope=analysis_scope.value,
                        status=RunStatus.RUNNING.value,
                        started_at=started_at,
                        completed_at=None,
                        cursor_from=None,
                        cursor_to=None,
                        discovered_count=0,
                        normalized_count=0,
                        selected_count=len(targets),
                        completed_count=0,
                        failed_count=0,
                        error_code=None,
                        error_detail=None,
                        schema_version=1,
                        created_at=started_at,
                    )
                    .returning(DailyRunRow)
                ).one()
                session.add_all(
                    RunItemRow(
                        id=uuid5(run_id, f"analysis:{target.version.id}"),
                        run_id=run_id,
                        paper_id=target.paper.id,
                        paper_version_id=target.version.id,
                        stage=PaperStage.SELECTED.value,
                        status=RunItemStatus.IN_PROGRESS.value,
                        failed_stage=None,
                        error_code=None,
                        retryable=None,
                        error_detail=None,
                        schema_version=1,
                        created_at=started_at,
                        updated_at=started_at,
                    )
                    for target in targets
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL analysis-run creation is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected analysis-run ownership constraints"
            ) from error
        return _run_from_row(row)

    def restart_analysis_run(
        self,
        run_id: UUID,
        *,
        targets: tuple[AnalysisTarget, ...],
        started_at: datetime,
        pipeline_selection_limit: int | None,
    ) -> DailyRun:
        if not targets:
            raise RepositoryError("analysis-run restart requires selected targets")
        requested = {target.version.id: target.paper.id for target in targets}
        if len(requested) != len(targets):
            raise RepositoryError("analysis-run restart targets must be unique")
        try:
            with self._sessions.begin() as session:
                run_row = session.scalars(
                    select(DailyRunRow).where(DailyRunRow.id == run_id).with_for_update()
                ).one_or_none()
                if (
                    run_row is None
                    or run_row.operation
                    not in (
                        RunOperation.STRUCTURED_ANALYSIS.value,
                        RunOperation.HISTORICAL_ANALYSIS.value,
                    )
                    or run_row.status
                    not in (
                        RunStatus.RUNNING.value,
                        RunStatus.FAILED.value,
                    )
                ):
                    raise RepositoryError("analysis run is missing or cannot resume")
                if session.scalar(
                    select(func.count(ReportRow.id)).where(ReportRow.run_id == run_id)
                ):
                    raise RepositoryError("published analysis run cannot resume")
                item_rows = tuple(
                    session.scalars(
                        select(RunItemRow).where(RunItemRow.run_id == run_id).with_for_update()
                    )
                )
                persisted = {item.paper_version_id: item for item in item_rows}
                for version_id, item in tuple(persisted.items()):
                    if version_id not in requested:
                        session.delete(item)
                        persisted.pop(version_id)
                for target in targets:
                    if target.version.id not in persisted:
                        item = RunItemRow(
                            id=uuid5(run_id, f"analysis:{target.version.id}"),
                            run_id=run_id,
                            paper_id=target.paper.id,
                            paper_version_id=target.version.id,
                            stage=PaperStage.SELECTED.value,
                            status=RunItemStatus.IN_PROGRESS.value,
                            failed_stage=None,
                            error_code=None,
                            retryable=None,
                            error_detail=None,
                            schema_version=1,
                            created_at=started_at,
                            updated_at=started_at,
                        )
                        session.add(item)
                        persisted[target.version.id] = item
                completed_count = 0
                for version_id, item in persisted.items():
                    if item.paper_id != requested[version_id]:
                        raise RepositoryError("analysis target version ownership changed")
                    if item.status == RunItemStatus.COMPLETED.value:
                        completed_count += 1
                        continue
                    item.stage = PaperStage.SELECTED.value
                    item.status = RunItemStatus.IN_PROGRESS.value
                    item.failed_stage = None
                    item.error_code = None
                    item.retryable = None
                    item.error_detail = None
                    item.updated_at = started_at
                run_row.status = RunStatus.RUNNING.value
                run_row.started_at = started_at
                run_row.completed_at = None
                if pipeline_selection_limit is not None:
                    run_row.pipeline_selection_limit = pipeline_selection_limit
                run_row.selected_count = len(persisted)
                run_row.completed_count = completed_count
                run_row.failed_count = 0
                run_row.error_code = None
                run_row.error_detail = None
                session.flush()
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL analysis-run restart is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError("PostgreSQL rejected analysis-run restart") from error
        return _run_from_row(run_row)

    def advance_analysis_item(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        expected_stage: PaperStage,
        next_stage: PaperStage,
        updated_at: datetime,
    ) -> None:
        if (expected_stage, next_stage) not in {
            (PaperStage.SELECTED, PaperStage.PDF_DOWNLOADED),
            (PaperStage.PDF_DOWNLOADED, PaperStage.PARSED),
        }:
            raise RepositoryError("invalid M2 analysis stage transition")
        statement = (
            update(RunItemRow)
            .where(
                RunItemRow.run_id == run_id,
                RunItemRow.paper_version_id == paper_version_id,
                RunItemRow.status == RunItemStatus.IN_PROGRESS.value,
                RunItemRow.stage == expected_stage.value,
            )
            .values(stage=next_stage.value, updated_at=updated_at)
            .returning(RunItemRow.id)
        )
        try:
            with self._sessions.begin() as session:
                updated_id = session.scalar(statement)
                if updated_id is None:
                    raise RepositoryError("analysis item is missing or has an invalid stage")
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL analysis-stage update is unavailable"
            ) from error

    def persist_parsed_paper(
        self,
        *,
        run_id: UUID,
        parsed_paper: ParsedPaper,
        expected_stage: PaperStage,
        updated_at: datetime,
    ) -> ParsedPaper:
        if expected_stage is not PaperStage.PDF_DOWNLOADED:
            raise RepositoryError("parsed paper requires a PDF_DOWNLOADED item")
        try:
            with self._sessions.begin() as session:
                item = session.scalars(
                    select(RunItemRow).where(
                        RunItemRow.run_id == run_id,
                        RunItemRow.paper_version_id == parsed_paper.paper_version_id,
                        RunItemRow.status == RunItemStatus.IN_PROGRESS.value,
                        RunItemRow.stage == expected_stage.value,
                    )
                ).one_or_none()
                if item is None or item.paper_id != parsed_paper.paper_id:
                    raise RepositoryError("parsed paper does not match an active analysis item")
                existing = session.get(ParsedPaperRow, parsed_paper.id)
                if existing is None:
                    _add_parsed_paper(session, parsed_paper, created_at=updated_at)
                    canonical = parsed_paper
                elif (
                    existing.paper_id != parsed_paper.paper_id
                    or existing.paper_version_id != parsed_paper.paper_version_id
                    or existing.parser_name != parsed_paper.parser_name
                    or existing.parser_version != parsed_paper.parser_version
                ):
                    raise RepositoryError("stable parsed-paper identity conflicts with stored data")
                else:
                    canonical = _load_parsed_paper(session, existing)
                item.stage = PaperStage.PARSED.value
                item.updated_at = updated_at
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL parsed-paper persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected parsed-paper ownership constraints"
            ) from error
        return canonical

    def persist_analysis_bundle(
        self,
        *,
        run_id: UUID,
        bundle: AnalysisBundle,
        expected_stage: PaperStage,
        updated_at: datetime,
    ) -> None:
        expected_for_scope = (
            PaperStage.PARSED
            if bundle.analysis.analysis_scope is AnalysisScope.FULL_TEXT
            else PaperStage.SELECTED
        )
        if expected_stage is not expected_for_scope:
            raise RepositoryError("analysis scope does not match the durable pipeline stage")
        try:
            with self._sessions.begin() as session:
                item = session.scalars(
                    select(RunItemRow).where(
                        RunItemRow.run_id == run_id,
                        RunItemRow.paper_version_id == bundle.analysis.paper_version_id,
                        RunItemRow.status == RunItemStatus.IN_PROGRESS.value,
                        RunItemRow.stage == expected_stage.value,
                    )
                ).one_or_none()
                if item is None or item.paper_id != bundle.analysis.paper_id:
                    raise RepositoryError("analysis bundle does not match an active run item")
                existing = session.get(PaperAnalysisRow, bundle.analysis.id)
                if existing is None:
                    _add_analysis_bundle(session, bundle)
                elif (
                    existing.paper_id != bundle.analysis.paper_id
                    or existing.paper_version_id != bundle.analysis.paper_version_id
                    or existing.parsed_paper_id != bundle.analysis.parsed_paper_id
                    or existing.analysis_scope != bundle.analysis.analysis_scope.value
                    or existing.provider != bundle.analysis.provider
                    or existing.configured_model != bundle.analysis.configured_model
                    or existing.model_version != bundle.analysis.model_version
                    or existing.prompt_version != bundle.analysis.prompt_version
                ):
                    raise RepositoryError("stable analysis identity conflicts with stored data")
                item.stage = PaperStage.EVIDENCE_EXTRACTED.value
                item.status = RunItemStatus.COMPLETED.value
                item.updated_at = updated_at
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL analysis persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected analysis and evidence ownership constraints"
            ) from error

    def fail_analysis_item(
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
        statement = (
            update(RunItemRow)
            .where(
                RunItemRow.run_id == run_id,
                RunItemRow.paper_version_id == paper_version_id,
                RunItemRow.status == RunItemStatus.IN_PROGRESS.value,
            )
            .values(
                status=RunItemStatus.FAILED.value,
                failed_stage=failed_stage.value,
                error_code=error_code[:80],
                retryable=retryable,
                error_detail=error_detail[:1000],
                updated_at=updated_at,
            )
            .returning(RunItemRow.id)
        )
        try:
            with self._sessions.begin() as session:
                updated_id = session.scalar(statement)
                if updated_id is None:
                    raise RepositoryError("analysis item is missing or already terminal")
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL item-failure persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected analysis item failure constraints"
            ) from error

    def finalize_analysis_run(self, run_id: UUID, *, completed_at: datetime) -> DailyRun:
        try:
            with self._sessions.begin() as session:
                run_row = session.scalars(
                    select(DailyRunRow)
                    .where(
                        DailyRunRow.id == run_id,
                        DailyRunRow.operation.in_(
                            (
                                RunOperation.STRUCTURED_ANALYSIS.value,
                                RunOperation.HISTORICAL_ANALYSIS.value,
                            )
                        ),
                        DailyRunRow.status == RunStatus.RUNNING.value,
                    )
                    .with_for_update()
                ).one_or_none()
                if run_row is None:
                    raise RepositoryError("analysis run is missing or no longer running")
                item_rows = tuple(
                    session.scalars(
                        select(RunItemRow)
                        .where(RunItemRow.run_id == run_id)
                        .order_by(RunItemRow.created_at, RunItemRow.id)
                    )
                )
                if len(item_rows) != run_row.selected_count or any(
                    item.status == RunItemStatus.IN_PROGRESS.value for item in item_rows
                ):
                    raise RepositoryError("analysis run cannot publish with nonterminal items")
                completed_count = sum(
                    item.status == RunItemStatus.COMPLETED.value for item in item_rows
                )
                failed_items = tuple(
                    item for item in item_rows if item.status == RunItemStatus.FAILED.value
                )
                if completed_count == 0:
                    status = RunStatus.FAILED
                    error_code = "NO_SELECTED_PAPER_COMPLETED"
                    error_detail = "No selected paper completed evidence extraction."
                elif failed_items:
                    status = RunStatus.PARTIAL
                    error_code = None
                    error_detail = None
                else:
                    status = RunStatus.COMPLETE
                    error_code = None
                    error_detail = None
                run_row.status = status.value
                run_row.completed_at = completed_at
                run_row.completed_count = completed_count
                run_row.failed_count = len(failed_items)
                run_row.error_code = error_code
                run_row.error_detail = error_detail
                if status in (RunStatus.COMPLETE, RunStatus.PARTIAL):
                    _add_analysis_report(
                        session,
                        run_row=run_row,
                        failed_items=failed_items,
                        generated_at=completed_at,
                    )
                session.flush()
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL analysis publication is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected report ownership constraints"
            ) from error
        return _run_from_row(run_row)

    def fail_analysis_run(
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
                row = session.scalars(
                    update(DailyRunRow)
                    .where(
                        DailyRunRow.id == run_id,
                        DailyRunRow.operation.in_(
                            (
                                RunOperation.STRUCTURED_ANALYSIS.value,
                                RunOperation.HISTORICAL_ANALYSIS.value,
                            )
                        ),
                        DailyRunRow.status == RunStatus.RUNNING.value,
                    )
                    .values(
                        status=RunStatus.FAILED.value,
                        completed_at=completed_at,
                        completed_count=completed_count,
                        failed_count=failed_count,
                        error_code=error_code[:80],
                        error_detail=error_detail[:1000],
                    )
                    .returning(DailyRunRow)
                ).one_or_none()
                if row is None:
                    raise RepositoryError("analysis run is missing or no longer running")
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL analysis-run failure persistence is unavailable"
            ) from error
        except IntegrityError as error:
            raise RepositoryIntegrityError(
                "PostgreSQL rejected analysis-run failure constraints"
            ) from error
        return _run_from_row(row)

    def get_paper_analysis(
        self,
        paper_id: UUID,
        *,
        paper_version_id: UUID | None,
        analysis_scope: AnalysisScope | None = None,
        canonical_only: bool = False,
    ) -> AnalysisDetail | None:
        statement = (
            select(
                PaperAnalysisRow,
                PaperVersionRow.version,
                ParsedPaperRow.parser_name,
                ParsedPaperRow.parser_version,
            )
            .join(PaperVersionRow, PaperVersionRow.id == PaperAnalysisRow.paper_version_id)
            .outerjoin(ParsedPaperRow, ParsedPaperRow.id == PaperAnalysisRow.parsed_paper_id)
            .where(PaperAnalysisRow.paper_id == paper_id)
        )
        if paper_version_id is None and not canonical_only:
            statement = statement.join(PaperRow, PaperRow.id == PaperAnalysisRow.paper_id).where(
                PaperVersionRow.version == PaperRow.current_version
            )
        elif paper_version_id is not None:
            statement = statement.where(PaperAnalysisRow.paper_version_id == paper_version_id)
        if analysis_scope is not None:
            statement = statement.where(PaperAnalysisRow.analysis_scope == analysis_scope.value)
        if canonical_only:
            canonical_analysis_ids = (
                select(ProductRunPaperInputRow.analysis_id)
                .join(DailyRunRow, DailyRunRow.id == ProductRunPaperInputRow.run_id)
                .join(
                    RunItemRow,
                    (RunItemRow.run_id == ProductRunPaperInputRow.run_id)
                    & (RunItemRow.paper_version_id == ProductRunPaperInputRow.paper_version_id),
                )
                .where(*_canonical_publication_item_predicates())
            )
            statement = statement.where(PaperAnalysisRow.id.in_(canonical_analysis_ids))
        ordering = (
            (PaperVersionRow.version.desc(),) if canonical_only and paper_version_id is None else ()
        )
        statement = statement.order_by(
            *ordering,
            case((PaperAnalysisRow.analysis_scope == AnalysisScope.FULL_TEXT.value, 0), else_=1),
            PaperAnalysisRow.generated_at.desc(),
            PaperAnalysisRow.id,
        ).limit(1)
        try:
            with self._sessions() as session:
                selected = session.execute(statement).one_or_none()
                if selected is None:
                    return None
                analysis_row, arxiv_version, parser_name, parser_version = selected
                claim_rows = tuple(
                    session.scalars(
                        select(AnalysisClaimRow)
                        .where(AnalysisClaimRow.analysis_id == analysis_row.id)
                        .order_by(AnalysisClaimRow.claim_key, AnalysisClaimRow.id)
                    )
                )
                evidence_rows = tuple(
                    session.scalars(
                        select(EvidenceRow)
                        .where(EvidenceRow.analysis_id == analysis_row.id)
                        .order_by(EvidenceRow.section, EvidenceRow.evidence_key, EvidenceRow.id)
                    )
                )
                link_rows = tuple(
                    session.execute(
                        select(EvidenceClaimRow.evidence_id, EvidenceClaimRow.claim_id)
                        .where(EvidenceClaimRow.analysis_id == analysis_row.id)
                        .order_by(EvidenceClaimRow.evidence_id, EvidenceClaimRow.claim_id)
                    )
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL analysis detail query is unavailable"
            ) from error
        supported_claims: dict[UUID, list[UUID]] = {}
        for evidence_id, claim_id in link_rows:
            supported_claims.setdefault(evidence_id, []).append(claim_id)
        return AnalysisDetail(
            analysis=_analysis_from_row(analysis_row),
            arxiv_version=arxiv_version,
            claims=tuple(_claim_from_row(row) for row in claim_rows),
            evidence=tuple(
                _evidence_from_row(row, tuple(supported_claims.get(row.id, ())))
                for row in evidence_rows
            ),
            parser_name=parser_name,
            parser_version=parser_version,
        )

    def list_paper_evidence(
        self,
        paper_id: UUID,
        *,
        analysis_id: UUID,
        paper_version_id: UUID | None,
        analysis_scope: AnalysisScope | None = None,
        canonical_only: bool = False,
    ) -> tuple[Evidence, ...] | None:
        analysis_statement = select(PaperAnalysisRow.id).where(
            PaperAnalysisRow.id == analysis_id,
            PaperAnalysisRow.paper_id == paper_id,
        )
        if paper_version_id is not None:
            analysis_statement = analysis_statement.where(
                PaperAnalysisRow.paper_version_id == paper_version_id
            )
        if analysis_scope is not None:
            analysis_statement = analysis_statement.where(
                PaperAnalysisRow.analysis_scope == analysis_scope.value
            )
        if canonical_only:
            canonical_analysis_ids = (
                select(ProductRunPaperInputRow.analysis_id)
                .join(DailyRunRow, DailyRunRow.id == ProductRunPaperInputRow.run_id)
                .join(
                    RunItemRow,
                    (RunItemRow.run_id == ProductRunPaperInputRow.run_id)
                    & (RunItemRow.paper_version_id == ProductRunPaperInputRow.paper_version_id),
                )
                .where(*_canonical_publication_item_predicates())
            )
            analysis_statement = analysis_statement.where(
                PaperAnalysisRow.id.in_(canonical_analysis_ids)
            )
        evidence_statement = (
            select(EvidenceRow)
            .where(EvidenceRow.analysis_id == analysis_id)
            .order_by(EvidenceRow.section, EvidenceRow.evidence_key, EvidenceRow.id)
        )
        links_statement = (
            select(EvidenceClaimRow.evidence_id, EvidenceClaimRow.claim_id)
            .where(EvidenceClaimRow.analysis_id == analysis_id)
            .order_by(EvidenceClaimRow.evidence_id, EvidenceClaimRow.claim_id)
        )
        try:
            with self._sessions() as session:
                if session.scalar(analysis_statement) is None:
                    return None
                evidence_rows = tuple(session.scalars(evidence_statement))
                link_rows = tuple(session.execute(links_statement))
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL evidence query is unavailable") from error
        supported_claims: dict[UUID, list[UUID]] = {}
        for evidence_id, claim_id in link_rows:
            supported_claims.setdefault(evidence_id, []).append(claim_id)
        return tuple(
            _evidence_from_row(row, tuple(supported_claims.get(row.id, ())))
            for row in evidence_rows
        )

    def list_runs(
        self, *, topic_slug: str | None, limit: int, offset: int
    ) -> tuple[tuple[DailyRun, ...], int]:
        base = select(DailyRunRow)
        count = select(func.count(DailyRunRow.id))
        if topic_slug is not None:
            base = base.join(TopicRow).where(TopicRow.slug == topic_slug)
            count = count.join(TopicRow).where(TopicRow.slug == topic_slug)
        statement = base.order_by(DailyRunRow.started_at.desc()).limit(limit).offset(offset)
        try:
            with self._sessions() as session:
                rows = tuple(session.scalars(statement))
                total = session.execute(count).scalar_one()
        except OperationalError as error:
            raise RepositoryUnavailableError("PostgreSQL run query is unavailable") from error
        return tuple(_run_from_row(row) for row in rows), total

    def get_latest_run(self, *, topic_slug: str | None) -> RunDetail | None:
        statement = select(DailyRunRow).where(
            DailyRunRow.pipeline_execution_mode != PipelineExecutionMode.SMOKE.value
        )
        if topic_slug is not None:
            statement = statement.join(TopicRow).where(TopicRow.slug == topic_slug)
        statement = statement.order_by(DailyRunRow.started_at.desc()).limit(1)
        return self._get_run_detail_by_statement(statement)


def _add_parsed_paper(session: Session, parsed: ParsedPaper, *, created_at: datetime) -> None:
    session.add(
        ParsedPaperRow(
            id=parsed.id,
            paper_id=parsed.paper_id,
            paper_version_id=parsed.paper_version_id,
            parser_name=parsed.parser_name,
            parser_version=parsed.parser_version,
            parsed_at=parsed.parsed_at,
            source=parsed.source,
            call_count=parsed.call_count,
            duration_ms=parsed.duration_ms,
            schema_version=parsed.schema_version,
            created_at=created_at,
        )
    )
    session.flush()
    for section in parsed.sections:
        session.add(
            ParsedSectionRow(
                id=section.id,
                parsed_paper_id=parsed.id,
                position=section.index,
                title=section.title,
            )
        )
    session.flush()
    for section in parsed.sections:
        for passage in section.passages:
            session.add(
                ParsedPassageRow(
                    id=passage.id,
                    parsed_paper_id=parsed.id,
                    parsed_section_id=section.id,
                    source_id=passage.source_id,
                    position=passage.passage_index,
                    text=passage.text,
                    coordinates=_coordinates_to_json(passage.coordinates),
                )
            )
    reference_by_source = {reference.source_id: reference for reference in parsed.references}
    for reference in parsed.references:
        session.add(
            ParsedReferenceRow(
                id=reference.id,
                parsed_paper_id=parsed.id,
                source_id=reference.source_id,
                title=reference.title,
                authors=list(reference.authors),
                publication_year=reference.year,
                raw_text=reference.raw_text,
            )
        )
    session.flush()
    for context in parsed.citation_contexts:
        reference = reference_by_source.get(context.reference_source_id)
        if reference is None:
            raise RepositoryError("citation context references an unknown parsed reference")
        session.add(
            CitationContextRow(
                id=context.id,
                parsed_paper_id=parsed.id,
                parsed_passage_id=context.parsed_passage_id,
                parsed_reference_id=reference.id,
                reference_source_id=context.reference_source_id,
                excerpt=context.excerpt,
                coordinates=_coordinates_to_json(context.coordinates),
            )
        )


def _load_parsed_paper(session: Session, row: ParsedPaperRow) -> ParsedPaper:
    section_rows = tuple(
        session.scalars(
            select(ParsedSectionRow)
            .where(ParsedSectionRow.parsed_paper_id == row.id)
            .order_by(ParsedSectionRow.position, ParsedSectionRow.id)
        )
    )
    passage_rows = tuple(
        session.scalars(
            select(ParsedPassageRow)
            .where(ParsedPassageRow.parsed_paper_id == row.id)
            .order_by(
                ParsedPassageRow.parsed_section_id,
                ParsedPassageRow.position,
                ParsedPassageRow.id,
            )
        )
    )
    section_position_by_id = {section_row.id: section_row.position for section_row in section_rows}
    passages_by_section: dict[UUID, list[ParsedPassage]] = {}
    for passage_row in passage_rows:
        passages_by_section.setdefault(passage_row.parsed_section_id, []).append(
            ParsedPassage(
                id=passage_row.id,
                source_id=passage_row.source_id,
                section_index=section_position_by_id[passage_row.parsed_section_id],
                passage_index=passage_row.position,
                text=passage_row.text,
                coordinates=_coordinates_from_json(passage_row.coordinates),
            )
        )
    sections = tuple(
        ParsedSection(
            id=section_row.id,
            index=section_row.position,
            title=section_row.title,
            passages=tuple(passages_by_section.get(section_row.id, ())),
        )
        for section_row in section_rows
    )
    reference_rows = tuple(
        session.scalars(
            select(ParsedReferenceRow)
            .where(ParsedReferenceRow.parsed_paper_id == row.id)
            .order_by(ParsedReferenceRow.source_id, ParsedReferenceRow.id)
        )
    )
    references = tuple(
        ParsedReference(
            id=reference_row.id,
            source_id=reference_row.source_id,
            title=reference_row.title,
            authors=tuple(reference_row.authors),
            year=reference_row.publication_year,
            raw_text=reference_row.raw_text,
        )
        for reference_row in reference_rows
    )
    context_rows = tuple(
        session.scalars(
            select(CitationContextRow)
            .where(CitationContextRow.parsed_paper_id == row.id)
            .order_by(CitationContextRow.id)
        )
    )
    citation_contexts = tuple(
        CitationContext(
            id=context_row.id,
            parsed_passage_id=context_row.parsed_passage_id,
            reference_source_id=context_row.reference_source_id,
            excerpt=context_row.excerpt,
            coordinates=_coordinates_from_json(context_row.coordinates),
        )
        for context_row in context_rows
    )
    return ParsedPaper(
        id=row.id,
        paper_id=row.paper_id,
        paper_version_id=row.paper_version_id,
        parser_name=row.parser_name,
        parser_version=row.parser_version,
        parsed_at=row.parsed_at,
        source=row.source,
        sections=sections,
        references=references,
        citation_contexts=citation_contexts,
        call_count=row.call_count,
        duration_ms=row.duration_ms,
        schema_version=row.schema_version,
    )


def _add_analysis_bundle(session: Session, bundle: AnalysisBundle) -> None:
    analysis = bundle.analysis
    session.add(
        PaperAnalysisRow(
            id=analysis.id,
            paper_id=analysis.paper_id,
            paper_version_id=analysis.paper_version_id,
            parsed_paper_id=analysis.parsed_paper_id,
            analysis_scope=analysis.analysis_scope.value,
            summary=analysis.summary,
            research_problem=analysis.research_problem,
            method_summary=analysis.method_summary,
            key_contributions=list(analysis.key_contributions),
            limitations=list(analysis.limitations),
            provider=analysis.provider,
            configured_model=analysis.configured_model,
            model_version=analysis.model_version,
            prompt_version=analysis.prompt_version,
            generated_at=analysis.generated_at,
            source=analysis.source,
            verification_status=analysis.verification_status.value,
            prompt_tokens=analysis.usage.prompt_tokens,
            completion_tokens=analysis.usage.completion_tokens,
            total_tokens=analysis.usage.total_tokens,
            call_count=analysis.usage.call_count,
            duration_ms=analysis.usage.duration_ms,
            estimated_cost_usd=analysis.usage.estimated_cost_usd,
            schema_version=analysis.schema_version,
            created_at=analysis.created_at,
        )
    )
    # These ownership constraints are deliberately composite and the ORM models
    # do not expose relationships. Flush each parent tier explicitly so
    # PostgreSQL validates the whole bundle in dependency order while the outer
    # transaction still commits or rolls back atomically.
    session.flush()
    for claim in bundle.claims:
        session.add(
            AnalysisClaimRow(
                id=claim.id,
                analysis_id=claim.analysis_id,
                paper_id=claim.paper_id,
                paper_version_id=claim.paper_version_id,
                claim_key=claim.key,
                claim_type=claim.claim_type.value,
                text=claim.text,
                provider=claim.provider,
                model_version=claim.model_version,
                prompt_version=claim.prompt_version,
                generated_at=claim.generated_at,
                source=claim.source,
                verification_status=claim.verification_status.value,
                schema_version=claim.schema_version,
                created_at=claim.created_at,
            )
        )
    session.flush()
    for item in bundle.evidence:
        session.add(
            EvidenceRow(
                id=item.id,
                analysis_id=item.analysis_id,
                paper_id=item.paper_id,
                paper_version_id=item.paper_version_id,
                evidence_key=item.key,
                section=item.section,
                passage_id=item.passage_id,
                coordinates=_coordinates_to_json(item.coordinates),
                excerpt=item.excerpt,
                evidence_type=item.evidence_type.value,
                extraction_source=item.extraction_source,
                provider=item.provider,
                model_version=item.model_version,
                prompt_version=item.prompt_version,
                generated_at=item.generated_at,
                verification_status=item.verification_status.value,
                schema_version=item.schema_version,
                created_at=item.created_at,
            )
        )
    session.flush()
    for item in bundle.evidence:
        session.add_all(
            EvidenceClaimRow(
                evidence_id=item.id,
                claim_id=claim_id,
                analysis_id=item.analysis_id,
            )
            for claim_id in item.supported_claim_ids
        )


def _add_analysis_report(
    session: Session,
    *,
    run_row: DailyRunRow,
    failed_items: tuple[RunItemRow, ...],
    generated_at: datetime,
) -> None:
    report_id = stable_report_id(run_row.id)
    status = RunStatus(run_row.status)
    session.add(
        ReportRow(
            id=report_id,
            run_id=run_row.id,
            topic_id=run_row.topic_id,
            logical_date=run_row.logical_date,
            status=status.value,
            title=f"Structured analysis for {run_row.logical_date.isoformat()}",
            summary=(
                f"{run_row.completed_count} of {run_row.selected_count} selected papers "
                "completed evidence extraction."
            ),
            source="deterministic_pipeline",
            generated_at=generated_at,
            schema_version=1,
            created_at=generated_at,
        )
    )
    session.flush()
    for item in failed_items:
        if (
            item.failed_stage is None
            or item.error_code is None
            or item.retryable is None
            or item.error_detail is None
        ):
            raise RepositoryError("failed run item lacks reportable failure metadata")
        session.add(
            ReportFailureRow(
                id=uuid5(report_id, str(item.paper_version_id)),
                report_id=report_id,
                paper_id=item.paper_id,
                paper_version_id=item.paper_version_id,
                failed_stage=item.failed_stage,
                error_code=item.error_code,
                retryable=item.retryable,
                error_detail=item.error_detail,
                schema_version=1,
                created_at=generated_at,
            )
        )


def _child_run_advisory_key(topic_id: UUID, logical_date: date) -> int:
    folded_uuid = (topic_id.int >> 64) ^ (topic_id.int & ((1 << 64) - 1))
    return (folded_uuid ^ logical_date.toordinal()) & ((1 << 63) - 1)


def _pipeline_advisory_key(execution_id: UUID) -> int:
    # Child use cases acquire their own non-negative lock from another
    # connection. The negative namespace guarantees the outer pipeline lock
    # cannot collide with or self-block those nested child locks.
    folded_uuid = (execution_id.int >> 64) ^ (execution_id.int & ((1 << 64) - 1))
    return -((folded_uuid & ((1 << 63) - 1)) + 1)


def _canonical_publication_item_predicates() -> tuple[Any, ...]:
    return (
        DailyRunRow.operation == RunOperation.PRODUCT_PUBLICATION.value,
        DailyRunRow.status.in_((RunStatus.COMPLETE.value, RunStatus.PARTIAL.value)),
        DailyRunRow.pipeline_execution_mode != PipelineExecutionMode.SMOKE.value,
        RunItemRow.status == RunItemStatus.COMPLETED.value,
        RunItemRow.stage == PaperStage.PUBLISHED.value,
    )


def _topic_from_row(row: TopicRow) -> StoredTopic:
    return StoredTopic(
        config=TopicConfig(
            id=row.id,
            slug=row.slug,
            name=row.name,
            description=row.description,
            categories=tuple(row.categories),
            include_terms=tuple(row.include_terms),
            exclude_terms=tuple(row.exclude_terms),
            overlap_hours=row.overlap_hours,
            initial_lookback_days=row.initial_lookback_days,
            max_results=row.max_results,
            representative_full_text_count=row.representative_full_text_count,
            schema_version=row.schema_version,
        ),
        created_at=row.created_at,
    )


def _paper_from_row(row: PaperRow) -> Paper:
    return Paper(
        id=row.id,
        canonical_arxiv_id=row.canonical_arxiv_id,
        title=row.title,
        abstract=row.abstract,
        current_version=row.current_version,
        first_submitted_at=row.first_submitted_at,
        latest_updated_at=row.latest_updated_at,
        primary_category=row.primary_category,
        categories=tuple(row.categories),
        authors=tuple(row.authors),
        pdf_url=row.pdf_url,
        schema_version=row.schema_version,
        created_at=row.created_at,
    )


def _version_from_row(row: PaperVersionRow, canonical_arxiv_id: str) -> PaperVersion:
    return PaperVersion(
        id=row.id,
        paper_id=row.paper_id,
        canonical_arxiv_id=canonical_arxiv_id,
        version=row.version,
        title=row.title,
        abstract=row.abstract,
        submitted_at=row.submitted_at,
        updated_at=row.updated_at,
        primary_category=row.primary_category,
        categories=tuple(row.categories),
        authors=tuple(row.authors),
        pdf_url=row.pdf_url,
        source_url=row.source_url,
        schema_version=row.schema_version,
        created_at=row.created_at,
    )


def _identity_from_row(row: PaperSourceIdentityRow) -> PaperSourceIdentity:
    return PaperSourceIdentity(
        id=row.id,
        paper_id=row.paper_id,
        paper_version_id=row.paper_version_id,
        source=row.source,
        external_id=row.external_id,
        source_version=row.source_version,
        source_url=row.source_url,
        schema_version=row.schema_version,
        created_at=row.created_at,
    )


def _cursor_from_row(row: IngestionCursorRow) -> IngestionCursor:
    return IngestionCursor(
        topic_id=row.topic_id,
        watermark=row.watermark,
        schema_version=row.schema_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


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


def _pipeline_execution_contract_values(
    contract: PipelineExecutionContract,
) -> dict[str, object]:
    return {
        "narrative_mode": contract.narrative_mode,
        "llm_provider": contract.llm_provider,
        "llm_configured_model": contract.llm_configured_model,
        "analysis_prompt_version": contract.analysis_prompt_version,
        "parser_name": contract.parser_name,
        "parser_version": contract.parser_version,
        "backfill_max_queries": contract.backfill_max_queries,
        "backfill_per_query_limit": contract.backfill_per_query_limit,
        "backfill_timeout_seconds": contract.backfill_timeout_seconds,
        "search_max_steps": contract.search_max_steps,
        "search_max_queries": contract.search_max_queries,
        "search_max_queue_size": contract.search_max_queue_size,
        "search_max_citation_depth": contract.search_max_citation_depth,
        "search_max_candidates": contract.search_max_candidates,
        "search_max_selected_candidates": contract.search_max_selected_candidates,
        "search_per_operation_timeout_seconds": (contract.search_per_operation_timeout_seconds),
        "search_overall_timeout_seconds": contract.search_overall_timeout_seconds,
        "max_comparisons_per_paper": contract.max_comparisons_per_paper,
        "pipeline_timeout_seconds": contract.pipeline_timeout_seconds,
        "crawler_prompt_version": contract.crawler_prompt_version,
        "selector_prompt_version": contract.selector_prompt_version,
        "comparison_prompt_version": contract.comparison_prompt_version,
        "report_prompt_version": contract.report_prompt_version,
        "daily_selection_policy_version": contract.daily_selection_policy_version,
        "pipeline_orchestration_version": contract.pipeline_orchestration_version,
        "embedding_model_identifier": contract.embedding_model_identifier,
        "embedding_model_revision": contract.embedding_model_revision,
        "embedding_tokenizer_identifier": contract.embedding_tokenizer_identifier,
        "embedding_tokenizer_revision": contract.embedding_tokenizer_revision,
        "embedding_dimension": contract.embedding_dimension,
        "embedding_preprocessing_contract": contract.embedding_preprocessing_contract,
        "embedding_model_provenance": contract.embedding_model_provenance,
        "embedding_source": contract.embedding_source,
        "topic_categories": list(contract.topic_categories),
        "topic_include_terms": list(contract.topic_include_terms),
        "topic_exclude_terms": list(contract.topic_exclude_terms),
        "topic_overlap_hours": contract.topic_overlap_hours,
        "topic_initial_lookback_days": contract.topic_initial_lookback_days,
        "topic_max_results": contract.topic_max_results,
        "topic_representative_full_text_count": (contract.topic_representative_full_text_count),
    }


def _pipeline_execution_contract_from_values(
    values: dict[str, Any],
) -> PipelineExecutionContract:
    try:
        return PipelineExecutionContract(
            narrative_mode=_pipeline_contract_text(values, "narrative_mode"),
            llm_provider=_pipeline_contract_text(values, "llm_provider"),
            llm_configured_model=_pipeline_contract_text(values, "llm_configured_model"),
            analysis_prompt_version=_pipeline_contract_text(values, "analysis_prompt_version"),
            parser_name=_pipeline_contract_optional_text(values, "parser_name"),
            parser_version=_pipeline_contract_optional_text(values, "parser_version"),
            backfill_max_queries=_pipeline_contract_int(values, "backfill_max_queries"),
            backfill_per_query_limit=_pipeline_contract_int(values, "backfill_per_query_limit"),
            backfill_timeout_seconds=_pipeline_contract_float(values, "backfill_timeout_seconds"),
            search_max_steps=_pipeline_contract_int(values, "search_max_steps"),
            search_max_queries=_pipeline_contract_int(values, "search_max_queries"),
            search_max_queue_size=_pipeline_contract_int(values, "search_max_queue_size"),
            search_max_citation_depth=_pipeline_contract_int(values, "search_max_citation_depth"),
            search_max_candidates=_pipeline_contract_int(values, "search_max_candidates"),
            search_max_selected_candidates=_pipeline_contract_int(
                values, "search_max_selected_candidates"
            ),
            search_per_operation_timeout_seconds=_pipeline_contract_float(
                values, "search_per_operation_timeout_seconds"
            ),
            search_overall_timeout_seconds=_pipeline_contract_float(
                values, "search_overall_timeout_seconds"
            ),
            max_comparisons_per_paper=_pipeline_contract_int(values, "max_comparisons_per_paper"),
            pipeline_timeout_seconds=_pipeline_contract_int(values, "pipeline_timeout_seconds"),
            crawler_prompt_version=_pipeline_contract_text(values, "crawler_prompt_version"),
            selector_prompt_version=_pipeline_contract_text(values, "selector_prompt_version"),
            comparison_prompt_version=_pipeline_contract_text(values, "comparison_prompt_version"),
            report_prompt_version=_pipeline_contract_text(values, "report_prompt_version"),
            daily_selection_policy_version=_pipeline_contract_text(
                values, "daily_selection_policy_version"
            ),
            pipeline_orchestration_version=_pipeline_contract_text(
                values, "pipeline_orchestration_version"
            ),
            embedding_model_identifier=_pipeline_contract_text(
                values, "embedding_model_identifier"
            ),
            embedding_model_revision=_pipeline_contract_text(values, "embedding_model_revision"),
            embedding_tokenizer_identifier=_pipeline_contract_text(
                values, "embedding_tokenizer_identifier"
            ),
            embedding_tokenizer_revision=_pipeline_contract_text(
                values, "embedding_tokenizer_revision"
            ),
            embedding_dimension=_pipeline_contract_int(values, "embedding_dimension"),
            embedding_preprocessing_contract=_pipeline_contract_text(
                values, "embedding_preprocessing_contract"
            ),
            embedding_model_provenance=_pipeline_contract_text(
                values, "embedding_model_provenance"
            ),
            embedding_source=_pipeline_contract_text(values, "embedding_source"),
            topic_categories=_pipeline_contract_text_tuple(values, "topic_categories"),
            topic_include_terms=_pipeline_contract_text_tuple(values, "topic_include_terms"),
            topic_exclude_terms=_pipeline_contract_text_tuple(values, "topic_exclude_terms"),
            topic_overlap_hours=_pipeline_contract_int(values, "topic_overlap_hours"),
            topic_initial_lookback_days=_pipeline_contract_int(
                values, "topic_initial_lookback_days"
            ),
            topic_max_results=_pipeline_contract_int(values, "topic_max_results"),
            topic_representative_full_text_count=_pipeline_contract_int(
                values, "topic_representative_full_text_count"
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RepositoryIntegrityError("stored pipeline execution contract is invalid") from error


def _pipeline_contract_text_tuple(values: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = values[key]
    if not isinstance(raw, list):
        raise ValueError(f"pipeline execution contract {key} is invalid")
    raw_values = cast(list[object], raw)
    if any(not isinstance(item, str) for item in raw_values):
        raise ValueError(f"pipeline execution contract {key} is invalid")
    return tuple(cast(str, item) for item in raw_values)


def _pipeline_contract_text(values: dict[str, Any], key: str) -> str:
    value = values[key]
    if not isinstance(value, str):
        raise ValueError(f"pipeline execution contract {key} is invalid")
    return value


def _pipeline_contract_optional_text(values: dict[str, Any], key: str) -> str | None:
    value = values[key]
    if value is not None and not isinstance(value, str):
        raise ValueError(f"pipeline execution contract {key} is invalid")
    return value


def _pipeline_contract_int(values: dict[str, Any], key: str) -> int:
    value = values[key]
    if type(value) is not int:
        raise ValueError(f"pipeline execution contract {key} is invalid")
    return value


def _pipeline_contract_float(values: dict[str, Any], key: str) -> float:
    value = values[key]
    if type(value) is not float:
        raise ValueError(f"pipeline execution contract {key} is invalid")
    return value


def _pipeline_execution_from_row(row: PipelineExecutionRow) -> PipelineExecution:
    return PipelineExecution(
        id=row.id,
        topic_id=row.topic_id,
        logical_date=row.logical_date,
        execution_mode=PipelineExecutionMode(row.execution_mode),
        analysis_scope=AnalysisScope(row.analysis_scope),
        selection_limit=row.selection_limit,
        contract=_pipeline_execution_contract_from_values(row.execution_contract),
        status=RunStatus(row.status),
        deadline_at=row.deadline_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        error_code=row.error_code,
        error_detail=row.error_detail,
        schema_version=row.schema_version,
        created_at=row.created_at,
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


def _coordinates_to_json(values: tuple[PageCoordinates, ...]) -> list[dict[str, int | float]]:
    return [
        {
            "page": value.page,
            "x": value.x,
            "y": value.y,
            "width": value.width,
            "height": value.height,
        }
        for value in values
    ]


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
