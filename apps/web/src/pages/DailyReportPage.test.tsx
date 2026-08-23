import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DailyReportPage } from "./DailyReportPage";
import { dailyRun, evidenceId, methodNodeId, paperId, report } from "../test/m4-fixtures";
import { jsonResponse, renderWithProviders, requestPath } from "../test/render";

describe("DailyReportPage", () => {
  it("shows PARTIAL failures, limitations, evidence links, and lineage navigation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = requestPath(input);
        if (path === "/api/v1/daily/latest") {
          return Promise.resolve(jsonResponse(dailyRun));
        }
        if (path === "/api/v1/reports/daily") {
          return Promise.resolve(
            jsonResponse({ items: [report], total: 1, limit: 20, offset: 0 }),
          );
        }
        return Promise.resolve(jsonResponse({ detail: "Not found" }, 404));
      }),
    );

    renderWithProviders(<DailyReportPage />);

    expect(screen.getByText("Loading daily publication")).toBeInTheDocument();
    expect((await screen.findAllByText("Broad LLM agents daily report")).length).toBeGreaterThan(0);
    expect(screen.getByText("Partial report")).toBeInTheDocument();
    expect(screen.getAllByText("ANALYSIS UNAVAILABLE").length).toBeGreaterThan(0);
    expect(screen.getAllByText("COMPARISON UNAVAILABLE").length).toBeGreaterThan(0);
    expect(screen.getAllByText("INSUFFICIENT DATA").length).toBeGreaterThan(0);
    expect(screen.getAllByText("GROBID_INVALID_TEI").length).toBeGreaterThan(0);
    expect(screen.getByText("Scope and limitations")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open evidence in paper" })).toHaveAttribute(
      "href",
      `/papers/${paperId}?topic=broad-llm-agents#evidence-${evidenceId}`,
    );
    expect(screen.getByRole("link", { name: "Memory verification lineage" })).toHaveAttribute(
      "href",
      `/lineages/${paperId}?topic=broad-llm-agents`,
    );
    expect(screen.getByRole("link", { name: /Source-grounded memory verification/ }))
      .toHaveAttribute("href", `/graph?entity_id=${methodNodeId}&topic=broad-llm-agents`);
    expect(screen.getByText("2 distinct papers in the latest 7-day window")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /2026-08-08/ })).toHaveAttribute(
      "href",
      "/reports/daily/2026-08-08?topic=broad-llm-agents",
    );
  });

  it("shows a failed run without inventing a report", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        if (requestPath(input) === "/api/v1/daily/latest") {
          return Promise.resolve(
            jsonResponse({
              ...dailyRun,
              run: { ...dailyRun.run, status: "FAILED", completed_count: 0 },
              report: null,
            }),
          );
        }
        return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 20, offset: 0 }));
      }),
    );

    renderWithProviders(<DailyReportPage />);

    expect(await screen.findByText("No report was published")).toBeInTheDocument();
    expect(screen.getByText("FAILED")).toBeInTheDocument();
  });

  it("shows a complete no-update publication for the current logical date", async () => {
    const noUpdateReport = {
      ...report,
      logical_date: "2026-08-23",
      period_start: "2026-08-23",
      period_end: "2026-08-23",
      status: "COMPLETE",
      publication_outcome: "NO_UPDATE",
      summary: "The daily run completed normally with no update.",
      failures: [],
      sections: [],
      counts: { retrieved: 0, selected: 0, processed: 0, completed: 0, failed: 0 },
      highlighted_papers: [],
      major_entities: [],
      notable_comparisons: [],
      lineage_highlights: [],
      evidence: [],
    };
    const noUpdateRun = {
      ...dailyRun,
      run: {
        ...dailyRun.run,
        logical_date: "2026-08-23",
        status: "COMPLETE",
        pipeline_status: "COMPLETE",
        publication_outcome: "NO_UPDATE",
        selected_count: 0,
        completed_count: 0,
        failed_count: 0,
        error_code: null,
        error_detail: null,
      },
      items: [],
      report: noUpdateReport,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) =>
        requestPath(input) === "/api/v1/daily/latest"
          ? Promise.resolve(jsonResponse(noUpdateRun))
          : Promise.resolve(
              jsonResponse({ items: [noUpdateReport], total: 1, limit: 20, offset: 0 }),
            ),
      ),
    );

    renderWithProviders(<DailyReportPage />);

    expect((await screen.findAllByText("No research updates today")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("No relevant papers were found today.").length).toBeGreaterThan(0);
    expect(screen.getAllByText("NO UPDATE").length).toBeGreaterThan(0);
    expect(screen.queryByText("No report was published")).not.toBeInTheDocument();
    expect(screen.getAllByText("2026-08-23").length).toBeGreaterThan(0);
  });
});
