# Product and Trust Boundaries

## Product scope

The product covers planning, reasoning, memory, tool use, web and computer-use
agents, multi-agent coordination, evaluation, benchmarks, safety, and security
where an LLM-agent workflow is material. It excludes generic chatbots, pure RAG,
conventional reinforcement-learning agents, agent-based simulation, non-LLM
chemical or biological agents, and embodied systems without a material
LLM-agent component.

## Source boundaries

- Daily discovery uses arXiv only.
- arXiv metadata and PDF URLs enter through `ArxivPort`; application code owns
  query construction, overlap, cursor state, version identity, and persistence.
- Only an arXiv-hosted PDF attached to an explicitly selected stored version may
  enter `FULL_TEXT` analysis.
- Semantic Scholar and PaSa-derived historical search begin in M3 and cannot
  become daily discovery providers.
- Publisher scraping, paywall bypass, publisher PDF download, arbitrary web
  search, and hidden metadata providers are prohibited.

## Analysis-scope boundary

`FULL_TEXT` and `ABSTRACT_ONLY` are explicit pre-execution modes. The selected
scope is persisted on the structured-analysis run and each successful analysis,
and participates in the analysis's stable identity.
Abstract-only mode uses only the stored arXiv abstract and never downloads or
parses a PDF. Full-text mode requires GROBID; download or parser failure is an
item failure and never triggers abstract-only analysis.

The user selects persisted paper UUIDs. M2 does not silently select every paper,
exceed the configured representative-paper bound, or accept a stale version when
the current explicit version was requested.

## Model and parser boundaries

- DeepSeek `deepseek-v4-flash` is the sole M2 LLM provider and model.
- The analysis call receives bounded paper passages but no shell, filesystem
  write, arbitrary network, code execution, or other tool access.
- DeepSeek output must be one schema-valid JSON object. Empty, malformed,
  schema-invalid, domain-invalid, ungrounded, or usage-inconsistent output fails;
  it is not repaired, coerced, retried for content, or sent to another model.
- GROBID 0.9.0 CRF is the sole scientific parser. Requests explicitly disable
  Crossref/biblio-glutton consolidation and retain TEI source structure.
- The GROBID adapter accepts bounded PDF input and bounded namespaced TEI output.
  A non-200 response, empty extraction, malformed XML, unsafe declaration,
  invalid coordinate, duplicate identity, or unresolved citation target is
  explicit parser failure. No alternate parser exists.

PaperQA2 was reviewed at an exact version and commit but is not installed,
vendored, or called. Project-owned deterministic grounding preserves GROBID
section, passage, and coordinate provenance without PaperQA2's parser, providers,
index, or malformed-JSON repair behavior.

## Runtime boundaries

- FastAPI is read-oriented. It does not schedule work, perform migrations, load
  analysis secrets, or expose a public execution endpoint.
- Manual work enters through the project CLI, `scripts/run-daily.ps1`,
  `paper-harness-daily`, or `gcloud run jobs execute`.
- The browser calls only Web/API and never receives database credentials,
  DeepSeek secrets, GROBID identity tokens, or service-role credentials.
- Web/API and Daily share PostgreSQL/domain contracts but are independently
  deployed Cloud Run units.
- GROBID is a separate service. Local access may be unauthenticated only in an
  explicit development environment. Production requires Google identity
  authentication and HTTPS.

## Code boundaries

Ports represent real external boundaries: arXiv, scholarly search, LLM, PDF
parsing, and persistence. A separate evidence engine is not present because M2
uses deterministic project-owned validation and mapping rather than an external
component. Production wiring cannot import test doubles.

The allowed dependency direction is adapter to port to application to domain.
Domain code cannot depend on FastAPI, SQLAlchemy, arxiv.py, GROBID, DeepSeek,
Semantic Scholar, PaperQA2, or Google Cloud libraries.

## Persistence and provenance boundaries

PostgreSQL 15+ with pgvector and `DATABASE_URL` is the only persistence contract.
The browser never accesses it directly. SQLite, files, a provider SDK, or an
in-memory production store cannot replace it.

Parsed text, analyses, claims, evidence, reports, and failures retain stable IDs,
schema versions, exact paper/version ownership, source, and timestamps. AI
records additionally retain provider, configured and returned model identities,
prompt version, scope, verification state, token/call/duration data, and available
cost estimates. Evidence retains section, passage, optional coordinates, concise
exact excerpt, evidence type, extraction source, and supported claim IDs.

Complete prompts, model responses, hidden reasoning, full paper text, PDFs,
secrets, and authorization headers are not persisted for debugging or logged.
Analysis, claims, evidence, and evidence-claim links commit atomically; database
constraints reject cross-analysis or cross-version ownership.

## Failure and publication boundary

Item stages advance only after their required durable write commits. Each failure
records failed stage, stable error code, retryability, and concise detail. A run
publishes a deterministic report only when at least one selected paper completes:
`COMPLETE` contains no failures, while `PARTIAL` prominently includes every
failed selected item. Zero completed selected papers produces `FAILED` and no
report.

FastAPI exposes persisted analysis/evidence and latest-run report/failure data.
React presents scope, provenance, verification status, source excerpts,
coordinates, `PARTIAL` banners, and item-level error details without implying
that unverified model output is certain.

## Cloud boundary

The browser boundary is direct Cloud Run IAP with an explicit owner allowlist.
The GROBID service retains Cloud Run IAM invocation checks and has no public
invoker; only the Daily service account receives invocation permission. Its
network endpoint uses normal Cloud Run ingress so service-to-service IAM works
without a VPC connector, NAT gateway, or load balancer. Network-internal-only
ingress would require separately reviewed networking and is not configured.

Runtime and analysis resources remain disabled until immutable images and fixed
secret versions exist. There is no `allUsers` binding, exported service-account
key, paid load balancer, Cloud SQL instance, Kubernetes cluster, VPS, Redis,
permanent worker, VPC connector, or Cloud NAT resource. The Terraform design is
implemented but no production resource has been applied.
