"""Deterministic test doubles kept outside production wiring."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID, uuid4, uuid5

from paper_harness.application.read_models import (
    AnalysisDetail,
    AnalysisTarget,
    PaperDetail,
    RunDetail,
    RunItemDetail,
    StoredTopic,
)
from paper_harness.domain.analysis import AnalysisBundle, AnalysisScope, Evidence, ParsedPaper
from paper_harness.domain.models import (
    DailyRun,
    IngestionCursor,
    Paper,
    PaperStage,
    RunItem,
    RunItemStatus,
    RunOperation,
    RunStatus,
    TopicConfig,
)
from paper_harness.ports.arxiv import ArxivPaperRecord, ArxivPdf, ArxivPortError
from paper_harness.ports.repository import RepositoryError


class FakeArxiv:
    def __init__(
        self,
        records: tuple[ArxivPaperRecord, ...] = (),
        error: ArxivPortError | None = None,
        pdf_content: bytes = b"%PDF-1.7\nfixture",
        pdf_error: ArxivPortError | None = None,
    ) -> None:
        self.records = records
        self.error = error
        self.pdf_content = pdf_content
        self.pdf_error = pdf_error
        self.calls: list[tuple[str, datetime, datetime, int]] = []
        self.pdf_calls: list[tuple[str, int, str]] = []

    def search(
        self,
        *,
        query: str,
        updated_from: datetime,
        updated_until: datetime,
        max_results: int,
    ) -> tuple[ArxivPaperRecord, ...]:
        self.calls.append((query, updated_from, updated_until, max_results))
        if self.error is not None:
            raise self.error
        return self.records

    def download_pdf(
        self,
        *,
        canonical_arxiv_id: str,
        version: int,
        pdf_url: str,
    ) -> ArxivPdf:
        self.pdf_calls.append((canonical_arxiv_id, version, pdf_url))
        if self.pdf_error is not None:
            raise self.pdf_error
        return ArxivPdf(
            canonical_arxiv_id=canonical_arxiv_id,
            version=version,
            source_url=pdf_url,
            content=self.pdf_content,
        )


class FakeRepository:
    def __init__(self) -> None:
        self.topic: StoredTopic | None = None
        self.cursor: IngestionCursor | None = None
        self.run: DailyRun | None = None
        self.items: tuple[RunItem, ...] = ()
        self.papers: tuple[Paper, ...] = ()
        self.paper_detail: PaperDetail | None = None
        self.analysis_detail: AnalysisDetail | None = None
        self.analysis_targets: tuple[AnalysisTarget, ...] = ()
        self.parsed_papers: dict[UUID, ParsedPaper] = {}
        self.ready_error: Exception | None = None
        self.analysis_persist_error: RepositoryError | None = None
        self.finalize_error: RepositoryError | None = None
        self.locked = False

    @contextmanager
    def daily_run_lock(self, topic_id: UUID, logical_date: date) -> Generator[None]:
        del topic_id, logical_date
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def upsert_topic(self, topic: TopicConfig) -> StoredTopic:
        created_at = datetime(2026, 1, 1, tzinfo=UTC)
        self.topic = StoredTopic(config=topic, created_at=created_at)
        return self.topic

    def get_ingestion_cursor(self, topic_id: UUID) -> IngestionCursor | None:
        del topic_id
        return self.cursor

    def get_run_for_date(self, topic_id: UUID, logical_date: date) -> DailyRun | None:
        del topic_id, logical_date
        return self.run

    def start_ingestion_run(
        self,
        *,
        topic_id: UUID,
        logical_date: date,
        started_at: datetime,
        cursor_from: datetime,
        cursor_to: datetime,
    ) -> DailyRun:
        self.run = DailyRun(
            id=uuid4(),
            topic_id=topic_id,
            logical_date=logical_date,
            operation=RunOperation.ARXIV_INGESTION,
            analysis_scope=None,
            status=RunStatus.RUNNING,
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
        return self.run

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
        del topic
        self.items = tuple(
            RunItem(
                id=uuid5(run_id, f"{record.canonical_arxiv_id}:v{record.version}"),
                run_id=run_id,
                paper_id=uuid5(run_id, record.canonical_arxiv_id),
                paper_version_id=uuid5(
                    run_id, f"version:{record.canonical_arxiv_id}:v{record.version}"
                ),
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
            for record in records
        )
        self.cursor = IngestionCursor(
            topic_id=self.run.topic_id if self.run is not None else uuid4(),
            watermark=watermark,
            schema_version=1,
            created_at=persisted_at,
            updated_at=persisted_at,
        )
        if self.run is None or self.run.id != run_id:
            raise AssertionError("run was not started")
        self.run = replace(
            self.run,
            status=RunStatus.COMPLETE,
            completed_at=completed_at,
            discovered_count=len(records),
            normalized_count=len(self.items),
        )
        return self.run

    def fail_ingestion_run(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> DailyRun:
        if self.run is None or self.run.id != run_id:
            raise AssertionError("run was not started")
        self.run = replace(
            self.run,
            status=RunStatus.FAILED,
            completed_at=completed_at,
            error_code=error_code,
            error_detail=error_detail,
        )
        return self.run

    def check_ready(self) -> None:
        if self.ready_error is not None:
            raise self.ready_error

    def list_topics(self) -> tuple[StoredTopic, ...]:
        return () if self.topic is None else (self.topic,)

    def list_papers(
        self, *, topic_slug: str | None, limit: int, offset: int
    ) -> tuple[tuple[Paper, ...], int]:
        del topic_slug
        return self.papers[offset : offset + limit], len(self.papers)

    def get_paper(self, paper_id: UUID) -> PaperDetail | None:
        del paper_id
        return self.paper_detail

    def list_runs(
        self, *, topic_slug: str | None, limit: int, offset: int
    ) -> tuple[tuple[DailyRun, ...], int]:
        del topic_slug, limit, offset
        return (() if self.run is None else (self.run,)), int(self.run is not None)

    def get_latest_run(self, *, topic_slug: str | None) -> RunDetail | None:
        del topic_slug
        return (
            None
            if self.run is None
            else RunDetail(
                run=self.run,
                items=tuple(
                    RunItemDetail(
                        item=item,
                        canonical_arxiv_id="2601.01234",
                        paper_title="A Reliable LLM Agent",
                    )
                    for item in self.items
                ),
            )
        )

    def get_analysis_targets(
        self, topic_id: UUID, paper_ids: tuple[UUID, ...]
    ) -> tuple[AnalysisTarget, ...]:
        del topic_id
        return tuple(
            target
            for paper_id in paper_ids
            for target in self.analysis_targets
            if target.paper.id == paper_id
        )

    def get_analysis_run_for_date(self, topic_id: UUID, logical_date: date) -> DailyRun | None:
        del topic_id, logical_date
        if self.run is not None and self.run.operation is RunOperation.STRUCTURED_ANALYSIS:
            return self.run
        return None

    def start_analysis_run(
        self,
        *,
        topic_id: UUID,
        logical_date: date,
        analysis_scope: AnalysisScope,
        started_at: datetime,
        targets: tuple[AnalysisTarget, ...],
    ) -> DailyRun:
        self.run = DailyRun(
            id=uuid4(),
            topic_id=topic_id,
            logical_date=logical_date,
            operation=RunOperation.STRUCTURED_ANALYSIS,
            analysis_scope=analysis_scope,
            status=RunStatus.RUNNING,
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
        self.items = tuple(
            RunItem(
                id=uuid5(self.run.id, str(target.version.id)),
                run_id=self.run.id,
                paper_id=target.paper.id,
                paper_version_id=target.version.id,
                stage=PaperStage.SELECTED,
                status=RunItemStatus.IN_PROGRESS,
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
        return self.run

    def advance_analysis_item(
        self,
        *,
        run_id: UUID,
        paper_version_id: UUID,
        expected_stage: PaperStage,
        next_stage: PaperStage,
        updated_at: datetime,
    ) -> None:
        del run_id
        self.items = tuple(
            replace(item, stage=next_stage, updated_at=updated_at)
            if item.paper_version_id == paper_version_id and item.stage is expected_stage
            else item
            for item in self.items
        )

    def persist_parsed_paper(
        self,
        *,
        run_id: UUID,
        parsed_paper: ParsedPaper,
        expected_stage: PaperStage,
        updated_at: datetime,
    ) -> ParsedPaper:
        canonical = self.parsed_papers.setdefault(parsed_paper.id, parsed_paper)
        if (
            canonical.paper_id != parsed_paper.paper_id
            or canonical.paper_version_id != parsed_paper.paper_version_id
            or canonical.parser_name != parsed_paper.parser_name
            or canonical.parser_version != parsed_paper.parser_version
        ):
            raise RepositoryError("stable parsed-paper identity conflicts with stored data")
        self.advance_analysis_item(
            run_id=run_id,
            paper_version_id=parsed_paper.paper_version_id,
            expected_stage=expected_stage,
            next_stage=PaperStage.PARSED,
            updated_at=updated_at,
        )
        return canonical

    def persist_analysis_bundle(
        self,
        *,
        run_id: UUID,
        bundle: AnalysisBundle,
        expected_stage: PaperStage,
        updated_at: datetime,
    ) -> None:
        del run_id, expected_stage
        if self.analysis_persist_error is not None:
            raise self.analysis_persist_error
        parsed = (
            None
            if bundle.analysis.parsed_paper_id is None
            else self.parsed_papers[bundle.analysis.parsed_paper_id]
        )
        self.analysis_detail = AnalysisDetail(
            analysis=bundle.analysis,
            arxiv_version=1,
            claims=bundle.claims,
            evidence=bundle.evidence,
            parser_name=None if parsed is None else parsed.parser_name,
            parser_version=None if parsed is None else parsed.parser_version,
        )
        self.items = tuple(
            replace(
                item,
                stage=PaperStage.EVIDENCE_EXTRACTED,
                status=RunItemStatus.COMPLETED,
                updated_at=updated_at,
            )
            if item.paper_version_id == bundle.analysis.paper_version_id
            else item
            for item in self.items
        )

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
        del run_id
        self.items = tuple(
            replace(
                item,
                status=RunItemStatus.FAILED,
                failed_stage=failed_stage,
                error_code=error_code,
                retryable=retryable,
                error_detail=error_detail,
                updated_at=updated_at,
            )
            if item.paper_version_id == paper_version_id
            else item
            for item in self.items
        )

    def finalize_analysis_run(self, run_id: UUID, *, completed_at: datetime) -> DailyRun:
        if self.finalize_error is not None:
            raise self.finalize_error
        if self.run is None or self.run.id != run_id:
            raise AssertionError("run was not started")
        completed = sum(item.status is RunItemStatus.COMPLETED for item in self.items)
        failed = sum(item.status is RunItemStatus.FAILED for item in self.items)
        status = (
            RunStatus.COMPLETE
            if failed == 0
            else (RunStatus.PARTIAL if completed else RunStatus.FAILED)
        )
        self.run = replace(
            self.run,
            status=status,
            completed_at=completed_at,
            completed_count=completed,
            failed_count=failed,
            error_code=None if completed else "NO_SELECTED_PAPER_COMPLETED",
            error_detail=None if completed else "No selected paper completed evidence extraction.",
        )
        return self.run

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
        if self.run is None or self.run.id != run_id:
            raise AssertionError("run was not started")
        self.items = tuple(
            replace(
                item,
                status=RunItemStatus.FAILED,
                failed_stage=failed_stage,
                error_code=error_code,
                retryable=retryable,
                error_detail=error_detail,
                updated_at=completed_at,
            )
            if item.status is RunItemStatus.IN_PROGRESS
            else item
            for item in self.items
        )
        completed_count = sum(item.status is RunItemStatus.COMPLETED for item in self.items)
        failed_count = sum(item.status is RunItemStatus.FAILED for item in self.items)
        self.run = replace(
            self.run,
            status=RunStatus.FAILED,
            completed_at=completed_at,
            completed_count=completed_count,
            error_code=error_code,
            error_detail=error_detail,
            failed_count=failed_count,
        )
        return self.run

    def get_paper_analysis(
        self,
        paper_id: UUID,
        *,
        paper_version_id: UUID | None,
        analysis_scope: AnalysisScope | None = None,
    ) -> AnalysisDetail | None:
        if self.analysis_detail is None:
            return None
        if self.analysis_detail.analysis.paper_id != paper_id:
            return None
        if (
            paper_version_id is not None
            and self.analysis_detail.analysis.paper_version_id != paper_version_id
        ):
            return None
        if (
            analysis_scope is not None
            and self.analysis_detail.analysis.analysis_scope is not analysis_scope
        ):
            return None
        return self.analysis_detail

    def list_paper_evidence(
        self,
        paper_id: UUID,
        *,
        analysis_id: UUID,
        paper_version_id: UUID | None,
        analysis_scope: AnalysisScope | None = None,
    ) -> tuple[Evidence, ...] | None:
        detail = self.get_paper_analysis(
            paper_id,
            paper_version_id=paper_version_id,
            analysis_scope=analysis_scope,
        )
        if detail is None or detail.analysis.id != analysis_id:
            return None
        return detail.evidence
