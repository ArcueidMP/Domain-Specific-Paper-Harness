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
});
