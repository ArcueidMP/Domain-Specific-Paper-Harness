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
  default: vi.fn(() => ({
    on, destroy, zoom: vi.fn(() => 1), resize: vi.fn(),
    nodes: () => ({ unselect: vi.fn() }),
    edges: () => ({ removeClass: vi.fn() }),
    getElementById: () => ({ select: vi.fn(), connectedEdges: () => ({ addClass: vi.fn() }) }),
  })),
}));

describe("KnowledgeGraphPage", () => {
  beforeEach(() => {
    on.mockClear();
    destroy.mockClear();
    vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
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
    expect(screen.getByText("Node neighborhood")).toBeInTheDocument();
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

  it("selects a visible node without refetching or destroying the canvas", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(knowledgeGraph)));
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<KnowledgeGraphPage />);
    await screen.findByRole("img", { name: /3 visible nodes/ });
    const requests = fetchMock.mock.calls.length;
    const destroys = destroy.mock.calls.length;
    await userEvent.click(screen.getByTitle("Source-grounded memory verification"));
    expect(screen.getByRole("heading", { name: "Source-grounded memory verification" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(requests);
    expect(destroy).toHaveBeenCalledTimes(destroys);
  });

  it("searches outside the overview, opens a neighborhood, and returns to the overview", async () => {
    const outsideId = "916f7b0f-aad0-4cda-bd8b-1d8cff02d119";
    const outside = { ...knowledgeGraph.nodes[0]!, id: outsideId, display_label: "Outside overview" };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL((input as Request).url);
      if (url.pathname.endsWith("/search")) {
        expect(url.searchParams.get("topic")).toBe("broad-llm-agents");
        expect(url.searchParams.get("q")).toBe("Outside");
        return Promise.resolve(jsonResponse({ items: [outside], total: 1, offset: 0, limit: 20 }));
      }
      return Promise.resolve(jsonResponse(url.searchParams.has("entity_id")
        ? { ...knowledgeGraph, nodes: [outside], edges: [] } : knowledgeGraph));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<KnowledgeGraphPage />);
    await screen.findByRole("img", { name: /3 visible nodes/ });
    await userEvent.type(screen.getByRole("searchbox", { name: "Search nodes" }), "Outside");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await userEvent.click(await screen.findByTitle("Outside overview"));
    expect(await screen.findByRole("heading", { name: "Outside overview" })).toBeInTheDocument();
    expect((fetchMock.mock.calls.at(-1)?.[0] as Request).url).toContain(`entity_id=${outsideId}`);
    await userEvent.click(screen.getByRole("button", { name: "Return to overview" }));
    expect(await screen.findByRole("img", { name: /3 visible nodes/ })).toBeInTheDocument();
  });
});
