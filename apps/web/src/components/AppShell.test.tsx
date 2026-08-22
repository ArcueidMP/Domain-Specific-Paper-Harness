import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithProviders } from "../test/render";
import { AppShell } from "./AppShell";

const topics = {
  items: [
    {
      id: "11111111-1111-4111-8111-111111111111",
      slug: "broad-llm-agents",
      name: "Broad LLM Agents",
      description: "Research about LLM-centered agents.",
      schema_version: 1,
      created_at: "2026-08-22T00:00:00Z",
    },
    {
      id: "22222222-2222-4222-8222-222222222222",
      slug: "brain-computer-interfaces",
      name: "Brain-Computer Interfaces",
      description: "Research about brain-computer interfaces.",
      schema_version: 1,
      created_at: "2026-08-22T00:00:00Z",
    },
    {
      id: "33333333-3333-4333-8333-333333333333",
      slug: "world-models",
      name: "World Models",
      description: "Research about world models.",
      schema_version: 1,
      created_at: "2026-08-22T00:00:00Z",
    },
  ],
  total: 3,
};

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="Current location">{`${location.pathname}${location.search}`}</output>;
}

function shellRoutes() {
  return (
    <Routes>
      <Route path="/" element={<AppShell />}>
        <Route index element={<LocationProbe />} />
        <Route path="papers" element={<LocationProbe />} />
      </Route>
    </Routes>
  );
}

describe("AppShell topic selection", () => {
  it("writes the default topic to the URL and preserves it in global navigation", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(topics))));

    renderWithProviders(shellRoutes());

    await waitFor(() =>
      expect(screen.getByLabelText("Current location")).toHaveTextContent(
        "/?topic=broad-llm-agents",
      ),
    );
    expect(screen.getByLabelText("Active topic")).toHaveValue("broad-llm-agents");
    expect(screen.getByRole("link", { name: "Papers" })).toHaveAttribute(
      "href",
      "/papers?topic=broad-llm-agents",
    );
  });

  it("switches topic in place and updates every global navigation destination", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(topics))));
    const user = userEvent.setup();

    renderWithProviders(shellRoutes(), "/?topic=world-models");

    await screen.findByRole("option", { name: "World Models" });
    await user.selectOptions(screen.getByLabelText("Active topic"), "brain-computer-interfaces");

    expect(screen.getByLabelText("Current location")).toHaveTextContent(
      "/?topic=brain-computer-interfaces",
    );
    expect(screen.getByRole("link", { name: "Reports" })).toHaveAttribute(
      "href",
      "/reports/daily?topic=brain-computer-interfaces",
    );
  });
});
