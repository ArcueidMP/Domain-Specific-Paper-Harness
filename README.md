# Domain-Specific Paper Harness

Domain-Specific Paper Harness is a private research-intelligence product for
tracking broad LLM-agent research. M1 and M2 are implemented locally: the system
ingests versioned arXiv metadata, analyzes explicitly selected paper versions
with strict DeepSeek output, grounds claims in abstract or GROBID-parsed text,
and presents analyses, evidence, reports, and item failures through FastAPI and
React. No production runtime is deployed yet.

The permanent product, safety, source, and engineering rules are in
[AGENTS.md](AGENTS.md). Current milestone and deployment state are in
[docs/STATUS.md](docs/STATUS.md).

## Product purpose and scope

The product covers LLM-centered planning, reasoning, memory, tool use, web and
computer-use agents, multi-agent coordination, evaluation, benchmarks, safety,
and security. It excludes ordinary chatbots, pure RAG without an agent workflow,
conventional non-LLM reinforcement-learning agents, agent-based simulation, and
embodied systems without a material LLM-agent component.

Source boundaries are strict:

- daily discovery uses arXiv only;
- only an arXiv-hosted PDF may enter full-text analysis;
- M3 historical and related-work search may use Semantic Scholar, the persisted
  corpus, and a bounded PaSa-derived workflow;
- publisher scraping, paywall bypass, publisher-PDF download, arbitrary web
  search, and hidden metadata-provider fallbacks are prohibited; and
- paper text is untrusted content, never an instruction to the analysis runtime.

## Implemented capabilities

### Platform and ingestion

- Exact CPython 3.13.13 and frozen uv/pnpm dependency sets.
- Validated topic configuration for broad LLM-agent research.
- arxiv.py 4.0.0 behind `ArxivPort`, with bounded timeouts, transient retries,
  `Retry-After`, complete-window saturation checks, and no partial cursor advance.
- Canonical arXiv identities, explicit versions, overlap windows, PostgreSQL
  advisory locking, database idempotency, and atomic batch/cursor/run completion.
- PostgreSQL 15+ with pgvector, normalized schemas, and explicit Alembic
  migrations through `0002_m2_structured_analysis`.
- A protected operator CLI and a separate Daily Job entrypoint. FastAPI never
  runs the pipeline through startup hooks, background tasks, or a public run
  endpoint.

### Structured analysis and evidence

- Explicit `FULL_TEXT` and `ABSTRACT_ONLY` scopes selected before execution and
  persisted on the run and each successful analysis. Parser failure never
  changes the selected scope, including zero-success runs.
- DeepSeek is the sole analysis provider, fixed to `deepseek-v4-flash` behind
  `LLMPort`. Configuration, authentication, response envelopes, JSON schemas,
  domain invariants, usage totals, and evidence grounding are validated before
  persistence. Malformed JSON is rejected rather than repaired or retried.
- GROBID 0.9.0 CRF is the sole scientific PDF parser behind `PdfParserPort`.
  Requests disable metadata consolidation, retain raw citations and source
  coordinates, and reject invalid or oversized PDF/TEI data. No parser fallback
  exists.
- Parsed sections, passages, references, citation contexts, analyses, claims,
  evidence-to-claim links, model usage, prompt/model versions, source scope,
  verification status, and generation timestamps are normalized in PostgreSQL.
- Analysis, claims, evidence, and their ownership links commit atomically. Run
  finalization and deterministic `COMPLETE` or `PARTIAL` report publication are
  one transaction; failed items retain stage, stable error code, retryability,
  and concise detail.
- FastAPI exposes paper detail, structured analysis, grounded evidence, run
  history, and latest-run report/failure data from its generated OpenAPI
  contract. React provides paper analysis and evidence views plus prominent
  `PARTIAL` and item-failure display.
- PaperQA2 `v2026.03.18` at commit
  `ac4ff91ad703e6816cb620ea579a98ca0c42c36f` was audited and rejected for code
  or package reuse because its parser, providers, index, malformed-JSON repair,
  and provenance model conflict with this product. M2 uses project-owned
  deterministic grounding functions and adds no PaperQA2 dependency.

## Architecture summary

The repository is a Ports-and-Adapters modular monolith with three independently
deployable runtime units:

```mermaid
flowchart TD
    Browser["Owner browser"] --> IAP["Direct Cloud Run IAP"]
    IAP --> Web["Web/API service<br/>React + FastAPI"]
    Web --> DB[("PostgreSQL 15+ + pgvector")]
    Scheduler["Cloud Scheduler<br/>05:00 Asia/Kuala_Lumpur"] --> Daily["Daily Cloud Run Job"]
    Daily --> Arxiv["arXiv API and arXiv-hosted PDFs"]
    Daily --> DeepSeek["DeepSeek V4 Flash"]
    Daily --> Grobid["IAM-private GROBID 0.9.0 CRF"]
    Daily --> DB
```

This is the configured deployment shape, not a claim that production resources
exist. Dependencies point inward from adapters to ports, application use cases,
and the domain. PostgreSQL is the source of truth. The target excludes Cloud SQL,
Kubernetes, Redis, Celery, Neo4j, a permanent worker, public unauthenticated
endpoints, and unapproved fixed-cost networking.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/BOUNDARIES.md](docs/BOUNDARIES.md), and
[docs/FAILURE_POLICY.md](docs/FAILURE_POLICY.md) for detailed boundaries.

## Prerequisites

Development targets Windows with PowerShell:

- Git;
- CPython 3.13.13 exactly;
- uv 0.12.3 or a compatible repository-approved uv release;
- Node.js 24.x and pnpm 11.0.9 through Corepack;
- Docker Desktop with Docker Compose and a running Linux engine;
- Terraform 1.15.8; and
- Google Cloud CLI for deployment work.

The current workstation resolves uv and Terraform under `D:\Tools` when they
are not already on `PATH`. Every first-party Python environment and container
must use CPython 3.13.13; another Python release is not a fallback.

## Local setup

From the repository root:

```powershell
Copy-Item .env.example .env
D:\Tools\uv\uv.exe sync --frozen --python 3.13.13
corepack pnpm install --frozen-lockfile
docker compose up --detach --wait db
$env:DATABASE_URL = "postgresql+psycopg://paper_harness:paper_harness_local@localhost:5432/paper_harness"
D:\Tools\uv\uv.exe run --frozen --python 3.13.13 alembic upgrade head
```

`.env` is ignored. Never place a real secret in a tracked file. Stop PostgreSQL
without deleting its volume with `docker compose down`; use `--volumes` only
when intentionally discarding local data.

## Verification

The sole canonical Windows verification entrypoint is:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify.ps1
```

It verifies exact Python and frozen dependencies, Ruff, formatting, strict
Pyright, FastAPI/OpenAPI/generated-TypeScript drift, frontend lint/typecheck/unit
tests/build, credential-free Playwright, Compose, Terraform, clean and M1-to-M2
Alembic upgrades against disposable pgvector PostgreSQL, repository/contract/unit
tests, and the Web/API, Daily Job, and pinned GROBID wrapper images. It builds the
GROBID wrapper but does not start a live GROBID service.

Default verification is deterministic: it does not call live arXiv, DeepSeek,
Semantic Scholar, or Google Cloud, and it does not start a GROBID service. The
explicit real-arXiv test remains opt-in through `RUN_LIVE_ARXIV_TEST=1` and
`TEST_DATABASE_URL`.

## Local run

Start PostgreSQL, apply migrations, and launch FastAPI plus Vite:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
```

The web application is at `http://127.0.0.1:5173`; API documentation is at
`http://127.0.0.1:8000/api/docs`.

Run arXiv ingestion explicitly:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://paper_harness:paper_harness_local@localhost:5432/paper_harness"
powershell -ExecutionPolicy Bypass -File scripts/run-daily.ps1
```

M2 analysis is a separate protected operation over selected persisted paper
UUIDs. For full-text analysis, start the pinned local GROBID service and provide
the DeepSeek key only in the local environment:

```powershell
docker compose --profile analysis up --detach --wait grobid
$env:GROBID_URL = "http://127.0.0.1:8070"
$env:GROBID_AUTH_MODE = "none"
$env:LLM_PROVIDER = "deepseek"
$env:LLM_MODEL = "deepseek-v4-flash"
$env:DEEPSEEK_API_KEY = "<local-secret>"
$paperId = "replace-with-a-persisted-paper-uuid"
D:\Tools\uv\uv.exe run --frozen --python 3.13.13 paper-harness analyze-papers `
  --paper-id $paperId `
  --analysis-scope full_text
```

Choose `abstract_only` before execution to analyze only the persisted abstract;
that mode does not call or require GROBID but still requires DeepSeek. A failed
full-text parse never changes to abstract-only analysis.

## Deployment

Deployment uses the existing GCP project and defaults to `asia-southeast1`.
Terraform remains foundation-first and the deployment script rejects delete or
replacement plans. The web service uses direct Cloud Run IAP. The optional M2
GROBID service has minimum instances zero, maximum instances one, concurrency
one, no `allUsers` binding, and only the Daily service account receives
`roles/run.invoker`. Daily obtains an ephemeral identity token for the GROBID
service URL; no VPC connector, NAT gateway, load balancer, or fixed-cost runtime
resource is introduced.

`deploy_runtime_resources=true` requires immutable Web/API and Daily image
digests plus a fixed `DATABASE_URL` secret version. M2 additionally requires
`deploy_analysis_resources=true`, an immutable mirrored GROBID wrapper digest,
and a fixed DeepSeek secret version. Secret values never enter Terraform state.

No foundation or runtime resource is currently deployed. The earlier foundation
apply could not establish TCP 443 connections to Google API endpoints and stopped
before resource creation. Production deployment is also blocked on an
owner-supplied PostgreSQL `DATABASE_URL` and DeepSeek API key in Secret Manager.

## Configuration and required secrets

| Name | Current contract |
| --- | --- |
| `DATABASE_URL` | Required for persistence; production must use PostgreSQL 15+ with pgvector through a fixed Secret Manager version |
| `DEEPSEEK_API_KEY` | Required only for structured analysis; no mock, anonymous access, or alternate model |
| `LLM_PROVIDER` | Must be `deepseek` for M2 |
| `LLM_MODEL` | Must be `deepseek-v4-flash` for M2 |
| `GROBID_URL` | Required only for `full_text`; local URL may be HTTP, production must be the private HTTPS Cloud Run URI |
| `GROBID_AUTH_MODE` | `none` for local development; production requires `google_identity` |
| `GROBID_AUDIENCE` | Required with Google identity and equal to the private GROBID service audience |
| `SEMANTIC_SCHOLAR_API_KEY` | Reserved for authenticated M3 scholarly search; unused by M2 |

The read API starts without DeepSeek, GROBID, or Semantic Scholar credentials.
Only the operation that needs a dependency validates it, and missing configuration
fails explicitly.

## Current limitations

- Production PostgreSQL and DeepSeek Secret Manager versions are not supplied,
  and Google API connectivity currently prevents Terraform apply.
- The strict DeepSeek adapter is fixture-tested but no live DeepSeek analysis has
  been run because no key was supplied.
- Daily ingestion and selected-paper analysis are separate operator/Job commands;
  automatic discovery-to-selection-to-analysis orchestration is not yet wired.
- The CRF GROBID image is CPU-bounded and smaller than the full deep-learning
  image, but its lower extraction accuracy is an explicit provenance limitation.
- M3 Semantic Scholar/PaSa/SPECTER2 comparison and M4 graph, lineage, trends, and
  complete daily/historical report views are not implemented.
- Terraform state is local and ignored; a reviewed remote-state strategy is
  required before multi-operator production use.
- Full PDFs, complete prompts/responses, model weights, secrets, and credentials
  are never stored in Git.
