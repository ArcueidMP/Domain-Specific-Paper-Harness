import { expect, test, type Page } from "@playwright/test";

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

async function installApiFixtures(page: Page) {
  await page.route("**/api/v1/topics*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            id: "cc6caeba-3832-42c4-8fbf-607a183490f8",
            slug: "broad-llm-agents",
            name: "Broad LLM Agents",
            description: "Broad LLM-agent research.",
            schema_version: 1,
            created_at: "2026-08-08T00:00:00Z",
          },
        ],
        total: 1,
      }),
    });
  });
  await page.route("**/api/v1/papers*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [paper], total: 1, limit: 20, offset: 0 }),
    });
  });
  await page.route("**/api/v1/runs/latest*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
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
      }),
    });
  });
}

test("dashboard and paper corpus render from the API contract without live credentials", async ({ page }) => {
  await installApiFixtures(page);

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "What changed in agent research?" })).toBeVisible();
  await expect(page.getByText(paper.title)).toBeVisible();
  await expect(page.getByText("COMPLETE", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Papers", exact: true }).click();

  await expect(page).toHaveURL(/\/papers$/);
  await expect(page.getByRole("heading", { name: "Papers" })).toBeVisible();
  await expect(page.getByText(paper.title)).toBeVisible();
  await expect(page.getByRole("link", { name: /Open PDF/ })).toHaveAttribute(
    "href",
    paper.pdf_url,
  );
});
