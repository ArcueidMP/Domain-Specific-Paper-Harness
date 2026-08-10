from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from paper_harness.application.reporting import (
    assemble_product_report,
    build_structured_report_sections,
)
from paper_harness.domain.analysis import ModelUsage, VerificationStatus
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.models import PaperStage, RunStatus
from paper_harness.domain.reports import (
    GeneratedReportNarrative,
    GeneratedReportSection,
    ReportCounts,
    ReportEvidenceReference,
    ReportFailure,
    ReportGraphChanges,
    ReportNarrativeMode,
    ReportNarrativeRequest,
    ReportPaperHighlight,
    ReportSectionKind,
    ReportType,
)

NOW = datetime(2026, 8, 10, 5, tzinfo=UTC)
REPORT_ID = UUID("59a1e247-56ba-4e33-be47-781e44834c95")
RUN_ID = UUID("e372d72a-4f79-4823-a5ee-2db88af8bed3")
TOPIC_ID = UUID("81a2be6f-8e92-4b32-aafb-19e3672978d9")
PAPER_ID = UUID("19b6b97a-d17d-45b0-89f7-b2bd7eeb0ea7")
VERSION_ID = UUID("890a4b87-0ee8-420a-b46b-3bce6d2e2568")
EVIDENCE_ID = UUID("a48ef43b-2e03-4e02-915c-61ca98243712")


def _request(
    *,
    partial: bool = False,
    report_type: ReportType = ReportType.DAILY,
    missing_sections: tuple[str, ...] = (),
) -> ReportNarrativeRequest:
    period_start = date(2026, 8, 10)
    period_end = period_start
    if report_type is ReportType.WEEKLY:
        period_start = date(2026, 8, 3)
        period_end = date(2026, 8, 9)
    elif report_type is ReportType.MONTHLY:
        period_start = date(2026, 8, 1)
        period_end = date(2026, 8, 31)
    failure = ReportFailure(
        id=UUID("f609158a-8450-493b-95cb-02916d691d99"),
        report_id=REPORT_ID,
        paper_id=PAPER_ID,
        paper_version_id=VERSION_ID,
        failed_stage=PaperStage.GRAPH_UPDATED,
        error_code="GRAPH_EXTRACTION_INVALID",
        retryable=False,
        error_detail="The persisted analysis did not yield a valid graph bundle.",
        schema_version=1,
        created_at=NOW,
    )
    return ReportNarrativeRequest(
        report_type=report_type,
        period_start=period_start,
        period_end=period_end,
        status=RunStatus.PARTIAL if partial else RunStatus.COMPLETE,
        counts=ReportCounts(
            retrieved=2,
            selected=2 if partial else 1,
            processed=2 if partial else 1,
            completed=1,
            failed=1 if partial else 0,
        ),
        highlighted_papers=(
            ReportPaperHighlight(
                paper_id=PAPER_ID,
                paper_version_id=VERSION_ID,
                title="Bounded Agent Planning",
                reason="Persisted analysis describes bounded planning.",
                evidence_ids=(EVIDENCE_ID,),
            ),
        ),
        major_entities=(),
        notable_comparisons=(),
        graph_changes=ReportGraphChanges(3, 2, 1, 0),
        trend_summaries=(
            "The 7-day snapshot is insufficient for trend interpretation and contains one paper.",
        ),
        lineage_highlights=(),
        failures=(failure,) if partial else (),
        limitations=("The result covers only the currently retrieved corpus.",),
        evidence=(
            ReportEvidenceReference(
                id=EVIDENCE_ID,
                paper_id=PAPER_ID,
                paper_version_id=VERSION_ID,
                section="Abstract",
                excerpt="The agent uses a bounded planning method.",
                evidence_type="SUPPORTS",
                verification_status=VerificationStatus.UNVERIFIED,
            ),
        ),
        missing_sections=missing_sections,
    )


def test_structured_mode_is_an_explicit_grounded_outline_not_a_model_fallback() -> None:
    request = _request(
        partial=True,
        missing_sections=("Lineage coverage is unavailable for the failed paper.",),
    )
    sections = build_structured_report_sections(request)

    assert tuple(section.kind for section in sections) == tuple(ReportSectionKind)
    assert "1 completed publication; 1 failed" in sections[0].narrative
    assert sections[0].evidence_ids == (EVIDENCE_ID,)
    assert "GRAPH_UPDATED failed with GRAPH_EXTRACTION_INVALID" in sections[-1].narrative
    assert "Lineage coverage is unavailable" in sections[-1].narrative

    report = assemble_product_report(
        request,
        report_id=REPORT_ID,
        run_id=RUN_ID,
        topic_id=TOPIC_ID,
        logical_date=date(2026, 8, 10),
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        generated=None,
        trend_snapshot_ids=(UUID("90f8011c-9888-46f1-85bd-d4e294a40d67"),),
        created_at=NOW,
    )

    assert report.status is RunStatus.PARTIAL
    assert report.source == "m4_structured_report"
    assert report.failures[0].error_code == "GRAPH_EXTRACTION_INVALID"
    assert tuple(section.kind for section in report.sections) == tuple(ReportSectionKind)
    assert report.provider is None


def test_deepseek_mode_maps_only_prevalidated_model_provenance_and_sections() -> None:
    request = _request()
    generated = GeneratedReportNarrative(
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-08-01",
        prompt_version="m4-report-v1",
        generated_at=NOW,
        summary="One paper completed the bounded daily publication scope.",
        sections=tuple(
            GeneratedReportSection(
                kind=kind,
                narrative=f"Grounded {kind.value.lower()} narrative.",
                evidence_ids=(EVIDENCE_ID,) if kind is ReportSectionKind.OVERVIEW else (),
            )
            for kind in ReportSectionKind
        ),
        usage=ModelUsage(100, 30, 130, 1, 250, None),
    )

    report = assemble_product_report(
        request,
        report_id=REPORT_ID,
        run_id=RUN_ID,
        topic_id=TOPIC_ID,
        logical_date=date(2026, 8, 10),
        narrative_mode=ReportNarrativeMode.DEEPSEEK,
        generated=generated,
        trend_snapshot_ids=(),
        created_at=NOW,
    )

    assert report.source == "deepseek_chat_completions"
    assert report.provider == "deepseek"
    assert report.usage == generated.usage


def test_report_mode_never_switches_after_generation_failure() -> None:
    request = _request()
    with pytest.raises(DomainInvariantError, match="requires generated narrative"):
        assemble_product_report(
            request,
            report_id=REPORT_ID,
            run_id=RUN_ID,
            topic_id=TOPIC_ID,
            logical_date=date(2026, 8, 10),
            narrative_mode=ReportNarrativeMode.DEEPSEEK,
            generated=None,
            trend_snapshot_ids=(),
            created_at=NOW,
        )


def test_aggregate_report_is_not_owned_by_one_daily_run() -> None:
    request = _request(report_type=ReportType.WEEKLY)
    report = assemble_product_report(
        request,
        report_id=REPORT_ID,
        run_id=None,
        topic_id=TOPIC_ID,
        logical_date=date(2026, 8, 9),
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        generated=None,
        trend_snapshot_ids=(),
        created_at=NOW,
    )
    assert report.run_id is None
    assert report.report_type is ReportType.WEEKLY


def test_model_output_cannot_cite_evidence_outside_the_structured_input() -> None:
    request = _request()
    generated = GeneratedReportNarrative(
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-08-01",
        prompt_version="m4-report-v1",
        generated_at=NOW,
        summary="Bounded synthesis.",
        sections=tuple(
            GeneratedReportSection(
                kind=kind,
                narrative=f"Grounded {kind.value.lower()} narrative.",
                evidence_ids=(UUID("383f065f-7789-4dc6-95ca-0c7365439e4d"),)
                if kind is ReportSectionKind.OVERVIEW
                else (),
            )
            for kind in ReportSectionKind
        ),
        usage=ModelUsage(100, 30, 130, 1, 250, None),
    )

    with pytest.raises(DomainInvariantError, match="unavailable evidence"):
        assemble_product_report(
            request,
            report_id=REPORT_ID,
            run_id=RUN_ID,
            topic_id=TOPIC_ID,
            logical_date=date(2026, 8, 10),
            narrative_mode=ReportNarrativeMode.DEEPSEEK,
            generated=generated,
            trend_snapshot_ids=(),
            created_at=NOW,
        )


def test_model_output_cannot_move_known_evidence_to_an_unrelated_section() -> None:
    request = _request()
    generated = GeneratedReportNarrative(
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-08-01",
        prompt_version="m4-report-v1",
        generated_at=NOW,
        summary="Bounded synthesis.",
        sections=tuple(
            GeneratedReportSection(
                kind=kind,
                narrative=f"Grounded {kind.value.lower()} narrative.",
                evidence_ids=(EVIDENCE_ID,) if kind is ReportSectionKind.TRENDS else (),
            )
            for kind in ReportSectionKind
        ),
        usage=ModelUsage(100, 30, 130, 1, 250, None),
    )

    with pytest.raises(DomainInvariantError, match="semantic section"):
        assemble_product_report(
            request,
            report_id=REPORT_ID,
            run_id=RUN_ID,
            topic_id=TOPIC_ID,
            logical_date=date(2026, 8, 10),
            narrative_mode=ReportNarrativeMode.DEEPSEEK,
            generated=generated,
            trend_snapshot_ids=(),
            created_at=NOW,
        )
