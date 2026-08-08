# Current Status

## Current milestone

M2 - Structured analysis and evidence is complete and accepted locally. M3 has
not started, and no production resource has been deployed.

## Completed capabilities

- M1 platform and arXiv-only ingestion remain intact: exact CPython 3.13.13,
  explicit versions, cursor overlap, advisory locking, atomic persistence,
  PostgreSQL 15+ with pgvector, FastAPI/OpenAPI, and React.
- M2 adds explicit `FULL_TEXT` and `ABSTRACT_ONLY` analysis scopes, strict
  DeepSeek `deepseek-v4-flash` validation, and GROBID 0.9.0 CRF as the sole
  full-text parser with no scope, parser, provider, or malformed-JSON fallback.
- Parsed text, analyses, claims, evidence, provenance, model usage, stable
  ownership links, and failure records use normalized M1-to-M2 migrations.
  Analysis bundles and `COMPLETE`/`PARTIAL` report publication are atomic.
- FastAPI and the generated frontend contract expose paper analysis, evidence,
  run history, reports, and item failures. React shows provenance, grounded
  excerpts, prominent `PARTIAL` state, and stage/error/retryability detail.
- PaperQA2 `v2026.03.18` was audited at an exact commit and rejected for code or
  package reuse; `source_copied` remains false.
- Compose contains an opt-in, digest-pinned hardened GROBID service. Terraform
  contains a separately gated IAM-private GROBID service and Daily invocation
  identity without `allUsers`, a load balancer, VPC connector, NAT, or fixed-cost
  runtime resource.

## Verification

- `scripts/verify.ps1` passed on exact CPython 3.13.13: Ruff, Pyright, generated
  OpenAPI and TypeScript drift checks, 189 Python tests passed with the two
  opt-in live tests skipped, 11 frontend unit tests, production build, one
  Chromium smoke, Compose, Terraform, a clean M2 Alembic upgrade/check,
  PostgreSQL integration,
  and the Web/API, Daily, and digest-pinned GROBID image builds.
- Both opt-in live tests passed separately. A real arXiv Atom record reached the
  migrated PostgreSQL repository, and arXiv `1706.03762v7` was downloaded and
  processed by the hardened local GROBID 0.9.0 CRF service into typed non-empty
  sections, passages, coordinates, and references. All disposable containers,
  networks, and verification volumes were removed afterward.

## Deployment

- The configured deployment target remains the existing billing-enabled GCP
  project in `asia-southeast1`. No foundation, Web/API, Daily, GROBID, Scheduler,
  secret version, image, or public endpoint has been deployed.
- Foundation apply previously stopped before resource creation because the
  Terraform Google provider could not establish TCP 443 connections to Google
  API endpoints.
- Terraform describes direct-IAP Web/API access and an IAM-authenticated GROBID
  Cloud Run service invoked only by the Daily service account. This is validated
  configuration, not deployed state. Private invocation has not been exercised
  in GCP.

## Current blockers

- Production deployment requires an owner-supplied PostgreSQL 15+ pgvector
  `DATABASE_URL` in an enabled, fixed Secret Manager version.
- Live M2 analysis and analysis-resource deployment require an owner-supplied
  DeepSeek API key in an enabled, fixed Secret Manager version.
- Terraform apply and GCP IAM-private service-invocation verification require
  this workstation to establish TCP 443 connections to Google API endpoints.

## Next milestone

M3 - PaSa and Semantic Scholar comparison is next but has not started. Retry the
safe GCP deployment when the documented external blockers are cleared; do not
weaken privacy or introduce a fixed-cost resource to bypass them.
