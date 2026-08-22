"""Structured, evidence-aware report domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from paper_harness.domain.analysis import ModelUsage, VerificationStatus
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.models import PaperStage, RunStatus


class ReportType(StrEnum):
    """Persisted report scopes.

    ``ANALYSIS`` preserves the M2 per-run publication record. M4 product
    publication uses ``DAILY`` and may create ``WEEKLY`` or ``MONTHLY`` only
    after the deterministic eligibility checks succeed.
    """

    ANALYSIS = "ANALYSIS"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class ReportNarrativeMode(StrEnum):
    STRUCTURED_ONLY = "STRUCTURED_ONLY"
    DEEPSEEK = "DEEPSEEK"


class ReportSectionKind(StrEnum):
    OVERVIEW = "OVERVIEW"
    TRENDS = "TRENDS"
    COMPARISONS = "COMPARISONS"
    LINEAGE = "LINEAGE"
    LIMITATIONS = "LIMITATIONS"


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainInvariantError(f"{name} must be timezone-aware")


def _require_text(value: str, name: str, *, maximum: int) -> None:
    if not value.strip():
        raise DomainInvariantError(f"{name} must not be empty")
    if "\x00" in value or len(value) > maximum:
        raise DomainInvariantError(f"{name} must be concise valid text")


@dataclass(frozen=True, slots=True)
class ReportFailure:
    id: UUID
    report_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    failed_stage: PaperStage
    error_code: str
    retryable: bool
    error_detail: str
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.error_code, "report failure code", maximum=80)
        _require_text(self.error_detail, "report failure detail", maximum=1000)
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class ReportCounts:
    retrieved: int
    selected: int
    processed: int
    completed: int
    failed: int

    def __post_init__(self) -> None:
        if min(self.retrieved, self.selected, self.processed, self.completed, self.failed) < 0:
            raise DomainInvariantError("report counts cannot be negative")
        if self.selected > self.retrieved:
            raise DomainInvariantError("report selected count cannot exceed retrieved count")
        if self.completed > self.processed or self.processed > self.selected:
            raise DomainInvariantError("report processing counts are inconsistent")
        if self.failed > self.selected:
            raise DomainInvariantError("report failed count cannot exceed selected count")
        if self.completed + self.failed != self.processed:
            raise DomainInvariantError("processed count must equal completed plus failed")


@dataclass(frozen=True, slots=True)
class ReportPaperHighlight:
    paper_id: UUID
    paper_version_id: UUID
    title: str
    reason: str
    evidence_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.title, "highlighted paper title", maximum=4000)
        _require_text(self.reason, "highlighted paper reason", maximum=2000)
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise DomainInvariantError("highlighted paper evidence IDs must be unique")


@dataclass(frozen=True, slots=True)
class ReportEntityHighlight:
    graph_entity_id: UUID
    entity_type: str
    label: str
    distinct_paper_count: int

    def __post_init__(self) -> None:
        _require_text(self.entity_type, "report entity type", maximum=40)
        _require_text(self.label, "report entity label", maximum=2000)
        if self.distinct_paper_count < 1:
            raise DomainInvariantError("report entity distinct-paper count must be positive")


@dataclass(frozen=True, slots=True)
class ReportComparisonHighlight:
    comparison_id: UUID
    source_paper_id: UUID
    source_paper_version_id: UUID
    target_paper_id: UUID
    target_paper_version_id: UUID
    summary: str
    comparability_status: str
    evidence_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        _require_text(self.summary, "comparison highlight", maximum=8000)
        _require_text(self.comparability_status, "comparability status", maximum=40)
        if self.source_paper_id == self.target_paper_id:
            raise DomainInvariantError("comparison highlight requires distinct papers")
        if self.source_paper_version_id == self.target_paper_version_id:
            raise DomainInvariantError("comparison highlight requires distinct paper versions")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise DomainInvariantError("comparison highlight evidence IDs must be unique")


@dataclass(frozen=True, slots=True)
class ReportLineageHighlight:
    lineage_snapshot_id: UUID
    root_paper_id: UUID
    summary: str
    uncertain: bool

    def __post_init__(self) -> None:
        _require_text(self.summary, "lineage highlight", maximum=4000)


@dataclass(frozen=True, slots=True)
class ReportEvidenceReference:
    id: UUID
    paper_id: UUID
    paper_version_id: UUID
    section: str
    excerpt: str
    evidence_type: str
    verification_status: VerificationStatus

    def __post_init__(self) -> None:
        _require_text(self.section, "report evidence section", maximum=500)
        _require_text(self.excerpt, "report evidence excerpt", maximum=600)
        _require_text(self.evidence_type, "report evidence type", maximum=32)


@dataclass(frozen=True, slots=True)
class ReportGraphChanges:
    entity_count: int
    edge_count: int
    new_entity_count: int
    inferred_edge_count: int

    def __post_init__(self) -> None:
        if (
            min(
                self.entity_count,
                self.edge_count,
                self.new_entity_count,
                self.inferred_edge_count,
            )
            < 0
        ):
            raise DomainInvariantError("graph change counts cannot be negative")
        if self.new_entity_count > self.entity_count or self.inferred_edge_count > self.edge_count:
            raise DomainInvariantError("graph change subtotals exceed their totals")


@dataclass(frozen=True, slots=True)
class ReportNarrativeRequest:
    report_type: ReportType
    period_start: date
    period_end: date
    status: RunStatus
    counts: ReportCounts
    highlighted_papers: tuple[ReportPaperHighlight, ...]
    major_entities: tuple[ReportEntityHighlight, ...]
    notable_comparisons: tuple[ReportComparisonHighlight, ...]
    graph_changes: ReportGraphChanges
    trend_summaries: tuple[str, ...]
    lineage_highlights: tuple[ReportLineageHighlight, ...]
    failures: tuple[ReportFailure, ...]
    limitations: tuple[str, ...]
    evidence: tuple[ReportEvidenceReference, ...]
    missing_sections: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.report_type is ReportType.ANALYSIS:
            raise DomainInvariantError("analysis reports do not use M4 narrative synthesis")
        if self.period_start > self.period_end:
            raise DomainInvariantError("report narrative period is reversed")
        if self.status not in (RunStatus.COMPLETE, RunStatus.PARTIAL):
            raise DomainInvariantError("only publishable states may request report narrative")
        if self.status is RunStatus.COMPLETE and self.failures:
            raise DomainInvariantError("complete narrative input cannot contain failures")
        if self.status is RunStatus.PARTIAL and not self.failures:
            raise DomainInvariantError("partial narrative input requires visible failures")
        if self.counts.completed < 1:
            raise DomainInvariantError("publishable report input requires a completed paper")
        for value in self.trend_summaries:
            _require_text(value, "trend summary", maximum=4000)
        for value in self.limitations:
            _require_text(value, "report limitation", maximum=2000)
        for value in self.missing_sections:
            _require_text(value, "missing report section", maximum=200)
        available_evidence_ids = tuple(item.id for item in self.evidence)
        if len(set(available_evidence_ids)) != len(available_evidence_ids):
            raise DomainInvariantError("available report evidence IDs must be unique")
        evidence_by_id = {item.id: item for item in self.evidence}
        available = set(evidence_by_id)
        referenced = {
            evidence_id
            for highlight in self.highlighted_papers
            for evidence_id in highlight.evidence_ids
        } | {
            evidence_id
            for highlight in self.notable_comparisons
            for evidence_id in highlight.evidence_ids
        }
        if not referenced.issubset(available):
            raise DomainInvariantError("report input cannot reference unavailable evidence")
        if any(
            evidence_by_id[evidence_id].verification_status is VerificationStatus.REJECTED
            for evidence_id in referenced
        ):
            raise DomainInvariantError("report input cannot cite rejected evidence")
        for highlight in self.highlighted_papers:
            if any(
                (
                    evidence_by_id[evidence_id].paper_id,
                    evidence_by_id[evidence_id].paper_version_id,
                )
                != (highlight.paper_id, highlight.paper_version_id)
                for evidence_id in highlight.evidence_ids
            ):
                raise DomainInvariantError(
                    "paper highlight evidence must belong to the highlighted paper version"
                )
        for highlight in self.notable_comparisons:
            allowed_owners = {
                (highlight.source_paper_id, highlight.source_paper_version_id),
                (highlight.target_paper_id, highlight.target_paper_version_id),
            }
            if any(
                (
                    evidence_by_id[evidence_id].paper_id,
                    evidence_by_id[evidence_id].paper_version_id,
                )
                not in allowed_owners
                for evidence_id in highlight.evidence_ids
            ):
                raise DomainInvariantError(
                    "comparison highlight evidence must belong to a compared paper version"
                )


def report_section_evidence_allowlist(
    *,
    highlighted_papers: tuple[ReportPaperHighlight, ...],
    notable_comparisons: tuple[ReportComparisonHighlight, ...],
) -> dict[ReportSectionKind, frozenset[UUID]]:
    """Return the only evidence roles each fixed report section may cite."""

    overview = frozenset(
        evidence_id for item in highlighted_papers for evidence_id in item.evidence_ids
    )
    comparisons = frozenset(
        evidence_id for item in notable_comparisons for evidence_id in item.evidence_ids
    )
    return {
        ReportSectionKind.OVERVIEW: overview,
        ReportSectionKind.TRENDS: frozenset(),
        ReportSectionKind.COMPARISONS: comparisons,
        ReportSectionKind.LINEAGE: frozenset(),
        ReportSectionKind.LIMITATIONS: frozenset(),
    }


@dataclass(frozen=True, slots=True)
class GeneratedReportSection:
    kind: ReportSectionKind
    narrative: str
    evidence_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        _require_text(self.narrative, "generated report section", maximum=8000)
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise DomainInvariantError("generated report evidence IDs must be unique")


@dataclass(frozen=True, slots=True)
class GeneratedReportNarrative:
    provider: str
    configured_model: str
    model_version: str
    prompt_version: str
    generated_at: datetime
    summary: str
    sections: tuple[GeneratedReportSection, ...]
    usage: ModelUsage

    def __post_init__(self) -> None:
        for value, name, maximum in (
            (self.provider, "report provider", 100),
            (self.configured_model, "configured report model", 200),
            (self.model_version, "report model version", 200),
            (self.prompt_version, "report prompt version", 100),
            (self.summary, "report summary", 8000),
        ):
            _require_text(value, name, maximum=maximum)
        _require_aware(self.generated_at, "generated_at")
        kinds = tuple(section.kind for section in self.sections)
        if len(set(kinds)) != len(kinds) or kinds != tuple(
            sorted(kinds, key=tuple(ReportSectionKind).index)
        ):
            raise DomainInvariantError(
                "generated report sections must be unique and canonically ordered"
            )


@dataclass(frozen=True, slots=True)
class ReportSection:
    id: UUID
    report_id: UUID
    kind: ReportSectionKind
    narrative: str
    evidence_ids: tuple[UUID, ...]
    schema_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.narrative, "report section", maximum=8000)
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise DomainInvariantError("report section evidence IDs must be unique")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class Report:
    id: UUID
    run_id: UUID | None
    topic_id: UUID
    logical_date: date
    status: RunStatus
    title: str
    summary: str
    source: str
    generated_at: datetime
    schema_version: int
    created_at: datetime
    failures: tuple[ReportFailure, ...] = ()
    sections: tuple[ReportSection, ...] = ()
    report_type: ReportType = ReportType.ANALYSIS
    period_start: date | None = None
    period_end: date | None = None
    counts: ReportCounts = ReportCounts(0, 0, 0, 0, 0)
    highlighted_papers: tuple[ReportPaperHighlight, ...] = ()
    major_entities: tuple[ReportEntityHighlight, ...] = ()
    notable_comparisons: tuple[ReportComparisonHighlight, ...] = ()
    graph_changes: ReportGraphChanges = ReportGraphChanges(0, 0, 0, 0)
    trend_snapshot_ids: tuple[UUID, ...] = ()
    lineage_highlights: tuple[ReportLineageHighlight, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    limitations: tuple[str, ...] = ()
    missing_sections: tuple[str, ...] = ()
    narrative_mode: ReportNarrativeMode = ReportNarrativeMode.STRUCTURED_ONLY
    provider: str | None = None
    configured_model: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    usage: ModelUsage | None = None
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED

    def __post_init__(self) -> None:
        if self.status not in (RunStatus.COMPLETE, RunStatus.PARTIAL):
            raise DomainInvariantError("only complete or partial runs may publish reports")
        _require_text(self.title, "report title", maximum=4000)
        _require_text(self.summary, "report summary", maximum=8000)
        _require_text(self.source, "report source", maximum=100)
        if self.status is RunStatus.COMPLETE and self.failures:
            raise DomainInvariantError("complete report cannot list failures")
        if self.status is RunStatus.PARTIAL and not self.failures:
            raise DomainInvariantError("partial report must list item failures")
        if any(failure.report_id != self.id for failure in self.failures):
            raise DomainInvariantError("report failure ownership is invalid")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")
        _require_aware(self.generated_at, "generated_at")
        _require_aware(self.created_at, "created_at")
        period_start = self.logical_date if self.period_start is None else self.period_start
        period_end = self.logical_date if self.period_end is None else self.period_end
        if period_start > period_end:
            raise DomainInvariantError("report period is reversed")
        if self.report_type in (ReportType.ANALYSIS, ReportType.DAILY) and self.run_id is None:
            raise DomainInvariantError("analysis and daily reports require a DailyRun")
        if self.report_type in (ReportType.WEEKLY, ReportType.MONTHLY) and self.run_id is not None:
            raise DomainInvariantError("aggregate reports are not owned by one DailyRun")
        if self.report_type in (ReportType.ANALYSIS, ReportType.DAILY):
            if period_start != self.logical_date or period_end != self.logical_date:
                raise DomainInvariantError(
                    "analysis and daily reports must cover exactly their logical date"
                )
        elif self.report_type is ReportType.WEEKLY:
            if (
                period_start.weekday() != 0
                or period_end != period_start + timedelta(days=6)
                or self.logical_date != period_end
            ):
                raise DomainInvariantError(
                    "weekly reports must cover Monday through Sunday and end on their logical date"
                )
        else:
            next_month = (
                date(period_start.year + 1, 1, 1)
                if period_start.month == 12
                else date(period_start.year, period_start.month + 1, 1)
            )
            if (
                period_start.day != 1
                or period_end != next_month - timedelta(days=1)
                or self.logical_date != period_end
            ):
                raise DomainInvariantError(
                    "monthly reports must cover a calendar month and end on their logical date"
                )
        if self.report_type is ReportType.ANALYSIS:
            if self.sections:
                raise DomainInvariantError("legacy analysis reports cannot carry M4 sections")
        else:
            if self.counts.completed < 1:
                raise DomainInvariantError("product report requires a completed paper")
            section_kinds = tuple(section.kind for section in self.sections)
            if len(set(section_kinds)) != len(section_kinds) or section_kinds != tuple(
                sorted(section_kinds, key=tuple(ReportSectionKind).index)
            ):
                raise DomainInvariantError(
                    "product report sections must be unique and canonically ordered"
                )
            if any(section.report_id != self.id for section in self.sections):
                raise DomainInvariantError("report section ownership is invalid")
        reference_groups = (
            self.trend_snapshot_ids,
            self.evidence_ids,
            tuple(item.paper_version_id for item in self.highlighted_papers),
            tuple(item.graph_entity_id for item in self.major_entities),
            tuple(item.comparison_id for item in self.notable_comparisons),
            tuple(item.lineage_snapshot_id for item in self.lineage_highlights),
        )
        if any(len(set(values)) != len(values) for values in reference_groups):
            raise DomainInvariantError("report references must be unique within each role")
        available_evidence_ids = set(self.evidence_ids)
        if any(
            not set(section.evidence_ids).issubset(available_evidence_ids)
            for section in self.sections
        ):
            raise DomainInvariantError("report section references unavailable evidence")
        section_allowlist = report_section_evidence_allowlist(
            highlighted_papers=self.highlighted_papers,
            notable_comparisons=self.notable_comparisons,
        )
        if any(
            not set(section.evidence_ids).issubset(section_allowlist[section.kind])
            for section in self.sections
        ):
            raise DomainInvariantError("report section cites evidence outside its semantic role")
        for value in self.limitations:
            _require_text(value, "report limitation", maximum=2000)
        for value in self.missing_sections:
            _require_text(value, "missing report section", maximum=200)
        model_values = (
            self.provider,
            self.configured_model,
            self.model_version,
            self.prompt_version,
            self.usage,
        )
        if self.narrative_mode is ReportNarrativeMode.DEEPSEEK:
            if any(value is None for value in model_values):
                raise DomainInvariantError("DeepSeek report requires complete model provenance")
        elif any(value is not None for value in model_values):
            raise DomainInvariantError("structured-only report cannot carry model provenance")


def aggregate_report_eligible(
    report_type: ReportType,
    *,
    distinct_daily_dates: int,
    included_paper_count: int,
) -> bool:
    """Return the explicit M4 weekly/monthly eligibility decision.

    Weekly synthesis requires all seven daily periods and at least three papers.
    Monthly synthesis requires at least twenty daily periods and ten papers. The
    thresholds intentionally prevent confident narrative from tiny samples.
    """

    if distinct_daily_dates < 0 or included_paper_count < 0:
        raise DomainInvariantError("report eligibility counts cannot be negative")
    if report_type is ReportType.WEEKLY:
        return distinct_daily_dates >= 7 and included_paper_count >= 3
    if report_type is ReportType.MONTHLY:
        return distinct_daily_dates >= 20 and included_paper_count >= 10
    raise DomainInvariantError("aggregate eligibility applies only to weekly or monthly reports")
