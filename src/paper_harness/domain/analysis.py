"""Framework-independent models for structured paper analysis and evidence."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from paper_harness.domain.errors import DomainInvariantError


class AnalysisScope(StrEnum):
    ABSTRACT_ONLY = "ABSTRACT_ONLY"
    FULL_TEXT = "FULL_TEXT"


class VerificationStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    HUMAN_VERIFIED = "HUMAN_VERIFIED"
    REJECTED = "REJECTED"


class ClaimType(StrEnum):
    RESEARCH_PROBLEM = "RESEARCH_PROBLEM"
    METHOD = "METHOD"
    CONTRIBUTION = "CONTRIBUTION"
    RESULT = "RESULT"
    LIMITATION = "LIMITATION"


class EvidenceType(StrEnum):
    SUPPORTS = "SUPPORTS"
    QUALIFIES = "QUALIFIES"
    CONTRADICTS = "CONTRADICTS"


MAX_MODEL_TOKEN_COUNT = 1_000_000
MAX_MODEL_COMPLETION_TOKEN_COUNT = 16_000
MAX_MODEL_DURATION_MS = 1_800_000
MAX_MODEL_CALL_COUNT = 4
MAX_MODEL_ESTIMATED_COST_USD = Decimal("9999999999.99999999")
MAX_PARSER_CALL_COUNT = 4
MAX_PARSER_DURATION_MS = 1_000_000


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainInvariantError(f"{name} must be timezone-aware")


def _require_text(value: str, name: str, *, maximum: int | None = None) -> None:
    if not value.strip():
        raise DomainInvariantError(f"{name} must not be empty")
    if "\x00" in value:
        raise DomainInvariantError(f"{name} must not contain null characters")
    if maximum is not None and len(value) > maximum:
        raise DomainInvariantError(f"{name} must not exceed {maximum} characters")


@dataclass(frozen=True, slots=True)
class PageCoordinates:
    page: int
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.x, self.y, self.width, self.height)):
            raise DomainInvariantError("coordinates must be finite")
        if self.page < 1:
            raise DomainInvariantError("coordinate page must be positive")
        if min(self.x, self.y, self.width, self.height) < 0:
            raise DomainInvariantError("coordinates cannot be negative")
        if self.width == 0 or self.height == 0:
            raise DomainInvariantError("coordinate width and height must be positive")


@dataclass(frozen=True, slots=True)
class ParsedPassage:
    id: UUID
    source_id: str
    section_index: int
    passage_index: int
    text: str
    coordinates: tuple[PageCoordinates, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.source_id, "passage source_id", maximum=200)
        _require_text(self.text, "passage text")
        if self.section_index < 0 or self.passage_index < 0:
            raise DomainInvariantError("passage indexes cannot be negative")


@dataclass(frozen=True, slots=True)
class ParsedSection:
    id: UUID
    index: int
    title: str
    passages: tuple[ParsedPassage, ...]

    def __post_init__(self) -> None:
        if self.index < 0:
            raise DomainInvariantError("section index cannot be negative")
        _require_text(self.title, "section title", maximum=500)
        if not self.passages:
            raise DomainInvariantError("parsed section must contain at least one passage")
        if any(passage.section_index != self.index for passage in self.passages):
            raise DomainInvariantError("passage section index must match its section")


@dataclass(frozen=True, slots=True)
class ParsedReference:
    id: UUID
    source_id: str
    title: str | None
    authors: tuple[str, ...]
    year: int | None
    raw_text: str | None

    def __post_init__(self) -> None:
        _require_text(self.source_id, "reference source_id", maximum=200)
        if self.title is not None:
            _require_text(self.title, "reference title", maximum=2000)
        if self.year is not None and not 1000 <= self.year <= 9999:
            raise DomainInvariantError("reference year must have four digits")
        if self.raw_text is not None:
            _require_text(self.raw_text, "reference raw text", maximum=5000)


@dataclass(frozen=True, slots=True)
class CitationContext:
    id: UUID
    parsed_passage_id: UUID
    reference_source_id: str
    excerpt: str
    coordinates: tuple[PageCoordinates, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.reference_source_id, "citation reference source_id", maximum=200)
        _require_text(self.excerpt, "citation excerpt", maximum=2000)


@dataclass(frozen=True, slots=True)
class ParsedPaper:
    id: UUID
    paper_id: UUID
    paper_version_id: UUID
    parser_name: str
    parser_version: str
    parsed_at: datetime
    source: str
    sections: tuple[ParsedSection, ...]
    references: tuple[ParsedReference, ...]
    citation_contexts: tuple[CitationContext, ...]
    call_count: int = 1
    duration_ms: int = 0
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_text(self.parser_name, "parser name", maximum=100)
        _require_text(self.parser_version, "parser version", maximum=100)
        _require_text(self.source, "parse source", maximum=100)
        _require_aware(self.parsed_at, "parsed_at")
        if not self.sections:
            raise DomainInvariantError("parsed paper must contain at least one body section")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")
        if not 1 <= self.call_count <= MAX_PARSER_CALL_COUNT:
            raise DomainInvariantError("parser call count exceeds the bounded retry policy")
        if not 0 <= self.duration_ms <= MAX_PARSER_DURATION_MS:
            raise DomainInvariantError("parser duration exceeds the persistence bound")
        passage_ids = {passage.id for section in self.sections for passage in section.passages}
        if len(passage_ids) != sum(len(section.passages) for section in self.sections):
            raise DomainInvariantError("parsed passage IDs must be unique")
        if any(context.parsed_passage_id not in passage_ids for context in self.citation_contexts):
            raise DomainInvariantError("citation contexts must reference a parsed passage")
        reference_source_ids = {reference.source_id for reference in self.references}
        if len(reference_source_ids) != len(self.references):
            raise DomainInvariantError("parsed reference source IDs must be unique")
        if any(
            context.reference_source_id not in reference_source_ids
            for context in self.citation_contexts
        ):
            raise DomainInvariantError("citation contexts must reference a parsed reference")


@dataclass(frozen=True, slots=True)
class AnalysisPassage:
    id: str
    section: str
    text: str
    coordinates: tuple[PageCoordinates, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.id, "analysis passage id", maximum=200)
        _require_text(self.section, "analysis passage section", maximum=500)
        _require_text(self.text, "analysis passage text")


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    paper_id: UUID
    paper_version_id: UUID
    canonical_arxiv_id: str
    arxiv_version: int
    title: str
    scope: AnalysisScope
    passages: tuple[AnalysisPassage, ...]
    parsed_paper_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_text(self.canonical_arxiv_id, "canonical arXiv id", maximum=64)
        _require_text(self.title, "paper title", maximum=4000)
        if self.arxiv_version < 1:
            raise DomainInvariantError("arXiv version must be positive")
        if not self.passages:
            raise DomainInvariantError("analysis requires at least one source passage")
        if len({passage.id for passage in self.passages}) != len(self.passages):
            raise DomainInvariantError("analysis source passage IDs must be unique")
        if self.scope is AnalysisScope.FULL_TEXT and self.parsed_paper_id is None:
            raise DomainInvariantError("full-text analysis requires an exact parsed paper")
        if self.scope is AnalysisScope.ABSTRACT_ONLY and self.parsed_paper_id is not None:
            raise DomainInvariantError("abstract-only analysis cannot reference a parsed paper")


@dataclass(frozen=True, slots=True)
class GeneratedClaim:
    key: str
    claim_type: ClaimType
    text: str

    def __post_init__(self) -> None:
        _require_text(self.key, "claim key", maximum=80)
        _require_text(self.text, "claim text", maximum=4000)


@dataclass(frozen=True, slots=True)
class GeneratedEvidence:
    key: str
    claim_keys: tuple[str, ...]
    passage_ids: tuple[str, ...]
    evidence_type: EvidenceType
    rationale: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.key, "evidence key", maximum=80)
        if not self.claim_keys:
            raise DomainInvariantError("evidence must support at least one claim")
        if len(set(self.claim_keys)) != len(self.claim_keys):
            raise DomainInvariantError("evidence claim keys must be unique")
        for key in self.claim_keys:
            _require_text(key, "evidence claim key", maximum=80)
        if not self.passage_ids:
            raise DomainInvariantError("evidence must reference at least one source passage")
        if len(set(self.passage_ids)) != len(self.passage_ids):
            raise DomainInvariantError("evidence passage IDs must be unique")
        for passage_id in self.passage_ids:
            _require_text(passage_id, "evidence passage id", maximum=200)
        if self.rationale is not None:
            _require_text(self.rationale, "evidence rationale", maximum=1000)


@dataclass(frozen=True, slots=True)
class ModelUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    call_count: int
    duration_ms: int
    estimated_cost_usd: Decimal | None

    def __post_init__(self) -> None:
        if (
            min(
                self.prompt_tokens,
                self.completion_tokens,
                self.total_tokens,
                self.call_count,
                self.duration_ms,
            )
            < 0
        ):
            raise DomainInvariantError("model usage values cannot be negative")
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise DomainInvariantError("total tokens must equal prompt plus completion tokens")
        if self.call_count < 1:
            raise DomainInvariantError("model call count must be positive")
        if (
            max(self.prompt_tokens, self.completion_tokens, self.total_tokens)
            > MAX_MODEL_TOKEN_COUNT
        ):
            raise DomainInvariantError("model token counts exceed the persistence bound")
        if self.completion_tokens > MAX_MODEL_COMPLETION_TOKEN_COUNT:
            raise DomainInvariantError("model completion tokens exceed the configured output bound")
        if self.call_count > MAX_MODEL_CALL_COUNT:
            raise DomainInvariantError("model call count exceeds the persistence bound")
        if self.duration_ms > MAX_MODEL_DURATION_MS:
            raise DomainInvariantError("model duration exceeds the persistence bound")
        if self.estimated_cost_usd is not None and not self.estimated_cost_usd.is_finite():
            raise DomainInvariantError("estimated model cost must be finite")
        if self.estimated_cost_usd is not None and self.estimated_cost_usd < 0:
            raise DomainInvariantError("estimated model cost cannot be negative")
        if (
            self.estimated_cost_usd is not None
            and self.estimated_cost_usd > MAX_MODEL_ESTIMATED_COST_USD
        ):
            raise DomainInvariantError("estimated model cost exceeds the persistence bound")


@dataclass(frozen=True, slots=True)
class GeneratedAnalysis:
    provider: str
    configured_model: str
    model_version: str
    prompt_version: str
    generated_at: datetime
    claims: tuple[GeneratedClaim, ...]
    evidence: tuple[GeneratedEvidence, ...]
    usage: ModelUsage

    def __post_init__(self) -> None:
        for value, name, maximum in (
            (self.provider, "provider", 100),
            (self.configured_model, "configured model", 200),
            (self.model_version, "model version", 200),
            (self.prompt_version, "prompt version", 100),
        ):
            _require_text(value, name, maximum=maximum)
        _require_aware(self.generated_at, "generated_at")
        if not self.claims or not self.evidence:
            raise DomainInvariantError("analysis needs claims and evidence")
        claim_keys = {claim.key for claim in self.claims}
        evidence_keys = {item.key for item in self.evidence}
        if len(claim_keys) != len(self.claims) or len(evidence_keys) != len(self.evidence):
            raise DomainInvariantError("claim and evidence keys must be unique")
        referenced_claims = {key for item in self.evidence for key in item.claim_keys}
        if referenced_claims != claim_keys:
            raise DomainInvariantError(
                "every claim must be supported by evidence and keys must exist"
            )


@dataclass(frozen=True, slots=True)
class PaperAnalysis:
    id: UUID
    paper_id: UUID
    paper_version_id: UUID
    parsed_paper_id: UUID | None
    analysis_scope: AnalysisScope
    summary: str
    research_problem: str
    method_summary: str
    key_contributions: tuple[str, ...]
    limitations: tuple[str, ...]
    provider: str
    configured_model: str
    model_version: str
    prompt_version: str
    generated_at: datetime
    source: str
    verification_status: VerificationStatus
    usage: ModelUsage
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.generated_at, "generated_at")
        _require_aware(self.created_at, "created_at")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")
        if self.analysis_scope is AnalysisScope.FULL_TEXT and self.parsed_paper_id is None:
            raise DomainInvariantError("full-text analysis requires parsed-paper provenance")
        if self.analysis_scope is AnalysisScope.ABSTRACT_ONLY and self.parsed_paper_id is not None:
            raise DomainInvariantError("abstract-only analysis cannot reference a parsed paper")
        for value, name in (
            (self.summary, "analysis summary"),
            (self.research_problem, "research problem"),
            (self.method_summary, "method summary"),
            (self.provider, "provider"),
            (self.configured_model, "configured model"),
            (self.model_version, "model version"),
            (self.prompt_version, "prompt version"),
            (self.source, "analysis source"),
        ):
            _require_text(value, name)
        for value in self.key_contributions:
            _require_text(value, "analysis contribution", maximum=2000)
        for value in self.limitations:
            _require_text(value, "analysis limitation", maximum=2000)


@dataclass(frozen=True, slots=True)
class AnalysisClaim:
    id: UUID
    analysis_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    key: str
    claim_type: ClaimType
    text: str
    provider: str
    model_version: str
    prompt_version: str
    generated_at: datetime
    source: str
    verification_status: VerificationStatus
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.key, "claim key", maximum=80)
        _require_text(self.text, "claim text", maximum=4000)
        _require_aware(self.generated_at, "generated_at")
        _require_aware(self.created_at, "created_at")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class Evidence:
    id: UUID
    analysis_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    key: str
    section: str
    passage_id: str
    coordinates: tuple[PageCoordinates, ...]
    excerpt: str
    evidence_type: EvidenceType
    supported_claim_ids: tuple[UUID, ...]
    extraction_source: str
    provider: str
    model_version: str
    prompt_version: str
    generated_at: datetime
    verification_status: VerificationStatus
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.key, "evidence key", maximum=80)
        _require_text(self.section, "evidence section", maximum=500)
        _require_text(self.passage_id, "evidence passage id", maximum=200)
        _require_text(self.excerpt, "evidence excerpt", maximum=600)
        _require_text(self.extraction_source, "extraction source", maximum=100)
        _require_aware(self.generated_at, "generated_at")
        _require_aware(self.created_at, "created_at")
        if not self.supported_claim_ids:
            raise DomainInvariantError("evidence must support at least one claim ID")
        if len(set(self.supported_claim_ids)) != len(self.supported_claim_ids):
            raise DomainInvariantError("evidence claim IDs must be unique")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class AnalysisBundle:
    analysis: PaperAnalysis
    claims: tuple[AnalysisClaim, ...]
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        claim_ids = {claim.id for claim in self.claims}
        evidence_ids = {item.id for item in self.evidence}
        if not claim_ids or not self.evidence:
            raise DomainInvariantError("analysis bundle requires claims and evidence")
        if len(claim_ids) != len(self.claims) or len(evidence_ids) != len(self.evidence):
            raise DomainInvariantError("analysis bundle claim and evidence IDs must be unique")
        if len({claim.key for claim in self.claims}) != len(self.claims) or len(
            {item.key for item in self.evidence}
        ) != len(self.evidence):
            raise DomainInvariantError("analysis bundle claim and evidence keys must be unique")
        if any(claim.analysis_id != self.analysis.id for claim in self.claims):
            raise DomainInvariantError("claims must belong to the bundle analysis")
        if any(
            claim.paper_id != self.analysis.paper_id
            or claim.paper_version_id != self.analysis.paper_version_id
            or claim.provider != self.analysis.provider
            or claim.model_version != self.analysis.model_version
            or claim.prompt_version != self.analysis.prompt_version
            or claim.generated_at != self.analysis.generated_at
            for claim in self.claims
        ):
            raise DomainInvariantError("claims must preserve analysis ownership and provenance")
        referenced_claim_ids: set[UUID] = set()
        for item in self.evidence:
            if item.analysis_id != self.analysis.id:
                raise DomainInvariantError("evidence must belong to the bundle analysis")
            if (
                item.paper_id != self.analysis.paper_id
                or item.paper_version_id != self.analysis.paper_version_id
                or item.provider != self.analysis.provider
                or item.model_version != self.analysis.model_version
                or item.prompt_version != self.analysis.prompt_version
                or item.generated_at != self.analysis.generated_at
            ):
                raise DomainInvariantError(
                    "evidence must preserve analysis ownership and provenance"
                )
            if not set(item.supported_claim_ids).issubset(claim_ids):
                raise DomainInvariantError("evidence cannot reference claims outside its analysis")
            referenced_claim_ids.update(item.supported_claim_ids)
        if referenced_claim_ids != claim_ids:
            raise DomainInvariantError("every persisted claim must have supporting evidence")
