# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError
from tests.fakes import FakeArxiv, fake_pipeline_execution_contract
from tests.integration.test_m3_postgres_repository import (
    _external_stub,
    _pending_local_candidate,
    _search_session,
)

from paper_harness.adapters.postgres import PostgresRepository
from paper_harness.application.analyze_papers import AnalyzePapers
from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.domain.analysis import (
    AnalysisRequest,
    AnalysisScope,
    ClaimType,
    EvidenceType,
    GeneratedAnalysis,
    GeneratedClaim,
    GeneratedEvidence,
    ModelUsage,
    ParsedPaper,
    ParsedPassage,
    ParsedSection,
)
from paper_harness.domain.errors import DuplicateDailyRunError
from paper_harness.domain.historical import (
    BackfillStatus,
    CandidateSelectionRequest,
    ComparisonRequest,
    ComparisonTargetDecision,
    CrawlerPlanRequest,
    GeneratedCandidateSelection,
    GeneratedComparison,
    GeneratedCrawlerPlan,
    HistoricalBackfillRun,
)
from paper_harness.domain.identity import (
    stable_parsed_paper_id,
    stable_parsed_passage_id,
    stable_parsed_section_id,
    stable_pipeline_execution_id,
)
from paper_harness.domain.models import (
    PaperStage,
    PipelineExecution,
    PipelineExecutionMode,
    RunItemStatus,
    RunOperation,
    RunStatus,
    TopicConfig,
)
from paper_harness.domain.reports import GeneratedReportNarrative, ReportNarrativeRequest
from paper_harness.entrypoints.api import create_app
from paper_harness.ports.arxiv import ArxivPaperRecord
from paper_harness.ports.llm import LLMOutputError
from paper_harness.ports.pdf_parser import PdfParseRequest
from paper_harness.ports.repository import RepositoryError, RepositoryIntegrityError

pytestmark = pytest.mark.integration


def _start_pipeline_execution(
    repository: PostgresRepository,
    topic: TopicConfig,
    *,
    logical_date: date,
    started_at: datetime,
    selection_limit: int,
    analysis_scope: AnalysisScope = AnalysisScope.ABSTRACT_ONLY,
) -> UUID:
    repository.upsert_topic(topic)
    execution_id = stable_pipeline_execution_id(topic.id, logical_date)
    repository.start_pipeline_execution(
        PipelineExecution(
            id=execution_id,
            topic_id=topic.id,
            logical_date=logical_date,
            execution_mode=PipelineExecutionMode.NORMAL,
            analysis_scope=analysis_scope,
            selection_limit=selection_limit,
            contract=fake_pipeline_execution_contract(),
            status=RunStatus.RUNNING,
            deadline_at=started_at + timedelta(hours=8),
            started_at=started_at,
            completed_at=None,
            error_code=None,
            error_detail=None,
            schema_version=1,
            created_at=started_at,
        )
    )
    return execution_id


class SelectiveAnalysisLLM:
    def __init__(self, failing_paper_id: UUID) -> None:
        self._failing_paper_id = failing_paper_id
        self.requests: list[AnalysisRequest] = []

    def analyze(self, request: AnalysisRequest) -> GeneratedAnalysis:
        self.requests.append(request)
        if request.paper_id == self._failing_paper_id:
            raise LLMOutputError("DeepSeek JSON output failed schema validation")
        return GeneratedAnalysis(
            provider="deepseek",
            configured_model="deepseek-v4-flash",
            model_version="DeepSeek-V4-Flash-2026-04-24",
            prompt_version="m2-analysis-v1",
            generated_at=datetime(2026, 1, 10, 5, 2, tzinfo=UTC),
            claims=(
                GeneratedClaim(
                    key="method_1",
                    claim_type=ClaimType.METHOD,
                    text="The paper evaluates a tool-using language model agent.",
                ),
            ),
            evidence=(
                GeneratedEvidence(
                    key="evidence_1",
                    claim_keys=("method_1",),
                    passage_ids=(request.passages[0].id,),
                    evidence_type=EvidenceType.SUPPORTS,
                ),
            ),
            usage=ModelUsage(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                call_count=1,
                duration_ms=500,
                estimated_cost_usd=None,
            ),
        )

    def select_prior_work(
        self,
        request: CandidateSelectionRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> GeneratedCandidateSelection:
        del timeout_seconds
        raise AssertionError(f"unexpected prior-work selection request: {request}")

    def plan_scholarly_search(
        self,
        request: CrawlerPlanRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> GeneratedCrawlerPlan:
        del timeout_seconds
        raise AssertionError(f"unexpected crawler planning request: {request}")

    def compare_papers(self, request: ComparisonRequest) -> GeneratedComparison:
        raise AssertionError(f"unexpected comparison request: {request}")

    def generate_report(self, request: ReportNarrativeRequest) -> GeneratedReportNarrative:
        raise AssertionError(f"unexpected report request: {request}")


class StaticParser:
    def __init__(self, text_value: str, *, call_count: int, duration_ms: int) -> None:
        self._text = text_value
        self._call_count = call_count
        self._duration_ms = duration_ms

    def parse(self, request: PdfParseRequest) -> ParsedPaper:
        parsed_id = stable_parsed_paper_id(request.paper_version_id, "grobid", "0.9.0")
        passage = ParsedPassage(
            id=stable_parsed_passage_id(parsed_id, "body-1"),
            source_id="body-1",
            section_index=0,
            passage_index=0,
            text=self._text,
        )
        return ParsedPaper(
            id=parsed_id,
            paper_id=request.paper_id,
            paper_version_id=request.paper_version_id,
            parser_name="grobid",
            parser_version="0.9.0",
            parsed_at=datetime(2026, 1, 10, 5, 1, tzinfo=UTC),
            source="grobid_tei",
            sections=(
                ParsedSection(
                    id=stable_parsed_section_id(parsed_id, 0),
                    index=0,
                    title="Results",
                    passages=(passage,),
                ),
            ),
            references=(),
            citation_contexts=(),
            call_count=self._call_count,
            duration_ms=self._duration_ms,
        )


def test_migration_readiness_and_versioned_idempotent_ingestion(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    postgres_repository.check_ready()
    first_time = datetime(2026, 1, 10, 5, tzinfo=UTC)
    first_arxiv = FakeArxiv((arxiv_record_v1, arxiv_record_v1))
    first_run = IngestArxiv(
        arxiv=first_arxiv, repository=postgres_repository, clock=lambda: first_time
    ).execute(topic_config, logical_date=date(2026, 1, 10))
    assert first_run.status is RunStatus.COMPLETE
    assert first_run.discovered_count == 1

    v2 = replace(
        arxiv_record_v1,
        version=2,
        title="A Reliable LLM Agent, Revised",
        updated_at=arxiv_record_v1.updated_at + timedelta(days=1),
        pdf_url="https://arxiv.org/pdf/2601.01234v2",
        source_url="https://arxiv.org/abs/2601.01234v2",
    )
    second_time = first_time + timedelta(days=1)
    second_arxiv = FakeArxiv((arxiv_record_v1, v2))
    IngestArxiv(
        arxiv=second_arxiv, repository=postgres_repository, clock=lambda: second_time
    ).execute(topic_config, logical_date=date(2026, 1, 11))

    papers, total = postgres_repository.list_papers(
        topic_slug=topic_config.slug, limit=10, offset=0
    )
    assert total == 1
    assert papers[0].current_version == 2
    detail = postgres_repository.get_paper(papers[0].id)
    assert detail is not None
    assert [version.version for version in detail.versions] == [2, 1]
    assert second_arxiv.calls[0][1] == first_time - timedelta(hours=topic_config.overlap_hours)
    client = TestClient(create_app(postgres_repository))
    assert client.get("/health/ready").status_code == 200
    api_papers = client.get(f"/api/v1/papers?topic={topic_config.slug}").json()
    assert api_papers["total"] == 0


def test_same_version_metadata_drift_preserves_first_snapshot_and_author_projection(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    first_time = datetime(2026, 1, 10, 5, tzinfo=UTC)

    def ingest(record: ArxivPaperRecord, *, observed_at: datetime) -> None:
        IngestArxiv(
            arxiv=FakeArxiv((record,)),
            repository=postgres_repository,
            clock=lambda: observed_at,
        ).execute(topic_config, logical_date=observed_at.date())

    ingest(arxiv_record_v1, observed_at=first_time)
    ingest(arxiv_record_v1, observed_at=first_time + timedelta(days=1))
    drift_records = (
        replace(
            arxiv_record_v1,
            title="Same version with reordered authors",
            abstract="This replay must not replace the first explicit-version snapshot.",
            updated_at=arxiv_record_v1.updated_at + timedelta(days=1),
            primary_category="cs.CL",
            categories=("cs.CL",),
            authors=("Alan Turing", "Ada Lovelace"),
            pdf_url="https://arxiv.org/pdf/2601.01234v1?reordered=1",
        ),
        replace(
            arxiv_record_v1,
            title="Same version with an inserted author",
            updated_at=arxiv_record_v1.updated_at + timedelta(days=2),
            authors=("Ada Lovelace", "Barbara Liskov", "Alan Turing"),
        ),
        replace(
            arxiv_record_v1,
            title="Same version with a removed author",
            updated_at=arxiv_record_v1.updated_at + timedelta(days=3),
            authors=("Ada Lovelace",),
        ),
    )
    for offset, record in enumerate(drift_records, start=2):
        ingest(record, observed_at=first_time + timedelta(days=offset))

    detail = postgres_repository.get_paper(
        postgres_repository.list_papers(topic_slug=None, limit=10, offset=0)[0][0].id
    )
    assert detail is not None
    assert detail.paper.title == arxiv_record_v1.title
    assert detail.paper.abstract == arxiv_record_v1.abstract
    assert detail.paper.current_version == 1
    assert detail.paper.latest_updated_at == arxiv_record_v1.updated_at
    assert detail.paper.primary_category == arxiv_record_v1.primary_category
    assert detail.paper.categories == arxiv_record_v1.categories
    assert detail.paper.authors == arxiv_record_v1.authors
    assert detail.paper.pdf_url == arxiv_record_v1.pdf_url
    assert len(detail.versions) == 1
    assert detail.versions[0].authors == arxiv_record_v1.authors
    assert detail.versions[0].title == arxiv_record_v1.title

    last_same_version_observation = first_time + timedelta(days=len(drift_records) + 1)
    with postgres_engine.connect() as connection:
        same_version_links = connection.execute(
            text(
                "SELECT pv.version, pva.position, a.display_name "
                "FROM paper_version_authors AS pva "
                "JOIN paper_versions AS pv ON pv.id = pva.paper_version_id "
                "JOIN authors AS a ON a.id = pva.author_id "
                "WHERE pv.paper_id = :paper_id "
                "ORDER BY pv.version, pva.position"
            ),
            {"paper_id": detail.paper.id},
        ).all()
        last_discovered_at = connection.execute(
            text(
                "SELECT last_discovered_at FROM topic_papers "
                "WHERE topic_id = :topic_id AND paper_id = :paper_id"
            ),
            {"topic_id": topic_config.id, "paper_id": detail.paper.id},
        ).scalar_one()
    assert same_version_links == [(1, 0, "Ada Lovelace"), (1, 1, "Alan Turing")]
    assert last_discovered_at == last_same_version_observation

    v2 = replace(
        arxiv_record_v1,
        version=2,
        title="A Reliable LLM Agent, Revised",
        abstract="Version two has an independent immutable author projection.",
        updated_at=arxiv_record_v1.updated_at + timedelta(days=5),
        primary_category="cs.CL",
        categories=("cs.CL", "cs.AI"),
        authors=("Grace Hopper", "Alan Turing"),
        pdf_url="https://arxiv.org/pdf/2601.01234v2",
        source_url="https://arxiv.org/abs/2601.01234v2",
    )
    v2_observed_at = first_time + timedelta(days=5)
    ingest(v2, observed_at=v2_observed_at)

    revised = postgres_repository.get_paper(detail.paper.id)
    assert revised is not None
    assert revised.paper.current_version == 2
    assert revised.paper.title == v2.title
    assert revised.paper.latest_updated_at == v2.updated_at
    assert revised.paper.authors == v2.authors
    assert [(version.version, version.authors) for version in revised.versions] == [
        (2, v2.authors),
        (1, arxiv_record_v1.authors),
    ]
    with postgres_engine.connect() as connection:
        all_links = connection.execute(
            text(
                "SELECT pv.version, pva.position, a.display_name "
                "FROM paper_version_authors AS pva "
                "JOIN paper_versions AS pv ON pv.id = pva.paper_version_id "
                "JOIN authors AS a ON a.id = pva.author_id "
                "WHERE pv.paper_id = :paper_id "
                "ORDER BY pv.version, pva.position"
            ),
            {"paper_id": detail.paper.id},
        ).all()
    assert all_links == [
        (1, 0, "Ada Lovelace"),
        (1, 1, "Alan Turing"),
        (2, 0, "Grace Hopper"),
        (2, 1, "Alan Turing"),
    ]


@pytest.mark.parametrize("database_error_type", [IntegrityError, DataError])
def test_arxiv_batch_database_rejection_is_sanitized_failed_and_replayable(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
    monkeypatch: pytest.MonkeyPatch,
    database_error_type: type[DBAPIError],
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    execution_id = _start_pipeline_execution(
        postgres_repository,
        topic_config,
        logical_date=now.date(),
        started_at=now,
        selection_limit=1,
    )
    original_persist_record = postgres_repository._persist_record

    def reject_persistence(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise database_error_type(
            "INSERT INTO paper_version_authors ...",
            {"database_secret": "must-not-escape"},
            RuntimeError("uq_version_author_position"),
        )

    monkeypatch.setattr(postgres_repository, "_persist_record", reject_persistence)
    use_case = IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=postgres_repository,
        clock=lambda: now,
    )
    with pytest.raises(
        RepositoryIntegrityError,
        match="^PostgreSQL rejected arXiv batch persistence$",
    ) as caught:
        use_case.execute(
            topic_config,
            logical_date=now.date(),
            pipeline_execution_mode=PipelineExecutionMode.NORMAL,
            pipeline_selection_limit=1,
            pipeline_execution_id=execution_id,
        )

    assert isinstance(caught.value.__cause__, database_error_type)
    failed = postgres_repository.get_run_for_date(
        topic_config.id,
        now.date(),
        pipeline_execution_id=execution_id,
    )
    assert failed is not None
    assert failed.status is RunStatus.FAILED
    assert failed.error_code == "PERSISTENCE_INTEGRITY_FAILED"
    assert failed.error_detail == "PostgreSQL rejected arXiv batch persistence"
    assert postgres_repository.get_ingestion_cursor(topic_config.id) is None
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM papers")).scalar_one() == 0

    monkeypatch.setattr(postgres_repository, "_persist_record", original_persist_record)
    resumed = use_case.execute(
        topic_config,
        logical_date=now.date(),
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=1,
        pipeline_execution_id=execution_id,
        resume_existing=True,
    )
    assert resumed.id == failed.id
    assert resumed.status is RunStatus.COMPLETE
    assert resumed.normalized_count == 1
    with postgres_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM daily_runs "
                    "WHERE operation = 'ARXIV_INGESTION' "
                    "AND pipeline_execution_id = :execution_id"
                ),
                {"execution_id": execution_id},
            ).scalar_one()
            == 1
        )


def test_new_version_rejects_duplicate_normalized_authors_atomically(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    duplicate_authors = replace(
        arxiv_record_v1,
        authors=("Ada Lovelace", "  ada   lovelace  "),
    )

    with pytest.raises(
        RepositoryIntegrityError,
        match="duplicate normalized authors",
    ):
        IngestArxiv(
            arxiv=FakeArxiv((duplicate_authors,)),
            repository=postgres_repository,
            clock=lambda: now,
        ).execute(topic_config, logical_date=now.date())

    failed = postgres_repository.get_run_for_date(topic_config.id, now.date())
    assert failed is not None
    assert failed.status is RunStatus.FAILED
    assert failed.error_code == "PERSISTENCE_INTEGRITY_FAILED"
    assert postgres_repository.get_ingestion_cursor(topic_config.id) is None
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM papers")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM authors")).scalar_one() == 0


def test_duplicate_logical_run_is_rejected_before_external_call(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=postgres_repository,
        clock=lambda: now,
    ).execute(topic_config, logical_date=now.date())
    second_arxiv = FakeArxiv((arxiv_record_v1,))
    with pytest.raises(DuplicateDailyRunError):
        IngestArxiv(
            arxiv=second_arxiv,
            repository=postgres_repository,
            clock=lambda: now,
        ).execute(topic_config, logical_date=now.date())
    assert second_arxiv.calls == []


def test_pipeline_selection_provenance_and_stage_are_persisted_atomically(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    execution_id = _start_pipeline_execution(
        postgres_repository,
        topic_config,
        logical_date=now.date(),
        started_at=now,
        selection_limit=1,
    )
    run = IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=postgres_repository,
        clock=lambda: now,
    ).execute(
        topic_config,
        logical_date=now.date(),
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=1,
        pipeline_execution_id=execution_id,
    )
    before = postgres_repository.get_run(run.id)
    assert before is not None
    assert before.run.pipeline_execution_mode is PipelineExecutionMode.NORMAL
    assert before.run.pipeline_selection_limit == 1
    assert before.items[0].item.stage is PaperStage.NORMALIZED
    assert before.items[0].item.status is RunItemStatus.COMPLETED

    postgres_repository.persist_ingestion_selection(
        run.id,
        selected_paper_version_ids=(before.items[0].item.paper_version_id,),
        updated_at=now + timedelta(seconds=1),
    )

    selected = postgres_repository.get_run(run.id)
    assert selected is not None
    assert selected.items[0].item.stage is PaperStage.SELECTED
    assert selected.items[0].item.status is RunItemStatus.COMPLETED


def test_pipeline_execution_contract_round_trips_and_refreshes_failed_replays(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    contract = fake_pipeline_execution_contract()
    execution_id = _start_pipeline_execution(
        postgres_repository,
        topic_config,
        logical_date=now.date(),
        started_at=now,
        selection_limit=1,
    )
    persisted = postgres_repository.get_pipeline_execution(execution_id)
    assert persisted is not None
    assert persisted.contract == contract

    changed_running_contract = replace(
        contract,
        daily_selection_policy_version="daily-selection-v2",
    )
    refreshed = postgres_repository.start_pipeline_execution(
        replace(persisted, contract=changed_running_contract)
    )
    assert refreshed.contract == changed_running_contract
    assert postgres_repository.get_pipeline_execution(execution_id) == refreshed

    postgres_repository.fail_pipeline_execution(
        execution_id,
        completed_at=now + timedelta(minutes=1),
        error_code="DAILY_PIPELINE_FAILED",
        error_detail="retryable execution failure",
    )
    changed_failed_contract = replace(
        contract,
        pipeline_orchestration_version="daily-pipeline-v2",
    )
    restarted = postgres_repository.restart_pipeline_execution(
        execution_id,
        started_at=now + timedelta(hours=1),
        deadline_at=now + timedelta(hours=9),
        contract=changed_failed_contract,
    )
    assert restarted.status is RunStatus.RUNNING
    assert restarted.contract == changed_failed_contract
    assert postgres_repository.get_pipeline_execution(execution_id) == restarted


def test_pipeline_execution_contract_json_ignores_unknown_fields(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    execution_id = _start_pipeline_execution(
        postgres_repository,
        topic_config,
        logical_date=now.date(),
        started_at=now,
        selection_limit=1,
    )
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE pipeline_executions SET execution_contract = "
                'execution_contract || \'{"future_semantic_field": "v2"}\'::jsonb '
                "WHERE id = :execution_id"
            ),
            {"execution_id": execution_id},
        )

    stored = postgres_repository.get_pipeline_execution(execution_id)
    assert stored is not None
    assert stored.contract == fake_pipeline_execution_contract()


def test_pipeline_execution_contract_json_rejects_wrong_scalar_type(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    execution_id = _start_pipeline_execution(
        postgres_repository,
        topic_config,
        logical_date=now.date(),
        started_at=now,
        selection_limit=1,
    )
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE pipeline_executions SET execution_contract = "
                "jsonb_set(execution_contract, '{pipeline_timeout_seconds}', "
                "'\"28800\"'::jsonb) WHERE id = :execution_id"
            ),
            {"execution_id": execution_id},
        )

    with pytest.raises(RepositoryError, match="stored pipeline execution contract is invalid"):
        postgres_repository.get_pipeline_execution(execution_id)


def test_pipeline_failure_normalizes_database_bounds(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    execution_id = _start_pipeline_execution(
        postgres_repository,
        topic_config,
        logical_date=now.date(),
        started_at=now,
        selection_limit=1,
    )

    failed = postgres_repository.fail_pipeline_execution(
        execution_id,
        completed_at=now + timedelta(minutes=1),
        error_code=f"  {'E' * 100}  ",
        error_detail=f"  {'detail' * 250}  ",
    )

    assert failed.error_code == "E" * 80
    assert failed.error_detail == ("detail" * 250)[:1000]


def test_search_session_execution_foreign_key_owns_the_same_topic(
    postgres_engine: Engine,
) -> None:
    with postgres_engine.connect() as connection:
        definition = connection.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'fk_search_sessions_pipeline_execution'"
            )
        ).scalar_one()

    normalized = " ".join(str(definition).split())
    assert "FOREIGN KEY (pipeline_execution_id, topic_id)" in normalized
    assert "REFERENCES pipeline_executions(id, topic_id)" in normalized


def test_search_session_rejects_cross_topic_execution_and_duplicate_source_analysis(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=postgres_repository,
        clock=lambda: now,
    ).execute(topic_config, logical_date=now.date())
    paper = postgres_repository.list_papers(
        topic_slug=topic_config.slug,
        limit=1,
        offset=0,
    )[0][0]
    AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=SelectiveAnalysisLLM(UUID(int=0)),
        repository=postgres_repository,
        clock=lambda: now + timedelta(minutes=1),
    ).execute(
        topic_config,
        paper_ids=(paper.id,),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=now.date(),
    )
    detail = postgres_repository.get_paper(paper.id)
    assert detail is not None
    analysis = postgres_repository.get_paper_analysis(
        paper.id,
        paper_version_id=detail.versions[-1].id,
    )
    assert analysis is not None
    source_version = detail.versions[-1]
    execution_id = _start_pipeline_execution(
        postgres_repository,
        topic_config,
        logical_date=now.date(),
        started_at=now + timedelta(minutes=2),
        selection_limit=1,
    )
    other_topic = replace(
        topic_config,
        id=UUID("b317c0f0-7c37-4e75-9af6-1a530486fa33"),
        slug="other-agents",
        name="Other Agents",
    )
    postgres_repository.upsert_topic(other_topic)
    base = replace(
        _search_session(
            UUID("be2f8ad3-b8de-4d65-a433-36096977df31"),
            topic_id=topic_config.id,
            source_paper_id=paper.id,
            source_paper_version_id=source_version.id,
            started_at=now + timedelta(minutes=3),
            objective="Find exact related work.",
        ),
        source_analysis_id=analysis.analysis.id,
        pipeline_execution_id=execution_id,
    )

    with pytest.raises(RepositoryError, match="rejected the search session"):
        postgres_repository.start_search_session(replace(base, topic_id=other_topic.id))

    first = postgres_repository.start_search_session(base)
    assert first.pipeline_execution_id == execution_id
    with pytest.raises(RepositoryError, match="rejected the search session"):
        postgres_repository.start_search_session(
            replace(
                base,
                id=UUID("c2bf12b1-edab-47e1-8c87-074eaa90a9eb"),
                objective="A divergent objective must not fork this execution source.",
            )
        )


def test_restart_ingestion_refuses_persisted_items_and_rolls_back_run_reset(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    completed = IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=postgres_repository,
        clock=lambda: now,
    ).execute(topic_config, logical_date=now.date())
    failed_at = now + timedelta(minutes=1)
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE daily_runs SET status = 'FAILED', completed_at = :failed_at, "
                "error_code = 'PUBLICATION_FAILED', error_detail = 'test interruption' "
                "WHERE id = :run_id"
            ),
            {"run_id": completed.id, "failed_at": failed_at},
        )

    before = postgres_repository.get_run(completed.id)
    assert before is not None
    assert before.run.status is RunStatus.FAILED
    assert len(before.items) == 1
    retry_at = now + timedelta(days=1)

    with pytest.raises(RepositoryError, match="persisted batch cannot be restarted"):
        postgres_repository.restart_ingestion_run(
            completed.id,
            started_at=retry_at,
            cursor_from=retry_at - timedelta(days=1),
            cursor_to=retry_at,
            pipeline_selection_limit=None,
        )

    after = postgres_repository.get_run(completed.id)
    assert after is not None
    assert after.run.status is RunStatus.FAILED
    assert after.run.started_at == now
    assert after.run.completed_at == failed_at
    assert after.run.discovered_count == completed.discovered_count
    assert after.run.normalized_count == completed.normalized_count
    assert after.run.error_code == "PUBLICATION_FAILED"
    assert after.items == before.items


def test_pipeline_selection_replay_is_idempotent_and_conflict_is_atomic(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    second_record = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.05678",
        title="Another LLM Agent",
        pdf_url="https://arxiv.org/pdf/2601.05678v1",
        source_url="https://arxiv.org/abs/2601.05678v1",
    )
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    execution_id = _start_pipeline_execution(
        postgres_repository,
        topic_config,
        logical_date=now.date(),
        started_at=now,
        selection_limit=1,
    )
    run = IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1, second_record)),
        repository=postgres_repository,
        clock=lambda: now,
    ).execute(
        topic_config,
        logical_date=now.date(),
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=1,
        pipeline_execution_id=execution_id,
    )
    normalized = postgres_repository.get_run(run.id)
    assert normalized is not None
    version_by_arxiv_id = {
        item.canonical_arxiv_id: item.item.paper_version_id for item in normalized.items
    }
    selected_version_id = version_by_arxiv_id[arxiv_record_v1.canonical_arxiv_id]
    other_version_id = version_by_arxiv_id[second_record.canonical_arxiv_id]

    postgres_repository.persist_ingestion_selection(
        run.id,
        selected_paper_version_ids=(selected_version_id,),
        updated_at=now + timedelta(seconds=1),
    )
    postgres_repository.persist_ingestion_selection(
        run.id,
        selected_paper_version_ids=(selected_version_id,),
        updated_at=now + timedelta(seconds=2),
    )
    after_replay = postgres_repository.get_run(run.id)
    assert after_replay is not None
    selected_ids = {
        item.item.paper_version_id
        for item in after_replay.items
        if item.item.stage is PaperStage.SELECTED
    }
    assert selected_ids == {selected_version_id}

    with pytest.raises(RepositoryError, match="selection conflicts"):
        postgres_repository.persist_ingestion_selection(
            run.id,
            selected_paper_version_ids=(other_version_id,),
            updated_at=now + timedelta(seconds=3),
        )

    after_conflict = postgres_repository.get_run(run.id)
    assert after_conflict == after_replay


def test_advisory_lock_prevents_concurrent_logical_run(
    postgres_repository: PostgresRepository, topic_config: TopicConfig
) -> None:
    logical_date = date(2026, 1, 10)
    with (
        postgres_repository.daily_run_lock(topic_config.id, logical_date),
        pytest.raises(DuplicateDailyRunError),
        postgres_repository.daily_run_lock(topic_config.id, logical_date),
    ):
        raise AssertionError("second lock must not be acquired")


def test_pipeline_advisory_lock_blocks_a_duplicate_but_allows_nested_child_lock(
    postgres_repository: PostgresRepository, topic_config: TopicConfig
) -> None:
    logical_date = date(2026, 1, 10)
    execution_id = uuid4()

    with postgres_repository.daily_pipeline_lock(execution_id):
        with postgres_repository.daily_run_lock(topic_config.id, logical_date):
            pass
        with (
            pytest.raises(DuplicateDailyRunError, match="another daily pipeline"),
            postgres_repository.daily_pipeline_lock(execution_id),
        ):
            raise AssertionError("duplicate pipeline lock must not be acquired")


def test_analysis_rejects_a_paper_not_owned_by_the_selected_topic(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=postgres_repository,
        clock=lambda: now,
    ).execute(topic_config, logical_date=now.date())
    paper = postgres_repository.list_papers(topic_slug=topic_config.slug, limit=1, offset=0)[0][0]
    other_topic = replace(
        topic_config,
        id=UUID("b317c0f0-7c37-4e75-9af6-1a530486fa33"),
        slug="other-agents",
        name="Other Agents",
    )

    with pytest.raises(ValueError, match="selected analysis identities were not found"):
        AnalyzePapers(
            arxiv=FakeArxiv(),
            parser=None,
            llm=SelectiveAnalysisLLM(UUID(int=0)),
            repository=postgres_repository,
            clock=lambda: now + timedelta(minutes=1),
        ).execute(
            other_topic,
            paper_ids=(paper.id,),
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
            logical_date=now.date(),
        )
    assert postgres_repository.get_analysis_run_for_date(other_topic.id, now.date()) is None


def test_batch_cursor_items_counts_and_completion_roll_back_together(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    started_at = datetime(2026, 1, 10, 5, tzinfo=UTC)
    postgres_repository.upsert_topic(topic_config)
    run = postgres_repository.start_ingestion_run(
        topic_id=topic_config.id,
        logical_date=started_at.date(),
        started_at=started_at,
        cursor_from=started_at - timedelta(days=1),
        cursor_to=started_at,
    )
    postgres_repository.fail_ingestion_run(
        run.id,
        completed_at=started_at + timedelta(seconds=1),
        error_code="TEST_PRECONDITION",
        error_detail="Force the atomic completion update to reject this run.",
    )

    with pytest.raises(RepositoryError, match="no longer running"):
        postgres_repository.persist_arxiv_batch_and_complete(
            topic=topic_config,
            run_id=run.id,
            records=(arxiv_record_v1,),
            watermark=started_at,
            advance_shared_cursor=True,
            persisted_at=started_at + timedelta(seconds=2),
            completed_at=started_at + timedelta(seconds=3),
        )

    papers, total = postgres_repository.list_papers(topic_slug=None, limit=10, offset=0)
    assert papers == ()
    assert total == 0
    assert postgres_repository.get_ingestion_cursor(topic_config.id) is None


def test_restart_analysis_preserves_completed_items_and_resets_failed_items(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    second_record = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.05678",
        title="Another LLM Agent",
        pdf_url="https://arxiv.org/pdf/2601.05678v1",
        source_url="https://arxiv.org/abs/2601.05678v1",
    )
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1, second_record)),
        repository=postgres_repository,
        clock=lambda: now,
    ).execute(topic_config, logical_date=now.date())
    papers, total = postgres_repository.list_papers(
        topic_slug=topic_config.slug,
        limit=10,
        offset=0,
    )
    assert total == 2
    targets = postgres_repository.get_analysis_targets(
        topic_config.id,
        tuple(paper.id for paper in papers),
    )
    execution_id = _start_pipeline_execution(
        postgres_repository,
        topic_config,
        logical_date=now.date(),
        started_at=now + timedelta(minutes=1),
        selection_limit=2,
    )
    run = postgres_repository.start_analysis_run(
        topic_id=topic_config.id,
        logical_date=now.date(),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        started_at=now + timedelta(minutes=1),
        targets=targets,
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=2,
        pipeline_execution_id=execution_id,
    )
    completed_version_id = targets[0].version.id
    failed_version_id = targets[1].version.id
    interrupted_at = now + timedelta(minutes=2)
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE run_items SET stage = 'EVIDENCE_EXTRACTED', status = 'COMPLETED', "
                "updated_at = :updated_at WHERE run_id = :run_id "
                "AND paper_version_id = :paper_version_id"
            ),
            {
                "run_id": run.id,
                "paper_version_id": completed_version_id,
                "updated_at": interrupted_at,
            },
        )
        connection.execute(
            text(
                "UPDATE run_items SET stage = 'ANALYZED', status = 'FAILED', "
                "failed_stage = 'EVIDENCE_EXTRACTED', error_code = 'EVIDENCE_GROUNDING_INVALID', "
                "retryable = false, error_detail = 'test interruption', updated_at = :updated_at "
                "WHERE run_id = :run_id AND paper_version_id = :paper_version_id"
            ),
            {
                "run_id": run.id,
                "paper_version_id": failed_version_id,
                "updated_at": interrupted_at,
            },
        )
        connection.execute(
            text(
                "UPDATE daily_runs SET status = 'FAILED', completed_at = :completed_at, "
                "completed_count = 1, failed_count = 1, error_code = 'DEPENDENCY_UNAVAILABLE', "
                "error_detail = 'test interruption' WHERE id = :run_id"
            ),
            {"run_id": run.id, "completed_at": interrupted_at},
        )

    restarted_at = now + timedelta(days=1)
    restarted = postgres_repository.restart_analysis_run(
        run.id,
        targets=targets,
        started_at=restarted_at,
        pipeline_selection_limit=None,
    )
    assert restarted.id == run.id
    assert restarted.status is RunStatus.RUNNING
    assert restarted.pipeline_selection_limit == 2
    assert restarted.completed_count == 1
    assert restarted.failed_count == 0
    detail = postgres_repository.get_run(run.id)
    assert detail is not None
    item_by_version = {item.item.paper_version_id: item.item for item in detail.items}
    completed_item = item_by_version[completed_version_id]
    assert completed_item.stage is PaperStage.EVIDENCE_EXTRACTED
    assert completed_item.status is RunItemStatus.COMPLETED
    assert completed_item.updated_at == interrupted_at
    retry_item = item_by_version[failed_version_id]
    assert retry_item.stage is PaperStage.SELECTED
    assert retry_item.status is RunItemStatus.IN_PROGRESS
    assert retry_item.failed_stage is None
    assert retry_item.error_code is None
    assert retry_item.retryable is None
    assert retry_item.error_detail is None
    assert retry_item.updated_at == restarted_at


def test_historical_analysis_honors_non_current_version_and_bulk_lookup(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    first_time = datetime(2026, 1, 10, 5, tzinfo=UTC)
    IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=postgres_repository,
        clock=lambda: first_time,
    ).execute(topic_config, logical_date=first_time.date())
    revised = replace(
        arxiv_record_v1,
        version=2,
        title="A Reliable LLM Agent, Revised",
        updated_at=arxiv_record_v1.updated_at + timedelta(days=1),
        pdf_url="https://arxiv.org/pdf/2601.01234v2",
        source_url="https://arxiv.org/abs/2601.01234v2",
    )
    second_time = first_time + timedelta(days=1)
    IngestArxiv(
        arxiv=FakeArxiv((revised,)),
        repository=postgres_repository,
        clock=lambda: second_time,
    ).execute(topic_config, logical_date=second_time.date())
    paper = postgres_repository.list_papers(
        topic_slug=topic_config.slug,
        limit=1,
        offset=0,
    )[0][0]
    detail = postgres_repository.get_paper(paper.id)
    assert detail is not None
    version_by_number = {version.version: version for version in detail.versions}
    non_current = version_by_number[1]
    current = version_by_number[2]
    assert paper.current_version == current.version == 2
    llm = SelectiveAnalysisLLM(UUID(int=0))
    logical_date = second_time.date() + timedelta(days=1)

    run = AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=llm,
        repository=postgres_repository,
        clock=lambda: second_time + timedelta(days=1),
    ).execute(
        topic_config,
        paper_version_ids=(non_current.id,),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=logical_date,
        run_operation=RunOperation.HISTORICAL_ANALYSIS,
    )

    assert run.operation is RunOperation.HISTORICAL_ANALYSIS
    assert run.status is RunStatus.COMPLETE
    assert llm.requests[0].paper_version_id == non_current.id
    assert llm.requests[0].arxiv_version == 1
    queried = postgres_repository.get_analysis_run_for_date(
        topic_config.id,
        logical_date,
        operation=RunOperation.HISTORICAL_ANALYSIS,
    )
    assert queried == run
    assert (
        postgres_repository.get_analysis_run_for_date(
            topic_config.id,
            logical_date,
        )
        is None
    )
    finalized = postgres_repository.get_run(run.id)
    assert finalized is not None
    assert finalized.report is not None
    assert finalized.report.run_id == run.id
    requested_versions = (current.id, non_current.id)
    assert postgres_repository.get_analyzed_paper_version_ids(
        requested_versions,
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
    ) == frozenset({non_current.id})
    assert (
        postgres_repository.get_analyzed_paper_version_ids(
            requested_versions,
            analysis_scope=AnalysisScope.FULL_TEXT,
        )
        == frozenset()
    )


def test_analysis_evidence_partial_report_and_exact_version_ownership_are_persisted(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    second_record = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.05678",
        title="Another LLM Agent",
        pdf_url="https://arxiv.org/pdf/2601.05678v1",
        source_url="https://arxiv.org/abs/2601.05678v1",
    )
    ingestion_time = datetime(2026, 1, 10, 5, tzinfo=UTC)
    IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1, second_record)),
        repository=postgres_repository,
        clock=lambda: ingestion_time,
    ).execute(topic_config, logical_date=ingestion_time.date())
    papers, total = postgres_repository.list_papers(
        topic_slug=topic_config.slug, limit=10, offset=0
    )
    assert total == 2
    paper_by_arxiv = {paper.canonical_arxiv_id: paper for paper in papers}
    successful_paper = paper_by_arxiv[arxiv_record_v1.canonical_arxiv_id]
    failing_paper = paper_by_arxiv[second_record.canonical_arxiv_id]

    analysis_time = ingestion_time + timedelta(minutes=1)
    run = AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=SelectiveAnalysisLLM(failing_paper.id),
        repository=postgres_repository,
        clock=lambda: analysis_time,
    ).execute(
        topic_config,
        paper_ids=(successful_paper.id, failing_paper.id),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=analysis_time.date(),
    )
    assert run.status is RunStatus.PARTIAL
    assert run.analysis_scope is AnalysisScope.ABSTRACT_ONLY
    assert run.selected_count == 2
    assert run.completed_count == 1
    assert run.failed_count == 1

    successful_analysis = postgres_repository.get_paper_analysis(
        successful_paper.id,
        paper_version_id=None,
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
    )
    assert successful_analysis is not None
    assert (
        successful_analysis.analysis.paper_version_id
        == successful_analysis.claims[0].paper_version_id
    )
    assert successful_analysis.evidence[0].supported_claim_ids == (
        successful_analysis.claims[0].id,
    )
    assert (
        postgres_repository.get_paper_analysis(
            failing_paper.id,
            paper_version_id=None,
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        )
        is None
    )
    assert (
        postgres_repository.list_paper_evidence(
            failing_paper.id,
            analysis_id=UUID(int=0),
            paper_version_id=None,
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        )
        is None
    )

    latest = postgres_repository.get_latest_run(topic_slug=topic_config.slug)
    assert latest is not None
    assert latest.run.id == run.id
    assert latest.report is not None
    assert latest.report.status is RunStatus.PARTIAL
    assert len(latest.report.failures) == 1
    assert latest.report.failures[0].paper_id == failing_paper.id
    assert latest.report.failures[0].failed_stage.value == "ANALYZED"
    assert latest.report.failures[0].error_code == "LLM_OUTPUT_INVALID"

    client = TestClient(create_app(postgres_repository))
    analysis_response = client.get(f"/api/v1/papers/{successful_paper.id}/analysis")
    assert analysis_response.status_code == 404
    assert analysis_response.json()["detail"]["code"] == "PAPER_NOT_FOUND"
    evidence_response = client.get(
        f"/api/v1/papers/{successful_paper.id}/evidence"
        f"?analysis_id={successful_analysis.analysis.id}"
    )
    assert evidence_response.status_code == 404
    assert evidence_response.json()["detail"]["code"] == "PAPER_NOT_FOUND"


def test_full_text_analysis_persists_exact_parser_provenance_and_reuses_canonical_parse(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=postgres_repository,
        clock=lambda: now,
    ).execute(topic_config, logical_date=now.date())
    paper = postgres_repository.list_papers(topic_slug=topic_config.slug, limit=1, offset=0)[0][0]
    canonical_text = (
        "Canonical persisted full-text evidence for the tool-using language model agent."
    )
    first_llm = SelectiveAnalysisLLM(paper.id)

    first_run = AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=StaticParser(canonical_text, call_count=2, duration_ms=321),
        llm=first_llm,
        repository=postgres_repository,
        clock=lambda: now + timedelta(minutes=1),
    ).execute(
        topic_config,
        paper_ids=(paper.id,),
        analysis_scope=AnalysisScope.FULL_TEXT,
        logical_date=now.date(),
    )

    assert first_run.status is RunStatus.PARTIAL
    assert (
        postgres_repository.get_paper_analysis(
            paper.id,
            paper_version_id=None,
            analysis_scope=AnalysisScope.FULL_TEXT,
        )
        is None
    )
    parsed_paper_id = first_llm.requests[0].parsed_paper_id
    assert parsed_paper_id is not None
    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT call_count, duration_ms FROM parsed_papers WHERE id = :parsed_id"),
            {"parsed_id": parsed_paper_id},
        ).one() == (2, 321)

    second_llm = SelectiveAnalysisLLM(UUID(int=0))
    second_run = AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=StaticParser(
            "A different retry parse must not replace the durable canonical passages.",
            call_count=1,
            duration_ms=12,
        ),
        llm=second_llm,
        repository=postgres_repository,
        clock=lambda: now + timedelta(days=1),
    ).execute(
        topic_config,
        paper_ids=(paper.id,),
        analysis_scope=AnalysisScope.FULL_TEXT,
        logical_date=now.date() + timedelta(days=1),
    )

    assert second_run.status is RunStatus.COMPLETE
    assert second_llm.requests[0].passages[0].text == canonical_text
    detail = postgres_repository.get_paper_analysis(
        paper.id,
        paper_version_id=None,
        analysis_scope=AnalysisScope.FULL_TEXT,
    )
    assert detail is not None
    assert detail.analysis.parsed_paper_id == parsed_paper_id
    assert detail.parser_name == "grobid"
    assert detail.parser_version == "0.9.0"
    response = TestClient(create_app(postgres_repository)).get(
        f"/api/v1/papers/{paper.id}/analysis?scope=FULL_TEXT"
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PAPER_NOT_FOUND"


def test_database_upgrades_from_m1_revision_to_current_head(
    postgres_engine: Engine,
    postgres_repository: PostgresRepository,
) -> None:
    del postgres_repository  # The fixture clears post-M1 rows before destructive migration tests.
    config = Config(str(Path("alembic.ini").resolve()))
    command.downgrade(config, "0001_m1_ingestion")
    try:
        with postgres_engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("0001_m1_ingestion")
        command.upgrade(config, "head")
        with postgres_engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("0006_topic_reprocessing")
    finally:
        command.upgrade(config, "head")


def test_database_upgrades_from_m2_revision_to_current_head(
    postgres_engine: Engine,
    postgres_repository: PostgresRepository,
) -> None:
    del postgres_repository  # The fixture clears M3 rows before the destructive downgrade test.
    config = Config(str(Path("alembic.ini").resolve()))
    command.downgrade(config, "0002_m2_structured_analysis")
    try:
        with postgres_engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("0002_m2_structured_analysis")
        command.upgrade(config, "head")
        with postgres_engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("0006_topic_reprocessing")
    finally:
        command.upgrade(config, "head")


def test_database_upgrades_from_m3_and_backfills_analysis_reports(
    postgres_engine: Engine,
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
) -> None:
    postgres_repository.upsert_topic(topic_config)
    config = Config(str(Path("alembic.ini").resolve()))
    command.downgrade(config, "0003_m3_pasa_semantic_scholar")
    run_id = UUID("253ee2e9-b72d-4ee7-89a8-e193b2577072")
    report_id = UUID("63f7dd65-2dd6-4845-a4a0-e89a4a4be5b4")
    logical_date = date(2026, 1, 10)
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO daily_runs ("
                    "id, topic_id, logical_date, operation, analysis_scope, status, "
                    "started_at, completed_at, cursor_from, cursor_to, discovered_count, "
                    "normalized_count, selected_count, completed_count, failed_count, "
                    "schema_version) VALUES ("
                    ":id, :topic_id, :logical_date, 'STRUCTURED_ANALYSIS', 'FULL_TEXT', "
                    "'PARTIAL', :now, :now, NULL, NULL, 0, 0, 3, 2, 1, 1)"
                ),
                {
                    "id": run_id,
                    "topic_id": topic_config.id,
                    "logical_date": logical_date,
                    "now": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO reports ("
                    "id, run_id, topic_id, logical_date, status, title, summary, source, "
                    "generated_at, schema_version, created_at) VALUES ("
                    ":id, :run_id, :topic_id, :logical_date, 'PARTIAL', "
                    "'Legacy analysis report', 'Preserved M2 report', "
                    "'structured_analysis', :now, 1, :now)"
                ),
                {
                    "id": report_id,
                    "run_id": run_id,
                    "topic_id": topic_config.id,
                    "logical_date": logical_date,
                    "now": now,
                },
            )

        command.upgrade(config, "head")
        with postgres_engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0006_topic_reprocessing"
            )
            assert connection.execute(
                text(
                    "SELECT report_type, period_start, period_end, retrieved_count, "
                    "selected_count, processed_count, completed_count, failed_count, "
                    "narrative_mode, verification_status FROM reports WHERE id = :id"
                ),
                {"id": report_id},
            ).one() == (
                "ANALYSIS",
                logical_date,
                logical_date,
                3,
                3,
                3,
                2,
                1,
                "STRUCTURED_ONLY",
                "UNVERIFIED",
            )
            assert connection.scalar(text("SELECT to_regclass('graph_entities')")) == (
                "graph_entities"
            )
            assert connection.scalar(text("SELECT to_regclass('trend_snapshots')")) == (
                "trend_snapshots"
            )
            assert connection.scalar(text("SELECT to_regclass('lineage_snapshots')")) == (
                "lineage_snapshots"
            )
            assert connection.scalar(text("SELECT to_regclass('report_sections')")) == (
                "report_sections"
            )
    finally:
        command.upgrade(config, "head")


def test_database_upgrades_populated_m4_to_m5_pipeline_provenance(
    postgres_engine: Engine,
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
) -> None:
    postgres_repository.upsert_topic(topic_config)
    config = Config(str(Path("alembic.ini").resolve()))
    command.downgrade(config, "0004_m4_graph_trends_reports")
    run_id = UUID("505e6559-801c-4e46-b578-89635bff8bb4")
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO daily_runs ("
                    "id, topic_id, logical_date, operation, analysis_scope, status, "
                    "started_at, completed_at, cursor_from, cursor_to, discovered_count, "
                    "normalized_count, selected_count, completed_count, failed_count, "
                    "schema_version) VALUES ("
                    ":id, :topic_id, :logical_date, 'ARXIV_INGESTION', NULL, 'COMPLETE', "
                    ":now, :now, :now, :now, 0, 0, 0, 0, 0, 1)"
                ),
                {
                    "id": run_id,
                    "topic_id": topic_config.id,
                    "logical_date": now.date(),
                    "now": now,
                },
            )

        command.upgrade(config, "head")
        with postgres_engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("0006_topic_reprocessing")
            assert connection.execute(
                text(
                    "SELECT pipeline_execution_mode, pipeline_selection_limit "
                    "FROM daily_runs WHERE id = :id"
                ),
                {"id": run_id},
            ).one() == ("STANDALONE", None)
    finally:
        command.upgrade(config, "head")


def test_m5_downgrade_refuses_persisted_pipeline_provenance_without_guard(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    execution_id = _start_pipeline_execution(
        postgres_repository,
        topic_config,
        logical_date=now.date(),
        started_at=now,
        selection_limit=1,
    )
    assert postgres_repository.get_pipeline_execution(execution_id) is not None
    assert postgres_repository.list_runs(topic_slug=None, limit=10, offset=0)[1] == 0

    config = Config(str(Path("alembic.ini").resolve()))
    with pytest.raises(RuntimeError, match="M5 downgrade refused"):
        command.downgrade(config, "0004_m4_graph_trends_reports")

    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0006_topic_reprocessing"
        )


def test_m5_downgrade_refuses_standalone_comparison_target_decisions(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=postgres_repository,
        clock=lambda: now,
    ).execute(topic_config, logical_date=now.date())
    paper = postgres_repository.list_papers(
        topic_slug=topic_config.slug,
        limit=1,
        offset=0,
    )[0][0]
    AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=SelectiveAnalysisLLM(UUID(int=0)),
        repository=postgres_repository,
        clock=lambda: now + timedelta(minutes=1),
    ).execute(
        topic_config,
        paper_ids=(paper.id,),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=now.date(),
    )
    detail = postgres_repository.get_paper(paper.id)
    assert detail is not None
    analysis = postgres_repository.get_paper_analysis(
        paper.id,
        paper_version_id=detail.versions[-1].id,
    )
    assert analysis is not None
    session = replace(
        _search_session(
            UUID("227bf491-bdd4-4f6e-9304-0dc98d8f0e42"),
            topic_id=topic_config.id,
            source_paper_id=paper.id,
            source_paper_version_id=detail.versions[-1].id,
            started_at=now + timedelta(minutes=2),
            objective="Persist a standalone comparison target.",
        ),
        source_analysis_id=analysis.analysis.id,
    )
    postgres_repository.start_search_session(session)
    target_record = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.09999",
        title="Historical LLM Agent",
        pdf_url="https://arxiv.org/pdf/2601.09999v1",
        source_url="https://arxiv.org/abs/2601.09999v1",
    )
    external = _external_stub(target_record, semantic_scholar_id="f" * 40)
    candidate, discovery = _pending_local_candidate(
        session.id,
        external,
        created_at=now + timedelta(minutes=2),
    )
    postgres_repository.persist_local_search_candidates(
        session.id,
        papers=(external,),
        candidates=(candidate,),
        discoveries=(discovery,),
    )
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE search_candidates SET comparison_target_decision = :decision, "
                "comparison_target_reason = :reason WHERE id = :candidate_id"
            ),
            {
                "decision": ComparisonTargetDecision.TARGET.value,
                "reason": "Persisted standalone comparison target.",
                "candidate_id": candidate.id,
            },
        )

    config = Config(str(Path("alembic.ini").resolve()))
    with pytest.raises(RuntimeError, match="M5 downgrade refused"):
        command.downgrade(config, "0004_m4_graph_trends_reports")

    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0006_topic_reprocessing"
        )


def test_m2_downgrade_refuses_existing_analysis_without_explicit_data_loss_guard(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=postgres_repository,
        clock=lambda: now,
    ).execute(topic_config, logical_date=now.date())
    paper = postgres_repository.list_papers(topic_slug=topic_config.slug, limit=1, offset=0)[0][0]
    AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=SelectiveAnalysisLLM(UUID(int=0)),
        repository=postgres_repository,
        clock=lambda: now + timedelta(minutes=1),
    ).execute(
        topic_config,
        paper_ids=(paper.id,),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=now.date(),
    )

    config = Config(str(Path("alembic.ini").resolve()))
    with pytest.raises(RuntimeError, match="M2 downgrade refused"):
        command.downgrade(config, "0001_m1_ingestion")
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0006_topic_reprocessing"
        )


def test_m3_downgrade_refuses_existing_historical_data_without_explicit_guard(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
) -> None:
    postgres_repository.upsert_topic(topic_config)
    postgres_repository.start_historical_backfill(
        HistoricalBackfillRun(
            id=UUID("d31f9413-eb8e-486d-88b2-18275187133d"),
            topic_id=topic_config.id,
            window_from=date(2025, 7, 10),
            window_to=date(2026, 1, 10),
            query_plan=("LLM agent",),
            max_results_per_query=500,
            overall_timeout_seconds=3600.0,
            embedding_model_identifier="allenai/specter2_base",
            embedding_model_revision="base-revision",
            embedding_tokenizer_identifier="allenai/specter2_base",
            embedding_tokenizer_revision="tokenizer-revision",
            embedding_dimension=768,
            embedding_preprocessing_contract=(
                "title + tokenizer separator + abstract; cls; max_length=512"
            ),
            embedding_model_provenance="huggingface:allenai/specter2_base@base-revision",
            embedding_source="specter2_base_title_abstract_cls",
            status=BackfillStatus.RUNNING,
            next_query_index=0,
            discovered_count=0,
            persisted_count=0,
            representative_count=0,
            started_at=datetime(2026, 1, 10, 5, tzinfo=UTC),
            completed_at=None,
            error_code=None,
            error_detail=None,
            schema_version=1,
            created_at=datetime(2026, 1, 10, 5, tzinfo=UTC),
        )
    )

    config = Config(str(Path("alembic.ini").resolve()))
    with pytest.raises(RuntimeError, match="M3 downgrade refused"):
        command.downgrade(config, "0002_m2_structured_analysis")
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0006_topic_reprocessing"
        )


def test_m4_downgrade_refuses_existing_graph_data_without_explicit_guard(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
) -> None:
    postgres_repository.upsert_topic(topic_config)
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO graph_entities ("
                "id, topic_id, entity_type, paper_id, canonical_label, normalized_key, "
                "display_label, aliases, provenance, source, schema_version, created_at, "
                "updated_at) VALUES ("
                ":id, :topic_id, 'METHOD', NULL, 'Tool use', 'tool use', 'Tool use', "
                "ARRAY['Tool use'], 'DETERMINISTICALLY_DERIVED', "
                "'persisted_structured_data', 1, :now, :now)"
            ),
            {
                "id": UUID("d614ee67-edc8-43f3-bd0d-55fd01331fb7"),
                "topic_id": topic_config.id,
                "now": now,
            },
        )

    config = Config(str(Path("alembic.ini").resolve()))
    with pytest.raises(RuntimeError, match="M4 downgrade refused"):
        command.downgrade(config, "0003_m3_pasa_semantic_scholar")
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0006_topic_reprocessing"
        )
