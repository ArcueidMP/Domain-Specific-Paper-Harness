from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from tests.fakes import FakeArxiv, FakeRepository

from paper_harness.application.analyze_papers import AnalyzePapers
from paper_harness.application.read_models import AnalysisTarget
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
    ) -> None:
        self.failing_paper_id = failing_paper_id
        self.authentication_failure = authentication_failure
        self.ungrounded = ungrounded
        self.calls: list[AnalysisRequest] = []

    def analyze(self, request: AnalysisRequest) -> GeneratedAnalysis:
        self.calls.append(request)
        if self.authentication_failure:
            raise LLMAuthenticationError("DeepSeek authentication failed with HTTP 401")
        if request.paper_id == self.failing_paper_id:
            raise LLMOutputError("DeepSeek JSON output failed schema validation")
        excerpt = "not present in the source" if self.ungrounded else request.passages[0].text[:30]
        return GeneratedAnalysis(
            provider="deepseek",
            configured_model="deepseek-v4-flash",
            model_version="DeepSeek-V4-Flash-2026-04-24",
            prompt_version="m2-analysis-v1",
            generated_at=datetime(2026, 1, 10, 5, 2, tzinfo=UTC),
            summary="The paper evaluates agent reliability.",
            research_problem="Tool-using agents require reliable operation.",
            method_summary="The authors evaluate a tool-using agent.",
            key_contributions=("A reliability evaluation.",),
            limitations=("Evaluation scope is bounded.",),
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
                    passage_id=request.passages[0].id,
                    excerpt=excerpt,
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
            passage_id="abstract",
            excerpt="grounded excerpt",
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
