# Domain-Specific Paper Harness

Domain-Specific Paper Harness is a private research-intelligence product for
tracking broad LLM-agent research. M1 through M4 are implemented and verified
locally. M4 adds a provenance-aware knowledge graph, bounded research lineages,
deterministic 7/30/90-day trends, structured daily and sufficient-data periodic
reports, and the complete read-only product UI. No production runtime is
deployed.

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
- PostgreSQL 15+ with pgvector, normalized schemas, and explicit sequential
  Alembic migrations.
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

### Historical search and comparison (M3)

- A typed authenticated Semantic Scholar adapter covers paper search, metadata,
  references, citations, and recommendations with strict schema/pagination
  validation, bounded transient retries, explicit arXiv-to-Semantic-Scholar
  source mapping, and no anonymous production access.
- A first-party PaSa-derived Crawler/Selector persists bounded sessions,
  scholarly actions, candidate discoveries, component scores, decisions, stop
  reasons, the Crawler query/expansion decision, and model usage even when a
  later stage fails. PaSa was audited as an architecture reference only; no
  source, prompt, checkpoint, training code, or independent database is copied.
- Six-month backfill persists its exact query plan and next-query index, stable
  external identities, bibliographic/arXiv availability stubs, representative
  ranks, and embedding provenance. Interrupted `RUNNING` and explicitly retried
  `FAILED` rows resume with identical settings from the last committed query
  boundary.
- Evidence-linked comparisons require a completed search session, a selected
  local historical candidate, and analyses/evidence for both exact versions.
  Search sessions and comparisons pin the exact analysis IDs/scopes that formed
  their inputs. The matrix, comparability, evidence links, model provenance, and
  relations are one atomic persistence bundle.
- Relation confidence is uncalibrated model-assessed evidential support, not a
  probability or verification result. The comparison UI must keep that score
  separate from its visible `UNVERIFIED` status.
- Ai2 Scholar QA was also audited as an architecture reference only. No Scholar
  QA package or code is installed, copied, or invoked.
- The v0.1 embedding model is the official `allenai/specter2_base` revision
  `3447645e1def9117997203454fa4495937bfbd83`, with the tokenizer pinned to the
  same revision. It produces 768-dimensional, unnormalized CLS embeddings from
  title + separator token + abstract with a 512-token maximum. Full model and
  tokenizer provenance is persisted with every embedding. There is no generic,
  commercial, adapter, or alternate-model fallback.

### Knowledge graph, trends, reports, and product UI (M4)

- A distinct `PRODUCT_PUBLICATION` run references a prior structured-analysis
  run and consumes only persisted M2 analyses/evidence plus M3 comparisons. It
  never rewrites M2 terminal items or performs arbitrary search.
- Topic-scoped Paper, ResearchProblem, Method, Task, Dataset, and Benchmark
  entities use conservative exact-key normalization. Mentions and eleven edge
  types retain exact paper/version/analysis/comparison ownership, supporting
  Evidence IDs, provenance, model metadata where applicable, confidence
  meaning, and verification status.
- Graph reads independently bound nodes, edges, and mentions and expose exact
  totals/truncation. Lineage follows directed predecessor relations and is
  deterministic, cycle-safe, and independently bounded by depth, nodes, and
  edges; it states corpus scope, truncation, and explicit/verified predecessor
  availability rather than claiming global completeness.
- Exact 7-, 30-, and 90-day windows compare against equal preceding windows.
  Paper/entity/relation counts, sufficiency thresholds, zero/small-denominator
  handling, new/recurring entities, and representative-paper ranking are
  deterministic and persisted. Trend API entity rows are Top-N bounded with
  total/truncation metadata.
- Daily reports persist a fixed five-section structure, deterministic counts,
  highlights, graph/trend/lineage links, failures, missing sections, evidence
  links, and complete model provenance. `DEEPSEEK` and `STRUCTURED_ONLY` are
  explicit preselected modes; the latter is never a fallback after model
  failure. Model narrative cannot add statistics and each section has its own
  Evidence-ID allowlist.
- Weekly synthesis requires seven daily dates and at least three papers;
  monthly synthesis requires at least twenty daily dates and ten papers.
  Insufficient periods persist no aggregate report.
- FastAPI exposes graph, trends, lineages, latest/dated daily publications,
  report history, exact weekly/monthly periods, and historical run detail from
  the generated OpenAPI contract. React provides the dashboard, report,
  Cytoscape graph, Recharts trends, lineage, navigation, run status, partial
  banners, and item-failure views.
- STORM commit `fb951af7744dab086e34962e9bc6fe878e145f83` was audited as an
  architecture-only reference. No STORM package, source, prompt, retriever,
  model wrapper, embedding stack, or filesystem persistence is copied or used.

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
    Daily --> SemanticScholar["Semantic Scholar API"]
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
tests/build, credential-free Playwright, Compose, Terraform, clean and sequential
Alembic upgrades against disposable pgvector PostgreSQL, repository/contract/unit
tests, and the Web/API, Daily Job, and pinned GROBID wrapper images. It builds the
GROBID wrapper but does not start a live GROBID service.

Default verification is deterministic: it does not call live arXiv, DeepSeek,
Semantic Scholar, or Google Cloud, and it does not start a GROBID service. The
explicit real-arXiv test remains opt-in through `RUN_LIVE_ARXIV_TEST=1` and
`TEST_DATABASE_URL`. The Semantic Scholar smoke remains opt-in through
`RUN_LIVE_SEMANTIC_SCHOLAR_TEST=1`; selecting it requires
`SEMANTIC_SCHOLAR_API_KEY`, while leaving both unset is an expected skip.

The real SPECTER2 Base smoke is explicit and opt-in because it downloads the
pinned weights. It has been executed locally on CPython 3.13.13 with
Transformers 5.3.0 and real pgvector persistence/retrieval. Normal verification
does not download model weights.

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

The wrapper also exposes each protected operation explicitly. M3 historical
backfill and related-work search validate the Semantic Scholar key only when
that operation is selected; default arXiv ingestion remains keyless:

```powershell
$env:SEMANTIC_SCHOLAR_API_KEY = "<local-secret>"
$through = Get-Date -Format "yyyy-MM-dd"
& .\scripts\run-daily.ps1 `
  -Operation historical-backfill `
  -OperationArgument @("--through", $through)
```

`search-related` accepts its bounded Crawler/Selector options through
`-OperationArgument`; `compare-papers` accepts the persisted search-session and
paper-version identifiers the CLI requires. Related-work selection and
comparison also require `DEEPSEEK_API_KEY`. Local historical backfill and
related-work execution additionally require the opt-in SPECTER2 runtime and a
prepared artifact:

```powershell
D:\Tools\uv\uv.exe sync --frozen --extra specter2 --python 3.13.13
$modelPath = Join-Path $env:TEMP "paper-harness-specter2-base"
D:\Tools\uv\uv.exe run --frozen --extra specter2 --python 3.13.13 python `
  -m paper_harness.adapters.specter2.prepare `
  --output $modelPath
$env:SPECTER2_MODEL_PATH = $modelPath
```

Preparation downloads only the fixed upstream revision, verifies the official
PyTorch weight SHA-256, loads with `trust_remote_code=False` and
`weights_only=True`, converts to safetensors, and writes a strict manifest.
Runtime loading is local-only and offline. The production Daily image performs
this preparation once at build time and contains only the prepared artifact.

Backfill resumes from its last committed query boundary after either a process
interruption or an explicit same-window retry of a `FAILED` row. Resume and
idempotent `COMPLETE` reads require the exact persisted query plan, limits, and
embedding model provenance.

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

Publish one M4 daily product after its selected papers have persisted M2
analyses and M3 comparisons. DeepSeek is the default narrative mode; choose
`structured_only` explicitly for the deterministic report mode:

```powershell
$logicalDate = Get-Date -Format "yyyy-MM-dd"
& .\scripts\run-daily.ps1 `
  -Operation publish-product `
  -LogicalDate $logicalDate `
  -OperationArgument @("--narrative-mode", "structured_only")
```

Eligible weekly and monthly reports are explicit protected operations. Period
bounds must cover a complete Monday-through-Sunday week or calendar month:

```powershell
& .\scripts\run-daily.ps1 `
  -Operation generate-periodic-report `
  -OperationArgument @(
    "--report-type", "weekly",
    "--period-start", "2026-08-03",
    "--period-end", "2026-08-09",
    "--narrative-mode", "deepseek"
  )
```

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
and a fixed DeepSeek secret version. M3 Semantic Scholar access is a separate
opt-in: `attach_semantic_scholar_secret_to_daily=true` requires a fixed
`semantic_scholar_secret_version` and grants access only to the Daily service
account. The Web/API service never receives that secret. Secret values never
enter Terraform state.

No foundation or runtime resource is currently deployed. The earlier foundation
apply could not establish TCP 443 connections to Google API endpoints and stopped
before resource creation. Production deployment is also blocked on an
owner-supplied PostgreSQL `DATABASE_URL` and DeepSeek API key in Secret Manager.

## Configuration and required secrets

| Name | Current contract |
| --- | --- |
| `DATABASE_URL` | Required for persistence; production must use PostgreSQL 15+ with pgvector through a fixed Secret Manager version |
| `DEEPSEEK_API_KEY` | Required for structured analysis, related-work selection, comparison, and `deepseek` report mode; no mock, anonymous access, or alternate model |
| `LLM_PROVIDER` | Must be `deepseek` for every model-backed operation |
| `LLM_MODEL` | Must be `deepseek-v4-flash` for every model-backed operation |
| `GROBID_URL` | Required only for `full_text`; local URL may be HTTP, production must be the private HTTPS Cloud Run URI |
| `GROBID_AUTH_MODE` | `none` for local development; production requires `google_identity` |
| `GROBID_AUDIENCE` | Required with Google identity and equal to the private GROBID service audience |
| `SEMANTIC_SCHOLAR_API_KEY` | Required only for `historical-backfill` and `search-related`; no anonymous access or provider fallback, and never injected into Web/API |

The read API starts without DeepSeek, GROBID, or Semantic Scholar credentials.
Only the operation that needs a dependency validates it, and missing configuration
fails explicitly.

## Current limitations

- Production PostgreSQL and DeepSeek Secret Manager versions are not supplied,
  and Google API connectivity currently prevents Terraform apply.
- Live DeepSeek report-synthesis state is recorded in
  [docs/STATUS.md](docs/STATUS.md); default verification remains credential-free.
- The Semantic Scholar adapter is fixture-tested but no live scholarly-search
  smoke test has run because no API key was supplied.
- Daily ingestion and selected-paper analysis are separate operator/Job commands;
  the configured Terraform Scheduler invokes the keyless default ingestion command
  only. Analysis, search, comparison, product publication, and eligible periodic
  reports remain explicit commands and are not yet chained automatically.
- The CRF GROBID image is CPU-bounded and smaller than the full deep-learning
  image, but its lower extraction accuracy is an explicit provenance limitation.
- The model-bearing Daily image is approximately 786 MB. Normal CI deliberately
  builds the model-free Daily target; deployment selects the production target
  that bakes the pinned SPECTER2 Base artifact so each Job does not redownload
  weights.
- SPECTER2 proximity-adapter adoption is deferred. The released Adapters stack
  requires Transformers 4.57.x, below the project's patched Transformers 5.3+
  security floor. Reconsidering the adapter requires a future explicit
  architecture decision after upstream compatibility exists.
- Terraform state is local and ignored; a reviewed remote-state strategy is
  required before multi-operator production use.
- Full PDFs, complete prompts/responses, model weights, secrets, and credentials
  are never stored in Git.
