import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PapersPage } from "./PapersPage";
import { jsonResponse, renderWithProviders } from "../test/render";

const papers = [
  {
    id: "00511b3e-1303-4e03-b846-d29fd641942d",
    canonical_arxiv_id: "2608.01234",
    title: "Planning with Verifiable Agent Memory",
    abstract: "Memory-grounded planning for language-model agents.",
    current_version: 2,
    first_submitted_at: "2026-08-01T00:00:00Z",
    latest_updated_at: "2026-08-07T03:00:00Z",
    primary_category: "cs.AI",
    categories: ["cs.AI"],
    authors: ["Ada North"],
    pdf_url: "https://arxiv.org/pdf/2608.01234v2",
    schema_version: 1,
    created_at: "2026-08-08T00:00:00Z",
  },
  {
    id: "34a39291-c144-4d1c-8059-2d338988fa18",
    canonical_arxiv_id: "2608.05678",
    title: "Reliable Tool Use in Web Agents",
    abstract: "Evaluating browser tools under uncertain observations.",
    current_version: 1,
    first_submitted_at: "2026-08-05T00:00:00Z",
    latest_updated_at: "2026-08-05T00:00:00Z",
    primary_category: "cs.CL",
    categories: ["cs.CL"],
    authors: ["Bo East"],
    pdf_url: "https://arxiv.org/pdf/2608.05678v1",
    schema_version: 1,
    created_at: "2026-08-08T00:00:00Z",
  },
];

const firstPaper = papers[0]!;
const secondPaper = papers[1]!;

describe("PapersPage", () => {
  it("renders papers and filters the currently loaded page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ items: papers, total: 2, limit: 20, offset: 0 }))),
    );
    const user = userEvent.setup();

    renderWithProviders(<PapersPage />);

    expect(await screen.findByText(firstPaper.title)).toBeInTheDocument();
    expect(screen.getByText(secondPaper.title)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: firstPaper.title })).toHaveAttribute(
      "href",
      `/papers/${firstPaper.id}`,
    );

    await user.type(screen.getByRole("searchbox"), "Ada North");

    expect(screen.getByText(firstPaper.title)).toBeInTheDocument();
    expect(screen.queryByText(secondPaper.title)).not.toBeInTheDocument();
  });

  it("renders the empty corpus state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ items: [], total: 0, limit: 20, offset: 0 }))),
    );

    renderWithProviders(<PapersPage />);

    expect(await screen.findByText("The paper corpus is empty")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Paper pages" })).not.toBeInTheDocument();
  });
});
