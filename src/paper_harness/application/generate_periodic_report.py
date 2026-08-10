"""Explicit sufficient-data weekly and monthly report generation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from paper_harness.application.report_inputs import build_periodic_report_plan
from paper_harness.application.reporting import (
    assemble_product_report,
    require_matching_narrative_mode,
)
from paper_harness.domain.identity import stable_periodic_report_id
from paper_harness.domain.models import TopicConfig
from paper_harness.domain.reports import (
    GeneratedReportNarrative,
    Report,
    ReportNarrativeMode,
    ReportNarrativeRequest,
    ReportType,
    aggregate_report_eligible,
)
from paper_harness.ports.llm import LLMPort
from paper_harness.ports.repository import RepositoryPort


class PeriodicReportInsufficientDataError(RuntimeError):
    error_code = "REPORT_DATA_INSUFFICIENT"
    retryable = False


class GeneratePeriodicReport:
    def __init__(
        self,
        *,
        repository: RepositoryPort,
        llm: LLMPort | None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._llm = llm
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        topic: TopicConfig,
        *,
        report_type: ReportType,
        period_start: date,
        period_end: date,
        narrative_mode: ReportNarrativeMode,
    ) -> Report:
        if report_type not in (ReportType.WEEKLY, ReportType.MONTHLY):
            raise ValueError("periodic generation supports only weekly or monthly reports")
        if period_start > period_end:
            raise ValueError("periodic report period is reversed")
        if report_type is ReportType.WEEKLY and (
            period_start.weekday() != 0 or period_end != period_start + timedelta(days=6)
        ):
            raise ValueError("weekly report periods must run from Monday through Sunday")
        if report_type is ReportType.MONTHLY:
            next_month = (
                date(period_start.year + 1, 1, 1)
                if period_start.month == 12
                else date(period_start.year, period_start.month + 1, 1)
            )
            if period_start.day != 1 or period_end != next_month - timedelta(days=1):
                raise ValueError("monthly report periods must cover one complete calendar month")
        if narrative_mode is ReportNarrativeMode.DEEPSEEK and self._llm is None:
            raise ValueError("DeepSeek narrative mode requires the configured LLM adapter")
        existing = self._repository.get_report(
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            topic_slug=topic.slug,
        )
        if existing is not None:
            require_matching_narrative_mode(
                existing.report.narrative_mode,
                narrative_mode,
            )
            return existing.report
        source = self._repository.get_periodic_report_input(
            topic.id,
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
        )
        if source is None:
            raise PeriodicReportInsufficientDataError(
                "weekly or monthly synthesis requires persisted daily reports"
            )
        if not aggregate_report_eligible(
            report_type,
            distinct_daily_dates=len(source.daily_reports),
            included_paper_count=len(source.included_paper_ids),
        ):
            raise PeriodicReportInsufficientDataError(
                "weekly or monthly synthesis requires the configured daily-report and paper "
                "coverage"
            )
        report_id = stable_periodic_report_id(
            topic.id,
            report_type.value,
            period_start,
            period_end,
        )
        plan = build_periodic_report_plan(source, report_id=report_id)
        generated = (
            None
            if narrative_mode is ReportNarrativeMode.STRUCTURED_ONLY
            else self._generate(plan.request)
        )
        report = assemble_product_report(
            plan.request,
            report_id=report_id,
            run_id=None,
            topic_id=topic.id,
            logical_date=period_end,
            narrative_mode=narrative_mode,
            generated=generated,
            trend_snapshot_ids=plan.trend_snapshot_ids,
            created_at=self._aware_now(),
        )
        return self._repository.persist_periodic_report(report)

    def _generate(self, request: ReportNarrativeRequest) -> GeneratedReportNarrative:
        if self._llm is None:
            raise AssertionError("DeepSeek mode was validated before report generation")
        return self._llm.generate_report(request)

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("periodic report clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


__all__ = ["GeneratePeriodicReport", "PeriodicReportInsufficientDataError"]
