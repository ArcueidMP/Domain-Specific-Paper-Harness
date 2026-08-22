from __future__ import annotations

from decimal import Decimal

from paper_harness.application.pipeline_accounting import PipelineAccounting
from paper_harness.domain.analysis import ModelUsage


def _usage(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    call_count: int,
    cost: Decimal | None,
) -> ModelUsage:
    return ModelUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        call_count=call_count,
        duration_ms=125,
        estimated_cost_usd=cost,
    )


def test_pipeline_accounting_aggregates_known_calls_tokens_duration_and_cost() -> None:
    accounting = PipelineAccounting()
    accounting.record_arxiv_operation()
    accounting.record_scholarly_operation()
    accounting.begin_grobid_operation()
    accounting.complete_grobid_operation(2)
    accounting.begin_model_operation()
    accounting.complete_model_operation(
        _usage(
            prompt_tokens=100,
            completion_tokens=25,
            call_count=2,
            cost=Decimal("0.0012"),
        )
    )
    accounting.begin_model_operation()
    accounting.complete_model_operation(
        _usage(
            prompt_tokens=80,
            completion_tokens=20,
            call_count=1,
            cost=Decimal("0.0008"),
        )
    )

    snapshot = accounting.snapshot()

    assert snapshot.arxiv_operation_count == 1
    assert snapshot.semantic_scholar_operation_count == 1
    assert snapshot.grobid_api_call_count == 2
    assert snapshot.model_api_call_count == 3
    assert snapshot.prompt_tokens == 180
    assert snapshot.completion_tokens == 45
    assert snapshot.total_tokens == 225
    assert snapshot.model_duration_ms == 250
    assert snapshot.estimated_cost_usd == Decimal("0.0020")
    assert snapshot.external_call_count_lower_bound == 7


def test_pipeline_accounting_marks_cost_unknown_after_unpriced_or_failed_model_call() -> None:
    accounting = PipelineAccounting()
    accounting.begin_model_operation()
    accounting.complete_model_operation(
        _usage(
            prompt_tokens=10,
            completion_tokens=5,
            call_count=1,
            cost=None,
        )
    )
    accounting.begin_model_operation()

    snapshot = accounting.snapshot()

    assert snapshot.model_api_call_count == 2
    assert snapshot.total_tokens == 15
    assert snapshot.estimated_cost_usd is None
