from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from paper_harness.domain.analysis import ModelUsage, VerificationStatus
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.identity import stable_periodic_report_id
from paper_harness.domain.models import PaperStage, RunStatus
from paper_harness.domain.reports import (
    GeneratedReportNarrative,
    GeneratedReportSection,
    Report,
    ReportComparisonHighlight,
    ReportCounts,
    ReportEvidenceReference,
    ReportFailure,
    ReportGraphChanges,
    ReportNarrativeMode,
    ReportNarrativeRequest,
    ReportPaperHighlight,
    ReportSection,
    ReportSectionKind,
    ReportType,
    aggregate_report_eligible,
)

NOW = datetime(2026, 8, 10, 5, tzinfo=UTC)
REPORT_ID = UUID("6d6e3589-5055-43ea-9ac8-1fb35be7d970")
RUN_ID = UUID("8407582f-900d-4e82-8993-5aa372a33cb3")
TOPIC_ID = UUID("d5f3f010-5f08-4f25-bd82-d7d3a21fdce0")
PAPER_ID = UUID("3c09f4b2-dbb2-44e0-8904-8187113fa948")
VERSION_ID = UUID("1edbb50f-7757-4664-852f-ef5a1f217c0f")
EVIDENCE_ID = UUID("5630c88f-40e8-47fa-889d-6be1296d1720")


def _failure() -> ReportFailure:
    return ReportFailure(
        id=UUID("4e0ac932-e32c-43e7-879d-67052b06b330"),
        report_id=REPORT_ID,
        paper_id=PAPER_ID,
        paper_version_id=VERSION_ID,
        failed_stage=PaperStage.GRAPH_UPDATED,
        error_code="GRAPH_EXTRACTION_INVALID",
        retryable=False,
        error_detail="Persisted analysis could not produce a valid graph update.",
        schema_version=1,
        created_at=NOW,
    )


def _evidence() -> ReportEvidenceReference:
    return ReportEvidenceReference(
        id=EVIDENCE_ID,
        paper_id=PAPER_ID,
        paper_version_id=VERSION_ID,
        section="Abstract",
        excerpt="The agent uses a bounded planning method.",
        evidence_type="SUPPORTS",
        verification_status=VerificationStatus.UNVERIFIED,
    )


def _sections(*, evidence_ids: tuple[UUID, ...] = ()) -> tuple[ReportSection, ...]:
    return tuple(
        ReportSection(
            id=UUID(int=index + 100),
            report_id=REPORT_ID,
            kind=kind,
            narrative=f"Grounded {kind.value} section.",
            evidence_ids=evidence_ids if kind is ReportSectionKind.OVERVIEW else (),
            schema_version=1,
            created_at=NOW,
        )
        for index, kind in enumerate(ReportSectionKind)
    )


def test_report_counts_preserve_retrieval_and_terminal_item_accounting() -> None:
    assert ReportCounts(retrieved=8, selected=3, processed=3, completed=2, failed=1).failed == 1

    with pytest.raises(DomainInvariantError, match="completed plus failed"):
        ReportCounts(retrieved=8, selected=3, processed=3, completed=1, failed=1)
    with pytest.raises(DomainInvariantError, match="selected count"):
        ReportCounts(retrieved=1, selected=2, processed=0, completed=0, failed=0)


def test_partial_daily_report_requires_visible_item_failures() -> None:
    report = Report(
        id=REPORT_ID,
        run_id=RUN_ID,
        topic_id=TOPIC_ID,
        logical_date=date(2026, 8, 10),
        status=RunStatus.PARTIAL,
        title="Broad LLM agents daily report",
        summary="One selected paper was published and one graph update failed.",
        source="m4_product_publication",
        generated_at=NOW,
        schema_version=1,
        created_at=NOW,
        failures=(_failure(),),
        sections=_sections(),
        report_type=ReportType.DAILY,
        counts=ReportCounts(retrieved=2, selected=2, processed=2, completed=1, failed=1),
        missing_sections=("Lineage coverage is incomplete for the failed paper.",),
    )

    assert report.status is RunStatus.PARTIAL
    assert report.failures[0].error_code == "GRAPH_EXTRACTION_INVALID"

    with pytest.raises(DomainInvariantError, match="must list item failures"):
        Report(
            id=REPORT_ID,
            run_id=RUN_ID,
            topic_id=TOPIC_ID,
            logical_date=date(2026, 8, 10),
            status=RunStatus.PARTIAL,
            title="Broad LLM agents daily report",
            summary="Incomplete report.",
            source="m4_product_publication",
            generated_at=NOW,
            schema_version=1,
            created_at=NOW,
            sections=_sections(),
            report_type=ReportType.DAILY,
        )


def test_failed_state_cannot_be_persisted_as_a_report() -> None:
    with pytest.raises(DomainInvariantError, match="only complete or partial"):
        Report(
            id=REPORT_ID,
            run_id=RUN_ID,
            topic_id=TOPIC_ID,
            logical_date=date(2026, 8, 10),
            status=RunStatus.FAILED,
            title="Invalid empty report",
            summary="No selected paper completed.",
            source="m4_product_publication",
            generated_at=NOW,
            schema_version=1,
            created_at=NOW,
            sections=_sections(),
            report_type=ReportType.DAILY,
        )


@pytest.mark.parametrize(
    ("report_type", "run_id", "logical_date", "period_start", "period_end", "message"),
    [
        (
            ReportType.DAILY,
            RUN_ID,
            date(2026, 8, 10),
            date(2026, 8, 9),
            date(2026, 8, 10),
            "exactly their logical date",
        ),
        (
            ReportType.WEEKLY,
            None,
            date(2026, 8, 9),
            date(2026, 8, 4),
            date(2026, 8, 10),
            "Monday through Sunday",
        ),
        (
            ReportType.MONTHLY,
            None,
            date(2026, 8, 31),
            date(2026, 8, 2),
            date(2026, 8, 31),
            "calendar month",
        ),
    ],
)
def test_report_calendar_scope_is_a_domain_invariant(
    report_type: ReportType,
    run_id: UUID | None,
    logical_date: date,
    period_start: date,
    period_end: date,
    message: str,
) -> None:
    with pytest.raises(DomainInvariantError, match=message):
        Report(
            id=REPORT_ID,
            run_id=run_id,
            topic_id=TOPIC_ID,
            logical_date=logical_date,
            status=RunStatus.COMPLETE,
            title="Invalid report period",
            summary="The period is not canonical for its report type.",
            source="m4_product_publication",
            generated_at=NOW,
            schema_version=1,
            created_at=NOW,
            sections=_sections(),
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            counts=ReportCounts(
                retrieved=1,
                selected=1,
                processed=1,
                completed=1,
                failed=0,
            ),
        )


def test_report_narrative_input_rejects_unavailable_evidence() -> None:
    highlight = ReportPaperHighlight(
        paper_id=PAPER_ID,
        paper_version_id=VERSION_ID,
        title="Bounded Agent Planning",
        reason="The persisted analysis identifies a bounded planning method.",
        evidence_ids=(EVIDENCE_ID,),
    )
    request = ReportNarrativeRequest(
        report_type=ReportType.DAILY,
        period_start=date(2026, 8, 10),
        period_end=date(2026, 8, 10),
        status=RunStatus.COMPLETE,
        counts=ReportCounts(retrieved=1, selected=1, processed=1, completed=1, failed=0),
        highlighted_papers=(highlight,),
        major_entities=(),
        notable_comparisons=(),
        graph_changes=ReportGraphChanges(2, 1, 2, 0),
        trend_summaries=("The 7-day window contains one included paper.",),
        lineage_highlights=(),
        failures=(),
        limitations=("The snapshot covers only the currently persisted corpus.",),
        evidence=(_evidence(),),
    )
    assert request.evidence[0].id == EVIDENCE_ID

    with pytest.raises(DomainInvariantError, match="unavailable evidence"):
        ReportNarrativeRequest(
            report_type=request.report_type,
            period_start=request.period_start,
            period_end=request.period_end,
            status=request.status,
            counts=request.counts,
            highlighted_papers=(
                ReportPaperHighlight(
                    paper_id=PAPER_ID,
                    paper_version_id=VERSION_ID,
                    title="Bounded Agent Planning",
                    reason="The report refers to evidence outside its validated input.",
                    evidence_ids=(UUID("4141312d-47a3-4551-bb6b-e69647a270fd"),),
                ),
            ),
            major_entities=(),
            notable_comparisons=(),
            graph_changes=request.graph_changes,
            trend_summaries=request.trend_summaries,
            lineage_highlights=(),
            failures=(),
            limitations=request.limitations,
            evidence=request.evidence,
        )

    with pytest.raises(DomainInvariantError, match="rejected evidence"):
        replace(
            request,
            evidence=(
                replace(
                    request.evidence[0],
                    verification_status=VerificationStatus.REJECTED,
                ),
            ),
        )

    with pytest.raises(DomainInvariantError, match="highlighted paper version"):
        replace(
            request,
            evidence=(replace(_evidence(), paper_id=UUID(int=9001)),),
        )


def test_report_narrative_input_rejects_comparison_evidence_from_a_third_paper() -> None:
    target_paper_id = UUID(int=2001)
    target_version_id = UUID(int=2002)
    unrelated_evidence = replace(
        _evidence(),
        paper_id=UUID(int=3001),
        paper_version_id=UUID(int=3002),
    )

    with pytest.raises(DomainInvariantError, match="compared paper version"):
        ReportNarrativeRequest(
            report_type=ReportType.DAILY,
            period_start=date(2026, 8, 10),
            period_end=date(2026, 8, 10),
            status=RunStatus.COMPLETE,
            counts=ReportCounts(1, 1, 1, 1, 0),
            highlighted_papers=(),
            major_entities=(),
            notable_comparisons=(
                ReportComparisonHighlight(
                    comparison_id=UUID(int=2000),
                    source_paper_id=PAPER_ID,
                    source_paper_version_id=VERSION_ID,
                    target_paper_id=target_paper_id,
                    target_paper_version_id=target_version_id,
                    summary="A bounded comparison.",
                    comparability_status="PARTIALLY_COMPARABLE",
                    evidence_ids=(unrelated_evidence.id,),
                ),
            ),
            graph_changes=ReportGraphChanges(2, 1, 2, 0),
            trend_summaries=(),
            lineage_highlights=(),
            failures=(),
            limitations=("Currently retrieved corpus only.",),
            evidence=(unrelated_evidence,),
        )


def test_generated_narrative_accepts_partial_outline_but_requires_canonical_order() -> None:
    sections = tuple(
        GeneratedReportSection(
            kind=kind, narrative=f"Grounded {kind.value} section.", evidence_ids=()
        )
        for kind in ReportSectionKind
    )
    narrative = GeneratedReportNarrative(
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-08-01",
        prompt_version="m4-report-v1",
        generated_at=NOW,
        summary="A bounded synthesis over persisted product data.",
        sections=sections,
        usage=ModelUsage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            call_count=1,
            duration_ms=500,
            estimated_cost_usd=None,
        ),
    )
    assert tuple(section.kind for section in narrative.sections) == tuple(ReportSectionKind)

    partial = replace(narrative, sections=(sections[0], sections[-1]))
    assert tuple(section.kind for section in partial.sections) == (
        ReportSectionKind.OVERVIEW,
        ReportSectionKind.LIMITATIONS,
    )

    with pytest.raises(DomainInvariantError, match="unique and canonically ordered"):
        GeneratedReportNarrative(
            provider=narrative.provider,
            configured_model=narrative.configured_model,
            model_version=narrative.model_version,
            prompt_version=narrative.prompt_version,
            generated_at=NOW,
            summary=narrative.summary,
            sections=tuple(reversed(sections)),
            usage=narrative.usage,
        )


@pytest.mark.parametrize(
    ("report_type", "daily_dates", "paper_count", "eligible"),
    [
        (ReportType.WEEKLY, 7, 3, True),
        (ReportType.WEEKLY, 6, 20, False),
        (ReportType.MONTHLY, 20, 10, True),
        (ReportType.MONTHLY, 19, 100, False),
    ],
)
def test_aggregate_report_eligibility_is_explicit(
    report_type: ReportType, daily_dates: int, paper_count: int, eligible: bool
) -> None:
    assert (
        aggregate_report_eligible(
            report_type,
            distinct_daily_dates=daily_dates,
            included_paper_count=paper_count,
        )
        is eligible
    )


def test_structured_and_model_narrative_provenance_cannot_be_mixed() -> None:
    usage = ModelUsage(1, 1, 2, 1, 10, None)
    with pytest.raises(DomainInvariantError, match="structured-only"):
        Report(
            id=REPORT_ID,
            run_id=RUN_ID,
            topic_id=TOPIC_ID,
            logical_date=date(2026, 8, 10),
            status=RunStatus.COMPLETE,
            title="Broad LLM agents daily report",
            summary="Persisted structured facts only.",
            source="m4_product_publication",
            generated_at=NOW,
            schema_version=1,
            created_at=NOW,
            sections=_sections(),
            report_type=ReportType.DAILY,
            counts=ReportCounts(retrieved=1, selected=1, processed=1, completed=1, failed=0),
            narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
            provider="deepseek",
            configured_model="deepseek-v4-flash",
            model_version="DeepSeek-V4-Flash-2026-08-01",
            prompt_version="m4-report-v1",
            usage=usage,
        )


def test_periodic_report_identity_is_stable_and_scope_specific() -> None:
    weekly = stable_periodic_report_id(
        TOPIC_ID,
        ReportType.WEEKLY.value,
        date(2026, 8, 3),
        date(2026, 8, 9),
    )
    assert weekly == stable_periodic_report_id(
        TOPIC_ID,
        ReportType.WEEKLY.value,
        date(2026, 8, 3),
        date(2026, 8, 9),
    )
    assert weekly != stable_periodic_report_id(
        TOPIC_ID,
        ReportType.MONTHLY.value,
        date(2026, 8, 1),
        date(2026, 8, 31),
    )
