# Current Status

## Current milestone

M4 - Knowledge graph, trends, reports, and product UI is complete and verified
locally on exact CPython 3.13.13. The merged M1-M3 ingestion, analysis,
evidence, scholarly-search, embedding, and comparison behavior remains intact.

## Completed capabilities

- A topic-scoped relational knowledge graph persists stable Paper,
  ResearchProblem, Method, Task, Dataset, and Benchmark entities, observed
  mentions, evidence-linked edges, provenance, verification state, and exact
  source analysis/comparison ownership. Public reads are SQL-bounded and expose
  only successful COMPLETE or PARTIAL product publications.
- Research lineages are persisted and projected with evidence ownership,
  explicit versus inferred provenance, chronological paper metadata, cycle-safe
  traversal, permitted predecessor relations, and depth/node/edge limits. The
  UI describes them as the currently retrieved corpus rather than a globally
  complete history.
- Deterministic 7/30/90-day trend snapshots persist exact windows, preceding
  equal-window comparisons, defined zero-denominator behavior, sufficiency,
  distinct-paper entity counts, relation activity, and representative papers.
  API responses apply typed top-N entity limits and expose totals/truncation.
- A separate `PRODUCT_PUBLICATION` run consumes a frozen snapshot of a prior M2
  analysis run plus persisted M3 comparisons. Graph, trend, lineage, and report
  artifacts are run-owned staging data until atomic COMPLETE/PARTIAL
  publication; FAILED runs expose item errors and leave no staged artifacts.
  Explicit retry reuses the stable failed run and its frozen inputs.
- DAILY reports and sufficient-data WEEKLY/MONTHLY reports persist structured
  counts, graph changes, trends, comparisons, lineage highlights, failures,
  missing sections, Evidence references, and narrative provenance. The
  preselected `STRUCTURED_ONLY` and `DEEPSEEK` modes are mode-aware and never
  fall back. DeepSeek output must satisfy strict JSON, ordered sections,
  section-specific Evidence allowlists, and a no-numeric-narrative boundary.
- FastAPI provides bounded graph, trend, lineage, daily/history report, and run
  reads. The OpenAPI-generated React client powers dashboard, daily history,
  Cytoscape graph, Recharts trends, lineage, run/item-failure, paper,
  comparison, and Evidence navigation with visible PARTIAL, insufficiency,
  uncertainty, provenance, and truncation states.
- Stanford STORM was audited at commit
  `fb951af7744dab086e34962e9bc6fe878e145f83`. Its package, source, prompts,
  retrievers, provider layer, and persistence were not copied or installed.
  M4 implements only the reviewed coverage-aware outline-to-section pattern as
  first-party typed synthesis over persisted local data and the existing
  DeepSeek port.

## Verification

- `scripts/verify.ps1` passes on exact CPython 3.13.13: frozen locks, Ruff,
  formatting, Pyright, OpenAPI/TypeScript contract hashes, 434 Python tests
  passed with four explicit credential/model live tests skipped, 26 frontend
  tests, two Playwright Chromium flows, Compose, Terraform, clean Alembic
  upgrade/head/check, PostgreSQL/pgvector integration, and API, Daily, and
  pinned GROBID image builds.
- M4 PostgreSQL verification includes 19 focused publication/repository tests;
  the full integration suite passes 42 tests with four explicit live tests
  skipped. Clean 0001-to-0004 and populated M3-to-M4 upgrades pass, Alembic has
  no drift, and the destructive downgrade guard preserves M3 data unless its
  explicit data-loss flag is supplied.
- The Python, frontend, and infrastructure repository command paths from all
  three GitHub Actions jobs pass, including in-place generated-contract diff
  checks and CI-tagged API/Daily image builds. The local non-administrator shell
  cannot rewrite protected Program Files for the CI bootstrap-only
  `corepack enable` step; the already resolved user-scoped pnpm 11.0.9 executed
  every frontend workflow command successfully.
- A credentialed live `deepseek-v4-flash` report probe exercised only synthetic
  bounded input and emitted no prompt, response, Evidence text, or secret. One
  nonconforming output was rejected as `LLM_OUTPUT_INVALID` without retry or
  fallback; a subsequent independently classified single call passed schema,
  ordered-section, Evidence-allowlist, no-numeric-narrative, and domain checks.

## Deployment

- No production foundation, database, Web/API, Daily Job, GROBID service,
  Scheduler, secret version, or endpoint was created or changed for M4.
- The repository Compose PostgreSQL volume on port 55433 was rebuilt from an
  empty database through the normal Alembic path to
  `0004_m4_graph_trends_reports`; final M3/M4 schema checks and Alembic drift
  detection pass.
- M4 commands are explicit batch operations. The current Terraform Scheduler
  still invokes the Daily image with its default arXiv-ingestion command; it
  does not yet orchestrate analysis, comparison, product publication, or
  periodic reports automatically.

## Current blockers

- Production deployment requires owner-supplied PostgreSQL `DATABASE_URL`,
  fixed DeepSeek and Semantic Scholar Secret Manager versions as applicable,
  and successful Google API/authentication connectivity. The missing local
  Semantic Scholar key blocks only its opt-in live adapter smoke and real M3
  historical calls, not deterministic M4 publication from persisted inputs.
- End-to-end scheduled daily publication remains an M5 deployment task because
  the configured Scheduler currently runs ingestion only.

## Next milestone

M5 - Product hardening and deployment. It has not started. Its scope includes
production secrets/database/authentication, full Daily Job orchestration and
Scheduler verification, operational rollback/backup work, and concurrency
hardening for periodic report generation.
