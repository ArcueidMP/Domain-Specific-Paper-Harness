# Failure Policy

## Principles

Failures are explicit, attributable to one operation or pipeline stage, and
persisted when a run/session record exists. Retrying repeats the same operation;
it never changes the provider, parser, model, embedding contract, analysis
scope, source, or persistence backend. There is no implicit fallback.

Configuration, authentication, domain-invariant, migration, database, external
schema, Pydantic, and non-transient HTTP failures fail immediately. Network
timeouts and HTTP 429, 500, 502, and 503 responses may use the owning adapter's
bounded retry policy, including bounded `Retry-After` handling. HTTP 400, 401,
403, and 422 responses are not retried.

## Item state and durable progress

The complete product state order is:

```text
DISCOVERED -> NORMALIZED -> ENRICHED -> RELEVANCE_SCORED -> SELECTED
-> PDF_DOWNLOADED -> PARSED -> ANALYZED -> EVIDENCE_EXTRACTED
-> PRIOR_WORK_RETRIEVED -> COMPARED -> GRAPH_UPDATED -> PUBLISHED
```

M1 ingestion reaches normalization. The M2 structured-analysis operation starts
explicitly selected items at `SELECTED` and terminates successfully at
`EVIDENCE_EXTRACTED`. Current M3 historical backfill, related-work search, and
comparison are separate protected operations with their own persisted records;
they do not simulate later graph/publication stages or silently advance M2 run
items. The M4 stages are not simulated or marked complete. An item advances
only after its required durable write commits.

`FULL_TEXT` and `ABSTRACT_ONLY` are selected before analysis. A failed PDF
download or GROBID parse records a full-text failure and never changes scope.
Abstract-only analysis never invokes GROBID.

## Structured-analysis dependency failures

The analysis operation validates DeepSeek before opening a run and validates
GROBID URL/identity before full-text work. The read API requires neither.

- DeepSeek authentication failure marks the current item and the analysis run
  `FAILED`, then stops. No later selected item is attempted.
- DeepSeek transport exhaustion, request rejection, empty output, malformed
  JSON, schema violation, domain violation, unsupported finish reason, or
  inconsistent usage is an `ANALYZED` item failure. Invalid content is neither
  repaired nor retried as content.
- An invalid arXiv PDF is a `PDF_DOWNLOADED` failure. Exhausted global arXiv
  availability aborts the run; no later item is attempted.
- GROBID authentication, availability, size, content-type, XML, TEI, coordinate,
  reference, or citation-target failure is a `PARSED` item failure. No alternate
  parser or abstract fallback is called.
- Unknown passages, non-verbatim excerpts, invalid claim links, or ownership
  violations are an `EVIDENCE_EXTRACTED` failure. No partial analysis bundle is
  persisted.

## Semantic Scholar and SPECTER2 configuration failures

The read API starts without `SEMANTIC_SCHOLAR_API_KEY`. Historical backfill and
related-work search validate the key only when invoked. Missing or blank
configuration fails before a backfill or search session is opened. Selecting
the explicit live smoke test without the key fails clearly; default verification
does not select it and records an expected skip.

The Semantic Scholar adapter maps 401/403 to non-retryable authentication
failure, 400/422 to non-retryable request failure, malformed or inconsistent
payloads to non-retryable response failure, and exhausted 429/5xx/transport
retries to retryable unavailability. Pagination, page/result counts,
`Retry-After`, request time, operation time, and response size are bounded. A
retry repeats the same Semantic Scholar operation and never changes providers.

SPECTER2 Base is the explicit v0.1 production model, not a fallback. Preparation
fails before writing an artifact if the model/tokenizer revision, official
source-weight hash, runtime versions, architecture, separator token, dimension,
or safe serialization contract differs. Runtime fails before backfill or search
persistence if the strict manifest, safetensors hash, pinned Transformers/PyTorch
versions, local artifact, or offline loading contract differs. It never downloads
at runtime, loads pickle weights, repairs an artifact, switches embeddings, or
loads the proximity adapter.

The proximity adapter remains rejected for v0.1 because Adapters 1.3.0 requires
Transformers 4.57.x, below the patched Transformers 5.3+ security floor. That is
a recorded architecture decision, not a runtime failure to be recovered through
fallback. A future adapter requires a new explicit decision after upstream
compatibility exists.

## Historical-backfill durability and retry policy

Each six-month backfill has a stable topic/window identity and persists its
exact query plan, per-query result bound, overall timeout, complete embedding
contract, next-query index, counts, and status. The embedding contract includes
model and tokenizer identifiers/revisions, dimension, preprocessing, model
provenance, and source. A successful page commits its stubs, identifiers, corpus
entries, embeddings, counts, and cursor together. A failed page does not advance
the cursor.

Resume behavior is explicit and configuration-safe:

- `RUNNING`: an interrupted process may resume at `next_query_index`, but only
  with the identical plan, limits, and complete embedding contract;
- `COMPLETE`: a repeat first verifies the same plan, limits, and embedding
  provenance, then returns the completed row without repeating work; and
- `FAILED`: a new explicit invocation with the identical persisted
  configuration returns the same row to `RUNNING`, clears its terminal error,
  and resumes at `next_query_index`.

Semantic Scholar, embedding-output, domain-invariant, or overall-timeout
failure records `FAILED`, a stable error code, concise detail, and completion
time before the error is returned. A retry is an explicit new invocation; it
cannot change the persisted plan or model provenance and never advances a
failed page's cursor.

## Related-work search failures and stop reasons

A related-work session persists each action as `RUNNING` before its external
call and then records either a bounded completed result or an action-level
failure. Candidate discoveries retain the action that produced them. A
successful DeepSeek Crawler plan is persisted immediately with its queries,
expansion choices, decision reason, model identity, and usage, so a later
Selector or provider failure does not erase that provenance. Invalid DeepSeek
Crawler/Selector output, Semantic Scholar failure, embedding failure, domain
violation, or repository error terminates the owning session as `FAILED`; no
alternate query generator, selector, source, or embedding is used.

Validated bounds terminate normally with an inspectable reason such as
`MAX_STEPS`, `MAX_QUERIES`, `MAX_QUEUE_SIZE`, `MAX_CANDIDATES`, or
`MAX_SELECTED_CANDIDATES`. Exhausting the queue is `QUEUE_EXHAUSTED`. Reaching
the overall deadline records a terminal `OVERALL_TIMEOUT` stop instead of
pretending the search was exhaustive. A terminal bounded stop may have fewer
than the requested number of selected candidates; the UI must show the reason
and actual counts.

## Comparison failures and confidence

Comparison is rejected before a model call unless the search session is
`COMPLETE`, its source version matches, the target is a selected local candidate,
and both exact versions have persisted analysis/evidence inputs. DeepSeek output
must contain all ordered comparison dimensions, a valid comparability status,
known evidence UUIDs, consistent usage, and valid relation schemas.

Unknown/cross-version evidence, duplicate relation types, or `IMPROVES_ON`
without direct comparability and bilateral evidence fails validation. Comparison,
dimensions, relations, and evidence links then either commit as one bundle or
roll back together.

An inferred relation's `confidence` is an uncalibrated model-assessed measure of
evidential support in `[0, 1]`. It is not a probability, accuracy estimate,
human-review result, or fallback for verification. `UNVERIFIED` remains visible
regardless of the numeric value.

## Transactions, run state, and reports

M1 validates external results before a short ingestion transaction; a failed
batch rolls back records and cursor advancement. M2 persists parsed text after
parsing, then analysis/claims/evidence atomically. M3 commits each backfill page,
search action/result, and comparison bundle at its own valid boundary. A
PostgreSQL advisory lock prevents duplicate logical M1/M2 runs; M3 uses stable
window/session ownership and database uniqueness constraints for its records.

After every selected M2 item is terminal, one locked transaction derives the
run state and, where allowed, publishes its deterministic report:

- `COMPLETE`: every selected item reached `EVIDENCE_EXTRACTED`; publish without
  failures.
- `PARTIAL`: at least one item completed and at least one failed; publish with
  every failed paper/version, stage, code, retryability value, and detail.
- `FAILED`: no selected item completed or a fatal run-level failure occurred;
  do not publish.

A publication database failure rolls back the state/report update and surfaces
a run-level error. A separate best-effort `FAILED` transition follows. If the
database remains unavailable, the run may remain `RUNNING`; operator
reconciliation is then required before another logical-date attempt.

## Destructive migration downgrade procedure

Downgrading `0003_m3_pasa_semantic_scholar` to
`0002_m2_structured_analysis` permanently removes historical stubs and aliases,
backfill state, corpus membership, search provenance, embeddings, comparisons,
relations, and evidence links. The migration refuses when any M3 data exists
unless `-x allow_m3_data_loss=true` is supplied.

Downgrading M2 to M1 similarly removes parsed text, analyses, claims, evidence,
reports, and structured-analysis run data and requires
`-x allow_m2_data_loss=true` when such data exists. Neither flag creates or
validates a backup.

Before either destructive downgrade:

1. Stop every writer and record the database identity, Alembic revision, UTC
   time, and affected table row counts.
2. Create a PostgreSQL custom-format export with `pg_dump --format=custom` in a
   secure location outside the repository. Do not print or commit its URI.
3. Record its SHA-256 checksum and verify the catalog with `pg_restore --list`.
4. Restore into a separate empty PostgreSQL 15+ pgvector database.
5. Run `alembic current --check-heads`, compare row counts, and exercise reads of
   the data that the requested downgrade would remove.
6. Retain the export and verification evidence under the operator backup policy.

Only after that verification, and only when destructive rollback is intended,
may the operator use the matching flag, for example:

```powershell
D:\Tools\uv\uv.exe run --frozen --python 3.13.13 alembic `
  -x allow_m3_data_loss=true downgrade 0002_m2_structured_analysis
```

Production currently has no configured database and no production M3 data.

## API readiness and presentation

Liveness reports process health. Readiness fails when PostgreSQL is unavailable
or Alembic is not at application head. FastAPI never runs migrations or
pipeline work during startup.

The API/UI expose persisted scope, provenance, verification, stop reasons,
actions, candidate origins/scores, comparability, evidence, report state, and
item failures. They must distinguish incomplete or bounded search from
exhaustive retrieval and must not present unverified relations or their numeric
support score as certainty.

## Logging

Production logs are concise structured JSON. INFO is reserved for runtime/run
start summaries, final result, and publication. WARNING records item failures
compatible with `PARTIAL`, exhausted transient operations, bounded incomplete
search, and incomplete reports. ERROR records run/session-level, publication,
database, migration, and required-dependency failures.

Logs never include secret values, authorization headers, database credentials,
full prompts, full model responses, paper text, model weights, or duplicate
copies of one exception at several layers.
