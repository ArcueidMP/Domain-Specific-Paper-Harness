"""Boundary for strict structured paper analysis by the configured LLM."""

from __future__ import annotations

from typing import Protocol

from paper_harness.domain.analysis import AnalysisRequest, GeneratedAnalysis


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
