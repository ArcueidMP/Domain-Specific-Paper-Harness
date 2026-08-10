"""Synchronous SQLAlchemy/PostgreSQL repository for the application persistence port."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Engine, case, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
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
from paper_harness.domain.identity import (
    normalize_author_name,
    stable_author_id,
    stable_paper_id,
    stable_paper_version_id,
    stable_report_id,
    stable_source_identity_id,
)
from paper_harness.domain.models import (
    DailyRun,
    IngestionCursor,
    Paper,
    PaperSourceIdentity,
    PaperStage,
    PaperVersion,
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
    ReportFailureRow,
    ReportRow,
    RunItemRow,
    TopicPaperRow,
    TopicRow,
)
from .product_repository import ProductRepositoryMixin

EXPECTED_DATABASE_REVISION = "0004_m4_graph_trends_reports"


class PostgresRepository(ProductRepositoryMixin, HistoricalRepositoryMixin):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def daily_run_lock(self, topic_id: UUID, logical_date: date) -> Generator[None]:
        key = _advisory_key(topic_id, logical_date)
        try:
            with self._engine.connect() as connection:
                acquired = connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_key)"), {"lock_key": key}
                ).scalar_one()
                if not acquired:
                    raise DuplicateDailyRunError(
                        f"another daily run holds the lock for {topic_id} on {logical_date}"
                    )
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

    def get_run_for_date(self, topic_id: UUID, logical_date: date) -> DailyRun | None:
        statement = select(DailyRunRow).where(
            DailyRunRow.topic_id == topic_id,
            DailyRunRow.logical_date == logical_date,
            DailyRunRow.operation == RunOperation.ARXIV_INGESTION.value,
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
    ) -> DailyRun:
        run_id = uuid4()
        statement = (
            insert(DailyRunRow)
            .values(
                id=run_id,
                topic_id=topic_id,
                logical_date=logical_date,
                operation=RunOperation.ARXIV_INGESTION.value,
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

    def persist_arxiv_batch_and_complete(
        self,
        *,
        topic: TopicConfig,
        run_id: UUID,
        records: tuple[ArxivPaperRecord, ...],
        watermark: datetime,
        persisted_at: datetime,
        completed_at: datetime,
    ) -> DailyRun:
        items: list[RunItem] = []
        try:
            with self._sessions.begin() as session:
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
        return _run_from_row(row)

    def _persist_record(
        self,
        session: Session,
        *,
        topic: TopicConfig,
        run_id: UUID,
        record: ArxivPaperRecord,
        persisted_at: datetime,
    ) -> RunItem:
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
        is_newer = paper_statement.excluded.current_version >= PaperRow.current_version
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
                    "latest_updated_at": func.greatest(
                        PaperRow.latest_updated_at, paper_statement.excluded.latest_updated_at
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

        session.execute(
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

        for position, author_name in enumerate(record.authors):
            display_name = normalize_author_name(author_name)
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
                insert(PaperVersionAuthorRow)
                .values(paper_version_id=version_id, author_id=author_id, position=position)
                .on_conflict_do_nothing(
                    index_elements=[
                        PaperVersionAuthorRow.paper_version_id,
                        PaperVersionAuthorRow.author_id,
                    ]
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
                set_={"last_discovered_at": topic_paper_statement.excluded.last_discovered_at},
            )
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

    def get_analysis_run_for_date(self, topic_id: UUID, logical_date: date) -> DailyRun | None:
        statement = select(DailyRunRow).where(
            DailyRunRow.topic_id == topic_id,
            DailyRunRow.logical_date == logical_date,
            DailyRunRow.operation == RunOperation.STRUCTURED_ANALYSIS.value,
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
    ) -> DailyRun:
        if not targets:
            raise RepositoryError("analysis run requires selected targets")
        run_id = uuid4()
        try:
            with self._sessions.begin() as session:
                row = session.scalars(
                    insert(DailyRunRow)
                    .values(
                        id=run_id,
                        topic_id=topic_id,
                        logical_date=logical_date,
                        operation=RunOperation.STRUCTURED_ANALYSIS.value,
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
                        DailyRunRow.operation == RunOperation.STRUCTURED_ANALYSIS.value,
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
                        DailyRunRow.operation == RunOperation.STRUCTURED_ANALYSIS.value,
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
        if paper_version_id is None:
            statement = statement.join(PaperRow, PaperRow.id == PaperAnalysisRow.paper_id).where(
                PaperVersionRow.version == PaperRow.current_version
            )
        else:
            statement = statement.where(PaperAnalysisRow.paper_version_id == paper_version_id)
        if analysis_scope is not None:
            statement = statement.where(PaperAnalysisRow.analysis_scope == analysis_scope.value)
        statement = statement.order_by(
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
        statement = select(DailyRunRow)
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


def _advisory_key(topic_id: UUID, logical_date: date) -> int:
    folded_uuid = (topic_id.int >> 64) ^ (topic_id.int & ((1 << 64) - 1))
    return (folded_uuid ^ logical_date.toordinal()) & ((1 << 63) - 1)


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
