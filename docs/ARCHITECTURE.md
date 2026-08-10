# Architecture

## System context

Domain-Specific Paper Harness is a private research-intelligence product for
broad LLM-agent research. Daily discovery is arXiv-only. PostgreSQL stores
canonical papers and versions, parsed scientific text, structured analyses and
evidence, historical-paper stubs, scholarly-search provenance, comparisons,
relations, provenance-aware graph records, deterministic trend and lineage
snapshots, run items, and structured reports. FastAPI exposes read-oriented data
and serves the compiled React application in production.

The Ports-and-Adapters modular monolith has three deployment units:

1. `web-api` serves health checks, FastAPI under `/api/v1`, and the React build.
   It is keyless with respect to DeepSeek and Semantic Scholar.
2. `daily` runs explicit arXiv ingestion, structured analysis, six-month
   historical backfill, related-work search, paper comparison, product
   publication, or sufficient-data periodic-report commands. These commands are
   protected operator operations and are not automatically chained by FastAPI
   or an in-process scheduler.
3. `grobid` is the sole scientific PDF parser for `FULL_TEXT` analysis. It is a
   separate IAM-authenticated Cloud Run service in the Terraform design and an
   opt-in hardened Compose service locally.

All first-party Python units use CPython 3.13.13. GROBID is an isolated Java
service. PostgreSQL 15 or newer with pgvector is the only persistence contract.

## Dependency direction and upstream reuse

Dependencies point inward:

```text
external API, model, parser, or database
        -> adapter
        -> port
        -> application use case
        -> domain
```

The domain owns identities, scopes, state transitions, provenance fields,
search limits, comparability, and invariants. It does not import FastAPI,
SQLAlchemy, arxiv.py, DeepSeek, GROBID, Semantic Scholar, SPECTER2, PaperQA2,
PaSa, Scholar QA, or Google Cloud code. Application use cases coordinate ports;
entrypoints perform configuration and constructor wiring.

Upstream reuse remains deliberately narrow:

- PaperQA2 `v2026.03.18` was rejected for code/package reuse in M2. Project-owned
  section-aware evidence grounding preserves GROBID provenance.
- PaSa commit `2aaa6a9b1e48d24a2b7e21e8551f863dad9eeb84` supplies only the
  Crawler/Selector architectural idea. The implementation is first-party typed
  application code; no PaSa source, prompt, checkpoint, training stack, or
  independent database is copied or called.
- Ai2 Scholar QA `0.8.13` at commit
  `db1fdf3746d6ae338473f0176110082228ee8635` supplies only comparison-planning
  and evidence-clustering concepts. Its provider, retriever, reranker, cache,
  tracing, and synthesis code is not installed, vendored, or copied.
- SPECTER2 Base supplies the selected v0.1 title-and-abstract embedding model.
  The exact model and tokenizer revision is
  `3447645e1def9117997203454fa4495937bfbd83`. The Daily production image
  contains a hash-verified safetensors conversion and loads it offline; no
  upstream Python source is copied.
- STORM commit `fb951af7744dab086e34962e9bc6fe878e145f83` supplies only the
  coverage-aware outline-to-section architectural pattern. M4 report assembly
  is first-party typed code over persisted local evidence. STORM source,
  prompts, internet retrievers, provider wrappers, embeddings, and filesystem
  persistence are not installed, copied, or called.

## Daily arXiv ingestion

The Daily Job builds an arXiv query from `TopicConfig`, reads a persisted cursor
with overlap, and requests metadata through `ArxivPort`. Normalization separates
the stable canonical arXiv identity from its explicit version. External data is
validated before a short transaction upserts papers, versions, source
identities, authors, run items, and the next cursor. Unique constraints and a
PostgreSQL advisory lock make a repeated logical window safe and idempotent.

Semantic Scholar is not part of this flow. It cannot become a daily discovery
provider.

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

`FULL_TEXT` never downgrades after a PDF or parser failure. DeepSeek is fixed to
`deepseek-v4-flash`, reasoning output is disabled, and malformed or
schema-invalid content is rejected rather than repaired. Stable IDs and
composite constraints prevent claims or evidence from crossing paper, version,
or analysis ownership.

## Historical backfill

The explicit historical operation computes one inclusive six-month window and
derives a bounded query plan from the topic's include terms:

```text
TopicConfig + through date
  -> persisted query plan and RUNNING backfill row
  -> authenticated Semantic Scholar search, one bounded query page at a time
  -> date, topic-relevance, and exclusion filtering
  -> stable external identities and bibliographic/arXiv availability stubs
  -> pinned SPECTER2 Base CLS embeddings for title + abstract
  -> atomic page persistence and next-query index
  -> deterministic representative selection within the same window
  -> COMPLETE backfill row
```

Only stubs with an arXiv identity are marked full-text-available. Non-arXiv
results remain bibliographic/abstract stubs and cannot enter PDF analysis.
Identity prefers canonical arXiv identity where present and retains Semantic
Scholar and supplied external identifiers as aliases.

A process interruption that leaves the row `RUNNING` resumes at its persisted
next-query index only when query plan, result bound, timeout, and the complete
embedding contract still match. That contract includes model and tokenizer
identifiers/revisions, dimension, preprocessing, model provenance, and source.
`COMPLETE` is idempotent after the same provenance check. A caught error marks
the row `FAILED`; a later explicit invocation with identical configuration
moves that same row back to `RUNNING` and resumes from the last committed query
boundary.

## PaSa-derived related-work search

Related-work search requires a persisted source-paper analysis and records one
analysis-pinned, version-pinned, year-bounded session:

```text
source paper version + analysis + objective
  -> local lexical and SPECTER2 vector retrieval
  -> explicit arXiv-to-Semantic-Scholar source identity action
  -> strict DeepSeek Crawler plan persisted with queries, expansion choices,
     decision reason, model identity, and usage
  -> bounded Semantic Scholar search/recommendation/citation expansion
  -> persisted action and candidate-discovery provenance
  -> deterministic component scores and bounded candidate ranking
  -> strict DeepSeek Selector decision
  -> persisted COMPLETE or FAILED session with stop reason and model usage
```

The Crawler/Selector receives no shell, filesystem, arbitrary HTTP, or code
execution. Its scholarly boundary is limited to approved paper search,
metadata, references, citations, recommendations, and arXiv-paper reading
operations. Search records every action, requested/result count, depth,
duration, terminal error, candidate origin, and decision. `max_steps`,
`max_queries`, `max_queue_size`, `max_citation_depth`, `max_candidates`,
`max_selected_candidates`, per-operation timeout, and overall timeout are
validated and persisted. Semantic Scholar calls share the smaller of the
per-operation timeout and remaining overall deadline across rate-limit waits,
retries, pages, and response reads.

Lexical and vector retrieval are a bounded union over the persisted corpus;
Semantic Scholar rank, lexical similarity, cosine similarity, citation
discovery, and recommendation discovery remain inspectable components rather
than an opaque score. References and citations retain their discovery-action
provenance. An overall deadline yields an explicit `OVERALL_TIMEOUT` terminal
stop; provider, schema, invariant, or persistence failure yields `FAILED`.

The v0.1 encoder is `allenai/specter2_base` without an adapter. It tokenizes
whitespace-normalized title + tokenizer separator token + abstract with
padding/truncation to 512 tokens and no token-type IDs, then persists the
unnormalized final-layer CLS vector. Runtime loading is safetensors-only,
`trust_remote_code=False`, local-only, and offline. There is no random,
commercial, adapter, generic, or keyword-only embedding fallback.

## Evidence-linked comparison

Comparison accepts only a completed search session and a target that the
session selected and linked to a local historical paper version. The session
pins the source analysis ID/scope; the comparison pins both source and target
analysis IDs/scopes. DeepSeek returns the fixed comparison dimensions, an
explicit comparability status and reason, concise summary, evidence UUIDs, and
optional inferred relations. The adapter rejects unknown or wrong-analysis
evidence, duplicate relation types, unsupported schemas, and an `IMPROVES_ON`
claim unless the result is directly comparable and has bilateral evidence.

Comparison, ordered dimensions, evidence links, and `LLM_INFERRED` relations
commit atomically with model, prompt, usage, verification, and version
provenance. Relation `confidence` is an uncalibrated model-assessed measure of
how strongly the cited evidence supports that relation. It is constrained to
`[0, 1]`, but it is not a probability, accuracy estimate, human confidence, or
verification. The UI must label it accordingly and display `UNVERIFIED`
separately.

## M4 graph, trends, lineages, and reports

M4 deliberately starts a separate `PRODUCT_PUBLICATION` run sourced from one
persisted M2 analysis run. It does not mutate M2's terminal
`EVIDENCE_EXTRACTED` items or pretend that M3's separately persisted search and
comparison records advanced them. Each product item requires an available M3
comparison and then advances durably through comparison, graph update,
trend-snapshot preparation, report generation, and publication. A publication
run with at least one completed item may become `PARTIAL`; zero completed items
or a report/publication transaction failure becomes `FAILED`.

The graph uses topic-scoped canonical entities for Paper, ResearchProblem,
Method, Task, Dataset, and Benchmark. Conservative NFKC, punctuation-variant,
whitespace, and case normalization merges only exact canonical keys. Mentions
and edges retain exact analysis/comparison/paper-relation owners, evidence IDs,
provenance, model metadata where applicable, confidence meaning, and
verification state. Text-explicit and LLM-inferred records require evidence;
LLM-inferred records additionally require complete model provenance and the
uncalibrated support score. Paper labels retain the 4,000-character title bound;
concept labels are complete validated fields capped at 500 characters and are
omitted rather than heuristically split or truncated. Graph reads independently
bound nodes, edges, and mentions and expose accurate totals and truncation.

Lineage snapshots traverse only permitted persisted paper relations within the
currently retrieved corpus. Traversal is deterministic, cycle-safe, and bounded
by depth, nodes, and edges. The response states truncation, corpus scope,
explicit and verified predecessor availability, and limitations; global
completeness is never claimed.

Trend snapshots use exact inclusive 7-, 30-, and 90-day windows and equal-sized
preceding windows over persisted paper/version activity. Paper volume uses each
version's first activity; entity mentions and relations use their owning
publication run's logical date. Counts are distinct by paper, entity appearances
and relation keys are deterministic, representative papers have a stable
ranking, and fixed thresholds distinguish `SUFFICIENT`, `LIMITED`, and
`INSUFFICIENT`. Zero denominators and small denominators suppress percentage
growth rather than inventing a value. Read projections return a bounded Top-N
entity list with total and truncation metadata; relation types and representative
papers are already fixed-domain bounded.

Daily reports use a fixed five-section outline and persist counts, paper/entity/
comparison/lineage highlights, trend links, failures, missing sections,
Evidence-ID links, and full model provenance where applicable. The narrative
mode is selected before execution: `DEEPSEEK` uses the existing strict LLMPort
method and rejects unknown or wrong-section citations, rejected Evidence,
malformed sections, and any model-authored numeric literal; `STRUCTURED_ONLY` is
an explicit deterministic mode and never an error fallback. Missing-section
declarations enter the authoritative request before synthesis. Weekly reports
require seven daily dates and at least three papers; monthly reports require at
least twenty daily dates and ten papers. Statistics remain deterministic and
authoritative in either mode.

## Read API and product UI

FastAPI reads through `RepositoryPort`; OpenAPI is the sole frontend contract.
The M4 read surface includes:

- graph filtering by topic, date, paper, exact entity, type, provenance, and
  verification with independent node/edge/mention bounds;
- 7/30/90-day trend snapshots with representative papers, explicit data
  sufficiency, entity-type filtering, and a bounded entity projection;
- bounded lineage lookup;
- latest and dated product-publication runs, item failures, daily-report history,
  and exact weekly/monthly period reports; and
- direct historical run lookup in addition to the existing paper, evidence,
  related-work, and comparison reads.

React provides the dashboard, daily-report history/detail, paper and comparison
views, a Cytoscape graph with trust filters, 7/30/90-day Recharts views, lineage
inspection, run status, and item-level failure display. The API remains
read-only: it has no public execution endpoint and never migrates on startup.

## Persistence and transactions

Migration `0002_m2_structured_analysis` owns parsed papers, analyses, evidence,
legacy analysis reports, and failures. M3 migration
`0003_m3_pasa_semantic_scholar` adds normalized external stubs and identifiers,
backfill runs and corpus entries, search sessions/actions/candidates/discoveries,
768-dimensional pgvector embeddings with complete model/tokenizer/preprocessing
provenance, comparisons/dimensions/evidence links, and paper
relations/relation-evidence links. Current migration
`0004_m4_graph_trends_reports` adds topic-scoped graph entities, mentions, edges
and evidence links; deterministic trend metrics and representative papers;
bounded lineage snapshots/nodes/edges; product-run source ownership; and
normalized report sections, highlights, trend/lineage/evidence links, periods,
counts, narrative mode, and model provenance. Existing M2 rows are preserved as
`ANALYSIS` reports.

External calls occur outside short write transactions. Backfill pages advance
their cursor only with the page's validated records. Search actions and
candidate provenance preserve their owning session. Comparison ownership
constraints prevent a relation or evidence link from crossing its two paper
versions. A failed comparison write rolls the complete bundle back.

The first product-publication start atomically stores its exact source analysis
and comparison input IDs. Graph, trend, and lineage writes are run-owned staging
data; public reads admit only terminal `COMPLETE` or `PARTIAL` owners. Final
report, report links, item publication states, and run status commit atomically.
Failure removes staging without changing prior published canonical entities, and
an explicit retry reuses the failed run plus frozen input snapshot. Because M3
comparisons necessarily follow M2 analysis, a delayed/backfilled logical date is
a current-state publication snapshot, not a historical end-of-day reconstruction.

## Runtime and deployment

Local Compose provides PostgreSQL plus optional Web/API, Daily, and GROBID
profiles. Terraform remains gated:

- the foundation creates required APIs, Artifact Registry, service accounts,
  and empty Secret Manager resources;
- `deploy_runtime_resources=true` requires immutable Web/API and Daily images
  plus a fixed `DATABASE_URL` secret version;
- `deploy_analysis_resources=true` additionally requires the runtime gate, an
  immutable mirrored GROBID wrapper image, and a fixed DeepSeek secret version;
  and
- M3 Semantic Scholar attachment is separately gated by a fixed secret version
  and grants access only to the Daily service account.

The Web/API service uses direct Cloud Run IAP. GROBID uses IAM invocation with
only the Daily service account as invoker. Semantic Scholar and DeepSeek secrets
are not injected into Web/API. No `allUsers`, VPC connector, Cloud NAT, load
balancer, or other fixed-cost networking resource is configured.

The Daily Dockerfile keeps its default CI target model-free. Deployment selects
an explicit production target that installs only the Daily embedding extra,
verifies and converts the pinned Base weights at build time, and copies the
prepared artifact into `/opt/models/specter2_base`. The runtime forces Hugging
Face and Transformers offline modes, so each Job does not redownload weights.

The proximity adapter is not part of v0.1. Adapters 1.3.0 requires Transformers
4.57.x, below the project's patched Transformers 5.3+ security floor. Adopting
the adapter later is a future explicit architecture decision after upstream
compatibility exists. No production resource has been applied; production
database/secret prerequisites and Google API connectivity remain independent
deployment blockers.

## Verification

`scripts/verify.ps1` is the canonical Windows entrypoint. The credential-free
gate covers exact Python, frozen dependency sets,
backend/frontend quality checks, generated-contract drift, Compose, Terraform,
clean sequential Alembic upgrades, PostgreSQL/pgvector repository tests,
credential-free browser tests, and all three focused image builds. M4 adds clean
and populated `0003 -> 0004` migration checks plus graph, lineage, trend,
publication, report, API-contract, React, and Playwright coverage.

Default verification never calls Semantic Scholar. The live adapter smoke is
explicitly opt-in with `RUN_LIVE_SEMANTIC_SCHOLAR_TEST=1`; selecting it without
`SEMANTIC_SCHOLAR_API_KEY` fails clearly, while leaving both unset results in an
expected skip. The explicit SPECTER2 Base smoke has passed on CPython 3.13.13
and Transformers 5.3.0 with finite, repeat-stable 768-dimensional output plus
real pgvector persistence and retrieval. The model-bearing production image has
also built and loaded the artifact offline; normal verification never downloads
model weights.
