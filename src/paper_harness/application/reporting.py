"""First-party M4 report orchestration over validated persisted inputs."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid5

from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.models import RunStatus
from paper_harness.domain.reports import (
    GeneratedReportNarrative,
    GeneratedReportSection,
    Report,
    ReportNarrativeMode,
    ReportNarrativeRequest,
    ReportSection,
    ReportSectionKind,
    ReportType,
    report_section_evidence_allowlist,
)


class ReportNarrativeModeConflictError(RuntimeError):
    error_code = "REPORT_NARRATIVE_MODE_CONFLICT"
    retryable = False


def require_matching_narrative_mode(
    existing: ReportNarrativeMode,
    requested: ReportNarrativeMode,
) -> None:
    if existing is not requested:
        raise ReportNarrativeModeConflictError(
            f"existing report uses {existing.value}; requested mode is {requested.value}"
        )


def build_structured_report_sections(
    request: ReportNarrativeRequest,
) -> tuple[GeneratedReportSection, ...]:
    """Build the explicitly selected no-LLM report mode from authoritative facts.

    This is a product mode selected before execution, never a fallback after an
    LLM failure. It follows the useful STORM outline-then-section pattern while
    keeping the outline fixed, bounded, and coverage-aware.
    """

    counts = request.counts
    overview_evidence = tuple(
        dict.fromkeys(
            evidence_id for item in request.highlighted_papers for evidence_id in item.evidence_ids
        )
    )
    comparison_evidence = tuple(
        dict.fromkeys(
            evidence_id for item in request.notable_comparisons for evidence_id in item.evidence_ids
        )
    )
    overview = (
        "No relevant new arXiv paper was selected for this topic on this logical date. "
        "The daily publication completed normally with no update."
        if counts.selected == 0
        else (
            f"The reporting period includes {counts.retrieved} retrieved papers and "
            f"{counts.selected} selected papers. {counts.completed} completed publication; "
            f"{counts.failed} failed."
        )
    )
    trends = (
        " ".join(request.trend_summaries)
        if request.trend_summaries
        else "No trend interpretation is available for the persisted reporting period."
    )
    comparisons = (
        " ".join(item.summary for item in request.notable_comparisons)
        if request.notable_comparisons
        else "No evidence-linked comparison highlight is available for this report."
    )
    lineage = (
        " ".join(item.summary for item in request.lineage_highlights)
        if request.lineage_highlights
        else (
            "No verified predecessor relation is currently available within the currently "
            "retrieved corpus."
        )
    )
    limitation_parts = [*request.limitations, *request.missing_sections]
    limitation_parts.extend(
        f"{item.paper_id}: {item.failed_stage.value} failed with {item.error_code}."
        for item in request.failures
    )
    limitations = (
        " ".join(limitation_parts)
        if limitation_parts
        else "Interpretation is limited to the currently retrieved and persisted corpus."
    )
    return (
        GeneratedReportSection(
            kind=ReportSectionKind.OVERVIEW,
            narrative=overview,
            evidence_ids=overview_evidence,
        ),
        GeneratedReportSection(
            kind=ReportSectionKind.TRENDS,
            narrative=trends,
            evidence_ids=(),
        ),
        GeneratedReportSection(
            kind=ReportSectionKind.COMPARISONS,
            narrative=comparisons,
            evidence_ids=comparison_evidence,
        ),
        GeneratedReportSection(
            kind=ReportSectionKind.LINEAGE,
            narrative=lineage,
            evidence_ids=(),
        ),
        GeneratedReportSection(
            kind=ReportSectionKind.LIMITATIONS,
            narrative=limitations,
            evidence_ids=(),
        ),
    )


def assemble_product_report(
    request: ReportNarrativeRequest,
    *,
    report_id: UUID,
    run_id: UUID | None,
    topic_id: UUID,
    logical_date: date,
    narrative_mode: ReportNarrativeMode,
    generated: GeneratedReportNarrative | None,
    trend_snapshot_ids: tuple[UUID, ...],
    created_at: datetime,
) -> Report:
    """Map a validated report input and preselected narrative mode to persistence."""

    if narrative_mode is ReportNarrativeMode.DEEPSEEK:
        if generated is None:
            raise DomainInvariantError("DeepSeek report mode requires generated narrative")
        section_values = generated.sections
        summary = generated.summary
        source = "deepseek_chat_completions"
    else:
        if generated is not None:
            raise DomainInvariantError("structured-only report cannot accept model output")
        section_values = build_structured_report_sections(request)
        summary = _structured_summary(request)
        source = "m4_structured_report"

    available_evidence_ids = {item.id for item in request.evidence}
    if any(
        not set(section.evidence_ids).issubset(available_evidence_ids) for section in section_values
    ):
        raise DomainInvariantError("report narrative references unavailable evidence")
    section_allowlist = report_section_evidence_allowlist(
        highlighted_papers=request.highlighted_papers,
        notable_comparisons=request.notable_comparisons,
    )
    if any(
        not set(section.evidence_ids).issubset(section_allowlist[section.kind])
        for section in section_values
    ):
        raise DomainInvariantError("report narrative cites evidence outside its semantic section")
    sections = tuple(
        ReportSection(
            id=uuid5(report_id, section.kind.value),
            report_id=report_id,
            kind=section.kind,
            narrative=section.narrative,
            evidence_ids=section.evidence_ids,
            schema_version=1,
            created_at=created_at,
        )
        for section in section_values
    )
    generated_at = created_at if generated is None else generated.generated_at
    return Report(
        id=report_id,
        run_id=run_id,
        topic_id=topic_id,
        logical_date=logical_date,
        status=request.status,
        title=_report_title(request.report_type, request.period_start, request.period_end),
        summary=summary,
        source=source,
        generated_at=generated_at,
        schema_version=1,
        created_at=created_at,
        failures=request.failures,
        sections=sections,
        report_type=request.report_type,
        period_start=request.period_start,
        period_end=request.period_end,
        counts=request.counts,
        highlighted_papers=request.highlighted_papers,
        major_entities=request.major_entities,
        notable_comparisons=request.notable_comparisons,
        graph_changes=request.graph_changes,
        trend_snapshot_ids=trend_snapshot_ids,
        lineage_highlights=request.lineage_highlights,
        evidence_ids=tuple(item.id for item in request.evidence),
        limitations=request.limitations,
        missing_sections=request.missing_sections,
        narrative_mode=narrative_mode,
        provider=None if generated is None else generated.provider,
        configured_model=None if generated is None else generated.configured_model,
        model_version=None if generated is None else generated.model_version,
        prompt_version=None if generated is None else generated.prompt_version,
        usage=None if generated is None else generated.usage,
    )


def _structured_summary(request: ReportNarrativeRequest) -> str:
    counts = request.counts
    if request.report_type is ReportType.DAILY and counts.selected == 0:
        return (
            "No relevant new arXiv paper was selected for this topic today; "
            "the daily run completed normally with no update."
        )
    qualifier = "complete" if request.status is RunStatus.COMPLETE else "partial"
    return (
        f"This {request.report_type.value.lower()} report is {qualifier}: "
        f"{counts.completed} of {counts.selected} selected papers completed publication, "
        f"with {counts.failed} item failures."
    )


def _report_title(report_type: ReportType, period_start: date, period_end: date) -> str:
    label = report_type.value.title()
    period = (
        period_start.isoformat()
        if period_start == period_end
        else f"{period_start.isoformat()} to {period_end.isoformat()}"
    )
    return f"{label} research intelligence report — {period}"


__all__ = [
    "ReportNarrativeModeConflictError",
    "assemble_product_report",
    "build_structured_report_sections",
    "require_matching_narrative_mode",
]
