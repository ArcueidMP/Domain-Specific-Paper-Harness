# Domain-Specific Paper Harness

Domain-Specific Paper Harness is a private research-intelligence product for
broad LLM-agent research. It discovers new and updated arXiv papers, analyzes
selected full text, retrieves approved historical work, builds traceable
comparisons and a provenance-aware knowledge graph, and publishes reports and
trend views through a FastAPI and React product.

This repository contains application code, tests, containers, and Google Cloud
infrastructure. It is not a paper-writing project, a generic search engine, or a
general chatbot.

## Product scope

Included topics are LLM-agent planning, reasoning, memory, tool use, web and
computer-use agents, multi-agent coordination, evaluation, benchmarks, safety,
and security. Daily discovery is arXiv-only.

Historical retrieval is limited to the local corpus and authenticated Semantic
Scholar search, metadata, reference, citation, and recommendation endpoints.
Only papers with an arXiv-hosted PDF enter full-text analysis. Publisher pages
and publisher PDFs are never scraped.

## Implemented capabilities

- Canonical arXiv identity and explicit version tracking with cursor overlap,
  database uniqueness, idempotent ingestion, and PostgreSQL advisory locking.
- Exact CPython 3.13.13 runtime, uv lock, FastAPI read API, generated TypeScript
  contract, React product views, Alembic migrations, PostgreSQL, and pgvector.
- Strict DeepSeek structured analysis, private GROBID full-text parsing,
  evidence and claim provenance, explicit analysis scope, and item failures.
- Authenticated Semantic Scholar retrieval, bounded PaSa-derived search,
  SPECTER2 title-and-abstract embeddings, historical materialization, and
  structured evidence-linked comparisons.
- Deterministic graph, lineage, 7/30/90-day trends, daily and periodic reports,
  run status, partial-state banners, and item-level failure display.
- M5 pipeline accounting, bounded execution, dependency-license review,
  container hardening, Terraform resources, and production operator scripts.

Migration `0005_m5_pipeline_provenance` is the current schema addition for the
complete Daily pipeline and its provenance. Existing merged migrations remain
immutable.

## Architecture

The code is a Ports-and-Adapters modular monolith with three deployable units:

1. `web-api`: FastAPI under `/api/v1`, health endpoints, and the production
   React build. It has no DeepSeek or Semantic Scholar credential.
2. `daily`: a bounded Cloud Run Job that performs discovery through atomic
   product publication.
3. `grobid`: the sole scientific PDF parser, deployed as an IAM-private Cloud
   Run service.

PostgreSQL 15 or newer with pgvector and `DATABASE_URL` is the only persistence
contract. FastAPI is read-oriented and never starts the Daily pipeline.

```text
external system -> adapter -> port -> application use case -> domain
```

The production defaults are `asia-southeast1`, a 05:00 schedule in
`Asia/Kuala_Lumpur`, zero minimum Cloud Run instances, immutable Artifact
Registry image digests, Secret Manager numeric versions, and an IAP owner
allowlist.

See [Architecture](docs/ARCHITECTURE.md), [Boundaries](docs/BOUNDARIES.md),
[Failure policy](docs/FAILURE_POLICY.md), and the [Runbook](docs/RUNBOOK.md).

## Prerequisites

- Windows PowerShell 5.1 or PowerShell 7
- CPython 3.13.13, normally managed by uv
- uv 0.12.3 or compatible current project tooling
- Node.js 24 and Corepack/pnpm from `packageManager`
- Docker Desktop with Compose
- Terraform matching `infra/terraform/versions.tf`
- Google Cloud CLI for production operations
- PostgreSQL 15+ with pgvector

Do not substitute another Python version. First-party Python is pinned to
`>=3.13.13,<3.14` everywhere.

## Local setup

From the repository root:

```powershell
uv python install 3.13.13
uv sync --frozen --python 3.13.13
corepack pnpm install --frozen-lockfile
docker compose up --detach --wait db
$env:DATABASE_URL = "postgresql+psycopg://paper_harness:paper_harness_local@localhost:5432/paper_harness"
uv run --frozen --python 3.13.13 alembic upgrade head
```

Start the API and React development server with:

```powershell
scripts/dev.ps1
```

The defaults are `http://127.0.0.1:8000` for the API and
`http://127.0.0.1:5173` for the web app.

## Verification

The sole canonical Windows verification entry point is:

```powershell
scripts/verify.ps1
```

It runs frozen Python and frontend dependency checks, Ruff, Pyright, pytest,
frontend lint/typecheck/unit/build/browser checks, generated API contract checks,
Docker Compose validation, Terraform format/validation, a clean Alembic upgrade,
PostgreSQL integration tests, and the three focused runtime image builds.

Default verification requires no live cloud or provider credentials.

## Local Daily run

Set only the credentials required by the selected operation. Never store them
in a repository file.

```powershell
$env:DATABASE_URL = "postgresql+psycopg://..."
$env:DEEPSEEK_API_KEY = "..."
$env:SEMANTIC_SCHOLAR_API_KEY = "..."
$env:GROBID_URL = "http://127.0.0.1:8070"
$env:GROBID_AUTH_MODE = "none"
scripts/run-daily.ps1 run-pipeline
```

The full pipeline is bounded by configured paper counts, search limits,
timeouts, retries, comparison limits, and a global application deadline. It
fails closed when a required dependency or configuration is missing.

## Production deployment

Production operations are direct and deliberately small:

1. Inspect the active account, project, billing, APIs, existing resources,
   remote Terraform state, secret version metadata, and current image digests.
2. Create the remote state bucket once, if it does not already exist, with
   `scripts/bootstrap-terraform-state.ps1`.
3. Add secret values through `scripts/add-secret-version.ps1`; never place a
   value in Terraform variables or command history.
4. Build and optionally push only the changed runtime images with
   `scripts/build-images.ps1 -Component ...`.
5. Put immutable image digests and enabled numeric secret versions in an
   untracked production `.tfvars` file.
6. Run `scripts/deploy.ps1` without `-Apply`, inspect the Terraform plan, and
   rerun with `-Apply` only when the planned changes are authorized.
7. Run `scripts/run-production-migration.ps1` and confirm Alembic head.
8. Verify private service and IAM configuration with
   `scripts/verify-private-runtime.ps1`.
9. Run one direct Daily execution with `scripts/run-production-daily.ps1`, then
   verify persisted reports, graph, trends, lineage, and read API output.
10. Create Scheduler paused through Terraform and inspect it with
    `scripts/verify-scheduler.ps1`. Then apply `scheduler_paused = false`
    through Terraform before triggering one Scheduler invocation and confirming
    that it creates one Daily execution. Cloud Scheduler does not run paused
    jobs, including manual invocations.

No deployment script grants temporary IAM roles. No script creates a public
endpoint. Terraform apply, secret changes, job executions, and Scheduler
changes remain explicit operator actions.

## Required production secrets

Secret Manager holds values; Terraform receives only secret names and enabled
numeric versions.

- `paper-harness-database-url`: normalized direct or session-affine PostgreSQL
  URL using `postgresql+psycopg://` and TLS.
- `paper-harness-deepseek-api-key`: required only by the Daily Job.
- `paper-harness-semantic-scholar-api-key`: a real non-empty API key required
  only by authenticated historical and related-work operations.

The browser never accesses PostgreSQL or Secret Manager directly. Service
accounts receive only the secret versions required by their runtime.

## Current limitations

- The repository does not provision a managed PostgreSQL service. A compatible
  production `DATABASE_URL` must be supplied externally.
- GROBID full-text processing is intentionally unavailable when its private
  service fails; there is no parser fallback.
- DeepSeek, Semantic Scholar, SPECTER2, PostgreSQL, and full-text analysis have
  no implicit production substitutes.
- Weekly and longer synthesis requires sufficient persisted source coverage;
  insufficient windows are reported honestly.
