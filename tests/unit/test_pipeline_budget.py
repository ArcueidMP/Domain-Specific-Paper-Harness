from __future__ import annotations

from paper_harness.application.pipeline_budget import (
    DEFAULT_PIPELINE_TIMEOUT_SECONDS,
    pipeline_budget,
)


def test_default_pipeline_timeout_exceeds_the_declared_worst_case() -> None:
    budget = pipeline_budget(
        timeout_seconds=DEFAULT_PIPELINE_TIMEOUT_SECONDS,
        selected_papers=10,
        search_timeout_seconds=300,
        comparisons_per_paper=3,
        backfill_timeout_seconds=1800,
    )

    assert budget.required_worst_case_seconds == 26_580
    assert budget.timeout_seconds == 28_800
    assert budget.timeout_seconds > budget.required_worst_case_seconds


def test_pipeline_accepts_a_bounded_timeout_below_the_additive_diagnostic() -> None:
    budget = pipeline_budget(
        timeout_seconds=7200,
        selected_papers=10,
        search_timeout_seconds=300,
        comparisons_per_paper=3,
        backfill_timeout_seconds=1800,
    )

    assert budget.timeout_seconds == 7200
    assert budget.required_worst_case_seconds == 26_580


def test_deployment_smoke_timeout_exceeds_its_shared_backfill_budget() -> None:
    budget = pipeline_budget(
        timeout_seconds=7200,
        selected_papers=2,
        search_timeout_seconds=180,
        comparisons_per_paper=1,
        # Smoke reuses the NORMAL backfill contract because that run is keyed
        # globally by topic and weekly window rather than pipeline execution.
        backfill_timeout_seconds=1800,
    )

    assert budget.required_worst_case_seconds == 6420
    assert budget.timeout_seconds > budget.required_worst_case_seconds


def test_production_smoke_timeout_covers_the_same_shared_backfill_contract() -> None:
    budget = pipeline_budget(
        timeout_seconds=7200,
        selected_papers=1,
        search_timeout_seconds=300,
        comparisons_per_paper=3,
        backfill_timeout_seconds=1800,
    )

    assert budget.required_worst_case_seconds == 5520
    assert budget.timeout_seconds > budget.required_worst_case_seconds
