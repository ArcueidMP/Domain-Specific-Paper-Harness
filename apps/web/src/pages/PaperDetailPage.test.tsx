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

function installFixtures(options: { analysis?: Response; evidence?: Response } = {}) {
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
    expect(screen.getByText("p. 4")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /claim-memory-verification/ })).toHaveAttribute(
      "href",
      `#claim-${claimId}`,
    );
  });

  it("shows an honest no-analysis state and does not request evidence", async () => {
    const noAnalysis = jsonResponse(
      { detail: { code: "ANALYSIS_NOT_FOUND", message: "No analysis is available." } },
      404,
    );
    installFixtures({ analysis: noAnalysis });

    renderPage();

    expect(await screen.findByText("Analysis not available")).toBeInTheDocument();
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
    expect(screen.queryByText("Analysis not available")).not.toBeInTheDocument();
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
    expect(screen.queryByText("Analysis not available")).not.toBeInTheDocument();
  });
});
