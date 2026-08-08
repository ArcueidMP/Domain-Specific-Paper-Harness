# Current Status

## Current milestone

M1 — Platform and reliable ingestion is implemented and accepted locally. M2 is
deferred by owner direction.

## Completed capabilities

- Exact CPython 3.13.13, frozen uv/pnpm workspaces, CI, Compose, two production
  images, and a validated two-phase Terraform deployment.
- Versioned/idempotent arXiv-only ingestion with overlap, advisory locking,
  bounded transport behavior, saturation safety, atomic persistence, and
  explicit run state.
- PostgreSQL 15+ with pgvector, clean Alembic migration, normalized M1 schema,
  read-oriented FastAPI/OpenAPI, and an initial React dashboard/paper corpus.
- Permanent architecture, trust-boundary, failure-policy, reuse, notice, and
  operator documentation.

## Verification

- `scripts/verify.ps1`: passed in full on CPython 3.13.13, including Ruff,
  formatting, strict Pyright, contract drift, 37 Python tests with the explicit
  live test skipped, 5 Vitest tests, 1 Playwright Chromium test, clean pgvector
  migration checks, Terraform validation, and both Docker image builds.
- Explicit real arXiv `1706.03762` to disposable PostgreSQL: 1 passed.
- Production dependency audits: no known pnpm or Python package
  vulnerabilities; Python remediation upgraded pytest to 9.1.1 (the local,
  unpublished application package is not present in the public audit index).

## Deployment

- Billing-enabled project: `dsp-paper-harness-sg-c75705`; default region:
  `asia-southeast1`.
- Reviewed foundation plan: 19 add, 0 change, 0 destroy; plan-level deletion and
  replacement guard passed.
- Foundation apply was attempted but stopped before resource creation because
  the Terraform Google provider could not complete TCP connections to Google API
  endpoints. Post-attempt checks found no `paper-harness` service accounts and
  none of the target Artifact Registry, Secret Manager, Cloud Run, or Scheduler
  APIs/resources applied.
- No image was pushed and no Web/API service, Daily Job, Scheduler, or public
  endpoint exists.

## Current blockers

- Re-run the reviewed foundation apply when this workstation can establish TCP
  443 connections to Google API endpoints.
- Runtime deployment then requires an owner-supplied production PostgreSQL
  `DATABASE_URL` as an enabled `paper-harness-database-url` Secret version.

## Next milestone

M2 — Structured analysis and evidence remains deferred. Do not begin it until
the owner explicitly resumes implementation.
