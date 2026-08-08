import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DashboardPage } from "./DashboardPage";
import { jsonResponse, renderWithProviders, requestPath } from "../test/render";

const paper = {
  id: "00511b3e-1303-4e03-b846-d29fd641942d",
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
};

const run = {
  id: "df0b73ea-cea0-4eb5-9501-e5680b472f85",
  topic_id: "cc6caeba-3832-42c4-8fbf-607a183490f8",
  logical_date: "2026-08-08",
  operation: "DAILY_DISCOVERY",
  status: "COMPLETE",
  started_at: "2026-08-08T05:00:00+08:00",
  completed_at: "2026-08-08T05:02:00+08:00",
  cursor_from: null,
  cursor_to: "2026-08-08T00:00:00Z",
  discovered_count: 7,
  normalized_count: 6,
  failed_count: 0,
  error_code: null,
  error_detail: null,
  schema_version: 1,
  created_at: "2026-08-08T05:00:00+08:00",
  items: [],
};

describe("DashboardPage", () => {
  it("shows loading state and then renders persisted corpus and run data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = requestPath(input);
        if (path === "/api/v1/topics") {
          return Promise.resolve(jsonResponse({
            items: [
              {
                id: run.topic_id,
                slug: "broad-llm-agents",
                name: "Broad LLM Agents",
                description: "Broad LLM-agent research.",
                schema_version: 1,
                created_at: "2026-08-08T00:00:00Z",
              },
            ],
            total: 1,
          }));
        }
        if (path === "/api/v1/papers") {
          return Promise.resolve(jsonResponse({ items: [paper], total: 1, limit: 5, offset: 0 }));
        }
        if (path === "/api/v1/runs/latest") {
          return Promise.resolve(jsonResponse(run));
        }
        return Promise.resolve(jsonResponse({ detail: "Not found" }, 404));
      }),
    );

    renderWithProviders(<DashboardPage />);

    expect(screen.getByText("Loading the corpus")).toBeInTheDocument();
    expect(await screen.findByText(paper.title)).toBeInTheDocument();
    expect(screen.getByText("COMPLETE")).toBeInTheDocument();
    expect(screen.getAllByText("6")).toHaveLength(2);
  });

  it("renders honest empty states when no run or papers exist", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = requestPath(input);
        if (path === "/api/v1/topics") {
          return Promise.resolve(jsonResponse({ items: [], total: 0 }));
        }
        if (path === "/api/v1/papers") {
          return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 5, offset: 0 }));
        }
        return Promise.resolve(jsonResponse({ detail: "No run has been recorded." }, 404));
      }),
    );

    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("No papers have been ingested")).toBeInTheDocument();
    expect(await screen.findByText("No ingestion run recorded")).toBeInTheDocument();
  });

  it("shows retryable errors without replacing them with empty data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ detail: "Database is unavailable." }, 503))),
    );

    renderWithProviders(<DashboardPage />);

    const alerts = await screen.findAllByRole("alert");
    expect(alerts).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Try again" })).toHaveLength(2);
    expect(screen.queryByText("No papers have been ingested")).not.toBeInTheDocument();
  });
});
