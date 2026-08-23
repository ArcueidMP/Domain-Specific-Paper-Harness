"""Deterministic M4 report-input assembly from persisted publication artifacts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from uuid import UUID, uuid5

from paper_harness.application.product_models import (
    GraphWriteResult,
    PeriodicReportInput,
    ProductPaperInput,
    ProductPublicationInput,
)
from paper_harness.application.read_models import RunDetail
from paper_harness.domain.analysis import VerificationStatus
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.knowledge import (
    GraphEntityType,
    LineageSnapshot,
    TrendDataSufficiency,
    TrendGrowthStatus,
    TrendSnapshot,
    TrendWindow,
)
from paper_harness.domain.models import (
    PaperStage,
    RunItem,
    RunItemStatus,
    RunOperation,
    RunStatus,
)
from paper_harness.domain.reports import (
    ReportComparisonHighlight,
    ReportCounts,
    ReportEntityHighlight,
    ReportFailure,
    ReportGraphChanges,
    ReportLineageHighlight,
    ReportNarrativeRequest,
    ReportPaperHighlight,
    ReportType,
    aggregate_report_eligible,
)

MAX_REPORT_HIGHLIGHTED_PAPERS = 200
MAX_REPORT_COMPARISONS = 10
MAX_REPORT_ENTITY_HIGHLIGHTS = 12
MAX_REPORT_EVIDENCE = 100


@dataclass(frozen=True, slots=True)
class DailyReportAssemblyPlan:
    request: ReportNarrativeRequest
    trend_snapshot_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class PeriodicReportAssemblyPlan:
    request: ReportNarrativeRequest
    trend_snapshot_ids: tuple[UUID, ...]


def build_daily_report_plan(
    run_detail: RunDetail,
    publication_input: ProductPublicationInput,
    *,
    report_id: UUID,
    graph_results: tuple[GraphWriteResult, ...],
    trends: tuple[TrendSnapshot, ...],
    lineages: tuple[LineageSnapshot, ...],
    omitted_entity_types: tuple[GraphEntityType, ...],
) -> DailyReportAssemblyPlan:
    """Build authoritative daily report input before any optional LLM call."""

    run = run_detail.run
    if run.operation is not RunOperation.PRODUCT_PUBLICATION or run.status is not RunStatus.RUNNING:
        raise DomainInvariantError("daily report input requires a running product publication")
    if run.source_run_id != publication_input.source_run.run.id:
        raise DomainInvariantError("product run has the wrong source analysis run")
    ready_items = tuple(
        item
        for item in run_detail.items
        if item.item.status is RunItemStatus.IN_PROGRESS
        and item.item.stage is PaperStage.TREND_SNAPSHOTS_GENERATED
    )
    failed_items = tuple(
        item for item in run_detail.items if item.item.status is RunItemStatus.FAILED
    )
    no_update = (
        not run_detail.items
        and run.selected_count == 0
        and not publication_input.papers
        and not publication_input.cards
        and not publication_input.input_failures
    )
    if len(ready_items) + len(failed_items) != len(run_detail.items):
        raise DomainInvariantError("daily report input contains nonterminal product stages")
    metadata_only = bool(failed_items) and len(failed_items) == len(run_detail.items)
    if not ready_items and not no_update and not metadata_only:
        raise DomainInvariantError("failed product run cannot assemble a report")
    status = RunStatus.PARTIAL if failed_items else RunStatus.COMPLETE
    failures = tuple(
        _report_failure(report_id, detail.item)
        for detail in sorted(failed_items, key=lambda item: str(item.item.paper_version_id))
    )
    inputs_by_version = {item.paper_version_id: item for item in publication_input.papers}
    ready_inputs = tuple(inputs_by_version[item.item.paper_version_id] for item in ready_items)
    evidence_by_id = {evidence.id: evidence for item in ready_inputs for evidence in item.evidence}
    usable_evidence_ids = {
        evidence_id
        for evidence_id, evidence in evidence_by_id.items()
        if evidence.verification_status is not VerificationStatus.REJECTED
    }
    highlighted_papers = tuple(
        ReportPaperHighlight(
            paper_id=card.paper_id,
            paper_version_id=card.paper_version_id,
            title=card.title,
            reason=(
                _concise(analysis.analysis.analysis.summary, 2000)
                if (analysis := inputs_by_version.get(card.paper_version_id)) is not None
                else (
                    "Structured analysis is unavailable; source metadata remains published. "
                    "The author abstract is shown only on the metadata card and is not used as "
                    "grounded report narrative."
                )
            ),
            evidence_ids=(
                ()
                if analysis is None
                else tuple(
                    evidence.id
                    for evidence in analysis.analysis.evidence
                    if evidence.verification_status is not VerificationStatus.REJECTED
                    and evidence.id in usable_evidence_ids
                )[:3]
            ),
        )
        for card in publication_input.cards[:MAX_REPORT_HIGHLIGHTED_PAPERS]
    )
    comparison_values = tuple(
        comparison for item in ready_inputs for comparison in item.comparisons
    )
    notable_comparisons = tuple(
        ReportComparisonHighlight(
            comparison_id=item.bundle.comparison.id,
            source_paper_id=item.bundle.comparison.source_paper_id,
            source_paper_version_id=item.bundle.comparison.source_paper_version_id,
            target_paper_id=item.bundle.comparison.target_paper_id,
            target_paper_version_id=item.bundle.comparison.target_paper_version_id,
            summary=_concise(item.bundle.comparison.summary, 8000),
            comparability_status=item.bundle.comparison.comparability_status.value,
            evidence_ids=tuple(
                dict.fromkeys(
                    evidence_id
                    for dimension in item.bundle.comparison.dimensions
                    for evidence_id in (
                        dimension.source_evidence_ids + dimension.target_evidence_ids
                    )
                    if evidence_id in usable_evidence_ids
                )
            )[:5],
        )
        for item in sorted(
            comparison_values,
            key=lambda value: (
                value.bundle.comparison.generated_at,
                str(value.bundle.comparison.id),
            ),
            reverse=True,
        )[:MAX_REPORT_COMPARISONS]
    )
    notable_comparisons = tuple(item for item in notable_comparisons if item.evidence_ids)
    ordered_trends = normalize_daily_trends(trends)
    primary_trend = ordered_trends[0] if ordered_trends else None
    major_entities = tuple(
        ReportEntityHighlight(
            graph_entity_id=item.entity_id,
            entity_type=item.entity_type.value,
            label=item.label,
            distinct_paper_count=item.change.current_count,
        )
        for item in (() if primary_trend is None else primary_trend.entity_counts)
        if item.change.current_count > 0
    )[:MAX_REPORT_ENTITY_HIGHLIGHTS]
    lineages_by_root = {item.root_paper_id: item for item in lineages}
    lineage_highlights = tuple(
        ReportLineageHighlight(
            lineage_snapshot_id=lineage.id,
            root_paper_id=item.paper_id,
            summary=_lineage_summary(lineage),
            uncertain=not lineage.verified_predecessor_available,
        )
        for item in ready_inputs
        if (lineage := lineages_by_root.get(item.paper_id)) is not None
    )
    missing_sections = (
        ()
        if no_update
        else _missing_sections(
            ready_inputs=ready_inputs,
            failed_count=len(failed_items),
            lineages_by_root=lineages_by_root,
            omitted_entity_types=omitted_entity_types,
        )
    )
    missing_trend_windows = (
        ()
        if no_update
        else tuple(
            window
            for window in TrendWindow
            if window not in {item.window for item in ordered_trends}
        )
    )
    if missing_trend_windows:
        missing_sections = (
            *missing_sections,
            "INSUFFICIENT_DATA: trend snapshots are unavailable for "
            + ", ".join(f"{window.days}-day" for window in missing_trend_windows)
            + ".",
        )
    referenced_evidence_ids = tuple(
        dict.fromkeys(
            evidence_id
            for group in (
                *(item.evidence_ids for item in highlighted_papers),
                *(item.evidence_ids for item in notable_comparisons),
            )
            for evidence_id in group
        )
    )[:MAX_REPORT_EVIDENCE]
    if any(evidence_id not in evidence_by_id for evidence_id in referenced_evidence_ids):
        raise DomainInvariantError("daily report references unavailable evidence")
    graph_changes = ReportGraphChanges(
        entity_count=len({value for item in graph_results for value in item.entity_ids}),
        edge_count=len({value for item in graph_results for value in item.edge_ids}),
        new_entity_count=len({value for item in graph_results for value in item.new_entity_ids}),
        inferred_edge_count=len(
            {value for item in graph_results for value in item.inferred_edge_ids}
        ),
    )
    counts = ReportCounts(
        retrieved=run.selected_count
        + sum(item.retrieved_candidate_count for item in publication_input.papers),
        selected=run.selected_count,
        processed=len(run_detail.items),
        completed=len(ready_items),
        failed=len(failed_items),
    )
    limitations = [
        "Statistics and lineages cover only the currently retrieved persisted corpus.",
        (
            "The logical-date report uses the exact analysis and comparison inputs persisted "
            "when publication first started; it is not a historical end-of-day reconstruction."
        ),
    ]
    if no_update:
        limitations.insert(
            0,
            "No relevant new arXiv paper was selected for this topic on this logical date.",
        )
    if any(item.data_sufficiency is not TrendDataSufficiency.SUFFICIENT for item in ordered_trends):
        limitations.append(
            "At least one trend window has limited or insufficient data; its interpretation is "
            "qualified."
        )
    request = ReportNarrativeRequest(
        report_type=ReportType.DAILY,
        period_start=run.logical_date,
        period_end=run.logical_date,
        status=status,
        counts=counts,
        highlighted_papers=highlighted_papers,
        major_entities=major_entities,
        notable_comparisons=notable_comparisons,
        graph_changes=graph_changes,
        trend_summaries=tuple(trend_summary(item) for item in ordered_trends),
        lineage_highlights=lineage_highlights,
        failures=failures,
        limitations=tuple(limitations),
        evidence=tuple(evidence_by_id[item] for item in referenced_evidence_ids),
        missing_sections=missing_sections,
    )
    return DailyReportAssemblyPlan(
        request=request,
        trend_snapshot_ids=tuple(item.id for item in ordered_trends),
    )


def normalize_daily_trends(trends: tuple[TrendSnapshot, ...]) -> tuple[TrendSnapshot, ...]:
    by_window: dict[TrendWindow, TrendSnapshot] = {}
    for snapshot in sorted(
        trends,
        key=lambda item: (item.window.days, item.generated_at, str(item.id)),
    ):
        by_window.setdefault(snapshot.window, snapshot)
    return tuple(by_window[window] for window in TrendWindow if window in by_window)


def _report_failure(report_id: UUID, item: RunItem) -> ReportFailure:
    if (
        item.failed_stage is None
        or item.error_code is None
        or item.retryable is None
        or item.error_detail is None
    ):
        raise DomainInvariantError("failed product item lacks reportable failure metadata")
    return ReportFailure(
        id=uuid5(report_id, str(item.paper_version_id)),
        report_id=report_id,
        paper_id=item.paper_id,
        paper_version_id=item.paper_version_id,
        failed_stage=item.failed_stage,
        error_code=item.error_code,
        retryable=item.retryable,
        error_detail=item.error_detail,
        schema_version=1,
        created_at=item.updated_at,
    )


def build_periodic_report_plan(
    source: PeriodicReportInput,
    *,
    report_id: UUID,
) -> PeriodicReportAssemblyPlan:
    daily_reports = tuple(sorted(source.daily_reports, key=lambda item: item.report.logical_date))
    if not aggregate_report_eligible(
        source.report_type,
        distinct_daily_dates=len(daily_reports),
        included_paper_count=len(source.included_paper_ids),
    ):
        raise DomainInvariantError("periodic report data is insufficient for synthesis")
    failures_by_version: dict[UUID, list[tuple[date, ReportFailure]]] = {}
    for detail in daily_reports:
        for failure in detail.report.failures:
            failures_by_version.setdefault(failure.paper_version_id, []).append(
                (detail.report.logical_date, failure)
            )
    failures = tuple(
        ReportFailure(
            id=uuid5(report_id, str(paper_version_id)),
            report_id=report_id,
            paper_id=occurrences[-1][1].paper_id,
            paper_version_id=paper_version_id,
            failed_stage=occurrences[-1][1].failed_stage,
            error_code=(
                occurrences[0][1].error_code
                if len({item.error_code for _day, item in occurrences}) == 1
                else "MULTIPLE_ITEM_FAILURES"
            ),
            retryable=all(item.retryable for _day, item in occurrences),
            error_detail=_concise(
                f"{len(occurrences)} daily failure occurrence(s): "
                + "; ".join(
                    f"{day}: {item.failed_stage.value}/{item.error_code}"
                    for day, item in occurrences
                ),
                1000,
            ),
            schema_version=1,
            created_at=max(item.created_at for _day, item in occurrences),
        )
        for paper_version_id, occurrences in sorted(
            failures_by_version.items(), key=lambda item: str(item[0])
        )
    )
    status = RunStatus.PARTIAL if failures else RunStatus.COMPLETE
    evidence_by_id = {item.id: item for detail in daily_reports for item in detail.evidence}
    usable_evidence_ids = {
        evidence_id
        for evidence_id, evidence in evidence_by_id.items()
        if evidence.verification_status is not VerificationStatus.REJECTED
    }
    paper_by_version = {
        item.paper_version_id: replace(
            item,
            evidence_ids=tuple(
                evidence_id
                for evidence_id in item.evidence_ids
                if evidence_id in usable_evidence_ids
            ),
        )
        for detail in daily_reports
        for item in detail.report.highlighted_papers
    }
    highlighted_papers = tuple(item for item in paper_by_version.values() if item.evidence_ids)[
        :MAX_REPORT_HIGHLIGHTED_PAPERS
    ]
    latest_daily_report = max(daily_reports, key=lambda item: item.report.logical_date)
    major_entities = tuple(
        sorted(
            latest_daily_report.report.major_entities,
            key=lambda item: (
                -item.distinct_paper_count,
                item.entity_type,
                item.label,
                str(item.graph_entity_id),
            ),
        )[:MAX_REPORT_ENTITY_HIGHLIGHTS]
    )
    comparison_by_id = {
        item.comparison_id: replace(
            item,
            evidence_ids=tuple(
                evidence_id
                for evidence_id in item.evidence_ids
                if evidence_id in usable_evidence_ids
            ),
        )
        for detail in daily_reports
        for item in detail.report.notable_comparisons
    }
    notable_comparisons = tuple(item for item in comparison_by_id.values() if item.evidence_ids)[
        :MAX_REPORT_COMPARISONS
    ]
    lineage_by_id = {
        item.lineage_snapshot_id: item
        for detail in daily_reports
        for item in detail.report.lineage_highlights
    }
    lineage_highlights = tuple(lineage_by_id.values())[:MAX_REPORT_HIGHLIGHTED_PAPERS]
    referenced_evidence_ids = tuple(
        dict.fromkeys(
            evidence_id
            for group in (
                *(item.evidence_ids for item in highlighted_papers),
                *(item.evidence_ids for item in notable_comparisons),
            )
            for evidence_id in group
        )
    )[:MAX_REPORT_EVIDENCE]
    if any(value not in evidence_by_id for value in referenced_evidence_ids):
        raise DomainInvariantError("periodic report references unavailable evidence")
    counts = ReportCounts(
        retrieved=sum(item.report.counts.retrieved for item in daily_reports),
        selected=sum(item.report.counts.selected for item in daily_reports),
        processed=sum(item.report.counts.processed for item in daily_reports),
        completed=sum(item.report.counts.completed for item in daily_reports),
        failed=sum(item.report.counts.failed for item in daily_reports),
    )
    graph_changes = source.graph_changes
    trends = tuple(sorted(source.trends, key=lambda item: (item.as_of_date, item.window.days)))
    missing_sections = tuple(
        dict.fromkeys(value for detail in daily_reports for value in detail.report.missing_sections)
    )
    repeated_failure_limitations = (
        (
            (
                "Repeated failures for the same paper version are aggregated with their daily "
                "occurrence dates."
            ),
        )
        if any(len(items) > 1 for items in failures_by_version.values())
        else ()
    )
    limitations = tuple(
        dict.fromkeys(
            (
                "This synthesis covers only sufficient persisted daily reports in the requested "
                "period; global corpus completeness is not claimed.",
                *repeated_failure_limitations,
                *(value for detail in daily_reports for value in detail.report.limitations),
            )
        )
    )
    request = ReportNarrativeRequest(
        report_type=source.report_type,
        period_start=source.period_start,
        period_end=source.period_end,
        status=status,
        counts=counts,
        highlighted_papers=highlighted_papers,
        major_entities=major_entities,
        notable_comparisons=notable_comparisons,
        graph_changes=graph_changes,
        trend_summaries=tuple(trend_summary(item) for item in trends),
        lineage_highlights=lineage_highlights,
        failures=failures,
        limitations=limitations,
        evidence=tuple(evidence_by_id[item] for item in referenced_evidence_ids),
        missing_sections=missing_sections,
    )
    return PeriodicReportAssemblyPlan(
        request=request,
        trend_snapshot_ids=tuple(item.id for item in trends),
    )


def trend_summary(snapshot: TrendSnapshot) -> str:
    prefix = (
        f"The {snapshot.window.days}-day window contains {snapshot.included_paper_count} papers"
    )
    if snapshot.data_sufficiency is TrendDataSufficiency.INSUFFICIENT:
        return prefix + "; trend interpretation is unavailable because data is insufficient."
    if snapshot.data_sufficiency is TrendDataSufficiency.LIMITED:
        return prefix + "; trend interpretation is limited by the small sample."
    change = snapshot.paper_count_change
    if change.growth_status is TrendGrowthStatus.ZERO_DENOMINATOR:
        return prefix + "; the preceding window is zero, so no percentage growth is reported."
    if change.growth_status is TrendGrowthStatus.LIMITED_SAMPLE:
        return prefix + "; percentage growth is withheld because the denominator is too small."
    return (
        prefix
        + f"; the absolute change is {change.absolute_change} against a denominator of "
        + f"{change.denominator_count}."
    )


def _lineage_summary(lineage: LineageSnapshot) -> str:
    if lineage.verified_predecessor_available:
        return (
            f"Available evidence suggests {len(lineage.nodes)} papers and "
            f"{len(lineage.edges)} relations in the currently retrieved lineage."
        )
    return (
        f"The currently retrieved lineage contains {len(lineage.nodes)} papers; "
        "no verified predecessor relation is currently available."
    )


def _missing_sections(
    *,
    ready_inputs: tuple[ProductPaperInput, ...],
    failed_count: int,
    lineages_by_root: dict[UUID, LineageSnapshot],
    omitted_entity_types: tuple[GraphEntityType, ...],
) -> tuple[str, ...]:
    values: list[str] = []
    if failed_count:
        values.append(
            f"ANALYSIS_UNAVAILABLE: {failed_count} selected paper"
            f"{'s' if failed_count != 1 else ''} published with metadata only."
        )
    related_work_unavailable = sum(item.related_work_available is False for item in ready_inputs)
    if related_work_unavailable:
        values.append(
            f"RELATED_WORK_UNAVAILABLE: {related_work_unavailable} analyzed paper"
            f"{'s' if related_work_unavailable != 1 else ''} have no usable related-work result."
        )
    comparison_unavailable = sum(not item.comparisons for item in ready_inputs)
    if comparison_unavailable:
        values.append(
            f"COMPARISON_UNAVAILABLE: {comparison_unavailable} analyzed paper"
            f"{'s' if comparison_unavailable != 1 else ''}; "
            "reason NO_COMPATIBLE_HISTORICAL_ANALYSIS."
        )
    missing_lineages = sum(item.paper_id not in lineages_by_root for item in ready_inputs)
    if missing_lineages:
        values.append(
            f"INSUFFICIENT_DATA: lineage is unavailable for {missing_lineages} analyzed paper"
            f"{'s' if missing_lineages != 1 else ''}."
        )
    if omitted_entity_types:
        labels = ", ".join(item.value for item in sorted(set(omitted_entity_types)))
        values.append(f"INSUFFICIENT_DATA: graph entities are unavailable for {labels}.")
    return tuple(values)


def _concise(value: str, maximum: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= maximum:
        return normalized
    return normalized[: maximum - 1].rstrip() + "…"
