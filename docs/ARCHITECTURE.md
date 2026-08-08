# Architecture

## System context

Domain-Specific Paper Harness is a private research-intelligence product for
broad LLM-agent research. Daily discovery is arXiv-only. PostgreSQL stores
canonical paper/version identities, parsed scientific text, structured analyses,
claims, evidence, run items, and deterministic reports. FastAPI exposes
read-oriented data and serves the compiled React application in production.

The Ports-and-Adapters modular monolith has three deployment units:

1. `web-api` serves health checks, FastAPI under `/api/v1`, and the React build.
2. `daily` runs explicit arXiv ingestion or structured analysis commands. The
   current M2 command analyzes selected persisted paper UUIDs; it is not yet
   automatically chained after discovery.
3. `grobid` is the sole scientific PDF parser for `FULL_TEXT` analysis. It is a
   separate IAM-authenticated Cloud Run service in the Terraform design and an
   opt-in hardened Compose service locally.

All first-party Python units use CPython 3.13.13. GROBID is an isolated Java
service. PostgreSQL 15 or newer with pgvector is the only persistence contract.

## Dependency direction

Dependencies point inward:

```text
external API or database
        -> adapter
        -> port
        -> application use case
        -> domain
```

The domain owns identities, scopes, state transitions, provenance fields, and
invariants. It does not import FastAPI, SQLAlchemy, arxiv.py, DeepSeek, GROBID,
PaperQA2, or Google Cloud code. Application use cases coordinate ports. Adapters
translate arXiv, DeepSeek, GROBID/TEI, Cloud Run identity, and PostgreSQL
boundaries. Entrypoints perform configuration and constructor wiring.

PaperQA2 was audited at `v2026.03.18` and is not installed or copied. Its PyPDF
parser, provider defaults, separate index and MD5 identities, JSON repair, and
loss of GROBID provenance conflict with the architecture. Evidence grounding is
therefore a project-owned deterministic function rather than an external engine.

## Ingestion data flow

The Daily Job builds an arXiv query from `TopicConfig`, reads a persisted cursor
with overlap, and requests metadata through `ArxivPort`. Normalization separates
the stable canonical arXiv identity from its explicit version. External data is
validated before a short transaction upserts papers, versions, source identities,
authors, run items, and the next cursor. Unique constraints and a PostgreSQL
advisory lock make a repeated logical window safe and idempotent.

## Structured-analysis data flow

An operator selects persisted paper UUIDs and an explicit scope before execution:

```text
selected version
  -> FULL_TEXT: download arXiv PDF -> GROBID TEI -> parsed paper
  -> ABSTRACT_ONLY: persisted arXiv abstract
  -> strict DeepSeek JSON
  -> schema and domain validation
  -> exact-substring evidence grounding
  -> atomic analysis + claims + evidence persistence
  -> COMPLETE/PARTIAL/FAILED run finalization
  -> atomic deterministic report publication for COMPLETE or PARTIAL
```

`FULL_TEXT` never downgrades after a PDF or parser failure. GROBID requests
explicitly disable header, citation, and funder consolidation; the parser adapter
retains ordered sections, passages, references, citation contexts, and optional
page coordinates from TEI. PDF and TEI sizes, request time, retries, and total
operation duration are bounded.

DeepSeek is fixed to `deepseek-v4-flash`. The adapter disables reasoning output,
requests one JSON object, rejects extra analysis fields, validates the response
envelope and token usage, and records provider, configured model, returned model
version, prompt version, generation time, duration, token counts, and estimated
cost. It never repairs malformed JSON or retries schema-invalid output.

Model evidence must reference a known passage and its excerpt must be an exact
substring of that passage. Stable IDs and composite database constraints prevent
claims or evidence from crossing paper, version, or analysis ownership. The
analysis, claims, evidence, and link rows commit or roll back together.

## Read API and product UI

FastAPI reads through `RepositoryPort`. Its OpenAPI document is the sole frontend
contract. M2 adds:

- `GET /api/v1/papers/{paper_id}`;
- `GET /api/v1/papers/{paper_id}/analysis`, optionally filtered by version/scope;
- `GET /api/v1/papers/{paper_id}/evidence`, optionally filtered by version/scope;
- `GET /api/v1/runs`; and
- `GET /api/v1/runs/latest`, including run items and an optional deterministic
  report with visible failures.

The React product provides dashboard, paper list/detail, structured-analysis,
provenance, and evidence views. Latest-run UI distinguishes `PARTIAL`, lists each
failed stage and stable error code, and links failures back to paper details.
The API still has no execution endpoint and never migrates on startup.

## Persistence and publication

Migration `0002_m2_structured_analysis` adds parsed papers/sections/passages,
references, citation contexts, paper analyses, claims, evidence, evidence-claim
links, reports, and report failures. It also extends run counters and supports
separate ingestion and analysis operations for one logical date.

External work completes before each short database transaction. Parsed-paper
persistence advances only the matching run item. The complete analysis bundle
and its terminal evidence stage commit atomically. Finalization locks the run,
requires every selected item to be terminal, derives `COMPLETE`, `PARTIAL`, or
`FAILED`, and publishes a report in the same transaction only for `COMPLETE` or
`PARTIAL`.

## Runtime and deployment

Local Compose provides PostgreSQL plus optional Web/API, Daily, and GROBID
profiles. The GROBID wrapper pins
`grobid/grobid:0.9.0-crf@sha256:24ba90eb1c959f65d812bcdb2cf79c677fa5fd7b95235de616b8bc9fa1317849`.
Its local service uses the official health endpoint, a read-only root filesystem,
writable tmpfs paths, zero core-dump ulimit, dropped capabilities,
no-new-privileges, and bounded CPU, memory, and process count.

Terraform remains gated:

- the foundation creates required APIs, Artifact Registry, service accounts, and
  empty Secret Manager resources;
- `deploy_runtime_resources=true` requires immutable Web/API and Daily images
  plus a fixed `DATABASE_URL` secret version; and
- `deploy_analysis_resources=true` additionally requires the runtime gate, an
  immutable mirrored GROBID wrapper image, and a fixed DeepSeek secret version.

The Web/API service uses direct Cloud Run IAP. GROBID uses IAM invocation checks,
minimum instances zero, maximum one, concurrency one, and grants `run.invoker`
only to the Daily service account. Daily obtains an ephemeral metadata-server ID
token whose audience is the GROBID service URL. No `allUsers`, VPC connector,
Cloud NAT, load balancer, or other fixed-cost networking resource is configured.

These production resources have not been applied. The current external blockers
are Google API TCP connectivity and owner-supplied production PostgreSQL and
DeepSeek secret versions.

## Verification

`scripts/verify.ps1` is the canonical Windows entrypoint. It verifies exact
Python, frozen dependency sets, backend/frontend quality gates, OpenAPI-generated
contract drift, Compose, Terraform, clean and M1-to-M2 database upgrades,
repository integration tests, credential-free browser tests, and focused Web/API,
Daily, and pinned GROBID wrapper image builds. The default suite does not start
GROBID or call live external providers.
