"""Evidence-linked systematic comparison for two analyzed paper versions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from paper_harness.domain.analysis import VerificationStatus
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import (
    COMPARISON_DIMENSION_ORDER,
    CandidateOrigin,
    Comparison,
    ComparisonBundle,
    ComparisonDimension,
    ComparisonRequest,
    ComparisonTargetDecision,
    PaperRelation,
    PaperRelationType,
    RelationProvenance,
    SearchSessionStatus,
    SearchTool,
    SelectionDecision,
)
from paper_harness.domain.identity import (
    stable_comparison_dimension_id,
    stable_comparison_id,
    stable_paper_relation_id,
)
from paper_harness.ports.llm import LLMPort
from paper_harness.ports.repository import RepositoryPort


class ComparisonInputMissingError(RuntimeError):
    error_code = "COMPARISON_INPUT_MISSING"
    retryable = False


class ComparePapers:
    def __init__(
        self,
        *,
        repository: RepositoryPort,
        llm: LLMPort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._llm = llm
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        *,
        search_session_id: UUID,
        source_paper_version_id: UUID,
        target_paper_version_id: UUID,
        target_analysis_id: UUID | None = None,
    ) -> ComparisonBundle:
        session_detail = self._repository.get_search_session(search_session_id)
        if (
            session_detail is None
            or session_detail.session.status is not SearchSessionStatus.COMPLETE
            or session_detail.session.source_paper_version_id != source_paper_version_id
        ):
            raise ComparisonInputMissingError(
                "comparison requires a completed search session for the source paper version"
            )
        selected_candidate = next(
            (
                candidate
                for candidate in session_detail.candidates
                if candidate.local_paper_version_id == target_paper_version_id
                and (
                    candidate.comparison_target_decision is ComparisonTargetDecision.TARGET
                    or (
                        session_detail.session.pipeline_execution_id is None
                        and candidate.comparison_target_decision is None
                        and candidate.decision is SelectionDecision.SELECTED
                    )
                )
            ),
            None,
        )
        if selected_candidate is None:
            raise ComparisonInputMissingError(
                "comparison target must be a bounded local historical target"
            )
        source = self._repository.get_comparison_paper_input(
            source_paper_version_id,
            analysis_id=session_detail.session.source_analysis_id,
        )
        target = self._repository.get_comparison_paper_input(
            target_paper_version_id,
            analysis_id=target_analysis_id,
        )
        if source is None or target is None:
            raise ComparisonInputMissingError(
                "comparison requires persisted structured analysis and evidence for both versions"
            )
        if selected_candidate.local_paper_id != target.paper_id:
            raise ComparisonInputMissingError(
                "selected historical candidate does not own the requested target version"
            )
        if (
            source.paper_id != session_detail.session.source_paper_id
            or source.analysis_scope is not session_detail.session.source_analysis_scope
        ):
            raise ComparisonInputMissingError(
                "search session source analysis provenance does not match its comparison input"
            )
        generated = self._llm.compare_papers(ComparisonRequest(source=source, target=target))
        created_at = self._aware_now()
        comparison_id = stable_comparison_id(
            search_session_id,
            source.paper_version_id,
            source.analysis_id,
            target.paper_version_id,
            target.analysis_id,
            generated.provider,
            generated.configured_model,
            generated.model_version,
            generated.prompt_version,
        )
        dimensions = tuple(
            ComparisonDimension(
                id=stable_comparison_dimension_id(comparison_id, item.name.value),
                comparison_id=comparison_id,
                name=item.name,
                position=COMPARISON_DIMENSION_ORDER.index(item.name),
                source_value=item.source_value,
                target_value=item.target_value,
                assessment=item.assessment,
                source_evidence_ids=item.source_evidence_ids,
                target_evidence_ids=item.target_evidence_ids,
                schema_version=1,
                created_at=created_at,
            )
            for item in generated.dimensions
        )
        comparison = Comparison(
            id=comparison_id,
            search_session_id=search_session_id,
            source_paper_id=source.paper_id,
            source_paper_version_id=source.paper_version_id,
            source_analysis_id=source.analysis_id,
            source_analysis_scope=source.analysis_scope,
            target_paper_id=target.paper_id,
            target_paper_version_id=target.paper_version_id,
            target_analysis_id=target.analysis_id,
            target_analysis_scope=target.analysis_scope,
            comparability_status=generated.comparability_status,
            comparability_reason=generated.comparability_reason,
            summary=generated.summary,
            dimensions=dimensions,
            provider=generated.provider,
            configured_model=generated.configured_model,
            model_version=generated.model_version,
            prompt_version=generated.prompt_version,
            generated_at=generated.generated_at,
            source="deepseek_structured_comparison",
            verification_status=VerificationStatus.UNVERIFIED,
            usage=generated.usage,
            schema_version=1,
            created_at=created_at,
        )
        inferred_relations = tuple(
            PaperRelation(
                id=stable_paper_relation_id(
                    comparison_id,
                    source.paper_version_id,
                    target.paper_version_id,
                    item.relation_type.value,
                    RelationProvenance.LLM_INFERRED.value,
                    generated.model_version,
                    generated.prompt_version,
                ),
                source_paper_id=source.paper_id,
                source_paper_version_id=source.paper_version_id,
                target_paper_id=target.paper_id,
                target_paper_version_id=target.paper_version_id,
                relation_type=item.relation_type,
                provenance=RelationProvenance.LLM_INFERRED,
                evidence_ids=item.evidence_ids,
                justification=item.justification,
                provider=generated.provider,
                model_version=generated.model_version,
                prompt_version=generated.prompt_version,
                confidence=item.confidence,
                verification_status=VerificationStatus.UNVERIFIED,
                generated_at=generated.generated_at,
                schema_version=1,
                created_at=created_at,
            )
            for item in generated.relations
        )
        metadata_relations: tuple[PaperRelation, ...] = ()
        candidate_ids = {candidate.semantic_scholar_id for candidate in session_detail.candidates}
        actions_by_id = {action.id: action for action in session_detail.actions}
        directly_referenced = any(
            discovery.candidate_id == selected_candidate.id
            and discovery.origin is CandidateOrigin.REFERENCES
            and discovery.action_id is not None
            and (action := actions_by_id.get(discovery.action_id)) is not None
            and action.tool is SearchTool.GET_REFERENCES
            and action.target_semantic_scholar_id is not None
            and action.target_semantic_scholar_id not in candidate_ids
            for discovery in session_detail.discoveries
        )
        if directly_referenced:
            metadata_relations = (
                PaperRelation(
                    id=stable_paper_relation_id(
                        comparison_id,
                        source.paper_version_id,
                        target.paper_version_id,
                        PaperRelationType.CITES.value,
                        RelationProvenance.METADATA_EXPLICIT.value,
                        None,
                        None,
                    ),
                    source_paper_id=source.paper_id,
                    source_paper_version_id=source.paper_version_id,
                    target_paper_id=target.paper_id,
                    target_paper_version_id=target.paper_version_id,
                    relation_type=PaperRelationType.CITES,
                    provenance=RelationProvenance.METADATA_EXPLICIT,
                    evidence_ids=(),
                    justification=(
                        "Semantic Scholar reference metadata lists the selected historical "
                        "paper as a direct reference of the source paper."
                    ),
                    provider=None,
                    model_version=None,
                    prompt_version=None,
                    confidence=None,
                    verification_status=VerificationStatus.UNVERIFIED,
                    generated_at=created_at,
                    schema_version=1,
                    created_at=created_at,
                ),
            )
        relations = metadata_relations + inferred_relations
        bundle = ComparisonBundle(comparison=comparison, relations=relations)
        self._repository.persist_comparison_bundle(bundle)
        return bundle

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise DomainInvariantError("comparison clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


__all__ = ["ComparePapers", "ComparisonInputMissingError"]
