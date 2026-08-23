# Failure Policy

## Principles

Failures are explicit, bounded, and attributable. The system never changes
provider, parser, database, analysis scope, model, or data source after failure.
Retry means repeating the same operation within its configured bound; it is not
fallback.

Validate at configuration, HTTP input, external response, model output, PDF/TEI
input, and persistence boundaries. Validation may identify incomplete data, but
it does not suppress independently usable persisted metadata. Do not return
silent empty results, repair malformed JSON, or present unavailable enrichment
as successful or grounded.

Normalize provider payloads once at the owning adapter boundary before domain
use. Missing, null, empty, and whitespace-only optional text is absent; valid
non-empty text is trimmed. Provider order, duplicate records, equal timestamps,
partial candidate coverage, and surplus candidates are normalized with stable
local sorting, deduplication, and capping. They are not response-validity
failures. Invalid required identity, timestamps, or types, and non-string values
for optional text, remain explicit failures for the narrowest identifiable item.

## Item and run states

Each item failure records:

- failed stage;
- stable error code;
- retryable flag; and
- concise diagnostic detail without sensitive content.

Run outcomes are:

- `COMPLETE`: the pipeline published the day's usable source metadata. Optional
  analysis-adjacent enrichment may be unavailable. A day with zero relevant
  selections is a normal `NO_UPDATE` outcome and publishes a zero-count daily
  report for that logical date.
- `PARTIAL`: at least one source metadata card published while one or more
  selected papers failed core metadata or source-analysis processing. The
  report preserves the failed stages and stable error codes alongside the
  available cards.
- `FAILED`: only invalid global configuration or authentication, unavailable or
  incompatible database/migration state, global arXiv failure before any usable
  candidate input exists, inability to persist any usable metadata, or an
  atomic publication transaction failure.

An item failure does not stop independent valid items. A source paper with a
valid identity, title, source URL, and persisted metadata remains publishable as
an `ANALYSIS_UNAVAILABLE` card. Related work, comparison, evidence enrichment,
graph relations, trend classification, lineage, benchmark alignment, and
historical targets are not publication prerequisites. Their absence is exposed
as an availability state and does not by itself make the product `PARTIAL`.

## Configuration and authentication

Fail immediately before work when required configuration is missing or invalid.
The read API may start without DeepSeek or Semantic Scholar credentials; an
operation requiring either credential may not.

HTTP 400, 401, 403, and 422 responses are terminal for that operation and are
not retried. Authentication failures never cause anonymous Semantic Scholar
access, another model, a public GROBID request, or mock data.

## Transient external failures

Only timeouts and HTTP 429/500/502/503 are retryable. The centralized policy
bounds attempts, backoff, total duration, and `Retry-After`. Exhaustion records a
single stable dependency failure at the owning boundary.

ArXiv, Semantic Scholar, DeepSeek, and GROBID response bodies are bounded before
parsing. External schemas are validated. Invalid Atom, JSON, provider envelopes,
TEI, embeddings, or domain values fail the narrowest identifiable item or
provider operation and are not retried as transient transport errors. They
become a run-level failure only when the dependency is globally unusable, no
selected item can complete, or publication cannot commit.

## ArXiv ingestion

Invalid configuration, database/head failure, or global arXiv unavailability
makes the ingestion run failed. Required paper identity and timestamp/type
violations remain item failures; valid independent papers continue. Pagination,
overlap, deduplication, and the cursor use the locally normalized and stably
sorted candidate set, never the provider's page order. A cursor advances with
the persisted normalized records in the same transaction, and a write failure
rolls back both records and cursor.

Canonical identity and explicit version constraints decide idempotency. Content
hashes are not a generic deduplication substitute. The PostgreSQL advisory lock
prevents concurrent logical Daily runs; lock contention fails clearly.

## Structured analysis

Parser failure never becomes abstract-only analysis. Empty, malformed,
schema-invalid, or domain-invalid DeepSeek output makes that paper's analysis
unavailable before any analysis, claim, or evidence row is persisted; it does
not stop other papers or remove the paper's metadata card. An all-item analysis
failure is `PARTIAL`, not a run-fatal substitute for missing enrichment.

Evidence must point to the selected exact version and a valid claim/relation.
An atomic per-paper write prevents half-written analyses. If some selected
papers succeed and others fail, the analysis run is partial; if none succeed,
it is failed.

## Historical and related-work search

Production historical search requires a non-empty authenticated Semantic
Scholar key. Search sessions persist actions, candidates, decisions, origins,
limits, and stop reasons. A bounded stop is not proof of exhaustive retrieval.

The six-month backfill advances a page only after validated persistence.
Semantic Scholar optional metadata is centrally normalized, and collection
order, duplicates, partial selector coverage, and surplus candidates are handled
deterministically rather than treated as whole-response failures.
Materialization records Semantic Scholar rank as provenance and preserves exact
arXiv version when available. Required identity and field types remain strict. A
non-arXiv result remains a stub and cannot enter full-text analysis.

SPECTER2 requires the pinned offline artifact and exact contract. Missing,
wrong-revision, wrong-dimension, non-finite, or zero output makes the owning
historical enrichment unavailable; it does not switch provider or prevent
publication of already usable Daily metadata.

## Comparison, graph, and reports

A comparison persists only when its source ownership, search session,
historical target, evidence links, and comparability values validate together.
A failed comparison bundle rolls back, and the source paper publishes with
`COMPARISON_UNAVAILABLE` and reason `NO_COMPATIBLE_HISTORICAL_ANALYSIS`.

Graph relations cannot reference missing entities or evidence. Inferred
relations require their model, prompt, verification, confidence, and evidence
provenance. A score cannot be presented as certainty.

Trend and lineage computation uses persisted structured data only. Insufficient
windows remain `INSUFFICIENT_DATA`; narrative generation cannot fill missing
values. Graph, trend, lineage, and generated report narrative validation may
omit the affected enrichment but cannot discard safe source metadata or
grounded analysis already available for publication.

Each product-publication attempt selects current valid persisted analyses,
comparisons, and evidence for its declared logical date and scope, then stages
derived data under one run. Final report, links, item states, revision-owned graph
changes, and terminal status commit atomically. Publication failure removes
staging rows and leaves prior canonical data unchanged.

A failed unpublished run may start a clean attempt and replan from the current
valid persisted inputs. It is not bound to a stale candidate or comparison
snapshot. It still cannot cross source ownership or scope, mutate upstream
records, or mutate a terminal complete/partial artifact. Explicit reprocessing
creates a separate publication revision instead. When a terminal same-date
publication already exists, reprocessing uses the widest terminal revision's
selected paper-version set so a later retry cannot hide previously successful
cards. Normal scheduled selection continues to exclude canonical versions.

## Database and migrations

Database connection, transaction, constraint, or migration-head failure is a
stable run-level error. Credentials are never echoed. A failed write rolls back.

FastAPI readiness distinguishes database unavailability from migration mismatch
and returns 503. It never migrates. Production migration is an explicit
one-task, zero-retry Cloud Run Job.

Merged Alembic revisions may not be edited, deleted, or renamed. Destructive
downgrades require an explicit backup, a distinct tested restore, and the
revision's deliberate data-loss flag. The database must match the
application-declared Alembic head.

The optional Demo synchronizer verifies both `public` and `demo` revisions
before deleting any target rows. Revision, permission, or copy failure rolls
back the Demo transaction and leaves the preceding snapshot readable. Its
independent workflow may fail visibly but never changes production CI, runtime
deployment, Daily publication, or the authoritative `public` schema.

## Production deployment

Deployment stops when:

- the active account/project/region is wrong;
- a plan contains an unexpected deletion, replacement, public principal, broad
  role, secret value resource, or prohibited fixed-cost service;
- a required runtime secret is absent or disabled;
- migration did not complete at the application head;
- Web/API IAP, owner allowlist, GROBID invoker, or public-access checks fail;
- the direct Daily execution is not terminal or does not publish the day's
  usable metadata/`NO_UPDATE` outcome; or
- a topic Scheduler does not invoke its corresponding Daily Job.

No deployment error permits a public endpoint, alternate database, temporary
IAM grant, service-account key, provider substitute, or manual Terraform-state
edit. Correct the root cause and generate a fresh plan if infrastructure
changed. A failed unpublished product run may then replan from current valid
persisted inputs. Each terminal publication revision remains immutable; a
successful same-date reprocess becomes the revision selected by public reads.

Each topic Scheduler directly targets its corresponding Daily Job. Enabled or
paused state is changed through Terraform configuration without a dispatcher or
secondary orchestration mechanism.

## Repository, build, and release failures

During implementation, run focused checks for the boundary being changed. Run
the single canonical Windows verification when source and production state are
ready for a milestone or release commit. It covers:

- non-exact Python or unfrozen dependencies;
- Ruff, Pyright, pytest, frontend, OpenAPI, or browser failures;
- tracked or prospective secret material;
- generated credential markers or production source maps;
- an incompatible migration graph;
- known incompatible dependency licenses;
- Compose, image build, Terraform format, or Terraform validation failure;
- clean migration behavior; and
- PostgreSQL integration.

Checks are not bypassed, hooks are not skipped, and the milestone or release
commit is not created unless that final canonical verification passes.

## API and presentation

Liveness reports process health. Readiness reports database and exact migration
compatibility. Neither endpoint starts external work.

API and UI responses preserve analysis scope, provenance, verification state,
comparability, search stop reasons, report state, and item errors. Partial or
bounded results are never labeled exhaustive. Unverified inferred relations are
never presented as facts.

## Logging

Production logs are concise structured JSON:

- `INFO`: service/run start summary, final result, and publication;
- `WARNING`: item failures compatible with partial state, exhausted transient
  operations, bounded incomplete search, and incomplete reports;
- `ERROR`: run-level, publication, database, migration, and required-dependency
  failures.

The terminal Daily event aggregates observable external call counts, token
usage when reported, durations, and available cost estimates. Unobservable
provider-internal retries make call counts a lower bound. Missing usage or price
is unknown, not zero.

Never log secrets, authorization headers, database URLs, full prompts, full
model responses, paper text, TEI, PDFs, model weights, or the same exception at
multiple layers.
