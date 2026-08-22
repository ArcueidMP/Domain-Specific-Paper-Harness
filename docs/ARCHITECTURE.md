# Architecture

## System context

Domain-Specific Paper Harness is a private research-intelligence product for
broad LLM-agent research. It continuously discovers arXiv papers, analyzes
selected full text, retrieves approved historical work, creates traceable
comparisons, and publishes a provenance-aware graph, trends, lineages, and
reports.

The system is a Ports-and-Adapters modular monolith with three independently
deployed runtime units:

1. `web-api` serves FastAPI under `/api/v1`, health endpoints, and the compiled
   React application.
2. `daily` runs bounded discovery through atomic product publication as a Cloud
   Run Job.
3. `grobid` is the sole scientific PDF parser and is an IAM-private Cloud Run
   service.

PostgreSQL 15 or newer with pgvector is the only persistence contract. All
first-party Python uses CPython 3.13.13. GROBID is a separate Java service.

## Dependency direction

Dependencies point inward:

```text
external API, model, parser, or database
        -> adapter
        -> port
        -> application use case
        -> domain
```

The domain owns identities, state transitions, provenance, analysis scope,
search limits, relation semantics, comparability, and invariants. It imports no
framework, provider SDK, database implementation, or cloud SDK. Application use
cases coordinate explicit ports, adapters validate external boundaries, and
entrypoints own configuration and constructor wiring.

The six real external ports are `ArxivPort`, `ScholarlySearchPort`, `LLMPort`,
`PdfParserPort`, `EvidenceEnginePort`, and `RepositoryPort`. Deterministic
ranking, normalization, comparison mapping, graph aggregation, trends, and
schema conversion remain ordinary typed functions.

## Daily arXiv ingestion

Daily discovery is arXiv-only. The application builds a query from
`TopicConfig`, reads a persisted cursor with overlap, and requests metadata
through the arxiv.py adapter. Normalization separates canonical arXiv identity
from explicit versions, normalizes timestamps, deduplicates by canonical ID and
version, and applies stable local ordering before pagination, overlap, cursor,
and top-N decisions. Provider page order and equal timestamps are not validity
invariants. A short transaction upserts papers, versions, source identities,
authors, run items, and the next cursor.

Database uniqueness, transactions, and a PostgreSQL advisory lock make repeated
logical windows safe. Semantic Scholar never participates in daily discovery.

## Complete Daily pipeline

The protected `run-pipeline` command coordinates existing use cases; it is not a
workflow framework and is never exposed as an HTTP endpoint:

```text
configuration and dependency preflight
  -> arXiv ingestion
  -> deterministic relevance selection
  -> exact-version structured analysis
  -> six-month historical backfill
  -> arXiv historical materialization
  -> bounded historical analysis
  -> related-work search per successful source paper
  -> evidence-linked comparisons
  -> graph, trend, lineage, and report publication
```

The logical date is computed once in the configured time zone. Pipeline
provenance and selection limits are persisted by the application migration.
Completed child artifacts may be reused when their source ownership, target
versions, and analysis scope are compatible with the consuming operation. A
failed or interrupted unpublished pipeline may re-evaluate and replan from
current valid persisted inputs; it is not forced to reuse a stale candidate or
comparison snapshot. Terminal published artifacts remain immutable.

External calls happen outside write transactions. Every phase has bounded
timeouts, retries, candidate counts, search steps, queue size, citation depth,
paper counts, and comparison counts. A global deadline encloses the application
pipeline and is shorter than the Cloud Run task timeout.

## Structured analysis and evidence

Full-text mode downloads only the selected arXiv version and sends it to private
GROBID. TEI is parsed with external XML resolution disabled. Parser failure is
an item failure and never changes analysis scope.

DeepSeek is the only production LLM provider. The adapter requires valid JSON,
validates it with Pydantic, enforces domain invariants, and persists only after
validation. Empty, malformed, schema-invalid, or domain-invalid output fails the
owning paper explicitly before persistence. Other independent valid papers
continue, and there is no model or JSON-repair fallback.

Evidence records the paper and version, section, page or coordinates when
available, concise excerpt, evidence type, supported claim or relation, source,
and defined confidence. Every AI-generated record also stores provider/model,
prompt version, scope, generation time, source provenance, usage, and
verification state. Hidden reasoning and full prompts are never stored.

## Historical and related work

Historical retrieval uses only authenticated Semantic Scholar endpoints and the
persisted corpus. The typed adapter centralizes authentication, pagination,
response validation, rate limits, timeouts, bounded transient retries, and
stable error mapping. It also centrally normalizes optional text metadata and
uses deterministic local sorting, deduplication, and capping. Missing/null/blank
optional text, provider order, duplicate candidates, and partial selector
coverage are not whole-response failures. Required identity and field types
remain strict.

The PaSa-derived loop is a first-party bounded Crawler/Selector implementation.
It can call only:

```text
search_papers
get_paper
get_references
get_citations
get_recommendations
read_arxiv_paper
```

Search sessions, actions, candidates, decisions, origins, ranks, model
provenance, and stop reasons persist. A pinned SPECTER2 Base model embeds title
and abstract text into pgvector; its exact revision, dimension, source, and
generation time are recorded. The production Daily image prepares the model at
build time and loads it offline.

Only historical papers with arXiv-hosted PDFs enter full-text analysis.
Non-arXiv results remain bibliographic/abstract stubs with approved identifiers
and citation relations.

## Comparisons and relations

Comparisons cover problem, task, method, architecture, datasets, benchmarks,
baselines, metrics, results, compute or inference budget, novelty claims,
limitations, code availability, and comparability. They persist a structured
matrix, concise summary, evidence links, and explicit comparability status.

Relations distinguish metadata-explicit, text-explicit, deterministically
derived, LLM-inferred, and human-verified provenance. Inferred relations require
supporting evidence, model and prompt identity, defined confidence, and
verification state. The UI never presents them as certain.

## Product publication

Each product-publication attempt selects current valid persisted analyses,
comparisons, and evidence for its logical date and declared scope. Graph, trend,
lineage, and report rows are staged under one product run. The final report,
links, item publication states, canonical graph updates, and terminal run state
commit atomically. Failed unpublished staging is discarded; a later attempt may
replan from current valid persisted inputs without inheriting a frozen target
set. Terminal published artifacts remain immutable.

Publication states are:

- `COMPLETE`: every selected priority paper completed all required stages.
- `PARTIAL`: at least one completed and at least one failed; the report names
  missing papers, failed stages, and stable error codes.
- `FAILED`: a required global dependency/configuration failed, no selected paper
  completed, or publication failed.

Independent valid items continue after an item failure. Upstream child statuses
remain observable but do not have to equal the product-publication status; the
report reflects its owning product run and carries the relevant missing-item
details.

Public reads admit only terminal complete or partial owners. Failed staging data
cannot leak into canonical product views. Deterministic 7/30/90-day aggregates
come only from persisted structured data and expose insufficient-data windows.

## Read API and web product

FastAPI is online and read-oriented. It serves the required topics, daily
reports, papers, analyses, evidence, related work, comparisons, graph, trends,
and runs endpoints. Readiness verifies database availability and exact Alembic
head; it never migrates or runs pipeline work.

The React application consumes types generated from FastAPI OpenAPI. It includes
dashboard, daily history, paper/evidence detail, comparison, graph, trends,
lineage, report, and run/failure views. The browser has no database or provider
credential and relies on the cloud identity boundary.

## Persistence and transactions

Normalized SQLAlchemy tables store topics, papers, versions and source
identities, authors, analyses and claims, evidence, historical stubs, search
state, comparisons, relations, graph entities, runs and items, reports, trends,
lineages, and embeddings. Alembic is the only schema-change mechanism.

Foreign keys, unique constraints, check constraints, and short transactions
protect real invariants. External work is validated before a transactional
write. Evidence, relations, comparisons, and publication rows cannot reference
missing or half-written records.

## Runtime and deployment

Local Compose binds ports to loopback and applies non-root execution,
read-only filesystems, dropped capabilities, no-new-privileges, CPU/memory/PID
limits, tmpfs scratch space, health checks, and graceful stops. Image bases are
digest-pinned and the build context excludes secrets, Terraform state, backups,
PDFs, models, caches, logs, and generated frontend output.

Terraform declares:

- required APIs, Artifact Registry, Secret Manager containers, and dedicated
  service accounts;
- a one-task, zero-retry Alembic migration Job;
- IAP-protected Web/API with the owner as the sole application accessor;
- IAM-private GROBID callable only by the Daily identity;
- the bounded Daily Job with fixed numeric secret versions; and
- a paused-by-default 05:00 `Asia/Kuala_Lumpur` Scheduler target.

All Cloud Run services use zero minimum instances. Terraform contains no public
principal, project-wide runtime role, Cloud SQL, load balancer, VM, Kubernetes,
Redis, VPC connector, NAT, or exported service-account key.

Operations are direct: build images, resolve immutable digests, inspect and
apply Terraform, run migration, verify private runtime, run the Daily Job once,
verify persisted output, then create Scheduler paused and enable it only after a
successful forced invocation. Deployment scripts do not grant temporary IAM
roles or maintain a parallel release-state database.

## Upstream reuse

Upstream decisions are recorded in `docs/reuse-register.yaml` and
`THIRD_PARTY_NOTICES.md`:

- PaperQA2, Scholar QA, and STORM were audited but not copied or installed;
  selected architectural ideas are implemented as first-party typed code.
- PaSa contributes only its Crawler/Selector architecture; no source, model,
  training stack, arbitrary web tool, or database is reused.
- SPECTER2 Base is the only included model artifact, pinned to revision
  `3447645e1def9117997203454fa4495937bfbd83` and converted to verified
  safetensors for offline loading.

## Verification

`scripts/verify.ps1` is the canonical Windows entrypoint. It covers exact
runtime pins, frozen dependencies, backend/frontend quality, generated contract
drift, deterministic tests, PostgreSQL repositories, a clean migration,
browser flows, Compose, focused image builds, dependency-license policy,
Terraform formatting and validation, repository hygiene, and persistence
integration.

During iteration, run only focused unit, contract, static, and infrastructure
checks for the changed boundary. Run the canonical entrypoint once after M5
implementation and production acceptance are complete and the source is ready
for the completion commit.

Default verification has no live DeepSeek, Semantic Scholar, GROBID, or cloud
dependency. Live provider checks are explicitly opt-in and fail clearly when
their required configuration is absent.
