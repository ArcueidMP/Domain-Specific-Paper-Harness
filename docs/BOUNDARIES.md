# Product and Trust Boundaries

## Product scope

The product covers planning, reasoning, memory, tool use, web and computer-use
agents, multi-agent coordination, evaluation, benchmarks, safety, and security
where an LLM-agent workflow is material. It excludes generic chatbots, pure RAG,
conventional reinforcement-learning agents, agent-based simulation, non-LLM
chemical or biological agents, and embodied systems without a material
LLM-agent component.

## Source boundaries

- Daily discovery uses arXiv only. Semantic Scholar never becomes a daily feed
  or substitute discovery source.
- arXiv metadata and PDF URLs enter through `ArxivPort`; application code owns
  query construction, overlap, cursor state, version identity, and persistence.
- Only an arXiv-hosted PDF attached to an explicitly selected stored version may
  enter `FULL_TEXT` analysis.
- Historical and related-work metadata may come only from authenticated Semantic
  Scholar, the persisted local corpus, and the approved PaSa-derived workflow.
- Non-arXiv Semantic Scholar results are bibliographic/abstract stubs with
  `full_text_available=false`. They cannot enter full-text analysis.
- Publisher scraping, paywall bypass, publisher PDF download, arbitrary web
  search, Google/Serper/Scholar discovery, Crossref/OpenAlex substitution, and
  hidden metadata providers are prohibited.

## Historical-search tool boundary

The PaSa-derived Crawler/Selector is first-party application code. It may use
only the declared scholarly operations for paper search, one-paper metadata,
references, citations, recommendations, and arXiv-paper reading. It receives no
generic browser, arbitrary URL fetch, shell, filesystem write, code execution,
database query, or provider-registry access.

Every search session persists its exact source paper/version/analysis/scope,
effective year window, objective, limits, actions, candidate discoveries, score
components, decisions, stop reason, and model provenance. The effective
candidate set cannot exceed the validated selector bound. Per-operation and
overall deadlines, query count, action count, queue size, citation depth,
candidate count, and selected count are explicit; the model cannot raise or
bypass them.

PaSa itself is not a runtime dependency. No upstream PaSa prompts, source,
checkpoints, custom Transformers fork, training/PPO code, Google/Serper search,
ar5iv reader, or independent paper database is installed or copied.

## Analysis-scope boundary

`FULL_TEXT` and `ABSTRACT_ONLY` are explicit pre-execution modes. The selected
scope is persisted on the structured-analysis run and each successful analysis,
and participates in the analysis's stable identity. Abstract-only mode uses only
the stored arXiv abstract. Full-text mode requires GROBID; download or parser
failure is an item failure and never triggers abstract-only analysis.

Historical backfill selects representative arXiv stubs for later processing; it
does not silently download or analyze them. Related-work comparison requires
both exact local paper versions to have persisted analyses and evidence.

## Model, parser, and embedding boundaries

- DeepSeek `deepseek-v4-flash` is the sole LLM provider/model for structured
  analysis, Crawler planning, candidate selection, comparison, and the
  explicitly selected model-backed report mode.
- Model calls receive bounded structured inputs and no hidden tools. Empty,
  malformed, schema-invalid, domain-invalid, ungrounded, or usage-inconsistent
  output fails. It is not repaired, coerced, retried for content, or routed to
  another model.
- GROBID 0.9.0 CRF is the sole scientific parser. Requests explicitly disable
  Crossref/biblio-glutton consolidation. No alternate PDF parser exists.
- SPECTER2 Base is the only approved v0.1 paper embedding model. The contract
  pins model and tokenizer revision
  `3447645e1def9117997203454fa4495937bfbd83`, title/separator/abstract
  preprocessing, a 512-token bound, unnormalized final-layer CLS pooling, and
  768 dimensions.
- The production loader requires a hash-verified safetensors artifact prepared
  with Transformers 5.3.0, CPU PyTorch 2.13.0, `trust_remote_code=False`, and
  `weights_only=True`. Runtime is local-only and offline. It does not download
  weights per Job, load pickle weights, downgrade Python, or substitute a
  generic/commercial/alternate embedding provider or adapter.
- The proximity adapter is explicitly rejected for v0.1 because Adapters 1.3.0
  requires Transformers 4.57.x, below the patched Transformers 5.3+ security
  floor. Any future adapter adoption requires a new architecture decision after
  upstream compatibility exists; it is not a fallback path.

PaperQA2 and Ai2 Scholar QA were reviewed at exact revisions but are not
installed, vendored, copied, or called. M2 evidence grounding and M3 comparison
planning are project-owned typed code over persisted GROBID/analysis evidence.
STORM was likewise reviewed at an exact revision and is not installed, vendored,
copied, or called. M4 implements only its outline-to-section concept as
first-party typed synthesis over persisted local records and Evidence IDs.

## Graph, lineage, trend, and report boundary

Graph identities are topic-scoped. Non-paper entities merge only after exact
canonical-key equality under the documented conservative normalization; M4 does
not ask an LLM to invent aliases or entity equivalence. Every mention and edge
retains its exact paper version and analysis or comparison owner. Text-explicit
and inferred records require persisted supporting Evidence IDs, and rejected
records never enter trends or lineages.

Paper labels preserve titles up to the existing 4,000-character paper bound.
Concept labels are bounded to 500 characters and come only from complete
validated structured fields; M4 does not split prose heuristically. An overlong
or unavailable concept is omitted and reported as missing rather than truncated
into a false canonical identity.

Graph responses have separate node, edge, and per-node mention limits, accurate
totals, and truncation state. Trend responses bound their highest-activity entity
rows and expose the matching total. Lineage follows only the directed predecessor
relations `CITES`, `EXTENDS`, and `IMPROVES_ON`; it is cycle-safe and has
independent depth, node, and edge bounds. It reports
`CURRENTLY_RETRIEVED_CORPUS`, truncation, and predecessor availability; it cannot
assert exhaustive or global ancestry.

Seven-, thirty-, and ninety-day statistics are deterministic database-derived
facts over exact current and preceding windows. Fixed paper-count thresholds,
denominator handling, and stable representative ranking cannot be changed by a
model. Insufficient and limited windows remain visible, and a zero or small
denominator suppresses percentage growth. Paper volume uses each version's first
published activity date; entity and relation activity use the owning product
run's logical date, never the server's UTC write date.

Report synthesis receives only bounded persisted highlights, deterministic
statistics, missing-section declarations, limitations, failures, and a
section-specific Evidence-ID allowlist. Rejected Evidence is excluded. DeepSeek
output must contain the fixed ordered section schema, may cite only IDs allowed
for that section, and may not introduce numeric literals; all published counts
and percentages remain deterministic fields. Malformed output, an unknown or
wrong-section citation, or model-authored statistics fails; it is not repaired,
retried as content, polished by another pass, or replaced by the deterministic
mode.
`STRUCTURED_ONLY` is a preselected explicit mode, not a fallback. Weekly and
monthly synthesis is unavailable until fixed deterministic coverage thresholds
are met.

## Relation and confidence boundary

Every relation identifies exact source and target paper versions, a provenance
class, supporting evidence where required, verification state, and generation
metadata. `LLM_INFERRED` relations require evidence UUIDs, DeepSeek model/prompt
provenance, and a finite `confidence` score in `[0, 1]`.

That score means only uncalibrated model-assessed evidential support: how
strongly the cited evidence appears to support the proposed relation. It is not
a probability that the relation is true, an accuracy estimate, a human review
score, or a verification result. The product must label it separately from
`UNVERIFIED`, `HUMAN_VERIFIED`, or `REJECTED` status and must never render it as
certainty.

## Runtime and secret boundaries

- FastAPI is read-oriented. It does not schedule work, perform migrations, load
  DeepSeek/Semantic Scholar secrets, or expose a public execution endpoint.
- Manual work enters through the project CLI, `scripts/run-daily.ps1`, the Daily
  entrypoint, or `gcloud run jobs execute`.
- The browser calls only Web/API and never receives database credentials,
  DeepSeek or Semantic Scholar keys, GROBID identity tokens, or service-role
  credentials.
- Web/API starts and serves stored data without `SEMANTIC_SCHOLAR_API_KEY`.
  Historical backfill and related-work search validate it only when selected.
- Web/API and Daily share PostgreSQL/domain contracts but are independently
  deployed Cloud Run units. Terraform can attach the Semantic Scholar secret
  only to the Daily service account.
- GROBID is a separate service. Local unauthenticated access is development-only;
  production requires Google identity authentication and HTTPS.

## Code boundaries

Ports represent real external boundaries: arXiv, scholarly search, LLM,
scientific embedding, PDF parsing, and persistence. A separate evidence engine
is not present because grounding is deterministic project-owned validation.
Production wiring cannot import test doubles.

The allowed dependency direction is adapter to port to application to domain.
Domain code cannot depend on FastAPI, SQLAlchemy, arxiv.py, GROBID, DeepSeek,
Semantic Scholar, SPECTER2 runtime packages, PaperQA2, PaSa, Scholar QA, or
Google Cloud libraries.

## Persistence and provenance boundaries

PostgreSQL 15+ with pgvector and `DATABASE_URL` is the only persistence contract.
The browser never accesses it directly. SQLite, files, a provider SDK, or an
in-memory production store cannot replace it.

M3 external stubs retain Semantic Scholar IDs and supplied aliases while stable
identity prefers canonical arXiv identity where available. Later arXiv/DOI
enrichment transactionally promotes or merges an earlier S2-only stub and
rekeys its dependent corpus, embedding, candidate, and discovery rows. Search
sessions, actions, candidate discoveries, embeddings, comparisons, relations,
graph entities/mentions/edges, trend and lineage snapshots, product runs,
reports, sections, highlights, and evidence links retain stable IDs, schema
versions, ownership, timestamps, exact model/prompt revisions where applicable,
source, and verification state.

Complete prompts, model responses, hidden reasoning, full paper text, PDFs,
model weights, secrets, and authorization headers are not persisted for
debugging or logged. Backfill pages, candidate/action writes, and comparison
bundles use short atomic transactions. Database constraints reject cross-session,
cross-comparison, cross-paper, or cross-version ownership.

## Failure and publication boundary

Item stages advance only after their required durable write commits. Each item
failure records a failed stage, stable error code, retryability, and concise
detail. M3 protected operations do not silently mark later graph/publication
stages complete and are not invoked by FastAPI. M4 instead opens a distinct
`PRODUCT_PUBLICATION` run that references the source M2 run, consumes persisted
M3 comparisons, and owns its graph/trend/report/publication stages.

An interrupted `RUNNING` backfill and an explicitly reinvoked `FAILED`
backfill both resume from the last committed query boundary, only with the
identical plan, limits, and embedding provenance. A completed related-work
session records an explicit stop reason, including bounded exhaustion or
overall timeout. Provider, schema, domain, or persistence errors record
`FAILED`; they do not switch source or mode.

Comparison is accepted only for a completed session and a selected local target.
It either persists the comparison, dimensions, relations, and evidence links as
one valid bundle or persists none of them.

Product publication stages graph, trend, and lineage records under its run before
aggregate calculation. Read APIs expose only artifacts owned by terminal
`COMPLETE` or `PARTIAL` runs. Final report insertion, item `PUBLISHED` states, and
terminal run state commit atomically; a failed publication removes its staged
artifacts without mutating earlier published canonical entities. At least one
completed item permits a visibly `PARTIAL` report with all missing papers and
stable errors. No completed item, aggregate failure, model-output failure, or
final publication failure produces a report and marks the run `FAILED` where
persistence remains available.

The first start atomically freezes the exact analysis and comparison input IDs.
An explicit retry reuses the same failed run and frozen inputs after clearing its
staging rows. A logical-date report is therefore a current-state publication
snapshot for that source analysis date, not a claim that delayed backfill
reconstructs the historical end-of-day corpus. A completed artifact is idempotent
only for the same preselected narrative mode; another mode is a stable conflict.

## Cloud boundary

The browser boundary is direct Cloud Run IAP with an explicit owner allowlist.
The GROBID service retains Cloud Run IAM invocation checks and has no public
invoker; only the Daily service account receives invocation permission. The
Daily service account is also the only runtime identity eligible to read a
configured Semantic Scholar secret version.

Runtime, analysis, and M3 secret attachment remain disabled until immutable
images and fixed secret versions exist. There is no `allUsers` binding, exported
service-account key, paid load balancer, Cloud SQL instance, Kubernetes cluster,
VPS, Redis, permanent worker, VPC connector, or Cloud NAT resource. The
Terraform design has not been applied to production.
