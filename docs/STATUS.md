# Current Status
## Current milestone

M5 product hardening and production deployment are complete.

## Completed capabilities

M1-M4 product functionality is implemented: arXiv ingestion and versioning,
strict DeepSeek/GROBID analysis, evidence, authenticated Semantic Scholar and
bounded related-work retrieval, structured comparisons, graph, trends,
lineages, reports, the FastAPI read API, and the React product.

M5 source includes migration `0005_m5_pipeline_provenance`, complete Daily
orchestration, execution bounds and accounting, repository and generated-artifact
hygiene, focused dependency-license policy, container hardening, Terraform
runtime resources, and direct production scripts. External optional metadata,
collection order, and individual candidate failures are normalized or handled at
item level; required identity, timestamps, authentication, database, migration,
and publication transaction boundaries remain strict.

Production execution `paper-harness-daily-n6zqg` published a `PARTIAL` Daily
result with one completed selected paper. Its DAILY report, graph, 7/30/90-day
trends, and lineage are persisted and visible through the private product. The
Scheduler acceptance execution `paper-harness-daily-rkhpj` completed
successfully with the same persisted `PARTIAL` product result.

## Verification

The final canonical `scripts/verify.ps1 -PostgresPort 55433` invocation passed:
Ruff, formatting, Pyright, contracts, frontend checks, Playwright, Terraform,
clean Alembic migration to `0005`, 863 tests with four live opt-ins skipped,
and all three container images. Production persistence and authenticated browser
acceptance also passed.

## Deployment

The existing Google Cloud project has Web/API and GROBID Cloud Run services,
Daily and migration Cloud Run Jobs, immutable Artifact Registry images, fixed
Secret Manager versions, owner-only IAP, and migration `0005` at database head.
The private Web/API is available at
`https://paper-harness-web-nxdmkbsdtq-as.a.run.app`.

Cloud Scheduler job `paper-harness-daily` is enabled at `0 5 * * *` in
`Asia/Kuala_Lumpur`. Its authenticated forced invocation created execution
`paper-harness-daily-rkhpj`.

## Current blockers

No external blocker remains.

## Next milestone

Operate the enabled Daily schedule and review item-level `PARTIAL` diagnostics
through the private product.
