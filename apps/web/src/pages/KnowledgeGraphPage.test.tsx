import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgeGraphPage } from "./KnowledgeGraphPage";
import {
  evidenceId,
  historicalEvidenceId,
  historicalPaperId,
  knowledgeGraph,
  methodNodeId,
  paperId,
} from "../test/m4-fixtures";
import { jsonResponse, renderWithProviders } from "../test/render";

const { on, destroy } = vi.hoisted(() => ({ on: vi.fn(), destroy: vi.fn() }));

vi.mock("cytoscape", () => ({
  default: vi.fn(() => ({ on, destroy })),
}));

describe("KnowledgeGraphPage", () => {
  beforeEach(() => {
    on.mockClear();
    destroy.mockClear();
  });

  it("honors paper scope and entity selection from graph navigation links", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      void input;
      return Promise.resolve(jsonResponse(knowledgeGraph));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(
      <KnowledgeGraphPage />,
      `/graph?paper_id=${paperId}&entity_id=${methodNodeId}`,
    );

    expect(
      await screen.findByRole("heading", { name: "Source-grounded memory verification" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Paper-scoped graph")).toBeInTheDocument();
    await vi.waitFor(() => {
      const request = fetchMock.mock.calls.at(-1)?.[0];
       expect(request).toBeInstanceOf(Request);
       const params = new URL((request as Request).url).searchParams;
       expect(params.get("paper_id")).toBe(paperId);
       expect(params.get("entity_id")).toBe(methodNodeId);
     });
  });

  it("renders a bounded graph, inferred labels, provenance, and navigation", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      void input;
      return Promise.resolve(jsonResponse(knowledgeGraph));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<KnowledgeGraphPage />);

    expect(screen.getByText("Loading the knowledge graph")).toBeInTheDocument();
    expect(
      await screen.findByRole("img", { name: /3 visible nodes and 2 visible relations/ }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("AI-inferred").length).toBeGreaterThan(0);
    expect(screen.getByText("llm inferred")).toBeInTheDocument();
    expect(screen.getByText(/0\.72 model-reported support strength/)).toBeInTheDocument();
    expect(screen.getByText(/not a probability/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open paper" })).toHaveAttribute(
      "href",
      `/papers/${paperId}?topic=broad-llm-agents`,
    );
    expect(screen.getByRole("link", { name: "View related paper lineage" })).toHaveAttribute(
      "href",
      `/lineages/${paperId}?topic=broad-llm-agents`,
    );
    expect(screen.getAllByRole("link", { name: "Evidence 1 (source)" })[0]).toHaveAttribute(
      "href",
      `/papers/${paperId}?topic=broad-llm-agents#evidence-${evidenceId}`,
    );
    expect(screen.getByRole("link", { name: "Evidence 2 (target)" })).toHaveAttribute(
      "href",
      `/papers/${historicalPaperId}?topic=broad-llm-agents#evidence-${historicalEvidenceId}`,
    );

    await userEvent.selectOptions(screen.getByLabelText("Provenance"), "LLM_INFERRED");
    await vi.waitFor(() => {
      const request = fetchMock.mock.calls.at(-1)?.[0];
      expect(request).toBeInstanceOf(Request);
      expect((request as Request).url).toContain("provenance=LLM_INFERRED");
    });
  });

  it("keeps empty and error graph states distinct", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(
            { detail: { code: "GRAPH_NOT_FOUND", message: "No graph." } },
            404,
          ),
        ),
      ),
    );
    const empty = renderWithProviders(<KnowledgeGraphPage />);
    expect(await screen.findByText("No matching graph records")).toBeInTheDocument();
    empty.unmount();

    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ detail: "Storage unavailable." }, 503))),
    );
    renderWithProviders(<KnowledgeGraphPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Storage unavailable");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
