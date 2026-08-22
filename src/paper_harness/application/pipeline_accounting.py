"""Invocation-local accounting wrappers for the complete Daily pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from paper_harness.domain.analysis import (
    AnalysisRequest,
    GeneratedAnalysis,
    ModelUsage,
    ParsedPaper,
)
from paper_harness.domain.historical import (
    CandidateSelectionRequest,
    ComparisonRequest,
    CrawlerPlanRequest,
    GeneratedCandidateSelection,
    GeneratedComparison,
    GeneratedCrawlerPlan,
)
from paper_harness.domain.reports import GeneratedReportNarrative, ReportNarrativeRequest
from paper_harness.ports.arxiv import ArxivPaperRecord, ArxivPdf, ArxivPort
from paper_harness.ports.llm import LLMPort
from paper_harness.ports.pdf_parser import PdfParseRequest, PdfParserPort
from paper_harness.ports.scholarly_search import ScholarlyPaper, ScholarlySearchPort


@dataclass(frozen=True, slots=True)
class PipelineAccountingSnapshot:
    arxiv_operation_count: int
    semantic_scholar_operation_count: int
    grobid_api_call_count: int
    model_api_call_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model_duration_ms: int
    estimated_cost_usd: Decimal | None

    def __post_init__(self) -> None:
        if (
            min(
                self.arxiv_operation_count,
                self.semantic_scholar_operation_count,
                self.grobid_api_call_count,
                self.model_api_call_count,
                self.prompt_tokens,
                self.completion_tokens,
                self.total_tokens,
                self.model_duration_ms,
            )
            < 0
        ):
            raise ValueError("pipeline accounting values cannot be negative")
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("pipeline token accounting is inconsistent")
        if self.estimated_cost_usd is not None and self.estimated_cost_usd < 0:
            raise ValueError("pipeline estimated cost cannot be negative")

    @property
    def external_call_count_lower_bound(self) -> int:
        """Count known calls; provider-internal retries may make the real count higher."""

        return (
            self.arxiv_operation_count
            + self.semantic_scholar_operation_count
            + self.grobid_api_call_count
            + self.model_api_call_count
        )


class PipelineAccounting:
    def __init__(self) -> None:
        self._arxiv_operation_count = 0
        self._semantic_scholar_operation_count = 0
        self._grobid_api_call_count = 0
        self._model_api_call_count = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._model_duration_ms = 0
        self._estimated_cost_usd = Decimal(0)
        self._unknown_cost_events = 0

    def record_arxiv_operation(self) -> None:
        self._arxiv_operation_count += 1

    def record_scholarly_operation(self) -> None:
        self._semantic_scholar_operation_count += 1

    def begin_grobid_operation(self) -> None:
        self._grobid_api_call_count += 1

    def complete_grobid_operation(self, call_count: int) -> None:
        if call_count < 1:
            raise ValueError("successful GROBID output requires a positive call count")
        self._grobid_api_call_count += call_count - 1

    def begin_model_operation(self) -> None:
        self._model_api_call_count += 1
        self._unknown_cost_events += 1

    def complete_model_operation(self, usage: ModelUsage) -> None:
        self._model_api_call_count += usage.call_count - 1
        self._prompt_tokens += usage.prompt_tokens
        self._completion_tokens += usage.completion_tokens
        self._model_duration_ms += usage.duration_ms
        if usage.estimated_cost_usd is not None:
            self._estimated_cost_usd += usage.estimated_cost_usd
            self._unknown_cost_events -= 1

    def snapshot(self) -> PipelineAccountingSnapshot:
        return PipelineAccountingSnapshot(
            arxiv_operation_count=self._arxiv_operation_count,
            semantic_scholar_operation_count=self._semantic_scholar_operation_count,
            grobid_api_call_count=self._grobid_api_call_count,
            model_api_call_count=self._model_api_call_count,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            total_tokens=self._prompt_tokens + self._completion_tokens,
            model_duration_ms=self._model_duration_ms,
            estimated_cost_usd=(None if self._unknown_cost_events else self._estimated_cost_usd),
        )


class AccountingArxiv:
    def __init__(self, delegate: ArxivPort, accounting: PipelineAccounting) -> None:
        self._delegate = delegate
        self._accounting = accounting

    def search(
        self,
        *,
        query: str,
        updated_from: datetime,
        updated_until: datetime,
        max_results: int,
    ) -> tuple[ArxivPaperRecord, ...]:
        self._accounting.record_arxiv_operation()
        return self._delegate.search(
            query=query,
            updated_from=updated_from,
            updated_until=updated_until,
            max_results=max_results,
        )

    def get_papers_by_ids(
        self,
        *,
        canonical_arxiv_ids: tuple[str, ...],
    ) -> tuple[ArxivPaperRecord, ...]:
        self._accounting.record_arxiv_operation()
        return self._delegate.get_papers_by_ids(canonical_arxiv_ids=canonical_arxiv_ids)

    def download_pdf(
        self,
        *,
        canonical_arxiv_id: str,
        version: int,
        pdf_url: str,
    ) -> ArxivPdf:
        self._accounting.record_arxiv_operation()
        return self._delegate.download_pdf(
            canonical_arxiv_id=canonical_arxiv_id,
            version=version,
            pdf_url=pdf_url,
        )


class AccountingPdfParser:
    def __init__(self, delegate: PdfParserPort, accounting: PipelineAccounting) -> None:
        self._delegate = delegate
        self._accounting = accounting

    def parse(self, request: PdfParseRequest) -> ParsedPaper:
        self._accounting.begin_grobid_operation()
        parsed = self._delegate.parse(request)
        self._accounting.complete_grobid_operation(parsed.call_count)
        return parsed


class AccountingLLM:
    def __init__(self, delegate: LLMPort, accounting: PipelineAccounting) -> None:
        self._delegate = delegate
        self._accounting = accounting

    def analyze(self, request: AnalysisRequest) -> GeneratedAnalysis:
        self._accounting.begin_model_operation()
        generated = self._delegate.analyze(request)
        self._accounting.complete_model_operation(generated.usage)
        return generated

    def plan_scholarly_search(
        self,
        request: CrawlerPlanRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> GeneratedCrawlerPlan:
        self._accounting.begin_model_operation()
        generated = self._delegate.plan_scholarly_search(
            request,
            timeout_seconds=timeout_seconds,
        )
        self._accounting.complete_model_operation(generated.usage)
        return generated

    def select_prior_work(
        self,
        request: CandidateSelectionRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> GeneratedCandidateSelection:
        self._accounting.begin_model_operation()
        generated = self._delegate.select_prior_work(
            request,
            timeout_seconds=timeout_seconds,
        )
        self._accounting.complete_model_operation(generated.usage)
        return generated

    def compare_papers(self, request: ComparisonRequest) -> GeneratedComparison:
        self._accounting.begin_model_operation()
        generated = self._delegate.compare_papers(request)
        self._accounting.complete_model_operation(generated.usage)
        return generated

    def generate_report(self, request: ReportNarrativeRequest) -> GeneratedReportNarrative:
        self._accounting.begin_model_operation()
        generated = self._delegate.generate_report(request)
        self._accounting.complete_model_operation(generated.usage)
        return generated


class AccountingScholarlySearch:
    def __init__(
        self,
        delegate: ScholarlySearchPort,
        accounting: PipelineAccounting,
    ) -> None:
        self._delegate = delegate
        self._accounting = accounting

    def search_papers(
        self,
        query: str,
        year_from: int,
        year_to: int,
        limit: int,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[ScholarlyPaper, ...]:
        self._accounting.record_scholarly_operation()
        return self._delegate.search_papers(
            query,
            year_from,
            year_to,
            limit,
            timeout_seconds=timeout_seconds,
        )

    def get_paper(
        self,
        semantic_scholar_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ScholarlyPaper:
        self._accounting.record_scholarly_operation()
        return self._delegate.get_paper(
            semantic_scholar_id,
            timeout_seconds=timeout_seconds,
        )

    def get_paper_by_arxiv_id(
        self,
        canonical_arxiv_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ScholarlyPaper:
        self._accounting.record_scholarly_operation()
        return self._delegate.get_paper_by_arxiv_id(
            canonical_arxiv_id,
            timeout_seconds=timeout_seconds,
        )

    def get_references(
        self,
        semantic_scholar_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[ScholarlyPaper, ...]:
        self._accounting.record_scholarly_operation()
        return self._delegate.get_references(
            semantic_scholar_id,
            timeout_seconds=timeout_seconds,
        )

    def get_citations(
        self,
        semantic_scholar_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[ScholarlyPaper, ...]:
        self._accounting.record_scholarly_operation()
        return self._delegate.get_citations(
            semantic_scholar_id,
            timeout_seconds=timeout_seconds,
        )

    def get_recommendations(
        self,
        positive_paper_ids: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[ScholarlyPaper, ...]:
        self._accounting.record_scholarly_operation()
        return self._delegate.get_recommendations(
            positive_paper_ids,
            timeout_seconds=timeout_seconds,
        )
