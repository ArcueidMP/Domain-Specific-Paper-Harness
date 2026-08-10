import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TrendsPage } from "./TrendsPage";
import { sevenDayTrend, thirtyDayTrend } from "../test/m4-fixtures";
import { jsonResponse, renderWithProviders } from "../test/render";

describe("TrendsPage", () => {
  it("switches deterministic windows and exposes insufficient and zero-denominator states", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(input);
      const window = new URL(request.url).searchParams.get("window");
      const item = window === "30D" ? thirtyDayTrend : sevenDayTrend;
      return Promise.resolve(jsonResponse({ items: [item], total: 1 }));
    });
    vi.stubGlobal(
      "fetch",
      fetchMock,
    );

    renderWithProviders(<TrendsPage />);

    expect(screen.getByText("Loading the 7D trend")).toBeInTheDocument();
    expect(await screen.findByText("sufficient data")).toBeInTheDocument();
    expect(screen.getByText(/Product activity through/)).toBeInTheDocument();
    expect(screen.getByText(/not a reconstructed historical end-of-day corpus/)).toBeInTheDocument();
    const firstRequest = fetchMock.mock.calls[0]?.[0];
    expect(firstRequest).toBeInstanceOf(Request);
    expect(new URL((firstRequest as Request).url).searchParams.get("max_entities")).toBe("50");
    expect(screen.getByRole("link", { name: "Planning with Verifiable Agent Memory" })).toHaveAttribute(
      "href",
      "/papers/00511b3e-1303-4e03-b846-d29fd641942d",
    );
    expect(screen.getByRole("link", { name: "Source-grounded memory verification" }))
      .toHaveAttribute("href", `/graph?entity_id=${sevenDayTrend.entity_counts[0]?.entity_id}`);

    await userEvent.click(screen.getByRole("button", { name: "30 days" }));

    expect(await screen.findByText("Insufficient data")).toBeInTheDocument();
    expect(
      screen.getByText("Not calculated: the preceding window has zero papers."),
    ).toBeInTheDocument();
    expect(screen.getByText("2 papers", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "30 days" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("renders an honest empty snapshot instead of zero-filled charts", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ items: [], total: 0 }))),
    );

    renderWithProviders(<TrendsPage />);

    expect(await screen.findByText("No 7D trend snapshot")).toBeInTheDocument();
    expect(screen.queryByText("Paper activity")).not.toBeInTheDocument();
  });
});
