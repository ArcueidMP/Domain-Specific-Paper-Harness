"""Boundary for strict structured paper analysis by the configured LLM."""

from __future__ import annotations

from typing import Protocol

from paper_harness.domain.analysis import AnalysisRequest, GeneratedAnalysis
from paper_harness.domain.historical import (
    CandidateSelectionRequest,
    ComparisonRequest,
    CrawlerPlanRequest,
    GeneratedCandidateSelection,
    GeneratedComparison,
    GeneratedCrawlerPlan,
)
from paper_harness.domain.reports import GeneratedReportNarrative, ReportNarrativeRequest


class LLMPortError(RuntimeError):
    error_code = "LLM_FAILURE"
    retryable = False


class LLMConfigurationError(LLMPortError):
    error_code = "LLM_CONFIGURATION_INVALID"


class LLMAuthenticationError(LLMPortError):
    error_code = "LLM_AUTHENTICATION_FAILED"


class LLMRequestError(LLMPortError):
    error_code = "LLM_REQUEST_INVALID"


class LLMUnavailableError(LLMPortError):
    error_code = "LLM_UNAVAILABLE"
    retryable = True


class LLMOutputError(LLMPortError):
    error_code = "LLM_OUTPUT_INVALID"


class LLMPort(Protocol):
    def analyze(self, request: AnalysisRequest) -> GeneratedAnalysis: ...

    def plan_scholarly_search(
        self,
        request: CrawlerPlanRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> GeneratedCrawlerPlan: ...

    def select_prior_work(
        self,
        request: CandidateSelectionRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> GeneratedCandidateSelection: ...

    def compare_papers(self, request: ComparisonRequest) -> GeneratedComparison: ...

    def generate_report(self, request: ReportNarrativeRequest) -> GeneratedReportNarrative: ...
