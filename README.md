[English](README.md) | [简体中文](README.zh-CN.md)

# Domain-Specific Paper Harness

Domain-Specific Paper Harness is a self-hosted research-intelligence product for
continuously discovering, analyzing, and connecting papers in configured
research domains. It combines an arXiv-only Daily discovery pipeline with
grounded full-text analysis, historical research, comparisons, a
provenance-aware graph, deterministic trends, and a read-only web product.

[Usage guide](docs/USAGE.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) ·
[Apache-2.0 license](LICENSE)

![Paper Harness dashboard](docs/images/paper-harness-dashboard.png)

The initial topics are Broad LLM Agents, Brain-Computer Interfaces, and World
Models. Each topic owns its query, inclusion and exclusion rules, cursor,
selection, reports, graph, trends, and lineage. The project is a research
product rather than a paper-writing tool, generic search engine, or chatbot.

## What it provides

- Canonical arXiv identities and explicit version tracking with overlapping,
  idempotent discovery windows.
- Structured DeepSeek analysis of selected papers, with GROBID as the only
  full-text parser and explicit analysis provenance.
- Claims and concise evidence excerpts tied to exact paper versions and source
  coordinates when available.
- Authenticated Semantic Scholar historical retrieval, bounded PaSa-derived
  search behavior, and pinned SPECTER2 embeddings.
- Evidence-linked comparisons, research lineages, a provenance-aware knowledge
  graph, and deterministic 7/30/90-day trends.
- Honest `COMPLETE`, `PARTIAL`, `FAILED`, and `NO_UPDATE` presentation, including
  item-level failure and enrichment-availability details.
- A read-oriented FastAPI API and React interface for reports, papers, evidence,
  comparisons, graphs, trends, lineages, and run status.

See the [visual usage guide](docs/USAGE.md) for the main product flows and all
available screenshots.

## Architecture

The repository is a Ports-and-Adapters modular monolith with three deployable
runtime units:

1. **Web/API** serves FastAPI under `/api/v1` and the production React build. It
   reads persisted results and does not run background research jobs.
2. **Daily** runs one bounded pipeline per topic, from arXiv discovery through
   atomic product publication.
3. **GROBID** parses scientific PDFs and is intended to remain private to the
   Daily runtime.

PostgreSQL 15 or newer with pgvector is the only persistence contract. Alembic
migrations are explicit; the API never migrates at startup. Production
deployment targets Google Cloud Run and Cloud Run Jobs through Terraform, with
the Web/API behind IAP, GROBID private, and minimum instances set to zero where
supported.

```text
external service -> adapter -> port -> application use case -> domain
```

More detail is available in [Architecture](docs/ARCHITECTURE.md),
[Boundaries](docs/BOUNDARIES.md), and the
[Failure policy](docs/FAILURE_POLICY.md).

## Prerequisites

- Git
- Windows PowerShell 5.1 or PowerShell 7 (primary supported development path)
- [uv](https://docs.astral.sh/uv/) and exact CPython 3.13.13
- Node.js 24 with Corepack; the repository selects its pnpm version
- Docker Desktop with Docker Compose
- PostgreSQL 15+ with pgvector (provided locally by Compose)

Terraform and the Google Cloud CLI are required only for cloud deployment.
First-party Python is pinned to `>=3.13.13,<3.14`; another Python release is not
a supported substitute.

## Keyless quick start on Windows

This path starts an empty local database plus the read API and web interface. It
does not contact DeepSeek or Semantic Scholar and does not require provider or
cloud credentials.

```powershell
git clone https://github.com/ArcueidMP/Domain-Specific-Paper-Harness.git
Set-Location Domain-Specific-Paper-Harness
uv python install 3.13.13
corepack pnpm --version
.\scripts\dev.ps1
```

The script synchronizes frozen Python and frontend dependencies, starts the
local pgvector database, applies Alembic migrations, and starts both development
servers:

- Web: <http://127.0.0.1:5173>
- API: <http://127.0.0.1:8000>
- OpenAPI: <http://127.0.0.1:8000/docs>

If ports 5432, 8000, or 5173 are already occupied, choose alternate ports:

```powershell
.\scripts\dev.ps1 -PostgresPort 15432 -ApiPort 18000 -WebPort 15173
```

On Linux, set `POSTGRES_PORT=15432` before `docker compose`, use port 15432
in `DATABASE_URL`, and pass ports 18000 and 15173 to uvicorn and Vite
respectively.

Press `Ctrl+C` to stop the API and web processes. PostgreSQL remains available
in Docker. A fresh database has no Daily publications yet, so empty report and
paper states are expected until a pipeline run completes.

### Equivalent Linux commands

The PowerShell helper is the primary supported path. On Linux, run the same
operations explicitly from the repository root:

```bash
uv python install 3.13.13
uv sync --frozen --python 3.13.13
corepack pnpm install --frozen-lockfile
docker compose up --detach --wait db
export DATABASE_URL='postgresql+psycopg://paper_harness:paper_harness_local@localhost:5432/paper_harness'
uv run --frozen --python 3.13.13 alembic upgrade head
```

Then start the processes in two terminals, exporting the same `DATABASE_URL` in
the API terminal:

```bash
# Terminal 1
export DATABASE_URL='postgresql+psycopg://paper_harness:paper_harness_local@localhost:5432/paper_harness'
uv run --frozen --python 3.13.13 uvicorn paper_harness_api.main:app --host 127.0.0.1 --port 8000 --reload
```

```bash
# Terminal 2
corepack pnpm --filter @paper-harness/web dev --host 127.0.0.1 --port 5173
```

These Linux commands follow the same locked dependencies as CI. macOS has not
been verified and is not currently a supported development environment.

## Local API examples

With the keyless development servers running, use `curl.exe` in Windows
PowerShell:

```powershell
curl.exe http://127.0.0.1:8000/health/live
curl.exe http://127.0.0.1:8000/health/ready
curl.exe http://127.0.0.1:8000/api/v1/topics
curl.exe "http://127.0.0.1:8000/api/v1/papers?topic=broad-llm-agents&limit=10"
```

After a publication exists:

```powershell
curl.exe "http://127.0.0.1:8000/api/v1/daily/latest?topic=broad-llm-agents"
curl.exe "http://127.0.0.1:8000/api/v1/trends?topic=broad-llm-agents&window=7D"
curl.exe "http://127.0.0.1:8000/api/v1/runs/latest?topic=broad-llm-agents"
```

On Linux, replace `curl.exe` with `curl`.

## Run the full pipeline locally

The full pipeline makes live requests and requires credentials that you own. It
also needs a local GROBID service and the explicitly prepared, pinned SPECTER2
artifact. Never commit credentials or prepared model files.

First install the locked SPECTER2 runtime and prepare the model once. Preparation
downloads the exact upstream revision, verifies its source hash, and converts it
to a local safetensors-only artifact:

```powershell
uv sync --frozen --python 3.13.13 --extra specter2
$Specter2Path = Join-Path $env:LOCALAPPDATA "PaperHarness\models\specter2_base"
$Specter2Cache = Join-Path $env:LOCALAPPDATA "PaperHarness\cache\huggingface"
uv run --frozen --python 3.13.13 --extra specter2 python -m paper_harness.adapters.specter2.prepare `
  --output $Specter2Path `
  --cache-dir $Specter2Cache
```

If that artifact already exists, keep it and skip the preparation command. Start
PostgreSQL and local GROBID, then set session-only configuration and execute one
topic:

```powershell
docker compose --profile analysis up --detach --wait db grobid
$env:APP_ENV = "development"
$env:DATABASE_URL = "postgresql+psycopg://paper_harness:paper_harness_local@localhost:5432/paper_harness"
$env:LLM_PROVIDER = "deepseek"
$env:LLM_MODEL = "deepseek-v4-flash"
$env:DEEPSEEK_API_KEY = "<your-deepseek-api-key>"
$env:SEMANTIC_SCHOLAR_API_KEY = "<your-semantic-scholar-api-key>"
$env:GROBID_URL = "http://127.0.0.1:8070"
$env:GROBID_AUTH_MODE = "none"
$env:SPECTER2_MODEL_PATH = $Specter2Path
uv run --frozen --python 3.13.13 alembic upgrade head
uv run --frozen --python 3.13.13 --extra specter2 paper-harness-daily run-pipeline `
  --topic-config configs/topics/broad-llm-agents.yaml
```

Use `configs/topics/brain-computer-interfaces.yaml` or
`configs/topics/world-models.yaml` to run another independent topic. The CLI
prints structured terminal events and exits non-zero on a run-level failure.

The equivalent Linux preparation and run are:

```bash
uv sync --frozen --python 3.13.13 --extra specter2
export SPECTER2_MODEL_PATH="${XDG_DATA_HOME:-$HOME/.local/share}/paper-harness/specter2_base"
uv run --frozen --python 3.13.13 --extra specter2 python -m paper_harness.adapters.specter2.prepare \
  --output "$SPECTER2_MODEL_PATH" \
  --cache-dir "${XDG_CACHE_HOME:-$HOME/.cache}/paper-harness/huggingface"
docker compose --profile analysis up --detach --wait db grobid
export APP_ENV=development
export DATABASE_URL='postgresql+psycopg://paper_harness:paper_harness_local@localhost:5432/paper_harness'
export LLM_PROVIDER=deepseek
export LLM_MODEL=deepseek-v4-flash
export DEEPSEEK_API_KEY='<your-deepseek-api-key>'
export SEMANTIC_SCHOLAR_API_KEY='<your-semantic-scholar-api-key>'
export GROBID_URL='http://127.0.0.1:8070'
export GROBID_AUTH_MODE=none
uv run --frozen --python 3.13.13 alembic upgrade head
uv run --frozen --python 3.13.13 --extra specter2 paper-harness-daily run-pipeline \
  --topic-config configs/topics/broad-llm-agents.yaml
```

Skip only the model-preparation command on later runs; keep
`SPECTER2_MODEL_PATH` pointed at the prepared artifact.

### Runtime configuration

The complete local template is [.env.example](.env.example). Keep real values
in session environment variables or an ignored local secret store, never in a
tracked file.

| Variable | Consumer | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Web/API and Daily | PostgreSQL 15+ connection using the psycopg 3 driver |
| `LLM_PROVIDER=deepseek` | Daily | Selects the only supported production LLM provider |
| `LLM_MODEL=deepseek-v4-flash` | Daily | Selects the required DeepSeek model |
| `DEEPSEEK_API_KEY` | Daily | User-owned credential for generated analysis and narrative |
| `SEMANTIC_SCHOLAR_API_KEY` | Daily | User-owned credential for historical and related-work operations |
| `GROBID_URL` | Daily | URL of the sole full-text parser |
| `GROBID_AUTH_MODE` | Daily | `none` for local development; production requires Google identity |
| `SPECTER2_MODEL_PATH` | Daily | Prepared, pinned, offline model artifact |

The Web/API requires no DeepSeek or Semantic Scholar credential. The browser
never receives any database or provider secret.

## Publication states

- `COMPLETE` means the day's usable source metadata was published. Optional
  related-work, comparison, graph, trend, lineage, or evidence enrichment may
  still be explicitly unavailable.
- `PARTIAL` means usable metadata was published, but at least one selected paper
  failed core metadata or source-analysis processing. The failed stage and
  stable error code remain visible.
- `NO_UPDATE` is a normal complete publication with zero newly eligible papers
  for the topic and logical date.
- `FAILED` is reserved for run-level boundaries such as invalid configuration,
  authentication, database or migration failure, global arXiv failure before
  usable input, inability to persist usable metadata, or publication failure.

An independent item failure does not suppress other usable papers. Missing
optional enrichment is shown as unavailable rather than fabricated or used to
block an otherwise usable publication.

## Data sources and trust boundaries

- **Daily discovery:** arXiv only.
- **Historical and related work:** authenticated Semantic Scholar, the
  persisted local corpus, and the bounded PaSa-derived scholarly tool loop.
- **Full text:** only arXiv-hosted PDFs are eligible. Non-arXiv historical
  results remain bibliographic or abstract stubs.
- **Excluded behavior:** no publisher-site scraping, paywall bypass, publisher
  PDF download, generic web search, hidden provider substitution, or malformed
  model-output repair.

Paper titles, abstracts, metadata, PDFs, and excerpts remain subject to their
upstream authors' and providers' rights and terms. Apache-2.0 applies to the
repository's first-party source code, not to third-party paper content or model
outputs. AI-generated analyses and inferred relations carry provenance and are
not presented as human verified.

## Costs and external services

The keyless read-only quick start uses local compute and no paid model API. A
full run can incur charges or consume quotas for services you configure,
including DeepSeek, managed PostgreSQL, cloud compute and egress, and any
provider plan associated with Semantic Scholar. Local GROBID and SPECTER2 also
consume CPU, memory, disk, and network bandwidth. The project does not create a
spending cap or provision a managed database for you.

Review the configured paper counts, search limits, retries, timeouts, and model
pricing before running the pipeline or enabling a schedule.

## Verification

The canonical Windows verification command is:

```powershell
.\scripts\verify.ps1
```

It checks frozen dependencies, Python and frontend quality gates, tests,
generated API contracts, Docker Compose and runtime images, Terraform, clean
Alembic migration, and PostgreSQL integration. Default verification requires no
live provider or cloud credentials. During development, run the focused checks
for the boundary you changed before the canonical release check.

## Deployment

Terraform under `infra/terraform` defines the supported Google Cloud topology:
a private Web/API Cloud Run service, topic-specific Cloud Run Jobs, private
GROBID, Secret Manager references, and one Cloud Scheduler target per topic.
Schedules default to 20:00, 20:20, and 20:40 in `Asia/Kuala_Lumpur`.

Deployment requires an existing Google Cloud project, an externally managed
PostgreSQL 15+ database with pgvector, immutable runtime images, and user-owned
secret values. Inspect billing and every Terraform plan before applying it. See
the [production runbook](docs/RUNBOOK.md) for the exact operator workflow. No
deployment command grants a public endpoint by default.

## Current limitations

- This source release does not include a hosted public Demo or access to any
  maintainer production environment.
- A compatible managed PostgreSQL database is not provisioned by the project.
- Full-text analysis requires GROBID; there is no parser fallback.
- DeepSeek, authenticated Semantic Scholar, prepared SPECTER2, and PostgreSQL
  have no implicit production substitutes.
- The supported local development path is Windows PowerShell. Linux manual
  commands are provided and align with CI; macOS is unverified.
- Trend and periodic-report usefulness depends on sufficient persisted corpus
  history. Insufficient data remains visible instead of becoming a trend claim.
- arXiv PDFs above the configured ingestion bound remain item-level analysis
  failures while their usable source metadata may still publish.

## Contributing and security

Bug reports and focused pull requests are welcome on a best-effort maintenance
basis. Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing topic behavior,
data boundaries, or generated contracts. Report vulnerabilities privately as
described in [SECURITY.md](SECURITY.md); do not place credentials or sensitive
diagnostics in a public issue.

## License

First-party source code is licensed under the
[Apache License 2.0](LICENSE). Third-party acknowledgements and integration
boundaries are recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[docs/reuse-register.yaml](docs/reuse-register.yaml).
