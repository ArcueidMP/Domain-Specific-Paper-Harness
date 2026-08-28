# Domain-Specific Paper Harness

Domain-Specific Paper Harness is a private multi-topic research-intelligence
product. The production MVP is deployed. It discovers new and updated arXiv
papers, analyzes selected full text, retrieves approved historical work, builds
traceable comparisons and a provenance-aware knowledge graph, and publishes
reports and trend views through a FastAPI and React product.

This repository contains application code, tests, containers, and Google Cloud
infrastructure. It is not a paper-writing project, a generic search engine, or a
general chatbot.

## Product scope

The initial independent topics are Broad LLM Agents, Brain-Computer Interfaces,
and World Models. Each topic has its own arXiv query, cursor, Daily runs,
reports, graph, trends, and lineage. Daily discovery remains arXiv-only.

Historical retrieval is limited to the local corpus and authenticated Semantic
Scholar search, metadata, reference, citation, and recommendation endpoints.
Only papers with an arXiv-hosted PDF enter full-text analysis. Publisher pages
and publisher PDFs are never scraped.

## Implemented capabilities

- Canonical arXiv identity and explicit version tracking with cursor overlap,
  database uniqueness, idempotent ingestion, and PostgreSQL advisory locking.
- Independent Broad LLM Agents, Brain-Computer Interfaces, and World Models
  TopicConfigs with topic-scoped Jobs, reports, graph, trends, lineage, API
  queries, and frontend selection.
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
- Optional public-demo data isolation through a separately migrated PostgreSQL
  `demo` schema, least-privilege sync/read roles, deterministic canonical
  snapshots, and a non-blocking main-branch synchronization workflow.

Migration `0006_topic_reprocessing` adds additive same-date publication
revisions while preserving earlier analyses and reports. Existing merged
migrations remain immutable.

## Architecture

The code is a Ports-and-Adapters modular monolith with three deployable units:

1. `web-api`: FastAPI under `/api/v1`, health endpoints, and the production
   React build. It has no DeepSeek or Semantic Scholar credential.
2. `daily`: three topic-specific bounded Cloud Run Jobs sharing one image and
   performing discovery through atomic product publication.
3. `grobid`: the sole scientific PDF parser, deployed as an IAM-private Cloud
   Run service.

PostgreSQL 15 or newer with pgvector and `DATABASE_URL` is the only persistence
contract. FastAPI is read-oriented and never starts the Daily pipeline.
`DATABASE_SCHEMA` defaults to `public`; only the optional Demo runtimes set it
to `demo`.

```text
external system -> adapter -> port -> application use case -> domain
```

The production defaults are `asia-southeast1`, staggered 20:00/20:20/20:40
schedules in `Asia/Kuala_Lumpur`, after the daily arXiv announcement, zero
minimum Cloud Run instances, immutable Artifact Registry image digests, Secret
Manager numeric versions, and an IAP owner allowlist.

See [Architecture](docs/ARCHITECTURE.md), [Boundaries](docs/BOUNDARIES.md),
[Failure policy](docs/FAILURE_POLICY.md), [Current status](docs/STATUS.md), and
the [Runbook](docs/RUNBOOK.md).

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

Select another topic explicitly when needed:

```powershell
scripts/run-daily.ps1 run-pipeline --topic-config configs/topics/world-models.yaml
```

The full pipeline is bounded by configured paper counts, search limits,
timeouts, retries, comparison limits, and a global application deadline. It
fails closed when a required dependency or configuration is missing.

## Production deployment

Production operations are direct and deliberately small:

1. Inspect the active account, project, billing, APIs, and existing resources.
2. Create the remote state bucket once, if it does not already exist, with
   `scripts/bootstrap-terraform-state.ps1`.
3. Add secret values through `scripts/add-secret-version.ps1`; never place a
   value in Terraform variables or command history.
4. Build and optionally push only the changed runtime images with
   `scripts/build-images.ps1 -Component ...`.
5. Put the built image references and enabled Secret Manager versions in an
   untracked production `.tfvars` file.
6. Run `scripts/deploy.ps1` without `-Apply`, inspect the Terraform plan, and
   rerun with `-Apply` only when the planned changes are authorized.
7. Run `scripts/run-production-migration.ps1` and confirm Alembic head.
8. Verify private service and IAM configuration with
   `scripts/verify-private-runtime.ps1`.
9. Run each topic Job directly with `scripts/run-production-daily.ps1`, then
   verify its persisted report, graph, trends, lineage, and read API output.
   Add `-LogicalDate YYYY-MM-DD -Reprocess` for an additive same-date revision.
10. Apply and inspect the three one-to-one Scheduler targets through Terraform.

No deployment script grants temporary IAM roles. No script creates a public
endpoint. Terraform apply, secret changes, job executions, and Scheduler
changes remain explicit operator actions.

## Required production secrets

Secret Manager holds values; Terraform receives only secret names and enabled
numeric versions.

- `paper-harness-database-url`: normalized direct or session-affine PostgreSQL
  URL using `postgresql+psycopg://` and TLS.
- `paper-harness-deepseek-api-key`: required only by the Daily Jobs.
- `paper-harness-semantic-scholar-api-key`: a real non-empty API key required
  only by authenticated historical and related-work operations.
- `paper-harness-demo-sync-database-url` and
  `paper-harness-demo-read-database-url`: optional same-database credentials for
  deterministic snapshot synchronization and a future read-only public Demo.

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
- Demo schema roles, secret values, GitHub OIDC variables, and Cloudflare
  hosting are not provisioned by default. The private production service remains
  the only deployed Web/API until those optional resources are explicitly
  configured.
- arXiv PDFs above the configured 30 MiB ingestion bound remain item-level
  analysis failures. Their source metadata remains visible in honest `PARTIAL`
  reports, while unavailable related work, comparisons, graph, trends, and
  lineage are labeled without blocking Daily publication.
