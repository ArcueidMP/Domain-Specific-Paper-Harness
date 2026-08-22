from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from tests.fakes import FakeArxiv, FakeRepository

from paper_harness.application.analyze_papers import (
    AnalysisResumeError,
    AnalysisReuseContract,
    AnalyzePapers,
    EvidenceGroundingError,
    build_analysis_bundle,
)
from paper_harness.application.read_models import AnalysisTarget
from paper_harness.domain.analysis import (
    AnalysisPassage,
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
from paper_harness.domain.historical import (
    CandidateSelectionRequest,
    ComparisonRequest,
    CrawlerPlanRequest,
    GeneratedCandidateSelection,
    GeneratedComparison,
    GeneratedCrawlerPlan,
)
from paper_harness.domain.identity import (
    stable_analysis_id,
    stable_paper_id,
    stable_paper_version_id,
    stable_parsed_paper_id,
    stable_parsed_passage_id,
    stable_parsed_section_id,
)
from paper_harness.domain.models import (
    DailyRun,
    Paper,
    PaperStage,
    PaperVersion,
    PipelineExecutionMode,
    RunItemStatus,
    RunOperation,
    RunStatus,
    TopicConfig,
)
from paper_harness.domain.reports import GeneratedReportNarrative, ReportNarrativeRequest
from paper_harness.ports.arxiv import ArxivPaperRecord, ArxivUnavailableError
from paper_harness.ports.llm import LLMAuthenticationError, LLMOutputError
from paper_harness.ports.pdf_parser import (
    PdfParserAuthenticationError,
    PdfParseRequest,
    PdfParserOutputError,
    PdfParserPortError,
)
from paper_harness.ports.repository import (
    RepositoryError,
    RepositoryIntegrityError,
    RepositoryUnavailableError,
)

PIPELINE_EXECUTION_ID = UUID("a5f52f0e-2d5d-5be6-94aa-a6c48087724d")


class FakeParser:
    def __init__(self, error: PdfParserPortError | None = None) -> None:
        self.error = error
        self.calls: list[PdfParseRequest] = []

    def parse(self, request: PdfParseRequest) -> ParsedPaper:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        parsed_id = stable_parsed_paper_id(request.paper_version_id, "grobid", "0.9.0")
        passage = ParsedPassage(
            id=stable_parsed_passage_id(parsed_id, "s1"),
            source_id="s1",
            section_index=0,
            passage_index=0,
            text="The tool-using agent improves reliability by 12 percent.",
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
        )


class FakeLLM:
    def __init__(
        self,
        *,
        failing_paper_id: UUID | None = None,
        authentication_failure: bool = False,
        ungrounded: bool = False,
        ungrounded_paper_id: UUID | None = None,
    ) -> None:
        self.failing_paper_id = failing_paper_id
        self.authentication_failure = authentication_failure
        self.ungrounded = ungrounded
        self.ungrounded_paper_id = ungrounded_paper_id
        self.calls: list[AnalysisRequest] = []

    def analyze(self, request: AnalysisRequest) -> GeneratedAnalysis:
        self.calls.append(request)
        if self.authentication_failure:
            raise LLMAuthenticationError("DeepSeek authentication failed with HTTP 401")
        if request.paper_id == self.failing_paper_id:
            raise LLMOutputError("DeepSeek JSON output failed schema validation")
        passage_id = (
            "missing-passage"
            if self.ungrounded or request.paper_id == self.ungrounded_paper_id
            else request.passages[0].id
        )
        return GeneratedAnalysis(
            provider="deepseek",
            configured_model="deepseek-v4-flash",
            model_version="DeepSeek-V4-Flash-2026-04-24",
            prompt_version="m2-analysis-v1",
            generated_at=datetime(2026, 1, 10, 5, 2, tzinfo=UTC),
            claims=(
                GeneratedClaim(
                    key="claim_1",
                    claim_type=ClaimType.RESULT,
                    text="The agent improves reliability.",
                ),
            ),
            evidence=(
                GeneratedEvidence(
                    key="evidence_1",
                    claim_keys=("claim_1",),
                    passage_ids=(passage_id,),
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
        del request
        raise AssertionError("prior-work selection is outside the M2 analysis test")

    def plan_scholarly_search(
        self,
        request: CrawlerPlanRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> GeneratedCrawlerPlan:
        del request, timeout_seconds
        raise AssertionError("crawler planning is outside the M2 analysis test")

    def compare_papers(self, request: ComparisonRequest) -> GeneratedComparison:
        del request
        raise AssertionError("paper comparison is outside the M2 analysis test")

    def generate_report(self, request: ReportNarrativeRequest) -> GeneratedReportNarrative:
        del request
        raise AssertionError("report generation is outside the M2 analysis test")


def _grounding_request(*passages: AnalysisPassage) -> AnalysisRequest:
    return AnalysisRequest(
        paper_id=UUID("9a75db1f-afc6-45bb-a506-93c802ebd0ae"),
        paper_version_id=UUID("1d939b7e-853d-49b0-a5f0-e977c167cbf1"),
        canonical_arxiv_id="2601.01234",
        arxiv_version=1,
        title="Grounded Agent Evidence",
        scope=AnalysisScope.ABSTRACT_ONLY,
        passages=passages,
    )


def _generated_analysis(
    *,
    claims: tuple[GeneratedClaim, ...],
    evidence: tuple[GeneratedEvidence, ...],
) -> GeneratedAnalysis:
    return GeneratedAnalysis(
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-04-24",
        prompt_version="m2-analysis-v1",
        generated_at=datetime(2026, 1, 10, 5, 2, tzinfo=UTC),
        claims=claims,
        evidence=evidence,
        usage=ModelUsage(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            call_count=1,
            duration_ms=500,
            estimated_cost_usd=None,
        ),
    )


class FailingFailureWriteRepository(FakeRepository):
    def __init__(
        self,
        *,
        item_write_error: RepositoryError,
        run_transition_error: RepositoryError | None = None,
    ) -> None:
        super().__init__()
        self.item_write_error = item_write_error
        self.run_transition_error = run_transition_error
        self.fail_analysis_run_attempts = 0

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
        del (
            run_id,
            paper_version_id,
            failed_stage,
            error_code,
            retryable,
            error_detail,
            updated_at,
        )
        raise self.item_write_error

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
        self.fail_analysis_run_attempts += 1
        if self.run_transition_error is not None:
            raise self.run_transition_error
        return super().fail_analysis_run(
            run_id,
            completed_at=completed_at,
            failed_stage=failed_stage,
            error_code=error_code,
            retryable=retryable,
            error_detail=error_detail,
        )


def _target(record: ArxivPaperRecord) -> AnalysisTarget:
    created_at = datetime(2026, 1, 10, 5, tzinfo=UTC)
    paper_id = stable_paper_id(record.canonical_arxiv_id)
    paper = Paper(
        id=paper_id,
        canonical_arxiv_id=record.canonical_arxiv_id,
        title=record.title,
        abstract=record.abstract,
        current_version=record.version,
        first_submitted_at=record.submitted_at,
        latest_updated_at=record.updated_at,
        primary_category=record.primary_category,
        categories=record.categories,
        authors=record.authors,
        pdf_url=record.pdf_url,
        schema_version=1,
        created_at=created_at,
    )
    version = PaperVersion(
        id=stable_paper_version_id(record.canonical_arxiv_id, record.version),
        paper_id=paper_id,
        canonical_arxiv_id=record.canonical_arxiv_id,
        version=record.version,
        title=record.title,
        abstract=record.abstract,
        submitted_at=record.submitted_at,
        updated_at=record.updated_at,
        primary_category=record.primary_category,
        categories=record.categories,
        authors=record.authors,
        pdf_url=record.pdf_url,
        source_url=record.source_url,
        schema_version=1,
        created_at=created_at,
    )
    return AnalysisTarget(paper=paper, version=version)


def _versioned_targets(
    record: ArxivPaperRecord,
) -> tuple[AnalysisTarget, AnalysisTarget]:
    first = _target(record)
    revised_record = replace(
        record,
        version=2,
        title=f"{record.title}, Revised",
        updated_at=record.updated_at + timedelta(days=1),
        pdf_url=f"https://arxiv.org/pdf/{record.canonical_arxiv_id}v2",
        source_url=f"https://arxiv.org/abs/{record.canonical_arxiv_id}v2",
    )
    current = _target(revised_record)
    return replace(first, paper=current.paper), current


def test_abstract_only_scope_is_explicit_and_never_calls_pdf_or_parser(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    repository = FakeRepository()
    repository.analysis_targets = (_target(arxiv_record_v1),)
    arxiv = FakeArxiv()
    llm = FakeLLM()
    run = AnalyzePapers(
        arxiv=arxiv,
        parser=None,
        llm=llm,
        repository=repository,
        clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
    ).execute(
        topic_config,
        paper_ids=(repository.analysis_targets[0].paper.id,),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=date(2026, 1, 10),
    )

    assert run.status is RunStatus.COMPLETE
    assert run.completed_count == 1
    assert arxiv.pdf_calls == []
    assert llm.calls[0].scope is AnalysisScope.ABSTRACT_ONLY
    assert llm.calls[0].parsed_paper_id is None
    assert repository.analysis_detail is not None
    assert repository.analysis_detail.analysis.analysis_scope is AnalysisScope.ABSTRACT_ONLY
    assert repository.analysis_detail.analysis.parsed_paper_id is None
    assert repository.analysis_detail.parser_name is None
    assert repository.analysis_detail.parser_version is None


def test_exact_analysis_reuse_skips_a_new_model_call_only_for_matching_provenance(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    repository = FakeRepository()
    target = _target(arxiv_record_v1)
    repository.analysis_targets = (target,)
    AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=FakeLLM(),
        repository=repository,
        clock=lambda: datetime(2026, 1, 9, 5, tzinfo=UTC),
    ).execute(
        topic_config,
        paper_version_ids=(target.version.id,),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=date(2026, 1, 9),
    )
    repository.run = None
    repository.items = ()
    replay_llm = FakeLLM()

    run = AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=replay_llm,
        repository=repository,
        clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
    ).execute(
        topic_config,
        paper_version_ids=(target.version.id,),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=date(2026, 1, 10),
        reuse_contract=AnalysisReuseContract(
            provider="deepseek",
            configured_model="deepseek-v4-flash",
            prompt_version="m2-analysis-v1",
        ),
    )

    assert run.status is RunStatus.COMPLETE
    assert replay_llm.calls == []


@pytest.mark.parametrize(
    ("configured_model", "prompt_version"),
    [
        ("deepseek-v4-flash-next", "m2-analysis-v1"),
        ("deepseek-v4-flash", "m2-analysis-v2"),
    ],
)
def test_analysis_reuse_mismatch_forces_a_fresh_model_call(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
    configured_model: str,
    prompt_version: str,
) -> None:
    repository = FakeRepository()
    target = _target(arxiv_record_v1)
    repository.analysis_targets = (target,)
    AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=FakeLLM(),
        repository=repository,
        clock=lambda: datetime(2026, 1, 9, 5, tzinfo=UTC),
    ).execute(
        topic_config,
        paper_version_ids=(target.version.id,),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=date(2026, 1, 9),
    )
    repository.run = None
    repository.items = ()
    fresh_llm = FakeLLM()

    AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=fresh_llm,
        repository=repository,
        clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
    ).execute(
        topic_config,
        paper_version_ids=(target.version.id,),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=date(2026, 1, 10),
        reuse_contract=AnalysisReuseContract(
            provider="deepseek",
            configured_model=configured_model,
            prompt_version=prompt_version,
        ),
    )

    assert len(fresh_llm.calls) == 1


@pytest.mark.parametrize(
    ("existing_status", "retry_item_status"),
    [
        (RunStatus.RUNNING, RunItemStatus.IN_PROGRESS),
        (RunStatus.FAILED, RunItemStatus.FAILED),
    ],
)
def test_analysis_resume_preserves_completed_versions_and_retries_nonterminal_versions(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
    existing_status: RunStatus,
    retry_item_status: RunItemStatus,
) -> None:
    second_record = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.05678",
        title="Another LLM Agent",
        pdf_url="https://arxiv.org/pdf/2601.05678v1",
        source_url="https://arxiv.org/abs/2601.05678v1",
    )
    targets = (_target(arxiv_record_v1), _target(second_record))
    started_at = datetime(2026, 1, 10, 5, tzinfo=UTC)
    retry_at = started_at + timedelta(days=2)
    repository = FakeRepository()
    repository.analysis_targets = targets
    original = repository.start_analysis_run(
        topic_id=topic_config.id,
        logical_date=started_at.date(),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        started_at=started_at,
        targets=targets,
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
    )
    completed_item = replace(
        repository.items[0],
        stage=PaperStage.EVIDENCE_EXTRACTED,
        status=RunItemStatus.COMPLETED,
    )
    retry_item = repository.items[1]
    if retry_item_status is RunItemStatus.FAILED:
        retry_item = replace(
            retry_item,
            status=RunItemStatus.FAILED,
            failed_stage=PaperStage.ANALYZED,
            error_code="LLM_OUTPUT_INVALID",
            retryable=False,
            error_detail="schema validation failed",
        )
    repository.items = (completed_item, retry_item)
    repository.run = replace(
        original,
        status=existing_status,
        completed_at=(
            started_at + timedelta(minutes=1) if existing_status is RunStatus.FAILED else None
        ),
        completed_count=1 if existing_status is RunStatus.FAILED else 0,
        failed_count=1 if existing_status is RunStatus.FAILED else 0,
        error_code="LLM_AUTHENTICATION_FAILED" if existing_status is RunStatus.FAILED else None,
        error_detail="authentication failed" if existing_status is RunStatus.FAILED else None,
    )
    llm = FakeLLM()

    resumed = AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=llm,
        repository=repository,
        clock=lambda: retry_at,
    ).execute(
        topic_config,
        paper_version_ids=tuple(target.version.id for target in targets),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=started_at.date(),
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
        resume_existing=True,
    )

    assert resumed.id == original.id
    assert resumed.status is RunStatus.COMPLETE
    assert resumed.completed_count == 2
    assert [request.paper_version_id for request in llm.calls] == [targets[1].version.id]
    assert all(item.status is RunItemStatus.COMPLETED for item in repository.items)


@pytest.mark.parametrize("existing_status", [RunStatus.COMPLETE, RunStatus.PARTIAL])
def test_analysis_resume_reuses_terminal_complete_or_partial_report_owner(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
    existing_status: RunStatus,
) -> None:
    second_record = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.05678",
        title="Another LLM Agent",
        pdf_url="https://arxiv.org/pdf/2601.05678v1",
        source_url="https://arxiv.org/abs/2601.05678v1",
    )
    targets = (_target(arxiv_record_v1), _target(second_record))
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    repository = FakeRepository()
    repository.analysis_targets = targets
    original = repository.start_analysis_run(
        topic_id=topic_config.id,
        logical_date=now.date(),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        started_at=now,
        targets=targets,
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
    )
    completed = replace(
        repository.items[0],
        stage=PaperStage.EVIDENCE_EXTRACTED,
        status=RunItemStatus.COMPLETED,
    )
    second = replace(
        repository.items[1],
        stage=PaperStage.EVIDENCE_EXTRACTED,
        status=RunItemStatus.COMPLETED,
    )
    if existing_status is RunStatus.PARTIAL:
        second = replace(
            repository.items[1],
            status=RunItemStatus.FAILED,
            failed_stage=PaperStage.ANALYZED,
            error_code="LLM_OUTPUT_INVALID",
            retryable=False,
            error_detail="schema validation failed",
        )
    repository.items = (completed, second)
    repository.run = replace(
        original,
        status=existing_status,
        completed_at=now + timedelta(minutes=1),
        completed_count=2 if existing_status is RunStatus.COMPLETE else 1,
        failed_count=0 if existing_status is RunStatus.COMPLETE else 1,
    )
    llm = FakeLLM()

    reused = AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=llm,
        repository=repository,
        clock=lambda: now + timedelta(hours=1),
    ).execute(
        topic_config,
        paper_version_ids=tuple(target.version.id for target in targets),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=now.date(),
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
        resume_existing=True,
    )

    assert reused == repository.run
    assert reused.id == original.id
    assert reused.status is existing_status
    assert llm.calls == []
    if existing_status is RunStatus.PARTIAL:
        assert repository.items[1].status is RunItemStatus.FAILED
        assert repository.items[1].error_code == "LLM_OUTPUT_INVALID"


@pytest.mark.parametrize(
    ("analysis_scope", "pipeline_execution_mode"),
    [
        (AnalysisScope.FULL_TEXT, PipelineExecutionMode.NORMAL),
        (AnalysisScope.ABSTRACT_ONLY, PipelineExecutionMode.SMOKE),
    ],
)
def test_analysis_resume_rejects_scope_or_mode_mismatch(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
    analysis_scope: AnalysisScope,
    pipeline_execution_mode: PipelineExecutionMode,
) -> None:
    target = _target(arxiv_record_v1)
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    repository = FakeRepository()
    repository.analysis_targets = (target,)
    repository.start_analysis_run(
        topic_id=topic_config.id,
        logical_date=now.date(),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        started_at=now,
        targets=(target,),
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
    )

    with pytest.raises(AnalysisResumeError, match="provenance"):
        AnalyzePapers(
            arxiv=FakeArxiv(),
            parser=FakeParser(),
            llm=FakeLLM(),
            repository=repository,
            clock=lambda: now + timedelta(hours=1),
        ).execute(
            topic_config,
            paper_version_ids=(target.version.id,),
            analysis_scope=analysis_scope,
            logical_date=now.date(),
            pipeline_execution_mode=pipeline_execution_mode,
            pipeline_selection_limit=2,
            pipeline_execution_id=PIPELINE_EXECUTION_ID,
            resume_existing=True,
        )


def test_analysis_resume_rebuilds_current_unpublished_version_set(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    non_current, current = _versioned_targets(arxiv_record_v1)
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    repository = FakeRepository()
    repository.analysis_targets = (non_current, current)
    repository.start_analysis_run(
        topic_id=topic_config.id,
        logical_date=now.date(),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        started_at=now,
        targets=(non_current,),
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=1,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
    )

    llm = FakeLLM()
    resumed = AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=llm,
        repository=repository,
        clock=lambda: now + timedelta(hours=1),
    ).execute(
        topic_config,
        paper_version_ids=(current.version.id,),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=now.date(),
        pipeline_execution_mode=PipelineExecutionMode.NORMAL,
        pipeline_selection_limit=2,
        pipeline_execution_id=PIPELINE_EXECUTION_ID,
        resume_existing=True,
    )

    assert resumed.status is RunStatus.COMPLETE
    assert resumed.pipeline_selection_limit == 2
    assert [request.paper_version_id for request in llm.calls] == [current.version.id]


def test_exact_non_current_paper_version_is_honored_end_to_end(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    non_current, current = _versioned_targets(arxiv_record_v1)
    repository = FakeRepository()
    repository.analysis_targets = (non_current, current)
    arxiv = FakeArxiv()
    llm = FakeLLM()

    run = AnalyzePapers(
        arxiv=arxiv,
        parser=FakeParser(),
        llm=llm,
        repository=repository,
        clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
    ).execute(
        topic_config,
        paper_version_ids=(non_current.version.id,),
        analysis_scope=AnalysisScope.FULL_TEXT,
        logical_date=date(2026, 1, 10),
        run_operation=RunOperation.HISTORICAL_ANALYSIS,
    )

    assert run.operation is RunOperation.HISTORICAL_ANALYSIS
    assert non_current.paper.current_version == current.version.version == 2
    assert arxiv.pdf_calls == [
        (
            non_current.version.canonical_arxiv_id,
            non_current.version.version,
            non_current.version.pdf_url,
        )
    ]
    assert llm.calls[0].paper_version_id == non_current.version.id
    assert llm.calls[0].arxiv_version == 1
    assert repository.analysis_detail is not None
    assert repository.analysis_detail.analysis.paper_version_id == non_current.version.id


def test_full_text_retry_uses_the_canonical_stored_parse_and_exposes_parser_provenance(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    repository = FakeRepository()
    target = _target(arxiv_record_v1)
    repository.analysis_targets = (target,)
    parser = FakeParser()
    canonical = parser.parse(
        PdfParseRequest(
            paper_id=target.paper.id,
            paper_version_id=target.version.id,
            canonical_arxiv_id=target.version.canonical_arxiv_id,
            arxiv_version=target.version.version,
            content=b"%PDF-1.7\ncanonical",
        )
    )
    canonical_passage = replace(
        canonical.sections[0].passages[0],
        text="Canonical stored evidence passage for the tool-using agent.",
    )
    canonical = replace(
        canonical,
        sections=(replace(canonical.sections[0], passages=(canonical_passage,)),),
    )
    repository.parsed_papers[canonical.id] = canonical
    llm = FakeLLM()

    run = AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=FakeParser(),
        llm=llm,
        repository=repository,
        clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
    ).execute(
        topic_config,
        paper_ids=(target.paper.id,),
        analysis_scope=AnalysisScope.FULL_TEXT,
        logical_date=date(2026, 1, 10),
    )

    assert run.status is RunStatus.COMPLETE
    assert llm.calls[0].parsed_paper_id == canonical.id
    assert llm.calls[0].passages[0].text == canonical_passage.text
    assert repository.analysis_detail is not None
    assert repository.analysis_detail.analysis.parsed_paper_id == canonical.id
    assert repository.analysis_detail.parser_name == "grobid"
    assert repository.analysis_detail.parser_version == "0.9.0"


def test_full_text_parser_failure_never_downgrades_or_calls_llm(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    repository = FakeRepository()
    repository.analysis_targets = (_target(arxiv_record_v1),)
    parser = FakeParser(PdfParserOutputError("GROBID returned malformed TEI XML"))
    llm = FakeLLM()
    run = AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=parser,
        llm=llm,
        repository=repository,
        clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
    ).execute(
        topic_config,
        paper_ids=(repository.analysis_targets[0].paper.id,),
        analysis_scope=AnalysisScope.FULL_TEXT,
        logical_date=date(2026, 1, 10),
    )

    assert run.status is RunStatus.FAILED
    assert llm.calls == []
    assert repository.items[0].stage is PaperStage.PDF_DOWNLOADED
    assert repository.items[0].failed_stage is PaperStage.PARSED
    assert repository.items[0].error_code == "PDF_PARSER_OUTPUT_INVALID"


def test_one_success_and_one_model_failure_publish_partial_with_item_error(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    second_record = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.05678",
        title="Another LLM Agent",
        pdf_url="https://arxiv.org/pdf/2601.05678v1",
        source_url="https://arxiv.org/abs/2601.05678v1",
    )
    targets = (_target(arxiv_record_v1), _target(second_record))
    repository = FakeRepository()
    repository.analysis_targets = targets
    llm = FakeLLM(failing_paper_id=targets[1].paper.id)

    run = AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=llm,
        repository=repository,
        clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
    ).execute(
        topic_config,
        paper_ids=tuple(target.paper.id for target in targets),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=date(2026, 1, 10),
    )

    assert run.status is RunStatus.PARTIAL
    assert run.completed_count == 1
    assert run.failed_count == 1
    assert repository.items[1].failed_stage is PaperStage.ANALYZED
    assert repository.items[1].error_code == "LLM_OUTPUT_INVALID"


def test_item_failure_write_error_marks_the_run_failed_and_is_raised(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    item_write_error = RepositoryUnavailableError(
        "PostgreSQL could not persist the analysis item failure"
    )
    repository = FailingFailureWriteRepository(item_write_error=item_write_error)
    repository.analysis_targets = (_target(arxiv_record_v1),)
    llm = FakeLLM(failing_paper_id=repository.analysis_targets[0].paper.id)

    with pytest.raises(RepositoryUnavailableError) as raised:
        AnalyzePapers(
            arxiv=FakeArxiv(),
            parser=None,
            llm=llm,
            repository=repository,
            clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
        ).execute(
            topic_config,
            paper_ids=(repository.analysis_targets[0].paper.id,),
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
            logical_date=date(2026, 1, 10),
        )

    assert raised.value is item_write_error
    assert repository.fail_analysis_run_attempts == 1
    assert repository.run is not None
    assert repository.run.status is RunStatus.FAILED
    assert repository.run.error_code == "REPOSITORY_UNAVAILABLE"


def test_failed_run_transition_is_raised_from_the_item_failure_write_error(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    item_write_error = RepositoryIntegrityError("PostgreSQL rejected the analysis item failure")
    transition_error = RepositoryUnavailableError(
        "PostgreSQL could not persist the failed run transition"
    )
    repository = FailingFailureWriteRepository(
        item_write_error=item_write_error,
        run_transition_error=transition_error,
    )
    repository.analysis_targets = (_target(arxiv_record_v1),)
    llm = FakeLLM(failing_paper_id=repository.analysis_targets[0].paper.id)

    with pytest.raises(RepositoryUnavailableError) as raised:
        AnalyzePapers(
            arxiv=FakeArxiv(),
            parser=None,
            llm=llm,
            repository=repository,
            clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
        ).execute(
            topic_config,
            paper_ids=(repository.analysis_targets[0].paper.id,),
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
            logical_date=date(2026, 1, 10),
        )

    assert raised.value is transition_error
    assert raised.value.__cause__ is item_write_error
    assert repository.fail_analysis_run_attempts == 1
    assert repository.run is not None
    assert repository.run.status is RunStatus.RUNNING


def test_valid_passage_id_copies_unicode_whitespace_and_newlines_from_source() -> None:
    source = "  Agent Ω uses tools.\nThe result is stable across runs.  "
    request = _grounding_request(AnalysisPassage(id="abstract", section="Abstract", text=source))
    generated = _generated_analysis(
        claims=(
            GeneratedClaim(
                key="result",
                claim_type=ClaimType.RESULT,
                text="The result is stable.",
            ),
        ),
        evidence=(
            GeneratedEvidence(
                key="result_evidence",
                claim_keys=("result",),
                passage_ids=("abstract",),
                evidence_type=EvidenceType.SUPPORTS,
                rationale="This rationale need not match any source wording.",
            ),
        ),
    )

    bundle = build_analysis_bundle(request, generated, created_at=generated.generated_at)

    assert bundle.evidence[0].excerpt == source
    assert bundle.evidence[0].passage_id == "abstract"
    assert bundle.analysis.summary == "The result is stable."
    assert "rationale" not in bundle.evidence[0].excerpt


def test_multiple_passage_ids_expand_to_stably_ordered_source_evidence() -> None:
    first = AnalysisPassage(id="method", section="Method", text="The agent plans locally.")
    second = AnalysisPassage(id="result", section="Results", text="It succeeds on 12 tasks.")
    request = _grounding_request(first, second)
    generated = _generated_analysis(
        claims=(
            GeneratedClaim(
                key="method_claim",
                claim_type=ClaimType.METHOD,
                text="The agent uses local planning.",
            ),
        ),
        evidence=(
            GeneratedEvidence(
                key="method_evidence",
                claim_keys=("method_claim",),
                passage_ids=(second.id, first.id),
                evidence_type=EvidenceType.SUPPORTS,
            ),
        ),
    )

    first_bundle = build_analysis_bundle(request, generated, created_at=generated.generated_at)
    second_bundle = build_analysis_bundle(request, generated, created_at=generated.generated_at)

    assert tuple(item.passage_id for item in first_bundle.evidence) == (second.id, first.id)
    assert tuple(item.excerpt for item in first_bundle.evidence) == (second.text, first.text)
    assert first_bundle == second_bundle
    assert all(
        item.supported_claim_ids == (first_bundle.claims[0].id,) for item in first_bundle.evidence
    )


def test_reprocess_revision_gets_fresh_analysis_identity_without_changing_legacy_identity() -> None:
    passage = AnalysisPassage(id="abstract", section="Abstract", text="A grounded method.")
    request = _grounding_request(passage)
    generated = _generated_analysis(
        claims=(
            GeneratedClaim(
                key="method_claim",
                claim_type=ClaimType.METHOD,
                text="The paper presents a grounded method.",
            ),
        ),
        evidence=(
            GeneratedEvidence(
                key="method_evidence",
                claim_keys=("method_claim",),
                passage_ids=(passage.id,),
                evidence_type=EvidenceType.SUPPORTS,
            ),
        ),
    )
    revision_id = UUID("3b301c07-9aa3-4a3d-b13a-e7b3ba4db146")

    legacy = build_analysis_bundle(request, generated, created_at=generated.generated_at)
    revised = build_analysis_bundle(
        request,
        generated,
        created_at=generated.generated_at,
        revision_id=revision_id,
    )

    assert revised.analysis.revision_id == revision_id
    assert revised.analysis.id != legacy.analysis.id
    assert legacy.analysis.revision_id is None


def test_missing_passage_invalidates_its_whole_evidence_and_unsupported_claim() -> None:
    passage = AnalysisPassage(id="abstract", section="Abstract", text="A grounded method.")
    request = _grounding_request(passage)
    unsupported_text = "This unsupported result must never reach a report."
    generated = _generated_analysis(
        claims=(
            GeneratedClaim(
                key="method",
                claim_type=ClaimType.METHOD,
                text="The paper presents a grounded method.",
            ),
            GeneratedClaim(
                key="unsupported",
                claim_type=ClaimType.RESULT,
                text=unsupported_text,
            ),
        ),
        evidence=(
            GeneratedEvidence(
                key="valid",
                claim_keys=("method",),
                passage_ids=(passage.id,),
                evidence_type=EvidenceType.SUPPORTS,
            ),
            GeneratedEvidence(
                key="mixed_invalid",
                claim_keys=("unsupported",),
                passage_ids=(passage.id, "missing"),
                evidence_type=EvidenceType.SUPPORTS,
            ),
        ),
    )

    bundle = build_analysis_bundle(request, generated, created_at=generated.generated_at)

    assert tuple(claim.key for claim in bundle.claims) == ("method",)
    assert tuple(item.passage_id for item in bundle.evidence) == (passage.id,)
    assert unsupported_text not in bundle.analysis.summary
    assert unsupported_text not in bundle.analysis.method_summary
    assert unsupported_text not in bundle.analysis.key_contributions


def test_no_grounded_major_claim_is_an_explicit_grounding_failure() -> None:
    passage = AnalysisPassage(id="abstract", section="Abstract", text="A source passage.")
    request = _grounding_request(passage)
    generated = _generated_analysis(
        claims=(
            GeneratedClaim(
                key="limitation",
                claim_type=ClaimType.LIMITATION,
                text="Only one task is evaluated.",
            ),
        ),
        evidence=(
            GeneratedEvidence(
                key="limitation_evidence",
                claim_keys=("limitation",),
                passage_ids=(passage.id,),
                evidence_type=EvidenceType.QUALIFIES,
            ),
        ),
    )

    with pytest.raises(EvidenceGroundingError, match="no claim grounded"):
        build_analysis_bundle(request, generated, created_at=generated.generated_at)


def test_ungrounded_model_evidence_is_an_explicit_item_failure(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    repository = FakeRepository()
    repository.analysis_targets = (_target(arxiv_record_v1),)
    run = AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=FakeLLM(ungrounded=True),
        repository=repository,
        clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
    ).execute(
        topic_config,
        paper_ids=(repository.analysis_targets[0].paper.id,),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=date(2026, 1, 10),
    )
    assert run.status is RunStatus.FAILED
    assert repository.items[0].failed_stage is PaperStage.EVIDENCE_EXTRACTED
    assert repository.items[0].error_code == "EVIDENCE_GROUNDING_INVALID"


def test_grounding_failure_is_item_level_and_another_paper_completes(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    second_record = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.05678",
        title="Another Grounded LLM Agent",
        pdf_url="https://arxiv.org/pdf/2601.05678v1",
        source_url="https://arxiv.org/abs/2601.05678v1",
    )
    targets = (_target(arxiv_record_v1), _target(second_record))
    repository = FakeRepository()
    repository.analysis_targets = targets
    llm = FakeLLM(ungrounded_paper_id=targets[0].paper.id)

    run = AnalyzePapers(
        arxiv=FakeArxiv(),
        parser=None,
        llm=llm,
        repository=repository,
        clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
    ).execute(
        topic_config,
        paper_ids=tuple(target.paper.id for target in targets),
        analysis_scope=AnalysisScope.ABSTRACT_ONLY,
        logical_date=date(2026, 1, 10),
    )

    assert run.status is RunStatus.PARTIAL
    assert (run.completed_count, run.failed_count) == (1, 1)
    assert len(llm.calls) == 2
    assert repository.items[0].error_code == "EVIDENCE_GROUNDING_INVALID"
    assert repository.items[1].status is RunItemStatus.COMPLETED


def test_authentication_failure_aborts_the_run_immediately(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    second_record = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.05678",
        title="Another LLM Agent",
        pdf_url="https://arxiv.org/pdf/2601.05678v1",
        source_url="https://arxiv.org/abs/2601.05678v1",
    )
    repository = FakeRepository()
    repository.analysis_targets = (_target(arxiv_record_v1), _target(second_record))
    llm = FakeLLM(authentication_failure=True)
    with pytest.raises(LLMAuthenticationError):
        AnalyzePapers(
            arxiv=FakeArxiv(),
            parser=None,
            llm=llm,
            repository=repository,
            clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
        ).execute(
            topic_config,
            paper_ids=tuple(target.paper.id for target in repository.analysis_targets),
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
            logical_date=date(2026, 1, 10),
        )
    assert len(llm.calls) == 1
    assert all(item.status.value == "FAILED" for item in repository.items)
    assert repository.run is not None
    assert repository.run.status is RunStatus.FAILED


def test_fatal_dependency_item_failure_write_error_still_marks_the_run_failed(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    second_record = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.05678",
        title="Another LLM Agent",
        pdf_url="https://arxiv.org/pdf/2601.05678v1",
        source_url="https://arxiv.org/abs/2601.05678v1",
    )
    item_write_error = RepositoryIntegrityError(
        "PostgreSQL rejected the authentication item failure"
    )
    repository = FailingFailureWriteRepository(item_write_error=item_write_error)
    repository.analysis_targets = (_target(arxiv_record_v1), _target(second_record))
    llm = FakeLLM(authentication_failure=True)

    with pytest.raises(RepositoryIntegrityError) as raised:
        AnalyzePapers(
            arxiv=FakeArxiv(),
            parser=None,
            llm=llm,
            repository=repository,
            clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
        ).execute(
            topic_config,
            paper_ids=tuple(target.paper.id for target in repository.analysis_targets),
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
            logical_date=date(2026, 1, 10),
        )

    assert raised.value is item_write_error
    assert len(llm.calls) == 1
    assert repository.fail_analysis_run_attempts == 1
    assert repository.run is not None
    assert repository.run.status is RunStatus.FAILED
    assert repository.run.error_code == "PERSISTENCE_INTEGRITY_FAILED"
    assert all(item.status.value == "FAILED" for item in repository.items)


def test_parser_authentication_failure_aborts_before_the_next_selected_paper(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    second_record = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.05678",
        title="Another LLM Agent",
        pdf_url="https://arxiv.org/pdf/2601.05678v1",
        source_url="https://arxiv.org/abs/2601.05678v1",
    )
    repository = FakeRepository()
    repository.analysis_targets = (_target(arxiv_record_v1), _target(second_record))
    parser = FakeParser(PdfParserAuthenticationError("GROBID identity token was rejected"))
    arxiv = FakeArxiv()

    with pytest.raises(PdfParserAuthenticationError):
        AnalyzePapers(
            arxiv=arxiv,
            parser=parser,
            llm=FakeLLM(),
            repository=repository,
            clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
        ).execute(
            topic_config,
            paper_ids=tuple(target.paper.id for target in repository.analysis_targets),
            analysis_scope=AnalysisScope.FULL_TEXT,
            logical_date=date(2026, 1, 10),
        )

    assert len(arxiv.pdf_calls) == 1
    assert len(parser.calls) == 1
    assert repository.items[0].failed_stage is PaperStage.PARSED
    assert repository.items[0].error_code == "PDF_PARSER_AUTHENTICATION_FAILED"
    assert repository.items[1].status.value == "FAILED"
    assert repository.items[1].error_code == "PDF_PARSER_AUTHENTICATION_FAILED"
    assert repository.run is not None
    assert repository.run.status is RunStatus.FAILED


def test_global_arxiv_pdf_unavailability_aborts_before_the_next_selected_paper(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    second_record = replace(
        arxiv_record_v1,
        canonical_arxiv_id="2601.05678",
        title="Another LLM Agent",
        pdf_url="https://arxiv.org/pdf/2601.05678v1",
        source_url="https://arxiv.org/abs/2601.05678v1",
    )
    repository = FakeRepository()
    repository.analysis_targets = (_target(arxiv_record_v1), _target(second_record))
    arxiv = FakeArxiv(pdf_error=ArxivUnavailableError("arXiv PDF timed out after bounded retries"))

    with pytest.raises(ArxivUnavailableError):
        AnalyzePapers(
            arxiv=arxiv,
            parser=FakeParser(),
            llm=FakeLLM(),
            repository=repository,
            clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
        ).execute(
            topic_config,
            paper_ids=tuple(target.paper.id for target in repository.analysis_targets),
            analysis_scope=AnalysisScope.FULL_TEXT,
            logical_date=date(2026, 1, 10),
        )

    assert len(arxiv.pdf_calls) == 1
    assert all(item.status.value == "FAILED" for item in repository.items)
    assert all(item.failed_stage is PaperStage.PDF_DOWNLOADED for item in repository.items)
    assert repository.run is not None
    assert repository.run.status is RunStatus.FAILED


def test_duplicate_model_claim_references_are_rejected_before_persistence() -> None:
    with pytest.raises(ValueError, match="claim keys must be unique"):
        GeneratedEvidence(
            key="evidence_1",
            claim_keys=("claim_1", "claim_1"),
            passage_ids=("abstract",),
            evidence_type=EvidenceType.SUPPORTS,
        )


def test_analysis_identity_distinguishes_provider_model_revisions() -> None:
    version_id = UUID("8dc68364-70a2-47da-ac5c-4d9af4e3a9d8")
    parsed_id = UUID("bd35a33c-c99a-43c6-a1d6-cd41811ed6a9")
    first = stable_analysis_id(
        version_id,
        AnalysisScope.FULL_TEXT.value,
        parsed_id,
        "deepseek",
        "deepseek-v4-flash",
        "DeepSeek-V4-Flash-2026-04-24",
        "m2-analysis-v1",
    )
    revised = stable_analysis_id(
        version_id,
        AnalysisScope.FULL_TEXT.value,
        parsed_id,
        "deepseek",
        "deepseek-v4-flash",
        "DeepSeek-V4-Flash-2026-08-01",
        "m2-analysis-v1",
    )

    assert first != revised


def test_integrity_failure_is_sanitized_and_persists_a_failed_run(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    repository = FakeRepository()
    repository.analysis_targets = (_target(arxiv_record_v1),)
    repository.analysis_persist_error = RepositoryIntegrityError(
        "PostgreSQL rejected analysis and evidence ownership constraints"
    )

    with pytest.raises(RepositoryIntegrityError) as raised:
        AnalyzePapers(
            arxiv=FakeArxiv(),
            parser=None,
            llm=FakeLLM(),
            repository=repository,
            clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
        ).execute(
            topic_config,
            paper_ids=(repository.analysis_targets[0].paper.id,),
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
            logical_date=date(2026, 1, 10),
        )

    assert "The paper evaluates" not in str(raised.value)
    assert repository.run is not None
    assert repository.run.status is RunStatus.FAILED
    assert repository.run.error_code == "PERSISTENCE_INTEGRITY_FAILED"
    assert repository.items[0].status.value == "FAILED"


@pytest.mark.parametrize(
    "publication_error",
    [
        RepositoryIntegrityError("PostgreSQL rejected report ownership constraints"),
        RepositoryUnavailableError("PostgreSQL analysis publication is unavailable"),
    ],
)
def test_publication_transaction_failure_is_persisted_as_run_failed(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
    publication_error: RepositoryIntegrityError | RepositoryUnavailableError,
) -> None:
    repository = FakeRepository()
    repository.analysis_targets = (_target(arxiv_record_v1),)
    repository.finalize_error = publication_error

    with pytest.raises(type(publication_error)):
        AnalyzePapers(
            arxiv=FakeArxiv(),
            parser=None,
            llm=FakeLLM(),
            repository=repository,
            clock=lambda: datetime(2026, 1, 10, 5, tzinfo=UTC),
        ).execute(
            topic_config,
            paper_ids=(repository.analysis_targets[0].paper.id,),
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
            logical_date=date(2026, 1, 10),
        )

    assert repository.run is not None
    assert repository.run.status is RunStatus.FAILED
    assert repository.run.error_code == "PUBLICATION_FAILED"
