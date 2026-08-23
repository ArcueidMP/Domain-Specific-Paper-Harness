import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { PaperDetailPage } from "./PaperDetailPage";
import { jsonResponse, renderWithProviders, requestPath } from "../test/render";

const paperId = "00511b3e-1303-4e03-b846-d29fd641942d";
const paperVersionId = "465c74ac-bdf8-42e2-8652-7fec30fce680";
const parsedPaperId = "703fc4bd-3ff6-4c83-b8eb-cddda2e346b4";
const analysisId = "8b28f2c7-f706-40e8-a0dc-696001298cab";
const claimId = "c234ea44-3a86-44ce-a334-ccf45b1da322";
const comparisonId = "638a6949-a4df-4ef9-b195-25309f576acd";

const paper = {
  id: paperId,
  canonical_arxiv_id: "2608.01234",
  title: "Planning with Verifiable Agent Memory",
  abstract: "A study of memory-grounded planning for language-model agents.",
  current_version: 2,
  first_submitted_at: "2026-08-01T00:00:00Z",
  latest_updated_at: "2026-08-07T03:00:00Z",
  primary_category: "cs.AI",
  categories: ["cs.AI", "cs.CL"],
  authors: ["Ada North", "Lin West"],
  pdf_url: "https://arxiv.org/pdf/2608.01234v2",
  schema_version: 1,
  created_at: "2026-08-08T00:00:00Z",
  versions: [
    {
      id: paperVersionId,
      paper_id: paperId,
      canonical_arxiv_id: "2608.01234",
      version: 2,
      title: "Planning with Verifiable Agent Memory",
      abstract: "A study of memory-grounded planning for language-model agents.",
      submitted_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-07T03:00:00Z",
      primary_category: "cs.AI",
      categories: ["cs.AI", "cs.CL"],
      authors: ["Ada North", "Lin West"],
      pdf_url: "https://arxiv.org/pdf/2608.01234v2",
      source_url: "https://arxiv.org/abs/2608.01234v2",
      schema_version: 1,
      created_at: "2026-08-08T00:00:00Z",
    },
  ],
  source_identities: [],
  topic_slugs: ["broad-llm-agents"],
};

const analysis = {
  id: analysisId,
  paper_id: paperId,
  paper_version_id: paperVersionId,
  arxiv_version: 2,
  analysis_scope: "FULL_TEXT",
  parsed_paper_id: parsedPaperId,
  parser_name: "grobid",
  parser_version: "0.9.0",
  summary: "The paper introduces verifiable memory for agent planning.",
  research_problem: "Agent plans can drift when memory is not checked against source state.",
  method_summary: "The method validates memory entries before they influence the next plan.",
  key_contributions: ["A verification layer for agent memory."],
  limitations: ["Evaluation covers a bounded set of planning tasks."],
  provider: "deepseek",
  configured_model: "deepseek-v4-flash",
  model_version: "deepseek-v4-flash-2026-08",
  prompt_version: "analysis-v1",
  generated_at: "2026-08-08T05:10:00Z",
  source: "selected-arxiv-full-text",
  verification_status: "UNVERIFIED",
  usage: {
    prompt_tokens: 1200,
    completion_tokens: 300,
    total_tokens: 1500,
    call_count: 1,
    duration_ms: 2400,
    estimated_cost_usd: "0.0012",
  },
  schema_version: 1,
  created_at: "2026-08-08T05:10:01Z",
  claims: [
    {
      id: claimId,
      analysis_id: analysisId,
      paper_id: paperId,
      paper_version_id: paperVersionId,
      key: "claim-memory-verification",
      claim_type: "CONTRIBUTION",
      text: "The verification layer rejects memory entries that lack source support.",
      provider: "deepseek",
      model_version: "deepseek-v4-flash-2026-08",
      prompt_version: "analysis-v1",
      generated_at: "2026-08-08T05:10:00Z",
      source: "selected-arxiv-full-text",
      verification_status: "UNVERIFIED",
      schema_version: 1,
      created_at: "2026-08-08T05:10:01Z",
    },
  ],
};

const evidence = {
  items: [
    {
      id: "a50b18f1-bf2c-4121-87ed-4c2d6b3d193b",
      analysis_id: analysisId,
      paper_id: paperId,
      paper_version_id: paperVersionId,
      key: "evidence-memory-verification",
      section: "3.2 Memory verification",
      passage_id: "passage-3-2-4",
      coordinates: [{ page: 4, x: 12.5, y: 33, width: 72, height: 8 }],
      excerpt: "Each memory entry is checked against its cited observation before planning.",
      evidence_type: "SUPPORTS",
      supported_claim_ids: [claimId],
      extraction_source: "grobid-tei",
      provider: "deepseek",
      model_version: "deepseek-v4-flash-2026-08",
      prompt_version: "analysis-v1",
      generated_at: "2026-08-08T05:10:00Z",
      verification_status: "UNVERIFIED",
      schema_version: 1,
      created_at: "2026-08-08T05:10:01Z",
    },
  ],
  total: 1,
};

const analysisClaim = analysis.claims[0]!;
const evidenceItem = evidence.items[0]!;

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/papers/:paperId" element={<PaperDetailPage />} />
    </Routes>,
    `/papers/${paperId}`,
  );
}

function installFixtures(
  options: { analysis?: Response; evidence?: Response; related?: Response } = {},
) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === `/api/v1/papers/${paperId}/analysis`) {
        return Promise.resolve(options.analysis ?? jsonResponse(analysis));
      }
      if (path === `/api/v1/papers/${paperId}/evidence`) {
        return Promise.resolve(options.evidence ?? jsonResponse(evidence));
      }
      if (path === `/api/v1/papers/${paperId}/related`) {
        return Promise.resolve(
          options.related ??
            jsonResponse({
              paper_id: paperId,
              related_work_status: "RELATED_WORK_UNAVAILABLE",
              related_work_reason: "NO_RELATED_WORK_RESULT",
              session: null,
              actions: [],
              items: [],
              comparisons: [],
              total: 0,
            }),
        );
      }
      if (path === `/api/v1/papers/${paperId}`) {
        return Promise.resolve(jsonResponse(paper));
      }
      return Promise.resolve(jsonResponse({ detail: "Not found" }, 404));
    }),
  );
}

function requestedUrl(pathSuffix: string): URL {
  const call = vi
    .mocked(globalThis.fetch)
    .mock.calls.find(([input]) => requestPath(input).endsWith(pathSuffix));
  if (call === undefined) {
    throw new Error(`Expected a request ending in ${pathSuffix}.`);
  }
  const input = call[0];
  return new URL(input instanceof Request ? input.url : input.toString(), "http://localhost");
}

describe("PaperDetailPage", () => {
  it("renders explicit analysis scope, model provenance, claims, and grounded evidence", async () => {
    installFixtures();

    renderPage();

    expect(screen.getByText("Loading paper details")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: paper.title })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Analysis of arXiv v2" })).toBeInTheDocument();
    expect(screen.getByText("full text", { selector: ".scope-badge" })).toBeInTheDocument();
    expect(screen.getByText("deepseek / deepseek-v4-flash")).toBeInTheDocument();
    expect(screen.getByText(parsedPaperId)).toBeInTheDocument();
    expect(screen.getByText("grobid / 0.9.0")).toBeInTheDocument();
    expect(screen.getByText(analysisClaim.text)).toBeInTheDocument();
    expect(await screen.findByText(evidenceItem.excerpt)).toBeInTheDocument();
    expect(requestedUrl("/analysis").searchParams.get("paper_version_id")).toBe(paperVersionId);
    const evidenceUrl = requestedUrl("/evidence");
    expect(evidenceUrl.searchParams.get("analysis_id")).toBe(analysisId);
    expect(evidenceUrl.searchParams.get("paper_version_id")).toBe(paperVersionId);
    expect(evidenceUrl.searchParams.get("scope")).toBe("FULL_TEXT");
    expect(requestedUrl("/related").searchParams.get("paper_version_id")).toBe(paperVersionId);
    expect(screen.getByText("p. 4")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /claim-memory-verification/ })).toHaveAttribute(
      "href",
      `#claim-${claimId}`,
    );
    expect(screen.getByRole("link", { name: "View paper graph" })).toHaveAttribute(
      "href",
      `/graph?paper_id=${paperId}&topic=broad-llm-agents`,
    );
    expect(screen.getByRole("link", { name: "View research lineage" })).toHaveAttribute(
      "href",
      `/lineages/${paperId}?topic=broad-llm-agents`,
    );
  });

  it("shows an honest no-analysis state and does not request evidence", async () => {
    const noAnalysis = jsonResponse(
      { detail: { code: "ANALYSIS_NOT_FOUND", message: "No analysis is available." } },
      404,
    );
    installFixtures({ analysis: noAnalysis });

    renderPage();

    expect(await screen.findByText("ANALYSIS_UNAVAILABLE")).toBeInTheDocument();
    expect(screen.getByText(/No alternate scope or model output has been substituted/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Evidence viewer" })).not.toBeInTheDocument();
    expect(
      vi
        .mocked(globalThis.fetch)
        .mock.calls.some(([input]) => requestPath(input).endsWith("/evidence")),
    ).toBe(false);
  });

  it("does not hide a paper-not-found race as an absent analysis", async () => {
    installFixtures({
      analysis: jsonResponse(
        { detail: { code: "PAPER_NOT_FOUND", message: "The paper was removed." } },
        404,
      ),
    });

    renderPage();

    expect(await screen.findByText("Unable to load the analysis")).toBeInTheDocument();
    expect(screen.getByText(/The paper was removed/)).toBeInTheDocument();
    expect(screen.queryByText("ANALYSIS_UNAVAILABLE")).not.toBeInTheDocument();
  });

  it("keeps the analysis visible when no evidence records exist", async () => {
    installFixtures({ evidence: jsonResponse({ items: [], total: 0 }) });

    renderPage();

    expect(await screen.findByRole("heading", { name: "Analysis of arXiv v2" })).toBeInTheDocument();
    expect(await screen.findByText("No evidence records available")).toBeInTheDocument();
    expect(screen.getByText(/Claims are not presented as grounded/)).toBeInTheDocument();
  });

  it("shows structured FastAPI error messages without replacing them with empty data", async () => {
    installFixtures({
      analysis: jsonResponse(
        { detail: { code: "DATABASE_UNAVAILABLE", message: "Analysis storage is unavailable." } },
        503,
      ),
    });

    renderPage();

    expect(await screen.findByText("Unable to load the analysis")).toBeInTheDocument();
    expect(screen.getByText(/Analysis storage is unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByText("ANALYSIS_UNAVAILABLE")).not.toBeInTheDocument();
  });

  it("shows bounded related-work decisions, component scores, and comparison links", async () => {
    installFixtures({
      related: jsonResponse({
        paper_id: paperId,
        related_work_status: "AVAILABLE",
        related_work_reason: null,
        session: {
          id: "741e66ad-c55f-4b15-a847-0fd81e13a87a",
          topic_id: "155d96bf-c1d8-4f1f-a33b-4369390b63a5",
          source_paper_id: paperId,
          source_paper_version_id: paperVersionId,
          source_analysis_id: "8b28f2c7-f706-40e8-a0dc-696001298cab",
          source_analysis_scope: "FULL_TEXT",
          requested_year_from: 2025,
          effective_year_to: 2026,
          objective: "Find historical work on verifiable agent memory.",
          crawler_queries: ["verifiable agent memory", "agent memory benchmarks"],
          crawler_use_recommendations: true,
          crawler_expand_references: true,
          crawler_expand_citations: false,
          crawler_decision_reason: "Use bounded search and reference expansion.",
          crawler_generated_at: "2026-08-08T05:11:05Z",
          status: "COMPLETE",
          limits: {
            max_steps: 8,
            max_queries: 3,
            max_queue_size: 50,
            max_citation_depth: 2,
            max_candidates: 20,
            max_selected_candidates: 5,
            per_operation_timeout_seconds: 30,
            overall_timeout_seconds: 180,
          },
          started_at: "2026-08-08T05:11:00Z",
          completed_at: "2026-08-08T05:12:00Z",
          stop_reason: "QUEUE_EXHAUSTED",
          error_code: null,
          error_detail: null,
          provider: "deepseek",
          configured_model: "deepseek-v4-flash",
          model_version: "deepseek-v4-flash-2026-08",
          prompt_version: "m3-crawler-v1+m3-selector-v1",
          usage: {
            prompt_tokens: 100,
            completion_tokens: 20,
            total_tokens: 120,
            call_count: 2,
            duration_ms: 400,
            estimated_cost_usd: null,
          },
          schema_version: 1,
          created_at: "2026-08-08T05:11:00Z",
        },
        actions: [
          {
            id: "48eb7e28-ecea-431d-a4b1-1b95129e4893",
            session_id: "741e66ad-c55f-4b15-a847-0fd81e13a87a",
            step: 1,
            tool: "search_papers",
            status: "COMPLETED",
            query: "verifiable agent memory",
            target_semantic_scholar_id: null,
            target_arxiv_id: null,
            positive_paper_ids: [],
            year_from: 2025,
            year_to: 2026,
            requested_limit: 10,
            result_count: 1,
            relation_depth: 0,
            decision_reason: "Initial bounded scholarly query.",
            error_code: null,
            retryable: null,
            error_detail: null,
            duration_ms: 120,
            created_at: "2026-08-08T05:11:00Z",
            completed_at: "2026-08-08T05:11:01Z",
            schema_version: 1,
          },
        ],
        items: [
          {
            candidate: {
              id: "23f30c47-1f68-48c1-af4b-d88504f638ed",
              session_id: "741e66ad-c55f-4b15-a847-0fd81e13a87a",
              external_paper_id: "e004da65-339d-43ea-a490-66c2447b4089",
              semantic_scholar_id: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
              local_paper_id: "b431af71-5ea9-4903-a3ff-f611bdc50f32",
              local_paper_version_id: "55e69ff3-643b-4699-9699-235b29bc71a1",
              discovered_by_action_id: "48eb7e28-ecea-431d-a4b1-1b95129e4893",
              origins: ["SEARCH", "LOCAL_VECTOR"],
              relation_depth: 0,
              scores: {
                semantic_scholar: 0.8,
                lexical: 0.7,
                vector: 0.9,
                entity_overlap: 0.6,
                citation: 0.4,
                recommendation: 0.2,
                final: 0.78,
              },
              rank: 1,
              decision: "SELECTED",
              decision_reason: "High semantic overlap and matching evaluation task.",
              provider: "deepseek",
              configured_model: "deepseek-v4-flash",
              model_version: "deepseek-v4-flash-2026-08",
              prompt_version: "m3-selector-v1",
              generated_at: "2026-08-08T05:12:00Z",
              verification_status: "UNVERIFIED",
              schema_version: 1,
              created_at: "2026-08-08T05:11:00Z",
            },
            paper: {
              id: "e004da65-339d-43ea-a490-66c2447b4089",
              semantic_scholar_id: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
              title: "Historical Memory Checks for Tool-Using Agents",
              abstract: "A historical evaluation of memory validation in language-model agents.",
              year: 2025,
              publication_date: "2025-09-01",
              venue: "AgentBench Workshop",
              authors: ["Grace Scientist"],
              external_ids: { ArXiv: "2509.00001", DOI: "10.1000/agent.1" },
              arxiv_id: "2509.00001",
              doi: "10.1000/agent.1",
              citation_count: 12,
              influential_citation_count: 3,
              full_text_available: true,
              source: "semantic_scholar",
              schema_version: 1,
              created_at: "2026-08-08T05:11:00Z",
              updated_at: "2026-08-08T05:12:00Z",
            },
            discoveries: [
              {
                id: "57ba2a03-c303-455e-8978-0ee86df4a780",
                candidate_id: "23f30c47-1f68-48c1-af4b-d88504f638ed",
                action_id: "48eb7e28-ecea-431d-a4b1-1b95129e4893",
                origin: "SEARCH",
                relation_depth: 0,
                discovered_at: "2026-08-08T05:11:01Z",
              },
            ],
            relations: [
              {
                id: "ee9a1044-fb97-4df6-b959-1b6a507ac558",
                source_paper_id: paperId,
                source_paper_version_id: paperVersionId,
                target_paper_id: "b431af71-5ea9-4903-a3ff-f611bdc50f32",
                target_paper_version_id: "55e69ff3-643b-4699-9699-235b29bc71a1",
                relation_type: "EXTENDS",
                provenance: "LLM_INFERRED",
                evidence_ids: ["aaec48b6-0ce0-43f1-95e7-1954129d79ca"],
                justification: "The new method extends the historical memory check.",
                provider: "deepseek",
                model_version: "deepseek-v4-flash-2026-08",
                prompt_version: "m3-comparison-v1",
                confidence: 0.72,
                verification_status: "UNVERIFIED",
                generated_at: "2026-08-08T05:12:00Z",
                schema_version: 1,
                created_at: "2026-08-08T05:12:00Z",
              },
            ],
            comparison_id: comparisonId,
            comparison_status: "LIMITED_COMPARABILITY",
            comparison_reason: "The evaluation scopes differ.",
          },
        ],
        comparisons: [],
        total: 1,
      }),
    });

    renderPage();

    expect(await screen.findByRole("heading", { name: "Related work" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Historical Memory Checks for Tool-Using Agents" }),
    ).toBeInTheDocument();
    expect(screen.getByText("queue exhausted")).toBeInTheDocument();
    expect(screen.getByText("Use bounded search and reference expansion.")).toBeInTheDocument();
    expect(screen.getAllByText("verifiable agent memory")).toHaveLength(2);
    expect(screen.getByText("agent memory benchmarks")).toBeInTheDocument();
    expect(
      screen.getByText(/Recommendations enabled; references enabled; citations disabled/),
    ).toBeInTheDocument();
    expect(screen.getByText("78%")).toBeInTheDocument();
    expect(screen.getByText("High semantic overlap and matching evaluation task.")).toBeInTheDocument();
    expect(screen.getByText(/AI-guided selector/)).toBeInTheDocument();
    expect(screen.getByText(/AI-inferred/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Step 1: search papers/ })).toHaveAttribute(
      "href",
      "#search-action-48eb7e28-ecea-431d-a4b1-1b95129e4893",
    );
    expect(screen.getByText(/Depth 0 · discovered/)).toBeInTheDocument();
    expect(screen.getByText(/uncalibrated model-assessed evidential confidence/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open structured comparison" })).toHaveAttribute(
      "href",
      `/comparisons/${comparisonId}?topic=broad-llm-agents`,
    );
  });

  it("keeps an existing paper distinct from a missing related-work session", async () => {
    installFixtures();

    renderPage();

    expect(await screen.findByText("RELATED_WORK_UNAVAILABLE")).toBeInTheDocument();
    expect(screen.getByText(/No alternate provider or synthetic recommendations/)).toBeInTheDocument();
  });

  it("shows related-work storage failures as errors rather than empty data", async () => {
    installFixtures({
      related: jsonResponse(
        {
          detail: {
            code: "DATABASE_UNAVAILABLE",
            message: "Related-work storage is unavailable.",
          },
        },
        503,
      ),
    });

    renderPage();

    expect(await screen.findByText("Unable to load related work")).toBeInTheDocument();
    expect(screen.getByText(/Related-work storage is unavailable/)).toBeInTheDocument();
    expect(screen.queryByText("RELATED_WORK_UNAVAILABLE")).not.toBeInTheDocument();
  });
});
