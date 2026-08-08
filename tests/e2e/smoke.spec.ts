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

const paperVersionId = "465c74ac-bdf8-42e2-8652-7fec30fce680";
const parsedPaperId = "703fc4bd-3ff6-4c83-b8eb-cddda2e346b4";
const analysisId = "8b28f2c7-f706-40e8-a0dc-696001298cab";
const claimId = "c234ea44-3a86-44ce-a334-ccf45b1da322";

const paperDetail = {
  ...paper,
  versions: [
    {
      id: paperVersionId,
      paper_id: paper.id,
      canonical_arxiv_id: paper.canonical_arxiv_id,
      version: 2,
      title: paper.title,
      abstract: paper.abstract,
      submitted_at: paper.first_submitted_at,
      updated_at: paper.latest_updated_at,
      primary_category: paper.primary_category,
      categories: paper.categories,
      authors: paper.authors,
      pdf_url: paper.pdf_url,
      source_url: "https://arxiv.org/abs/2608.01234v2",
      schema_version: 1,
      created_at: paper.created_at,
    },
  ],
  source_identities: [],
  topic_slugs: ["broad-llm-agents"],
};

const analysis = {
  id: analysisId,
  paper_id: paper.id,
  paper_version_id: paperVersionId,
  arxiv_version: 2,
  analysis_scope: "FULL_TEXT",
  parsed_paper_id: parsedPaperId,
  parser_name: "grobid",
  parser_version: "0.9.0",
  summary: "The paper introduces verifiable memory for agent planning.",
  research_problem: "Agent plans can drift when memory is not checked against source state.",
  method_summary: "The method validates memory entries before they influence the next plan.",
  key_contributions: ["A verification layer for agent memory."],
  limitations: ["Evaluation covers a bounded set of planning tasks."],
  provider: "deepseek",
  configured_model: "deepseek-v4-flash",
  model_version: "deepseek-v4-flash-2026-08",
  prompt_version: "analysis-v1",
  generated_at: "2026-08-08T05:10:00Z",
  source: "selected-arxiv-full-text",
  verification_status: "UNVERIFIED",
  usage: {
    prompt_tokens: 1200,
    completion_tokens: 300,
    total_tokens: 1500,
    call_count: 1,
    duration_ms: 2400,
    estimated_cost_usd: "0.0012",
  },
  schema_version: 1,
  created_at: "2026-08-08T05:10:01Z",
  claims: [
    {
      id: claimId,
      analysis_id: analysisId,
      paper_id: paper.id,
      paper_version_id: paperVersionId,
      key: "claim-memory-verification",
      claim_type: "CONTRIBUTION",
      text: "The verification layer rejects memory entries that lack source support.",
      provider: "deepseek",
      model_version: "deepseek-v4-flash-2026-08",
      prompt_version: "analysis-v1",
      generated_at: "2026-08-08T05:10:00Z",
      source: "selected-arxiv-full-text",
      verification_status: "UNVERIFIED",
      schema_version: 1,
      created_at: "2026-08-08T05:10:01Z",
    },
  ],
};

const evidence = {
  items: [
    {
      id: "a50b18f1-bf2c-4121-87ed-4c2d6b3d193b",
      analysis_id: analysisId,
      paper_id: paper.id,
      paper_version_id: paperVersionId,
      key: "evidence-memory-verification",
      section: "3.2 Memory verification",
      passage_id: "passage-3-2-4",
      coordinates: [{ page: 4, x: 12.5, y: 33, width: 72, height: 8 }],
      excerpt: "Each memory entry is checked against its cited observation before planning.",
      evidence_type: "SUPPORTS",
      supported_claim_ids: [claimId],
      extraction_source: "grobid-tei",
      provider: "deepseek",
      model_version: "deepseek-v4-flash-2026-08",
      prompt_version: "analysis-v1",
      generated_at: "2026-08-08T05:10:00Z",
      verification_status: "UNVERIFIED",
      schema_version: 1,
      created_at: "2026-08-08T05:10:01Z",
    },
  ],
  total: 1,
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
  await page.route(
    /\/api\/v1\/papers(?:\/[^/?]+(?:\/(?:analysis|evidence))?)?(?:\?.*)?$/,
    async (route) => {
      const url = new URL(route.request().url());
      const pathname = url.pathname;
      let body: unknown;
      if (pathname === `/api/v1/papers/${paper.id}/analysis`) {
        if (url.searchParams.get("paper_version_id") !== paperVersionId) {
          await route.fulfill({ status: 400, body: "analysis version was not pinned" });
          return;
        }
        body = analysis;
      } else if (pathname === `/api/v1/papers/${paper.id}/evidence`) {
        if (
          url.searchParams.get("analysis_id") !== analysisId ||
          url.searchParams.get("paper_version_id") !== paperVersionId ||
          url.searchParams.get("scope") !== "FULL_TEXT"
        ) {
          await route.fulfill({ status: 400, body: "evidence analysis was not pinned" });
          return;
        }
        body = evidence;
      } else if (pathname === `/api/v1/papers/${paper.id}`) {
        body = paperDetail;
      } else {
        body = { items: [paper], total: 1, limit: 20, offset: 0 };
      }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    },
  );
  await page.route("**/api/v1/runs/latest*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        id: "df0b73ea-cea0-4eb5-9501-e5680b472f85",
        topic_id: "cc6caeba-3832-42c4-8fbf-607a183490f8",
        logical_date: "2026-08-08",
        operation: "STRUCTURED_ANALYSIS",
        analysis_scope: "FULL_TEXT",
        status: "PARTIAL",
        started_at: "2026-08-08T05:00:00+08:00",
        completed_at: "2026-08-08T05:02:00+08:00",
        cursor_from: null,
        cursor_to: null,
        discovered_count: 0,
        normalized_count: 0,
        selected_count: 2,
        completed_count: 1,
        failed_count: 1,
        error_code: null,
        error_detail: null,
        schema_version: 1,
        created_at: "2026-08-08T05:00:00+08:00",
        items: [
          {
            id: "125790a2-7520-492f-a6ad-b3f10ce9075c",
            run_id: "df0b73ea-cea0-4eb5-9501-e5680b472f85",
            paper_id: paper.id,
            paper_version_id: paperVersionId,
            canonical_arxiv_id: paper.canonical_arxiv_id,
            paper_title: paper.title,
            stage: "EVIDENCE_EXTRACTED",
            status: "COMPLETED",
            failed_stage: null,
            error_code: null,
            retryable: null,
            error_detail: null,
            schema_version: 1,
            created_at: "2026-08-08T05:01:00+08:00",
            updated_at: "2026-08-08T05:01:20+08:00",
          },
          {
            id: "8e21d299-e8db-4182-9e95-bb6281b2623e",
            run_id: "df0b73ea-cea0-4eb5-9501-e5680b472f85",
            paper_id: "39a22c66-6d3e-4237-b93d-e29898f66574",
            paper_version_id: "d74f0699-b2df-4fce-9453-d615993d02e9",
            canonical_arxiv_id: "2608.05678",
            paper_title: "A Failed Parser Fixture",
            stage: "PDF_DOWNLOADED",
            status: "FAILED",
            failed_stage: "PARSED",
            error_code: "GROBID_INVALID_TEI",
            retryable: false,
            error_detail: "The parser returned TEI without a body section.",
            schema_version: 1,
            created_at: "2026-08-08T05:01:00+08:00",
            updated_at: "2026-08-08T05:01:30+08:00",
          },
        ],
        report: {
          id: "a7673fa1-7de4-445b-b214-2232362eb584",
          run_id: "df0b73ea-cea0-4eb5-9501-e5680b472f85",
          topic_id: "cc6caeba-3832-42c4-8fbf-607a183490f8",
          logical_date: "2026-08-08",
          status: "PARTIAL",
          title: "Structured analysis report for 2026-08-08",
          summary: "1 of 2 selected papers completed evidence extraction; 1 failed.",
          source: "structured_analysis_pipeline",
          generated_at: "2026-08-08T05:02:00+08:00",
          schema_version: 1,
          created_at: "2026-08-08T05:02:00+08:00",
          failures: [
            {
              id: "c7276cc5-8134-49d6-a95e-2ee73c4025f8",
              report_id: "a7673fa1-7de4-445b-b214-2232362eb584",
              paper_id: "39a22c66-6d3e-4237-b93d-e29898f66574",
              paper_version_id: "d74f0699-b2df-4fce-9453-d615993d02e9",
              failed_stage: "PARSED",
              error_code: "GROBID_INVALID_TEI",
              retryable: false,
              error_detail: "The parser returned TEI without a body section.",
              schema_version: 1,
              created_at: "2026-08-08T05:02:00+08:00",
            },
          ],
        },
      }),
    });
  });
}

test("partial run and grounded paper analysis render without live credentials", async ({ page }) => {
  await installApiFixtures(page);

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "What changed in agent research?" })).toBeVisible();
  await expect(page.getByRole("link", { name: paper.title, exact: true })).toBeVisible();
  await expect(page.getByText("PARTIAL", { exact: true })).toBeVisible();
  await expect(page.getByText("Partial daily run")).toBeVisible();
  await expect(page.getByText("GROBID_INVALID_TEI")).toBeVisible();

  await page.getByRole("link", { name: "Papers", exact: true }).click();

  await expect(page).toHaveURL(/\/papers$/);
  await expect(page.getByRole("heading", { name: "Papers" })).toBeVisible();
  await expect(page.getByText(paper.title)).toBeVisible();
  await expect(page.getByRole("link", { name: /Open PDF/ })).toHaveAttribute(
    "href",
    paper.pdf_url,
  );

  await page.getByRole("link", { name: paper.title }).click();

  await expect(page).toHaveURL(new RegExp(`/papers/${paper.id}$`));
  await expect(page.getByRole("heading", { name: "Analysis of arXiv v2" })).toBeVisible();
  await expect(page.locator(".scope-badge", { hasText: "full text" })).toBeVisible();
  await expect(page.getByText(parsedPaperId)).toBeVisible();
  await expect(page.getByText("grobid / 0.9.0")).toBeVisible();
  await expect(page.getByText(analysis.claims[0].text)).toBeVisible();
  await expect(page.getByText(evidence.items[0].excerpt)).toBeVisible();
});
