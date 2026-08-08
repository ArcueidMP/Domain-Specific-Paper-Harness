# Architecture

## System context

Domain-Specific Paper Harness is a private research-intelligence product for broad
LLM-agent research. Daily discovery is arXiv-only. The product stores canonical
paper identities and versions in PostgreSQL, exposes read-oriented data through
FastAPI, and serves a React application from the same production Cloud Run
service.

The repository is a Ports-and-Adapters modular monolith with three deployment
units:

1. `web-api` serves FastAPI under `/api/v1`, health checks, and the compiled React
   application.
2. `daily` executes bounded ingestion and, as later milestones are implemented,
   analysis, comparison, graph, trend, and publication work.
3. `grobid` is the M2 private scientific-PDF parser. It is not part of the M1
   runtime deployment.

All first-party Python units use CPython 3.13.13. PostgreSQL 15 or newer with
pgvector is the only persistence contract. Local development uses the pinned
PostgreSQL 17 plus pgvector Compose image; production receives only a
secret-backed `DATABASE_URL`.

## Dependency direction

Dependencies point inward:

```text
external API or database
        -> adapter
        -> port
        -> application use case
        -> domain
```

The domain owns identities, state transitions, and invariants. It does not import
FastAPI, SQLAlchemy, arxiv.py, provider clients, or Google Cloud SDKs. Application
use cases coordinate ports. Adapters translate arXiv and PostgreSQL boundaries.
Entrypoints perform configuration and constructor wiring.

## M1 data flow

The Daily Job builds an arXiv query from the selected `TopicConfig`, reads a
persisted cursor with an overlap window, and requests real arXiv metadata through
`ArxivPort`. Normalization separates a stable canonical arXiv identity from its
explicit version. A short transaction upserts papers, versions, source identities,
authors, run state, and the next cursor. Database uniqueness constraints make a
repeated logical window idempotent.

FastAPI reads the same repository. Its OpenAPI document is the sole frontend API
contract; generated TypeScript types live with the web application. The API never
starts a Daily Job and never runs migrations at startup.

## Runtime and deployment

`compose.yaml` starts the local pgvector database and can build the production-like
web/API and Daily images through profiles. Alembic is always invoked explicitly.

Terraform has two deliberate phases:

- The default foundation phase enables required APIs and creates Artifact
  Registry, dedicated service accounts, and empty Secret Manager resources.
- `deploy_runtime_resources=true` requires immutable image digests and an existing
  enabled `DATABASE_URL` secret version. It creates an autoscaling-to-zero Cloud
  Run web/API service, a Cloud Run Daily Job, and the Scheduler trigger at
  `0 5 * * *` in `Asia/Kuala_Lumpur`.

The web service uses direct Cloud Run IAP, with no load balancer. IAP is the
authentication boundary and only the configured owner principal receives
`roles/iap.httpsResourceAccessor`. No `allUsers` binding exists. Scheduler uses a
dedicated identity with only Cloud Run Job invocation access. Runtime identities
receive logging and per-secret access rather than broad project roles.

Terraform does not create a database, a VPC connector, a load balancer, or any
permanently running compute resource. Secret values never enter Terraform state.

## Verification

`scripts/verify.ps1` is the canonical Windows entrypoint. It verifies the exact
Python runtime, frozen dependency sets, Python and frontend quality gates, Compose,
Terraform, a clean Alembic upgrade against a disposable pgvector database,
repository integration tests, and focused production image builds. Default tests
do not call arXiv, DeepSeek, Semantic Scholar, GROBID, or Google Cloud.
