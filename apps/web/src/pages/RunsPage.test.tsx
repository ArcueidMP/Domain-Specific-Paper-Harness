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
    expect(screen.queryByRole("button", { name: /run|retry|start/i })).not.toBeInTheDocument();
  });
});
