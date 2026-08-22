# Product and Trust Boundaries

## Product scope

Domain-Specific Paper Harness initially covers three independent topics: Broad
LLM Agents, Brain-Computer Interfaces, and World Models. TopicConfig owns each
topic's scope, categories, inclusion terms, and exclusions; no topic-specific
exclusion is applied as a global product restriction.

## Source boundaries

Daily discovery is arXiv-only. Semantic Scholar, Google Scholar, Crossref,
OpenAlex, publisher sites, news, social media, and arbitrary web search cannot
become daily discovery sources.

Historical inputs may come only from:

- authenticated Semantic Scholar paper search, metadata, references, citations,
  and recommendations;
- the persisted local corpus; and
- the bounded PaSa-derived scholarly tool loop.

Only an arXiv-hosted PDF may enter full-text analysis. A non-arXiv historical
paper is stored only as an available bibliographic/abstract stub, approved
identifiers, and citation relationships. The product never scrapes publisher
sites, bypasses paywalls, downloads publisher PDFs, or redistributes PDFs.

## Historical-search tool boundary

The PaSa-derived loop can invoke only:

```text
search_papers(query, year_from, year_to, limit)
get_paper(semantic_scholar_id)
get_references(semantic_scholar_id)
get_citations(semantic_scholar_id)
get_recommendations(positive_paper_ids)
read_arxiv_paper(arxiv_id)
```

Every loop has explicit maximum steps, queries, queue size, citation depth,
candidates, selected candidates, operation timeout, and overall timeout.
Instructions found inside titles, abstracts, PDFs, TEI, or references are data,
never agent instructions. The loop receives no shell, arbitrary network, code
execution, or filesystem-write access.

Adapters centrally normalize optional provider text: missing, null, empty, or
whitespace-only values become absent, and non-empty strings are trimmed.
Provider order, duplicates, equal timestamps, partial selector coverage, and
surplus candidates are normalized through stable local sorting, deduplication,
and capping. Required identity, timestamps, and field types remain strict;
invalid values fail the narrowest identifiable item without fabricated data.

## Daily execution boundary

FastAPI never starts the Daily pipeline. There is no public run endpoint,
startup task, `BackgroundTasks` workflow, in-process scheduler, permanent
worker, or message bus. Operators use the project CLI or the protected Cloud Run
Job.

The pipeline validates configuration, database/head, GROBID, DeepSeek,
authenticated Semantic Scholar, and the offline SPECTER2 artifact before
external work. Its logical date, target versions, selection limit, scope, and
ownership persist. Completed artifacts may be reused when ownership, identity,
and scope are compatible with the consuming operation. A failed unpublished
execution may re-evaluate and replan from current valid persisted inputs; only
a real ownership, required identity, or scope conflict fails the operation.

## Analysis boundary

Analysis scope is explicitly selected before execution and stored in
provenance. GROBID is the only full-text parser. Failure never changes full-text
analysis into abstract-only analysis.

DeepSeek is the only production LLM provider. Its adapter validates provider
status, strict JSON, Pydantic schema, and domain invariants before persistence.
It does not repair malformed output, expose reasoning, switch models, or create
mock production results.

An analysis failure belongs to its paper and does not stop independent valid
papers. If at least one selected paper completes the required product stages,
publication proceeds with an honest partial result and retained item errors.

Paper-analysis calls receive no tools, shell, arbitrary network, code execution,
or filesystem-write capability. Inputs and outputs are bounded in size and
time. Full prompts, model responses, and chain-of-thought are not persisted or
logged.

## Evidence, comparison, and graph boundary

Evidence must identify a paper version, section, concise excerpt, extraction
source, and any supported claim or relation. Page/coordinates are retained when
available. Confidence is stored only where its meaning is defined.

Comparisons require explicit dimensions and comparability. Claims of priority,
superiority, contradiction, improvement, or direct comparability are not made
without persisted support.

Graph relations distinguish metadata-explicit, text-explicit,
deterministically derived, LLM-inferred, and human-verified provenance.
LLM-inferred relations require evidence, model and prompt version, verification
state, and defined confidence. UI labels must preserve those distinctions.

Trend values are deterministic calculations over persisted records. LLM text
may explain them but cannot invent statistics. Empty, insufficient, and partial
windows remain visible.

## Runtime and secret boundaries

The three runtime identities are separated:

- Web/API can read only `DATABASE_URL` and contains no provider key.
- Daily can read `DATABASE_URL`, DeepSeek, and Semantic Scholar versions and can
  invoke private GROBID.
- Migration can read only `DATABASE_URL` and runs only Alembic.

Scheduler has its own identity and can invoke only the corresponding topic
Daily Jobs. GROBID has no secret accessor. The browser receives no database URL,
provider key, service credential, or direct database access.

Terraform references fixed enabled numeric Secret Manager versions. Secret
values do not enter Git, `.tfvars`, plans, state, images, logs, frontend code,
or command arguments. Long-lived service-account JSON keys are prohibited.

## Code boundaries

The domain imports no FastAPI, SQLAlchemy, arxiv.py, provider client, parser,
embedding implementation, or Google Cloud SDK. External behavior enters through
typed adapters and explicit ports. Test doubles are physically and logically
outside production wiring.

There is no generic workflow DSL, provider registry, event bus, large dependency
injection container, microservice messaging, multi-tenancy, application-level
auth framework, or speculative feature-flag layer.

## Persistence and provenance boundaries

Production persistence is PostgreSQL 15+ with pgvector and `DATABASE_URL`.
SQLite, JSON files, Firestore, provider SDK persistence, and fake production
stores are not substitutes.

Stable IDs, schema versions, timestamps, and source provenance are mandatory.
AI-generated data additionally records model, prompt version, scope, generation
time, source, and verification state where applicable. Foreign keys and unique
constraints prevent evidence, relations, comparisons, and publication records
from referring to missing owners.

Alembic is explicit and never runs during API startup. Readiness requires the
single database head to equal the application head. Existing merged migration
files are immutable; schema changes add a new revision.

## Failure and publication boundary

The per-paper state machine is:

```text
DISCOVERED -> NORMALIZED -> ENRICHED -> RELEVANCE_SCORED -> SELECTED
-> PDF_DOWNLOADED -> PARSED -> ANALYZED -> EVIDENCE_EXTRACTED
-> PRIOR_WORK_RETRIEVED -> COMPARED -> GRAPH_UPDATED -> PUBLISHED
```

Every failure stores its stage, stable error code, retryable flag, and concise
detail. An item is never marked complete after a required stage is skipped, but
its failure does not stop independent valid items.

Publication is one explicit transaction. `COMPLETE`, `PARTIAL`, and `FAILED`
have the meanings defined in `docs/FAILURE_POLICY.md`. Only terminal complete or
partial runs enter public reads. A failed publication cannot expose staging
rows or change prior publication revisions. Upstream child statuses need not
equal the product-publication status; the report matches its owning product run
and preserves relevant item errors. A failed unpublished run may clear staging
and replan from current valid persisted inputs. Explicit same-date reprocessing
creates an additive immutable publication revision; public reads select the
latest successful revision for that topic and logical date. Reprocessing uses
the logical-date lookback without advancing that topic's scheduled-ingestion
cursor.

## Cloud boundary

The browser reaches Web/API only through direct Cloud Run IAP and the configured
owner allowlist. GROBID accepts only the Daily service account. Scheduler accepts
no browser traffic and uses OAuth to call the Cloud Run Jobs API.

Terraform declares no public principal, broad project runtime role, Cloud SQL,
VM, Kubernetes, Redis, fixed-cost load balancer, VPC connector, Cloud NAT, or
exported key. All Cloud Run services use zero minimum instances where supported.

Cloud changes are explicit: inspect a plan, apply it directly, execute migration
and Daily Jobs directly, and enable each Scheduler only after successful
production data verification. Operator scripts do not add IAM roles or retain a
second source of truth for deployed cloud state.

## Build and release boundary

First-party images run as UID/GID 10001. Base images are digest-pinned. Local
ports bind to loopback. Read-only filesystems, dropped capabilities,
no-new-privileges, resource limits, tmpfs, probes, and stop windows are applied
where supported.

The build context excludes secrets, environment files, Terraform state, plans,
backups, exports, PDFs, model caches, logs, and generated frontend output. The
Daily production target prepares the pinned SPECTER2 artifact; the default
verification target never downloads model weights.

Use focused checks for the changed boundary during implementation, then run the
single canonical verification when source and production acceptance are ready
for a milestone or release commit. Release remains blocked by a failed final
canonical check, a modified merged migration, generated contract drift,
credential-shaped content, missing license material, an unpinned required
image/action, a failing image build, a destructive or public Terraform plan, a
non-immutable runtime image reference, or an invalid secret version.
