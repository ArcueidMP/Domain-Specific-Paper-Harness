import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { RunsPage } from "./RunsPage";
import { dailyRun, runDetail, runId } from "../test/m4-fixtures";
import { jsonResponse, renderWithProviders, requestPath } from "../test/render";

describe("RunsPage", () => {
  it("shows run operation, item stages, stable failures, and no execution controls", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = requestPath(input);
        if (path === "/api/v1/runs") {
          return Promise.resolve(
            jsonResponse({ items: [dailyRun.run], total: 1, limit: 50, offset: 0 }),
          );
        }
        if (path === `/api/v1/runs/${runId}`) {
          return Promise.resolve(jsonResponse(runDetail));
        }
        return Promise.resolve(jsonResponse({ detail: "Not found" }, 404));
      }),
    );

    renderWithProviders(
      <Routes>
        <Route path="/runs/:runId" element={<RunsPage />} />
      </Routes>,
      `/runs/${runId}`,
    );

    expect(screen.getByText("Loading run detail")).toBeInTheDocument();
    expect(await screen.findByText("Item stages")).toBeInTheDocument();
    expect(screen.getAllByText("GROBID_INVALID_TEI").length).toBeGreaterThan(0);
    expect(screen.getAllByText("parsed").length).toBeGreaterThan(0);
    expect(screen.getByText("published")).toBeInTheDocument();
    expect(screen.getAllByText("Standalone operation").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /run|retry|start/i })).not.toBeInTheDocument();
  });

  it("shows the failed pipeline outcome when an ingestion child completed with no selection", async () => {
    const emptySelectionRun = {
      ...runDetail,
      status: "COMPLETE" as const,
      pipeline_execution_id: "05baa0ee-9bb2-5e06-ab74-ee77bca475f6",
      pipeline_execution_mode: "NORMAL" as const,
      pipeline_selection_limit: 1,
      pipeline_status: "FAILED" as const,
      pipeline_deadline_at: "2026-08-08T13:03:00+08:00",
      pipeline_completed_at: "2026-08-08T05:04:00+08:00",
      pipeline_error_code: "NO_RELEVANT_PAPER_SELECTED",
      pipeline_error_detail:
        "arXiv ingestion completed but no paper passed the deterministic relevance filter",
      items: [],
      report: null,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = requestPath(input);
        if (path === "/api/v1/runs") {
          return Promise.resolve(
            jsonResponse({ items: [emptySelectionRun], total: 1, limit: 50, offset: 0 }),
          );
        }
        if (path === `/api/v1/runs/${runId}`) {
          return Promise.resolve(jsonResponse(emptySelectionRun));
        }
        return Promise.resolve(jsonResponse({ detail: "Not found" }, 404));
      }),
    );

    renderWithProviders(
      <Routes>
        <Route path="/runs/:runId" element={<RunsPage />} />
      </Routes>,
      `/runs/${runId}`,
    );

    expect(await screen.findByText("NO_RELEVANT_PAPER_SELECTED")).toBeInTheDocument();
    expect(
      screen.getByText(
        "arXiv ingestion completed but no paper passed the deterministic relevance filter",
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText("FAILED").length).toBeGreaterThan(0);
    expect(screen.getAllByText("COMPLETE").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Operation").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Pipeline").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Normal execution").length).toBeGreaterThan(0);
  });

  it("labels deployment smoke provenance and its execution identity", async () => {
    const smokeExecutionId = "c75c6f01-4474-5f59-8ee9-12303d0ada95";
    const smokeRun = {
      ...runDetail,
      pipeline_execution_id: smokeExecutionId,
      pipeline_execution_mode: "SMOKE" as const,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = requestPath(input);
        if (path === "/api/v1/runs") {
          return Promise.resolve(
            jsonResponse({ items: [smokeRun], total: 1, limit: 50, offset: 0 }),
          );
        }
        if (path === `/api/v1/runs/${runId}`) {
          return Promise.resolve(jsonResponse(smokeRun));
        }
        return Promise.resolve(jsonResponse({ detail: "Not found" }, 404));
      }),
    );

    renderWithProviders(
      <Routes>
        <Route path="/runs/:runId" element={<RunsPage />} />
      </Routes>,
      `/runs/${runId}`,
    );

    expect(await screen.findByText(smokeExecutionId)).toBeInTheDocument();
    expect(screen.getAllByText("Deployment smoke").length).toBeGreaterThan(0);
    expect(screen.queryByText("Normal execution")).not.toBeInTheDocument();
  });

  it("labels a same-date reprocessed publication", async () => {
    const reprocessRun = {
      ...runDetail,
      pipeline_execution_mode: "REPROCESS" as const,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = requestPath(input);
        if (path === "/api/v1/runs") {
          return Promise.resolve(
            jsonResponse({ items: [reprocessRun], total: 1, limit: 50, offset: 0 }),
          );
        }
        if (path === `/api/v1/runs/${runId}`) {
          return Promise.resolve(jsonResponse(reprocessRun));
        }
        return Promise.resolve(jsonResponse({ detail: "Not found" }, 404));
      }),
    );

    renderWithProviders(
      <Routes>
        <Route path="/runs/:runId" element={<RunsPage />} />
      </Routes>,
      `/runs/${runId}`,
    );

    expect((await screen.findAllByText("Reprocessed publication")).length).toBeGreaterThan(0);
  });
});
