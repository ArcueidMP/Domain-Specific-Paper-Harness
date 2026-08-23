"""Explicit-scope structured analysis pipeline for selected arXiv papers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
    ClaimType,
    Evidence,
    GeneratedAnalysis,
    GeneratedClaim,
    GeneratedEvidence,
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
from paper_harness.domain.models import (
    DailyRun,
    PaperStage,
    PipelineExecutionMode,
    RunItemStatus,
    RunOperation,
    RunStatus,
    TopicConfig,
)
from paper_harness.ports.arxiv import ArxivPort, ArxivPortError
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


class AnalysisResumeError(ValueError):
    error_code = "ANALYSIS_RESUME_CONFLICT"
    retryable = False


@dataclass(frozen=True, slots=True)
class AnalysisReuseContract:
    provider: str
    configured_model: str
    prompt_version: str
    parser_name: str | None = None
    parser_version: str | None = None

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (self.provider, self.configured_model, self.prompt_version)
        ):
            raise ValueError("analysis reuse model provenance must be complete")
        if (self.parser_name is None) != (self.parser_version is None):
            raise ValueError("analysis reuse parser provenance must be complete")


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
        paper_ids: tuple[UUID, ...] = (),
        paper_version_ids: tuple[UUID, ...] = (),
        analysis_scope: AnalysisScope,
        logical_date: date | None = None,
        pipeline_execution_mode: PipelineExecutionMode = PipelineExecutionMode.STANDALONE,
        pipeline_selection_limit: int | None = None,
        pipeline_execution_id: UUID | None = None,
        run_operation: RunOperation = RunOperation.STRUCTURED_ANALYSIS,
        resume_existing: bool = False,
        reuse_contract: AnalysisReuseContract | None = None,
    ) -> DailyRun:
        if paper_ids and paper_version_ids:
            raise ValueError("structured analysis accepts one selected identity form")
        if run_operation not in (
            RunOperation.STRUCTURED_ANALYSIS,
            RunOperation.HISTORICAL_ANALYSIS,
        ):
            raise ValueError("analysis run operation is unsupported")
        if run_operation is RunOperation.HISTORICAL_ANALYSIS and not paper_version_ids:
            raise ValueError("historical analysis requires exact paper-version IDs")
        selected_ids = paper_ids or paper_version_ids
        if not selected_ids and pipeline_execution_mode is PipelineExecutionMode.STANDALONE:
            raise ValueError("standalone structured analysis requires a selected paper")
        if len(set(selected_ids)) != len(selected_ids):
            raise ValueError("selected analysis identities must be unique")
        if len(selected_ids) > topic.representative_full_text_count:
            raise ValueError("selected papers exceed the topic analysis bound")
        if analysis_scope is AnalysisScope.FULL_TEXT and self._parser is None:
            raise ValueError("full-text analysis requires the configured GROBID parser")

        started_at = self._aware_now()
        run_date = logical_date or started_at.astimezone(SCHEDULE_TIME_ZONE).date()
        with self._repository.daily_run_lock(topic.id, run_date):
            self._repository.upsert_topic(topic)
            targets = (
                self._repository.get_analysis_targets(topic.id, paper_ids)
                if paper_ids
                else self._repository.get_analysis_targets_by_version_ids(
                    topic.id,
                    paper_version_ids,
                )
            )
            targets = _normalize_targets(
                selected_ids,
                targets,
                match_versions=bool(paper_version_ids),
            )
            existing = self._repository.get_analysis_run_for_date(
                topic.id,
                run_date,
                pipeline_execution_id=pipeline_execution_id,
                operation=run_operation,
            )
            if existing is None:
                run = self._repository.start_analysis_run(
                    topic_id=topic.id,
                    logical_date=run_date,
                    analysis_scope=analysis_scope,
                    started_at=started_at,
                    targets=targets,
                    pipeline_execution_mode=pipeline_execution_mode,
                    pipeline_selection_limit=pipeline_selection_limit,
                    pipeline_execution_id=pipeline_execution_id,
                    operation=run_operation,
                )
            else:
                if not resume_existing:
                    raise DuplicateDailyRunError(
                        f"{run_operation.value} already exists for topic "
                        f"{topic.slug!r} on {run_date}"
                    )
                _require_matching_analysis_run(
                    existing,
                    operation=run_operation,
                    analysis_scope=analysis_scope,
                    pipeline_execution_mode=pipeline_execution_mode,
                    pipeline_execution_id=pipeline_execution_id,
                )
                if existing.status in (RunStatus.COMPLETE, RunStatus.PARTIAL):
                    return existing
                if existing.status not in (
                    RunStatus.RUNNING,
                    RunStatus.FAILED,
                ):
                    raise AnalysisResumeError(
                        f"analysis run in {existing.status.value} state cannot resume"
                    )
                run = self._repository.restart_analysis_run(
                    existing.id,
                    targets=targets,
                    started_at=started_at,
                    pipeline_selection_limit=pipeline_selection_limit,
                )

            detail = self._repository.get_run(run.id)
            if detail is None:
                raise RepositoryError("active analysis run could not be reloaded")
            completed_versions = {
                item.item.paper_version_id
                for item in detail.items
                if item.item.status is RunItemStatus.COMPLETED
            }

            for target in targets:
                if target.version.id in completed_versions:
                    continue
                if reuse_contract is not None and self._repository.attach_existing_analysis_to_run(
                    run_id=run.id,
                    paper_version_id=target.version.id,
                    analysis_scope=analysis_scope,
                    provider=reuse_contract.provider,
                    configured_model=reuse_contract.configured_model,
                    prompt_version=reuse_contract.prompt_version,
                    parser_name=reuse_contract.parser_name,
                    parser_version=reuse_contract.parser_version,
                    updated_at=self._aware_now(),
                ):
                    continue
                try:
                    self._analyze_target(
                        run_id=run.id,
                        target=target,
                        analysis_scope=analysis_scope,
                        revision_id=(
                            pipeline_execution_id
                            if pipeline_execution_mode is PipelineExecutionMode.REPROCESS
                            else None
                        ),
                    )
                except (
                    LLMAuthenticationError,
                    LLMConfigurationError,
                    PdfParserAuthenticationError,
                    PdfParserConfigurationError,
                ) as error:
                    if isinstance(
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
        revision_id: UUID | None,
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
                revision_id=revision_id,
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


def _normalize_targets(
    requested_ids: tuple[UUID, ...],
    targets: tuple[AnalysisTarget, ...],
    *,
    match_versions: bool,
) -> tuple[AnalysisTarget, ...]:
    by_id: dict[UUID, AnalysisTarget] = {}
    for target in targets:
        identity = target.version.id if match_versions else target.paper.id
        existing = by_id.get(identity)
        if existing is not None and existing != target:
            raise DomainInvariantError("analysis target identity has conflicting records")
        by_id.setdefault(identity, target)
    missing = [str(identity) for identity in requested_ids if identity not in by_id]
    if missing:
        raise ValueError(f"selected analysis identities were not found: {', '.join(missing)}")
    normalized = tuple(by_id[identity] for identity in requested_ids)
    if any(target.version.paper_id != target.paper.id for target in normalized):
        raise DomainInvariantError("analysis target version ownership is invalid")
    if not match_versions and any(
        target.version.version != target.paper.current_version for target in normalized
    ):
        raise DomainInvariantError("analysis target must use the paper's explicit current version")
    return normalized


def _require_matching_analysis_run(
    run: DailyRun,
    *,
    operation: RunOperation,
    analysis_scope: AnalysisScope,
    pipeline_execution_mode: PipelineExecutionMode,
    pipeline_execution_id: UUID | None,
) -> None:
    if (
        run.operation is not operation
        or run.analysis_scope is not analysis_scope
        or run.pipeline_execution_mode is not pipeline_execution_mode
        or run.pipeline_execution_id != pipeline_execution_id
    ):
        raise AnalysisResumeError(
            "persisted analysis provenance does not match the requested pipeline"
        )


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
    revision_id: UUID | None = None,
) -> AnalysisBundle:
    passage_by_id = {passage.id: passage for passage in request.passages}
    if len(passage_by_id) != len(request.passages):
        raise EvidenceGroundingError("analysis source passage IDs are not unique")

    grounded_sources: list[tuple[GeneratedEvidence, AnalysisPassage]] = []
    for item in generated.evidence:
        if any(passage_id not in passage_by_id for passage_id in item.passage_ids):
            continue
        grounded_sources.extend(
            (item, passage_by_id[passage_id]) for passage_id in item.passage_ids
        )

    grounded_claim_keys = {
        claim_key for item, _passage in grounded_sources for claim_key in item.claim_keys
    }
    grounded_generated_claims = tuple(
        claim for claim in generated.claims if claim.key in grounded_claim_keys
    )
    if not grounded_sources or not any(
        _is_major_claim(claim) for claim in grounded_generated_claims
    ):
        raise EvidenceGroundingError("analysis has no claim grounded in a known source passage")

    analysis_id = stable_analysis_id(
        request.paper_version_id,
        request.scope.value,
        request.parsed_paper_id,
        generated.provider,
        generated.configured_model,
        generated.model_version,
        generated.prompt_version,
        revision_id,
    )
    claim_ids = {
        claim.key: stable_claim_id(analysis_id, claim.key) for claim in grounded_generated_claims
    }
    analysis = PaperAnalysis(
        id=analysis_id,
        paper_id=request.paper_id,
        paper_version_id=request.paper_version_id,
        parsed_paper_id=request.parsed_paper_id,
        analysis_scope=request.scope,
        summary=_claim_summary(grounded_generated_claims),
        research_problem=_claim_text_or_absence(
            grounded_generated_claims,
            ClaimType.RESEARCH_PROBLEM,
            "No grounded research-problem claim was identified.",
        ),
        method_summary=_claim_text_or_absence(
            grounded_generated_claims,
            ClaimType.METHOD,
            "No grounded method claim was identified.",
        ),
        key_contributions=tuple(
            claim.text
            for claim in grounded_generated_claims
            if claim.claim_type is ClaimType.CONTRIBUTION
        ),
        limitations=tuple(
            claim.text
            for claim in grounded_generated_claims
            if claim.claim_type is ClaimType.LIMITATION
        ),
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
        revision_id=revision_id,
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
        for claim in grounded_generated_claims
    )
    evidence_values: list[Evidence] = []
    for item, passage in grounded_sources:
        evidence_id = stable_evidence_id(analysis_id, f"{item.key}:{passage.id}")
        evidence_values.append(
            Evidence(
                id=evidence_id,
                analysis_id=analysis_id,
                paper_id=request.paper_id,
                paper_version_id=request.paper_version_id,
                key=f"grounded_{evidence_id.hex}",
                section=passage.section,
                passage_id=passage.id,
                coordinates=passage.coordinates,
                excerpt=_source_excerpt(passage.text),
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
        )
    evidence = tuple(evidence_values)
    return AnalysisBundle(analysis=analysis, claims=claims, evidence=evidence)


def _claim_summary(claims: tuple[GeneratedClaim, ...]) -> str:
    return _bounded_text(" ".join(claim.text for claim in claims), maximum=8000)


def _claim_text_or_absence(
    claims: tuple[GeneratedClaim, ...],
    claim_type: ClaimType,
    absence_text: str,
) -> str:
    values = tuple(claim.text for claim in claims if claim.claim_type is claim_type)
    return absence_text if not values else _bounded_text(" ".join(values), maximum=4000)


def _is_major_claim(claim: GeneratedClaim) -> bool:
    return claim.claim_type is not ClaimType.LIMITATION


def _bounded_text(value: str, *, maximum: int) -> str:
    return value if len(value) <= maximum else value[:maximum].rstrip()


def _source_excerpt(value: str) -> str:
    """Return only deterministic text copied from a validated source passage."""

    return value[:600]


def _concise_detail(error: Exception) -> str:
    detail = " ".join(str(error).split())
    return (detail or type(error).__name__)[:1000]
