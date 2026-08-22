"""One explicit wall-clock contract for the bounded Daily pipeline."""

from __future__ import annotations

from dataclasses import dataclass

ARXIV_OPERATION_SECONDS = 90
PDF_PARSER_OPERATION_SECONDS = 180
LLM_OPERATION_SECONDS = 300
PIPELINE_SAFETY_SECONDS = 900
DEFAULT_PIPELINE_TIMEOUT_SECONDS = 28_800


@dataclass(frozen=True, slots=True)
class PipelineBudget:
    timeout_seconds: int
    required_worst_case_seconds: int


def pipeline_budget(
    *,
    timeout_seconds: int,
    selected_papers: int,
    search_timeout_seconds: float,
    comparisons_per_paper: int,
    backfill_timeout_seconds: float,
) -> PipelineBudget:
    if timeout_seconds < 1 or timeout_seconds > 86_400:
        raise ValueError("pipeline timeout must be between 1 and 86400 seconds")
    if selected_papers < 1 or comparisons_per_paper < 1:
        raise ValueError("pipeline paper and comparison bounds must be positive")
    if min(search_timeout_seconds, backfill_timeout_seconds) < 1:
        raise ValueError("pipeline dependency timeouts must be positive")

    analysis_seconds = (
        ARXIV_OPERATION_SECONDS + PDF_PARSER_OPERATION_SECONDS + LLM_OPERATION_SECONDS
    )
    required = round(
        # Current-paper and historical-paper analysis each consume the full bound.
        (2 * selected_papers * analysis_seconds)
        # Discovery plus exact historical arXiv materialization.
        + (2 * ARXIV_OPERATION_SECONDS)
        + backfill_timeout_seconds
        + (selected_papers * search_timeout_seconds)
        + (selected_papers * comparisons_per_paper * LLM_OPERATION_SECONDS)
        + LLM_OPERATION_SECONDS
        + PIPELINE_SAFETY_SECONDS
    )
    return PipelineBudget(
        timeout_seconds=timeout_seconds,
        required_worst_case_seconds=required,
    )
