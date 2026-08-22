import { expect, test, type Page } from "@playwright/test";

import {
  dailyRun,
  knowledgeGraph,
  lineage,
  ninetyDayTrend,
  report,
  runDetail,
  sevenDayTrend,
  thirtyDayTrend,
} from "../../apps/web/src/test/m4-fixtures";

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
const comparisonId = "638a6949-a4df-4ef9-b195-25309f576acd";
const targetPaperId = "b431af71-5ea9-4903-a3ff-f611bdc50f32";
const targetPaperVersionId = "55e69ff3-643b-4699-9699-235b29bc71a1";
const targetAnalysisId = "57eeea6e-ecae-409c-a023-a277516d5db8";
const sourceEvidenceId = "a50b18f1-bf2c-4121-87ed-4c2d6b3d193b";
const targetEvidenceId = "aaec48b6-0ce0-43f1-95e7-1954129d79ca";
const defaultTopic = "broad-llm-agents";

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

const targetPaperDetail = {
  ...paperDetail,
  id: targetPaperId,
  canonical_arxiv_id: "2509.00001",
  title: "Historical Memory Checks for Tool-Using Agents",
  current_version: 1,
  versions: [
    {
      ...paperDetail.versions[0],
      id: targetPaperVersionId,
      paper_id: targetPaperId,
      canonical_arxiv_id: "2509.00001",
      version: 1,
      title: "Historical Memory Checks for Tool-Using Agents",
    },
  ],
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

const relatedWork = {
  paper_id: paper.id,
  session: {
    id: "741e66ad-c55f-4b15-a847-0fd81e13a87a",
    topic_id: "cc6caeba-3832-42c4-8fbf-607a183490f8",
    source_paper_id: paper.id,
    source_paper_version_id: paperVersionId,
    source_analysis_id: analysisId,
    source_analysis_scope: "FULL_TEXT",
    requested_year_from: 2025,
    effective_year_to: 2026,
    objective: "Find historical work on verifiable agent memory.",
    crawler_queries: ["verifiable agent memory", "agent memory benchmarks"],
    crawler_use_recommendations: true,
    crawler_expand_references: true,
    crawler_expand_citations: false,
    crawler_decision_reason: "Use bounded search and reference expansion.",
    crawler_generated_at: "2026-08-08T05:11:05Z",
    status: "COMPLETE",
    limits: {
      max_steps: 8,
      max_queries: 3,
      max_queue_size: 50,
      max_citation_depth: 2,
      max_candidates: 20,
      max_selected_candidates: 5,
      per_operation_timeout_seconds: 30,
      overall_timeout_seconds: 180,
    },
    started_at: "2026-08-08T05:11:00Z",
    completed_at: "2026-08-08T05:12:00Z",
    stop_reason: "QUEUE_EXHAUSTED",
    error_code: null,
    error_detail: null,
    provider: "deepseek",
    configured_model: "deepseek-v4-flash",
    model_version: "deepseek-v4-flash-2026-08",
    prompt_version: "m3-crawler-v1+m3-selector-v1",
    usage: {
      prompt_tokens: 100,
      completion_tokens: 20,
      total_tokens: 120,
      call_count: 2,
      duration_ms: 400,
      estimated_cost_usd: null,
    },
    schema_version: 1,
    created_at: "2026-08-08T05:11:00Z",
  },
  actions: [
    {
      id: "48eb7e28-ecea-431d-a4b1-1b95129e4893",
      session_id: "741e66ad-c55f-4b15-a847-0fd81e13a87a",
      step: 1,
      tool: "search_papers",
      status: "COMPLETED",
      query: "verifiable agent memory",
      target_semantic_scholar_id: null,
      target_arxiv_id: null,
      positive_paper_ids: [],
      year_from: 2025,
      year_to: 2026,
      requested_limit: 10,
      result_count: 1,
      relation_depth: 0,
      decision_reason: "Initial bounded scholarly query.",
      error_code: null,
      retryable: null,
      error_detail: null,
      duration_ms: 120,
      created_at: "2026-08-08T05:11:00Z",
      completed_at: "2026-08-08T05:11:01Z",
      schema_version: 1,
    },
  ],
  items: [
    {
      candidate: {
        id: "23f30c47-1f68-48c1-af4b-d88504f638ed",
        session_id: "741e66ad-c55f-4b15-a847-0fd81e13a87a",
        external_paper_id: "e004da65-339d-43ea-a490-66c2447b4089",
        semantic_scholar_id: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        local_paper_id: targetPaperId,
        local_paper_version_id: targetPaperVersionId,
        discovered_by_action_id: "48eb7e28-ecea-431d-a4b1-1b95129e4893",
        origins: ["SEARCH", "LOCAL_VECTOR"],
        relation_depth: 0,
        scores: {
          semantic_scholar: 0.8,
          lexical: 0.7,
          vector: 0.9,
          entity_overlap: 0.6,
          citation: 0.4,
          recommendation: 0.2,
          final: 0.78,
        },
        rank: 1,
        decision: "SELECTED",
        decision_reason: "High semantic overlap and matching evaluation task.",
        provider: "deepseek",
        configured_model: "deepseek-v4-flash",
        model_version: "deepseek-v4-flash-2026-08",
        prompt_version: "m3-selector-v1",
        generated_at: "2026-08-08T05:12:00Z",
        verification_status: "UNVERIFIED",
        schema_version: 1,
        created_at: "2026-08-08T05:11:00Z",
      },
      paper: {
        id: "e004da65-339d-43ea-a490-66c2447b4089",
        semantic_scholar_id: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        title: targetPaperDetail.title,
        abstract: "A historical evaluation of memory validation in language-model agents.",
        year: 2025,
        publication_date: "2025-09-01",
        venue: "AgentBench Workshop",
        authors: ["Grace Scientist"],
        external_ids: { ArXiv: "2509.00001", DOI: "10.1000/agent.1" },
        arxiv_id: "2509.00001",
        doi: "10.1000/agent.1",
        citation_count: 12,
        influential_citation_count: 3,
        full_text_available: true,
        source: "semantic_scholar",
        schema_version: 1,
        created_at: "2026-08-08T05:11:00Z",
        updated_at: "2026-08-08T05:12:00Z",
      },
      discoveries: [
        {
          id: "57ba2a03-c303-455e-8978-0ee86df4a780",
          candidate_id: "23f30c47-1f68-48c1-af4b-d88504f638ed",
          action_id: "48eb7e28-ecea-431d-a4b1-1b95129e4893",
          origin: "SEARCH",
          relation_depth: 0,
          discovered_at: "2026-08-08T05:11:01Z",
        },
      ],
      relations: [
        {
          id: "ee9a1044-fb97-4df6-b959-1b6a507ac558",
          source_paper_id: paper.id,
          source_paper_version_id: paperVersionId,
          target_paper_id: targetPaperId,
          target_paper_version_id: targetPaperVersionId,
          relation_type: "EXTENDS",
          provenance: "LLM_INFERRED",
          evidence_ids: [sourceEvidenceId, targetEvidenceId],
          justification: "The new method extends the historical memory-validation protocol.",
          provider: "deepseek",
          model_version: "deepseek-v4-flash-2026-08",
          prompt_version: "m3-comparison-v1",
          confidence: 0.72,
          verification_status: "UNVERIFIED",
          generated_at: "2026-08-08T05:14:00Z",
          schema_version: 1,
          created_at: "2026-08-08T05:14:00Z",
        },
      ],
      comparison_id: comparisonId,
    },
  ],
  comparisons: [],
  total: 1,
};

const comparisonDimensionNames = [
  "RESEARCH_PROBLEM",
  "TASK",
  "METHOD",
  "ARCHITECTURE",
  "DATASETS",
  "BENCHMARKS",
  "BASELINES",
  "METRICS",
  "REPORTED_RESULTS",
  "COMPUTE_OR_INFERENCE_BUDGET",
  "CLAIMED_NOVELTY",
  "LIMITATIONS",
  "CODE_AVAILABILITY",
  "RESULT_COMPARABILITY",
];

const comparison = {
  id: comparisonId,
  search_session_id: relatedWork.session.id,
  source_paper_id: paper.id,
  source_paper_version_id: paperVersionId,
  source_analysis_id: analysisId,
  source_analysis_scope: "FULL_TEXT",
  target_paper_id: targetPaperId,
  target_paper_version_id: targetPaperVersionId,
  target_analysis_id: targetAnalysisId,
  target_analysis_scope: "FULL_TEXT",
  comparability_status: "PARTIALLY_COMPARABLE",
  comparability_reason: "The papers share a task but report different benchmark subsets.",
  summary: "The historical method overlaps with the new memory-verification workflow.",
  dimensions: comparisonDimensionNames.map((name, position) => ({
    id: `00000000-0000-4000-8000-${String(position + 1).padStart(12, "0")}`,
    comparison_id: comparisonId,
    name,
    position,
    source_value: `New paper ${name.toLocaleLowerCase().replaceAll("_", " ")}.`,
    target_value: `Historical paper ${name.toLocaleLowerCase().replaceAll("_", " ")}.`,
    assessment: `Scoped assessment for ${name.toLocaleLowerCase()}.`,
    source_evidence_ids: [sourceEvidenceId],
    target_evidence_ids: [targetEvidenceId],
    schema_version: 1,
    created_at: "2026-08-08T05:14:00Z",
  })),
  relations: relatedWork.items[0].relations,
  evidence: [
    {
      id: sourceEvidenceId,
      analysis_id: analysisId,
      paper_id: paper.id,
      paper_version_id: paperVersionId,
      analysis_scope: "FULL_TEXT",
      section: "3.2 Memory verification",
      excerpt: evidence.items[0].excerpt,
      evidence_type: "SUPPORTS",
      verification_status: "UNVERIFIED",
    },
    {
      id: targetEvidenceId,
      analysis_id: targetAnalysisId,
      paper_id: targetPaperId,
      paper_version_id: targetPaperVersionId,
      analysis_scope: "FULL_TEXT",
      section: "4 Historical protocol",
      excerpt: "The historical protocol checks memory before every tool invocation.",
      evidence_type: "SUPPORTS",
      verification_status: "UNVERIFIED",
    },
  ],
  provider: "deepseek",
  configured_model: "deepseek-v4-flash",
  model_version: "deepseek-v4-flash-2026-08",
  prompt_version: "m3-comparison-v1",
  generated_at: "2026-08-08T05:14:00Z",
  source: "deepseek_structured_comparison",
  verification_status: "UNVERIFIED",
  usage: {
    prompt_tokens: 500,
    completion_tokens: 200,
    total_tokens: 700,
    call_count: 1,
    duration_ms: 950,
    estimated_cost_usd: null,
  },
  schema_version: 1,
  created_at: "2026-08-08T05:14:00Z",
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
  await page.route("**/api/v1/comparisons/*", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(comparison) });
  });
  await page.route(
    /\/api\/v1\/papers(?:\/[^/?]+(?:\/(?:analysis|evidence|related))?)?(?:\?.*)?$/,
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
      } else if (pathname === `/api/v1/papers/${paper.id}/related`) {
        if (url.searchParams.get("paper_version_id") !== paperVersionId) {
          await route.fulfill({ status: 400, body: "related-work version was not pinned" });
          return;
        }
        body = relatedWork;
      } else if (pathname === `/api/v1/papers/${paper.id}`) {
        body = paperDetail;
      } else if (pathname === `/api/v1/papers/${targetPaperId}`) {
        body = targetPaperDetail;
      } else {
        body = { items: [paper], total: 1, limit: 20, offset: 0 };
      }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    },
  );
  await page.route("**/api/v1/daily/**", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(dailyRun) });
  });
  await page.route("**/api/v1/reports/daily*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [report], total: 1, limit: 20, offset: 0 }),
    });
  });
  await page.route("**/api/v1/graph*", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(knowledgeGraph) });
  });
  await page.route("**/api/v1/trends*", async (route) => {
    const requestedWindow = new URL(route.request().url()).searchParams.get("window");
    const snapshots =
      requestedWindow === "7D"
        ? [sevenDayTrend]
        : requestedWindow === "30D"
          ? [thirtyDayTrend]
          : requestedWindow === "90D"
            ? [ninetyDayTrend]
            : [sevenDayTrend, thirtyDayTrend, ninetyDayTrend];
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: snapshots, total: snapshots.length }),
    });
  });
  await page.route("**/api/v1/lineages/**", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(lineage) });
  });
  await page.route(/\/api\/v1\/runs(?:\/[^/?]+)?(?:\?.*)?$/, async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const body =
      pathname === "/api/v1/runs"
        ? { items: [dailyRun.run], total: 1, limit: 50, offset: 0 }
        : runDetail;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
}

test("partial run and grounded paper analysis render without live credentials", async ({ page }) => {
  await installApiFixtures(page);

  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "What changed in this research domain?" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: paper.title, exact: true }).first()).toBeVisible();
  await expect(page.getByText("PARTIAL", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Partial daily run")).toBeVisible();
  await expect(page.getByText("GROBID_INVALID_TEI")).toBeVisible();

  await page.getByRole("link", { name: "Papers", exact: true }).click();

  await expect(page).toHaveURL(new RegExp(`/papers\\?topic=${defaultTopic}$`));
  await expect(page.getByRole("heading", { name: "Papers" })).toBeVisible();
  await expect(page.getByText(paper.title)).toBeVisible();
  await expect(page.getByRole("link", { name: /Open PDF/ })).toHaveAttribute(
    "href",
    paper.pdf_url,
  );

  await page.getByRole("link", { name: paper.title }).click();

  await expect(page).toHaveURL(new RegExp(`/papers/${paper.id}\\?topic=${defaultTopic}$`));
  await expect(page.getByRole("heading", { name: "Analysis of arXiv v2" })).toBeVisible();
  await expect(page.locator(".scope-badge", { hasText: "full text" })).toBeVisible();
  await expect(page.getByText(parsedPaperId)).toBeVisible();
  await expect(page.getByText("grobid / 0.9.0")).toBeVisible();
  await expect(page.getByText(analysis.claims[0].text)).toBeVisible();
  await expect(page.getByText(evidence.items[0].excerpt)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Related work" })).toBeVisible();
  await expect(page.getByText(targetPaperDetail.title)).toBeVisible();
  await expect(page.getByText("queue exhausted")).toBeVisible();
  await expect(page.getByText("Use bounded search and reference expansion.")).toBeVisible();
  await expect(page.getByText("agent memory benchmarks")).toBeVisible();
  await expect(page.getByText("78%")).toBeVisible();
  await expect(page.getByText("AI-inferred")).toBeVisible();
  await expect(page.getByRole("link", { name: /Step 1: search papers/ })).toBeVisible();

  await page.getByRole("link", { name: "Open structured comparison" }).click();

  await expect(page).toHaveURL(
    new RegExp(`/comparisons/${comparisonId}\\?topic=${defaultTopic}$`),
  );
  await expect(page.getByRole("heading", { name: "Structured paper comparison" })).toBeVisible();
  await expect(page.getByText("partially comparable")).toBeVisible();
  await expect(page.getByText(comparison.comparability_reason)).toBeVisible();
  await expect(page.getByText("14 fixed dimensions")).toBeVisible();
  await expect(page.getByRole("row", { name: /result comparability/i })).toBeVisible();
  await expect(page.getByText("AI-generated · unverified")).toBeVisible();
  await expect(page.getByText("AI-inferred")).toBeVisible();
  await expect(page.getByText(comparison.evidence[0].excerpt)).toBeVisible();
  await expect(
    page.getByText(/72% uncalibrated model-assessed evidential confidence/),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: `Evidence 1: ${sourceEvidenceId}` }),
  ).toHaveAttribute("href", `#comparison-evidence-${sourceEvidenceId}`);
  await expect(page.getByRole("link", { name: "Evidence 1" }).first()).toHaveAttribute(
    "href",
    `#comparison-evidence-${sourceEvidenceId}`,
  );
});

test("M4 reports, graph, trends, lineage, and run failures remain traceable", async ({ page }) => {
  await installApiFixtures(page);

  await page.goto("/reports/daily");

  await expect(page.getByRole("heading", { name: "Research report" })).toBeVisible();
  await expect(page.getByText("Partial report")).toBeVisible();
  await expect(page.getByText("GROBID_INVALID_TEI").last()).toBeVisible();
  await expect(page.getByRole("link", { name: "Open evidence in paper" })).toHaveAttribute(
    "href",
    `/papers/${paper.id}?topic=${defaultTopic}#evidence-${sourceEvidenceId}`,
  );

  await page.getByRole("link", { name: "Graph", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Research connections" })).toBeVisible();
  await expect(page.getByRole("img", { name: /3 visible nodes and 2 visible relations/ })).toBeVisible();
  await expect(page.getByText("AI-inferred").first()).toBeVisible();
  await expect(page.getByText("llm inferred / unverified", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Evidence 1 (source)" }).first()).toHaveAttribute(
    "href",
    `/papers/${paper.id}?topic=${defaultTopic}#evidence-${sourceEvidenceId}`,
  );
  await expect(page.getByRole("link", { name: "Evidence 2 (target)" })).toHaveAttribute(
    "href",
    `/papers/${targetPaperId}?topic=${defaultTopic}#evidence-${targetEvidenceId}`,
  );
  await page.getByRole("link", { name: "View related paper lineage" }).click();

  await expect(page.getByRole("heading", { name: "How this work connects" })).toBeVisible();
  await expect(page.getByText("Lineage uncertainty")).toBeVisible();
  await expect(page.getByText("AI-inferred")).toBeVisible();
  await expect(page.getByRole("link", { name: "Evidence 1 (source)" })).toHaveAttribute(
    "href",
    `/papers/${paper.id}?topic=${defaultTopic}#evidence-${sourceEvidenceId}`,
  );
  await expect(page.getByRole("link", { name: "Evidence 2 (target)" })).toHaveAttribute(
    "href",
    `/papers/${targetPaperId}?topic=${defaultTopic}#evidence-${targetEvidenceId}`,
  );

  await page.getByRole("link", { name: "Trends", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Corpus trends" })).toBeVisible();
  await page.getByRole("button", { name: "30 days" }).click();
  await expect(page.getByText("Insufficient data").last()).toBeVisible();
  await expect(page.getByText(/preceding window has zero papers/)).toBeVisible();

  await page.getByRole("link", { name: "Runs", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Run status" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Item stages" })).toBeVisible();
  await expect(page.getByText("GROBID_INVALID_TEI").last()).toBeVisible();
  await expect(page.getByText("parsed").last()).toBeVisible();
});
