"""Explicit-scope structured analysis pipeline for selected arXiv papers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import NoReturn
from uuid import UUID

from paper_harness.application.ingest_arxiv import SCHEDULE_TIME_ZONE
from paper_harness.application.read_models import AnalysisTarget
from paper_harness.domain.analysis import (
    AnalysisBundle,
    AnalysisClaim,
    AnalysisPassage,
    AnalysisRequest,
    AnalysisScope,
    Evidence,
    GeneratedAnalysis,
    PaperAnalysis,
    ParsedPaper,
    VerificationStatus,
)
from paper_harness.domain.errors import DomainInvariantError, DuplicateDailyRunError
from paper_harness.domain.identity import (
    stable_analysis_id,
    stable_claim_id,
    stable_evidence_id,
)
from paper_harness.domain.models import DailyRun, PaperStage, TopicConfig
from paper_harness.ports.arxiv import ArxivPort, ArxivPortError, ArxivUnavailableError
from paper_harness.ports.llm import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMPort,
    LLMPortError,
)
from paper_harness.ports.pdf_parser import (
    PdfParserAuthenticationError,
    PdfParserConfigurationError,
    PdfParseRequest,
    PdfParserPort,
    PdfParserPortError,
)
from paper_harness.ports.repository import (
    RepositoryError,
    RepositoryPort,
)


class EvidenceGroundingError(RuntimeError):
    error_code = "EVIDENCE_GROUNDING_INVALID"
    retryable = False


class AnalyzePapers:
    def __init__(
        self,
        *,
        arxiv: ArxivPort,
        parser: PdfParserPort | None,
        llm: LLMPort,
        repository: RepositoryPort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._arxiv = arxiv
        self._parser = parser
        self._llm = llm
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        topic: TopicConfig,
        *,
        paper_ids: tuple[UUID, ...],
        analysis_scope: AnalysisScope,
        logical_date: date | None = None,
    ) -> DailyRun:
        if not paper_ids:
            raise ValueError("structured analysis requires at least one selected paper")
        if len(set(paper_ids)) != len(paper_ids):
            raise ValueError("selected paper IDs must be unique")
        if len(paper_ids) > topic.representative_full_text_count:
            raise ValueError("selected papers exceed the topic analysis bound")
        if analysis_scope is AnalysisScope.FULL_TEXT and self._parser is None:
            raise ValueError("full-text analysis requires the configured GROBID parser")

        started_at = self._aware_now()
        run_date = logical_date or started_at.astimezone(SCHEDULE_TIME_ZONE).date()
        with self._repository.daily_run_lock(topic.id, run_date):
            self._repository.upsert_topic(topic)
            if self._repository.get_analysis_run_for_date(topic.id, run_date) is not None:
                raise DuplicateDailyRunError(
                    f"structured analysis already exists for topic {topic.slug!r} on {run_date}"
                )
            targets = self._repository.get_analysis_targets(topic.id, paper_ids)
            _validate_targets(paper_ids, targets)
            run = self._repository.start_analysis_run(
                topic_id=topic.id,
                logical_date=run_date,
                analysis_scope=analysis_scope,
                started_at=started_at,
                targets=targets,
            )

            for target in targets:
                try:
                    self._analyze_target(
                        run_id=run.id,
                        target=target,
                        analysis_scope=analysis_scope,
                    )
                except (
                    LLMAuthenticationError,
                    LLMConfigurationError,
                    PdfParserAuthenticationError,
                    PdfParserConfigurationError,
                    ArxivUnavailableError,
                ) as error:
                    if isinstance(error, ArxivUnavailableError):
                        failed_stage = PaperStage.PDF_DOWNLOADED
                    elif isinstance(
                        error,
                        (PdfParserAuthenticationError, PdfParserConfigurationError),
                    ):
                        failed_stage = PaperStage.PARSED
                    else:
                        failed_stage = PaperStage.ANALYZED
                    self._record_item_failure(
                        run_id=run.id,
                        target=target,
                        failed_stage=failed_stage,
                        error=error,
                    )
                    self._repository.fail_analysis_run(
                        run.id,
                        completed_at=self._aware_now(),
                        failed_stage=failed_stage,
                        error_code=error.error_code,
                        retryable=error.retryable,
                        error_detail=_concise_detail(error),
                    )
                    raise

            try:
                return self._repository.finalize_analysis_run(
                    run.id,
                    completed_at=self._aware_now(),
                )
            except RepositoryError as error:
                try:
                    self._repository.fail_analysis_run(
                        run.id,
                        completed_at=self._aware_now(),
                        failed_stage=PaperStage.PUBLISHED,
                        error_code="PUBLICATION_FAILED",
                        retryable=False,
                        error_detail="Analysis report publication transaction failed.",
                    )
                except RepositoryError as transition_error:
                    raise transition_error from error
                raise

    def _analyze_target(
        self,
        *,
        run_id: UUID,
        target: AnalysisTarget,
        analysis_scope: AnalysisScope,
    ) -> None:
        parsed: ParsedPaper | None = None
        expected_stage = PaperStage.SELECTED
        if analysis_scope is AnalysisScope.FULL_TEXT:
            try:
                pdf = self._arxiv.download_pdf(
                    canonical_arxiv_id=target.version.canonical_arxiv_id,
                    version=target.version.version,
                    pdf_url=target.version.pdf_url,
                )
            except ArxivUnavailableError:
                raise
            except ArxivPortError as error:
                self._record_item_failure(
                    run_id=run_id,
                    target=target,
                    failed_stage=PaperStage.PDF_DOWNLOADED,
                    error=error,
                )
                return
            try:
                self._repository.advance_analysis_item(
                    run_id=run_id,
                    paper_version_id=target.version.id,
                    expected_stage=PaperStage.SELECTED,
                    next_stage=PaperStage.PDF_DOWNLOADED,
                    updated_at=self._aware_now(),
                )
            except RepositoryError as error:
                self._abort_repository_failure(
                    run_id=run_id,
                    target=target,
                    failed_stage=PaperStage.PDF_DOWNLOADED,
                    error=error,
                )
            expected_stage = PaperStage.PDF_DOWNLOADED
            try:
                if self._parser is None:
                    raise AssertionError("full-text parser was validated before the run")
                parsed = self._parser.parse(
                    PdfParseRequest(
                        paper_id=target.paper.id,
                        paper_version_id=target.version.id,
                        canonical_arxiv_id=target.version.canonical_arxiv_id,
                        arxiv_version=target.version.version,
                        content=pdf.content,
                    )
                )
                parsed = self._repository.persist_parsed_paper(
                    run_id=run_id,
                    parsed_paper=parsed,
                    expected_stage=expected_stage,
                    updated_at=self._aware_now(),
                )
            except (PdfParserAuthenticationError, PdfParserConfigurationError):
                raise
            except RepositoryError as error:
                self._abort_repository_failure(
                    run_id=run_id,
                    target=target,
                    failed_stage=PaperStage.PARSED,
                    error=error,
                )
            except (PdfParserPortError, DomainInvariantError) as error:
                self._record_item_failure(
                    run_id=run_id,
                    target=target,
                    failed_stage=PaperStage.PARSED,
                    error=error,
                )
                return
            expected_stage = PaperStage.PARSED

        request = _analysis_request(target, scope=analysis_scope, parsed=parsed)
        try:
            generated = self._llm.analyze(request)
        except (LLMAuthenticationError, LLMConfigurationError):
            raise
        except LLMPortError as error:
            self._record_item_failure(
                run_id=run_id,
                target=target,
                failed_stage=PaperStage.ANALYZED,
                error=error,
            )
            return

        try:
            bundle = build_analysis_bundle(
                request,
                generated,
                created_at=self._aware_now(),
            )
            self._repository.persist_analysis_bundle(
                run_id=run_id,
                bundle=bundle,
                expected_stage=expected_stage,
                updated_at=self._aware_now(),
            )
        except RepositoryError as error:
            self._abort_repository_failure(
                run_id=run_id,
                target=target,
                failed_stage=PaperStage.EVIDENCE_EXTRACTED,
                error=error,
            )
        except (EvidenceGroundingError, DomainInvariantError) as error:
            self._record_item_failure(
                run_id=run_id,
                target=target,
                failed_stage=PaperStage.EVIDENCE_EXTRACTED,
                error=error,
            )

    def _abort_repository_failure(
        self,
        *,
        run_id: UUID,
        target: AnalysisTarget,
        failed_stage: PaperStage,
        error: RepositoryError,
    ) -> NoReturn:
        self._record_item_failure(
            run_id=run_id,
            target=target,
            failed_stage=failed_stage,
            error=error,
        )
        detail = _concise_detail(error)
        try:
            self._repository.fail_analysis_run(
                run_id,
                completed_at=self._aware_now(),
                failed_stage=failed_stage,
                error_code=error.error_code,
                retryable=error.retryable,
                error_detail=detail,
            )
        except RepositoryError as transition_error:
            raise transition_error from error
        raise error

    def _record_item_failure(
        self,
        *,
        run_id: UUID,
        target: AnalysisTarget,
        failed_stage: PaperStage,
        error: Exception,
    ) -> None:
        try:
            self._repository.fail_analysis_item(
                run_id=run_id,
                paper_version_id=target.version.id,
                failed_stage=failed_stage,
                error_code=str(getattr(error, "error_code", "DOMAIN_INVARIANT_VIOLATION")),
                retryable=bool(getattr(error, "retryable", False)),
                error_detail=_concise_detail(error),
                updated_at=self._aware_now(),
            )
        except RepositoryError as item_write_error:
            self._abort_after_item_write_failure(
                run_id=run_id,
                failed_stage=failed_stage,
                error=item_write_error,
            )

    def _abort_after_item_write_failure(
        self,
        *,
        run_id: UUID,
        failed_stage: PaperStage,
        error: RepositoryError,
    ) -> NoReturn:
        try:
            self._repository.fail_analysis_run(
                run_id,
                completed_at=self._aware_now(),
                failed_stage=failed_stage,
                error_code=error.error_code,
                retryable=error.retryable,
                error_detail=_concise_detail(error),
            )
        except RepositoryError as transition_error:
            raise transition_error from error
        raise error

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("analysis clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def _validate_targets(requested_ids: tuple[UUID, ...], targets: tuple[AnalysisTarget, ...]) -> None:
    returned_ids = tuple(target.paper.id for target in targets)
    if returned_ids != requested_ids:
        missing = [str(paper_id) for paper_id in requested_ids if paper_id not in returned_ids]
        if missing:
            raise ValueError(f"selected papers were not found: {', '.join(missing)}")
        raise ValueError("analysis targets were returned out of requested order")
    if any(target.version.paper_id != target.paper.id for target in targets):
        raise DomainInvariantError("analysis target version ownership is invalid")
    if any(target.version.version != target.paper.current_version for target in targets):
        raise DomainInvariantError("analysis target must use the paper's explicit current version")


def _analysis_request(
    target: AnalysisTarget,
    *,
    scope: AnalysisScope,
    parsed: ParsedPaper | None,
) -> AnalysisRequest:
    if scope is AnalysisScope.ABSTRACT_ONLY:
        if parsed is not None:
            raise DomainInvariantError("abstract-only analysis cannot use a parsed full text")
        passages = (
            AnalysisPassage(
                id="abstract",
                section="Abstract",
                text=target.version.abstract,
            ),
        )
    else:
        if parsed is None:
            raise DomainInvariantError("full-text analysis requires a parsed paper")
        if parsed.paper_id != target.paper.id or parsed.paper_version_id != target.version.id:
            raise DomainInvariantError("parsed paper does not match the analysis target")
        passages = tuple(
            AnalysisPassage(
                id=passage.source_id,
                section=section.title,
                text=passage.text,
                coordinates=passage.coordinates,
            )
            for section in parsed.sections
            for passage in section.passages
        )
    return AnalysisRequest(
        paper_id=target.paper.id,
        paper_version_id=target.version.id,
        canonical_arxiv_id=target.version.canonical_arxiv_id,
        arxiv_version=target.version.version,
        title=target.version.title,
        scope=scope,
        passages=passages,
        parsed_paper_id=None if parsed is None else parsed.id,
    )


def build_analysis_bundle(
    request: AnalysisRequest,
    generated: GeneratedAnalysis,
    *,
    created_at: datetime,
) -> AnalysisBundle:
    passage_by_id = {passage.id: passage for passage in request.passages}
    if len(passage_by_id) != len(request.passages):
        raise EvidenceGroundingError("analysis source passage IDs are not unique")
    for item in generated.evidence:
        passage = passage_by_id.get(item.passage_id)
        if passage is None:
            raise EvidenceGroundingError("model evidence references an unknown source passage")
        if item.excerpt not in passage.text:
            raise EvidenceGroundingError("model evidence excerpt is not grounded in its passage")

    analysis_id = stable_analysis_id(
        request.paper_version_id,
        request.scope.value,
        request.parsed_paper_id,
        generated.provider,
        generated.configured_model,
        generated.model_version,
        generated.prompt_version,
    )
    claim_ids = {claim.key: stable_claim_id(analysis_id, claim.key) for claim in generated.claims}
    analysis = PaperAnalysis(
        id=analysis_id,
        paper_id=request.paper_id,
        paper_version_id=request.paper_version_id,
        parsed_paper_id=request.parsed_paper_id,
        analysis_scope=request.scope,
        summary=generated.summary,
        research_problem=generated.research_problem,
        method_summary=generated.method_summary,
        key_contributions=generated.key_contributions,
        limitations=generated.limitations,
        provider=generated.provider,
        configured_model=generated.configured_model,
        model_version=generated.model_version,
        prompt_version=generated.prompt_version,
        generated_at=generated.generated_at,
        source="deepseek_chat_completions",
        verification_status=VerificationStatus.UNVERIFIED,
        usage=generated.usage,
        schema_version=1,
        created_at=created_at,
    )
    claims = tuple(
        AnalysisClaim(
            id=claim_ids[claim.key],
            analysis_id=analysis_id,
            paper_id=request.paper_id,
            paper_version_id=request.paper_version_id,
            key=claim.key,
            claim_type=claim.claim_type,
            text=claim.text,
            provider=generated.provider,
            model_version=generated.model_version,
            prompt_version=generated.prompt_version,
            generated_at=generated.generated_at,
            source="deepseek_chat_completions",
            verification_status=VerificationStatus.UNVERIFIED,
            schema_version=1,
            created_at=created_at,
        )
        for claim in generated.claims
    )
    evidence = tuple(
        Evidence(
            id=stable_evidence_id(analysis_id, item.key),
            analysis_id=analysis_id,
            paper_id=request.paper_id,
            paper_version_id=request.paper_version_id,
            key=item.key,
            section=passage_by_id[item.passage_id].section,
            passage_id=item.passage_id,
            coordinates=passage_by_id[item.passage_id].coordinates,
            excerpt=item.excerpt,
            evidence_type=item.evidence_type,
            supported_claim_ids=tuple(claim_ids[key] for key in item.claim_keys),
            extraction_source=(
                "grobid_tei" if request.scope is AnalysisScope.FULL_TEXT else "arxiv_abstract"
            ),
            provider=generated.provider,
            model_version=generated.model_version,
            prompt_version=generated.prompt_version,
            generated_at=generated.generated_at,
            verification_status=VerificationStatus.UNVERIFIED,
            schema_version=1,
            created_at=created_at,
        )
        for item in generated.evidence
    )
    return AnalysisBundle(analysis=analysis, claims=claims, evidence=evidence)


def _concise_detail(error: Exception) -> str:
    detail = " ".join(str(error).split())
    return (detail or type(error).__name__)[:1000]
