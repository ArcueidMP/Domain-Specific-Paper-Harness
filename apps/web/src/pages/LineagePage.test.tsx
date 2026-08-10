import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { LineagePage } from "./LineagePage";
import {
  evidenceId,
  historicalEvidenceId,
  historicalPaperId,
  lineage,
  paperId,
} from "../test/m4-fixtures";
import { jsonResponse, renderWithProviders } from "../test/render";

describe("LineagePage", () => {
  it("renders chronological, evidence-linked relations with explicit uncertainty", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      void input;
      return Promise.resolve(jsonResponse(lineage));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(
      <Routes>
        <Route path="/lineages/:entityOrPaperId" element={<LineagePage />} />
      </Routes>,
      `/lineages/${paperId}`,
    );

    expect(screen.getByText("Loading research lineage")).toBeInTheDocument();
    expect(await screen.findByText("Lineage uncertainty")).toBeInTheDocument();
    expect(screen.getByText(/Product activity through/)).toBeInTheDocument();
    expect(screen.getByText(/not a reconstructed historical end-of-day corpus/)).toBeInTheDocument();
    expect(screen.getByText("AI-inferred")).toBeInTheDocument();
    expect(screen.getByText(/0\.72 model-reported support strength/)).toBeInTheDocument();
    expect(screen.getByText(/not a probability/)).toBeInTheDocument();
    expect(screen.getByText("No explicit predecessor is available", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Evidence 1 (source)" })).toHaveAttribute(
      "href",
      `/papers/${paperId}#evidence-${evidenceId}`,
    );
    expect(screen.getByRole("link", { name: "Evidence 2 (target)" })).toHaveAttribute(
      "href",
      `/papers/${historicalPaperId}#evidence-${historicalEvidenceId}`,
    );

    const historical = screen.getByRole("link", {
      name: "Historical Memory Checks for Tool-Using Agents",
    });
    const current = screen.getByRole("link", { name: "Planning with Verifiable Agent Memory" });
    expect(
      historical.compareDocumentPosition(current) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    await userEvent.selectOptions(screen.getByLabelText("Maximum depth"), "2");
    await vi.waitFor(() => {
      const request = fetchMock.mock.calls.at(-1)?.[0];
      expect(request).toBeInstanceOf(Request);
      expect((request as Request).url).toContain("max_depth=2");
    });
  });
});
