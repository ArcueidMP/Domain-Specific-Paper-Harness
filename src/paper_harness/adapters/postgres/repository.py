"""Synchronous SQLAlchemy/PostgreSQL repository for M1."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, datetime
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Engine, case, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from paper_harness.application.read_models import PaperDetail, RunDetail, StoredTopic
from paper_harness.domain.errors import DuplicateDailyRunError
from paper_harness.domain.identity import (
    normalize_author_name,
    stable_author_id,
    stable_paper_id,
    stable_paper_version_id,
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
    RepositoryUnavailableError,
)

from .models import (
    AuthorRow,
    DailyRunRow,
    IngestionCursorRow,
    PaperRow,
    PaperSourceIdentityRow,
    PaperVersionAuthorRow,
    PaperVersionRow,
    RunItemRow,
    TopicPaperRow,
    TopicRow,
)

EXPECTED_DATABASE_REVISION = "0001_m1_ingestion"


class PostgresRepository:
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
                try:
                    yield
                finally:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": key}
                    )
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
                status=RunStatus.RUNNING.value,
                started_at=started_at,
                completed_at=None,
                cursor_from=cursor_from,
                cursor_to=cursor_to,
                discovered_count=0,
                normalized_count=0,
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
        try:
            with self._sessions() as session:
                run_row = session.scalars(statement).one_or_none()
                if run_row is None:
                    return None
                item_rows = tuple(
                    session.scalars(
                        select(RunItemRow)
                        .where(RunItemRow.run_id == run_row.id)
                        .order_by(RunItemRow.created_at, RunItemRow.id)
                    )
                )
        except OperationalError as error:
            raise RepositoryUnavailableError(
                "PostgreSQL latest run query is unavailable"
            ) from error
        return RunDetail(run=_run_from_row(run_row), items=tuple(map(_item_from_row, item_rows)))


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
        status=RunStatus(row.status),
        started_at=row.started_at,
        completed_at=row.completed_at,
        cursor_from=row.cursor_from,
        cursor_to=row.cursor_to,
        discovered_count=row.discovered_count,
        normalized_count=row.normalized_count,
        failed_count=row.failed_count,
        error_code=row.error_code,
        error_detail=row.error_detail,
        schema_version=row.schema_version,
        created_at=row.created_at,
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
