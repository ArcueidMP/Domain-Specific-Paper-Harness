# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from tests.fakes import FakeArxiv

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
)
from paper_harness.domain.models import RunStatus, TopicConfig
from paper_harness.domain.reports import GeneratedReportNarrative, ReportNarrativeRequest
from paper_harness.entrypoints.api import create_app
from paper_harness.ports.arxiv import ArxivPaperRecord
from paper_harness.ports.llm import LLMOutputError
from paper_harness.ports.pdf_parser import PdfParseRequest
from paper_harness.ports.repository import RepositoryError

pytestmark = pytest.mark.integration


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
            summary="The paper evaluates an LLM agent.",
            research_problem="Agent evaluation requires reliable evidence.",
            method_summary="The authors evaluate a tool-using agent.",
            key_contributions=("An evidence-backed evaluation.",),
            limitations=("The abstract provides limited detail.",),
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
                    passage_id=request.passages[0].id,
                    excerpt=request.passages[0].text[:80],
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
    assert api_papers["total"] == 1
    assert api_papers["items"][0]["current_version"] == 2


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

    with pytest.raises(ValueError, match="selected papers were not found"):
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
            persisted_at=started_at + timedelta(seconds=2),
            completed_at=started_at + timedelta(seconds=3),
        )

    papers, total = postgres_repository.list_papers(topic_slug=None, limit=10, offset=0)
    assert papers == ()
    assert total == 0
    assert postgres_repository.get_ingestion_cursor(topic_config.id) is None


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
    assert analysis_response.status_code == 200
    evidence_response = client.get(
        f"/api/v1/papers/{successful_paper.id}/evidence"
        f"?analysis_id={successful_analysis.analysis.id}"
    )
    assert evidence_response.status_code == 200
    assert evidence_response.json()["total"] == 1


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

    assert first_run.status is RunStatus.FAILED
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
    assert response.status_code == 200
    payload = response.json()
    assert payload["parsed_paper_id"] == str(parsed_paper_id)
    assert payload["parser_name"] == "grobid"
    assert payload["parser_version"] == "0.9.0"


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
            ).scalar_one() == ("0004_m4_graph_trends_reports")
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
            ).scalar_one() == ("0004_m4_graph_trends_reports")
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
                == "0004_m4_graph_trends_reports"
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
            "0004_m4_graph_trends_reports"
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
            "0004_m4_graph_trends_reports"
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
            "0004_m4_graph_trends_reports"
        )
