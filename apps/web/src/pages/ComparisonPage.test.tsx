import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithProviders, requestPath } from "../test/render";
import { ComparisonPage } from "./ComparisonPage";

const comparisonId = "638a6949-a4df-4ef9-b195-25309f576acd";
const sourcePaperId = "00511b3e-1303-4e03-b846-d29fd641942d";
const targetPaperId = "b431af71-5ea9-4903-a3ff-f611bdc50f32";
const sourceEvidenceId = "a50b18f1-bf2c-4121-87ed-4c2d6b3d193b";
const targetEvidenceId = "aaec48b6-0ce0-43f1-95e7-1954129d79ca";

const dimensionNames = [
  "RESEARCH_PROBLEM",
  "TASK",
  "METHOD",
  "ARCHITECTURE",
  "DATASETS",
  "BENCHMARKS",
  "BASELINES",
  "METRICS",
  "REPORTED_RESULTS",
  "COMPUTE_OR_INFERENCE_BUDGET",
  "CLAIMED_NOVELTY",
  "LIMITATIONS",
  "CODE_AVAILABILITY",
  "RESULT_COMPARABILITY",
];

const comparison = {
  id: comparisonId,
  search_session_id: "741e66ad-c55f-4b15-a847-0fd81e13a87a",
  source_paper_id: sourcePaperId,
  source_paper_version_id: "465c74ac-bdf8-42e2-8652-7fec30fce680",
  source_analysis_id: "8b28f2c7-f706-40e8-a0dc-696001298cab",
  source_analysis_scope: "FULL_TEXT",
  target_paper_id: targetPaperId,
  target_paper_version_id: "55e69ff3-643b-4699-9699-235b29bc71a1",
  target_analysis_id: "57eeea6e-ecae-409c-a023-a277516d5db8",
  target_analysis_scope: "FULL_TEXT",
  comparability_status: "PARTIALLY_COMPARABLE",
  comparability_reason: "The papers share a task but report different benchmark subsets.",
  summary: "The historical method overlaps with the new memory-verification workflow.",
  dimensions: dimensionNames.map((name, position) => ({
    id: `00000000-0000-4000-8000-${String(position + 1).padStart(12, "0")}`,
    comparison_id: comparisonId,
    name,
    position,
    source_value: `New paper ${name.toLocaleLowerCase().replaceAll("_", " ")}.`,
    target_value: `Historical paper ${name.toLocaleLowerCase().replaceAll("_", " ")}.`,
    assessment: `Scoped assessment for ${name.toLocaleLowerCase()}.`,
    source_evidence_ids: [sourceEvidenceId],
    target_evidence_ids: [targetEvidenceId],
    schema_version: 1,
    created_at: "2026-08-08T05:14:00Z",
  })),
  relations: [
    {
      id: "ee9a1044-fb97-4df6-b959-1b6a507ac558",
      source_paper_id: sourcePaperId,
      source_paper_version_id: "465c74ac-bdf8-42e2-8652-7fec30fce680",
      target_paper_id: targetPaperId,
      target_paper_version_id: "55e69ff3-643b-4699-9699-235b29bc71a1",
      relation_type: "EXTENDS",
      provenance: "LLM_INFERRED",
      evidence_ids: [sourceEvidenceId, targetEvidenceId],
      justification: "The new method extends the historical memory-validation protocol.",
      provider: "deepseek",
      model_version: "deepseek-v4-flash-2026-08",
      prompt_version: "m3-comparison-v1",
      confidence: 0.72,
      verification_status: "UNVERIFIED",
      generated_at: "2026-08-08T05:14:00Z",
      schema_version: 1,
      created_at: "2026-08-08T05:14:00Z",
    },
  ],
  evidence: [
    {
      id: sourceEvidenceId,
      analysis_id: "8b28f2c7-f706-40e8-a0dc-696001298cab",
      paper_id: sourcePaperId,
      paper_version_id: "465c74ac-bdf8-42e2-8652-7fec30fce680",
      analysis_scope: "FULL_TEXT",
      section: "3.2 Memory verification",
      excerpt: "Each memory entry is checked against its cited observation before planning.",
      evidence_type: "SUPPORTS",
      verification_status: "UNVERIFIED",
    },
    {
      id: targetEvidenceId,
      analysis_id: "57eeea6e-ecae-409c-a023-a277516d5db8",
      paper_id: targetPaperId,
      paper_version_id: "55e69ff3-643b-4699-9699-235b29bc71a1",
      analysis_scope: "FULL_TEXT",
      section: "4 Historical protocol",
      excerpt: "The historical protocol checks memory before every tool invocation.",
      evidence_type: "SUPPORTS",
      verification_status: "UNVERIFIED",
    },
  ],
  provider: "deepseek",
  configured_model: "deepseek-v4-flash",
  model_version: "deepseek-v4-flash-2026-08",
  prompt_version: "m3-comparison-v1",
  generated_at: "2026-08-08T05:14:00Z",
  source: "deepseek_structured_comparison",
  verification_status: "UNVERIFIED",
  usage: {
    prompt_tokens: 500,
    completion_tokens: 200,
    total_tokens: 700,
    call_count: 1,
    duration_ms: 950,
    estimated_cost_usd: null,
  },
  schema_version: 1,
  created_at: "2026-08-08T05:14:00Z",
};

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/comparisons/:comparisonId" element={<ComparisonPage />} />
    </Routes>,
    `/comparisons/${comparisonId}`,
  );
}

function installFixtures(comparisonResponse: Response = jsonResponse(comparison)) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === `/api/v1/comparisons/${comparisonId}`) {
        return Promise.resolve(comparisonResponse);
      }
      if (path === `/api/v1/papers/${sourcePaperId}`) {
        return Promise.resolve(
          jsonResponse({
            id: sourcePaperId,
            title: "Planning with Verifiable Agent Memory",
            versions: [
              {
                id: comparison.source_paper_version_id,
                title: "Planning with Verifiable Agent Memory",
              },
            ],
          }),
        );
      }
      if (path === `/api/v1/papers/${targetPaperId}`) {
        return Promise.resolve(
          jsonResponse({
            id: targetPaperId,
            title: "Historical Memory Checks for Agents",
            versions: [
              {
                id: comparison.target_paper_version_id,
                title: "Historical Memory Checks for Agents",
              },
            ],
          }),
        );
      }
      return Promise.resolve(jsonResponse({ detail: "Not found" }, 404));
    }),
  );
}

describe("ComparisonPage", () => {
  it("renders the fixed evidence-linked matrix and explicit inference provenance", async () => {
    installFixtures();

    renderPage();

    expect(screen.getByText("Loading structured comparison")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "Structured paper comparison" }),
    ).toBeInTheDocument();
    expect(screen.getByText("partially comparable")).toBeInTheDocument();
    expect(screen.getByText(comparison.comparability_reason)).toBeInTheDocument();
    expect(screen.getByText(`${dimensionNames.length} fixed dimensions`)).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(dimensionNames.length + 1);
    expect(screen.getByRole("row", { name: /research problem/i })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /result comparability/i })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Evidence 1" })[0]).toHaveAttribute(
      "href",
      `#comparison-evidence-${sourceEvidenceId}`,
    );
    expect(screen.getByText(comparison.evidence[0]!.excerpt)).toBeInTheDocument();
    expect(screen.getByText(comparison.evidence[1]!.excerpt)).toBeInTheDocument();
    expect(
      screen.getAllByText(comparison.evidence[0]!.analysis_id, { exact: false }),
    ).toHaveLength(2);
    expect(screen.getByText(comparison.source_paper_version_id, { exact: false })).toBeInTheDocument();
    expect(screen.getByText("AI-generated · unverified")).toBeInTheDocument();
    expect(screen.getByText("AI-inferred")).toBeInTheDocument();
    expect(
      screen.getByText(/72% uncalibrated model-assessed evidential confidence/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: `Evidence 1: ${sourceEvidenceId}` }),
    ).toHaveAttribute("href", `#comparison-evidence-${sourceEvidenceId}`);
    expect(screen.getByText("deepseek / deepseek-v4-flash-2026-08")).toBeInTheDocument();
    expect(screen.getByText(/Author claims and reported results/)).toBeInTheDocument();
  });

  it("renders a truthful not-found state without querying paper metadata", async () => {
    installFixtures(
      jsonResponse(
        { detail: { code: "COMPARISON_NOT_FOUND", message: "Comparison was not found." } },
        404,
      ),
    );

    renderPage();

    expect(await screen.findByText("Comparison not found")).toBeInTheDocument();
    expect(
      vi
        .mocked(globalThis.fetch)
        .mock.calls.some(([input]) => requestPath(input).startsWith("/api/v1/papers/")),
    ).toBe(false);
  });

  it("keeps repository failures distinct from an empty comparison", async () => {
    installFixtures(
      jsonResponse(
        {
          detail: {
            code: "DATABASE_UNAVAILABLE",
            message: "Comparison storage is unavailable.",
          },
        },
        503,
      ),
    );

    renderPage();

    expect(await screen.findByText("Unable to load this comparison")).toBeInTheDocument();
    expect(screen.getByText(/Comparison storage is unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByText("Comparison not found")).not.toBeInTheDocument();
  });
});
