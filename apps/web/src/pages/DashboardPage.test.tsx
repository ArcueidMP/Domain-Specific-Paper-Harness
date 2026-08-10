import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DashboardPage } from "./DashboardPage";
import {
  dailyRun,
  ninetyDayTrend,
  sevenDayTrend,
  thirtyDayTrend,
  topicId,
} from "../test/m4-fixtures";
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

function installDashboardFixture() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === "/api/v1/topics") {
        return Promise.resolve(
          jsonResponse({
            items: [
              {
                id: topicId,
                slug: "broad-llm-agents",
                name: "Broad LLM Agents",
                description: "Broad LLM-agent research.",
                schema_version: 1,
                created_at: "2026-08-08T00:00:00Z",
              },
            ],
            total: 1,
          }),
        );
      }
      if (path === "/api/v1/papers") {
        return Promise.resolve(jsonResponse({ items: [paper], total: 1, limit: 5, offset: 0 }));
      }
      if (path === "/api/v1/daily/latest") {
        return Promise.resolve(jsonResponse(dailyRun));
      }
      if (path === "/api/v1/trends") {
        return Promise.resolve(
          jsonResponse({ items: [sevenDayTrend, thirtyDayTrend, ninetyDayTrend], total: 3 }),
        );
      }
      return Promise.resolve(jsonResponse({ detail: "Not found" }, 404));
    }),
  );
}

describe("DashboardPage", () => {
  it("renders the latest product publication, report highlights, trends, and corpus", async () => {
    installDashboardFixture();

    renderWithProviders(<DashboardPage />);

    expect(screen.getByText("Loading the corpus")).toBeInTheDocument();
    expect(await screen.findByText("Broad LLM agents daily report")).toBeInTheDocument();
    expect(screen.getByText("PRODUCT PUBLICATION")).toBeInTheDocument();
    expect(screen.getByText("Adds evidence-linked memory checks to planning.")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: paper.title }).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /7D/ })).toHaveAttribute("href", "/trends?window=7D");
  });

  it("renders honest empty states when no product run, papers, or trends exist", async () => {
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
        if (path === "/api/v1/trends") {
          return Promise.resolve(jsonResponse({ items: [], total: 0 }));
        }
        return Promise.resolve(
          jsonResponse(
            { detail: { code: "PRODUCT_RUN_NOT_FOUND", message: "No product run." } },
            404,
          ),
        );
      }),
    );

    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("No papers have been ingested")).toBeInTheDocument();
    expect(await screen.findByText("No product publication run recorded")).toBeInTheDocument();
    expect(await screen.findByText("No trend snapshots published")).toBeInTheDocument();
  });

  it("shows retryable API errors without replacing them with empty results", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ detail: "Database is unavailable." }, 503))),
    );

    renderWithProviders(<DashboardPage />);

    expect((await screen.findAllByRole("alert")).length).toBeGreaterThanOrEqual(3);
    expect(screen.queryByText("No papers have been ingested")).not.toBeInTheDocument();
  });

  it("keeps a PARTIAL banner and stable item failure visible", async () => {
    installDashboardFixture();

    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("Partial daily run")).toBeInTheDocument();
    expect(screen.getByText("Partial report")).toBeInTheDocument();
    expect(screen.getAllByText("GROBID_INVALID_TEI").length).toBeGreaterThan(0);
    expect(screen.getAllByText("PARSED").length).toBeGreaterThan(0);
  });
});
