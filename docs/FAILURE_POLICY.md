# Failure Policy

## Principles

Failures are explicit, attributable to a stable operation or pipeline stage, and
persisted when a run record exists. Retry repeats the same operation; it never
changes provider, parser, model, analysis mode, or persistence backend.

Configuration, authentication, domain-invariant, migration, database, external
schema, Pydantic, and non-transient HTTP failures fail immediately. Timeouts and
HTTP 429, 500, 502, and 503 responses may use centralized bounded retry with
`Retry-After` support. HTTP 400, 401, 403, and 422 responses are not retried.

## Item state

The complete pipeline state order is:

```text
DISCOVERED -> NORMALIZED -> ENRICHED -> RELEVANCE_SCORED -> SELECTED
-> PDF_DOWNLOADED -> PARSED -> ANALYZED -> EVIDENCE_EXTRACTED
-> PRIOR_WORK_RETRIEVED -> COMPARED -> GRAPH_UPDATED -> PUBLISHED
```

An item advances only after the required stage has succeeded and its durable
write has committed. Item failures record the failed stage, a stable error code,
whether the exact operation is retryable, and concise diagnostic detail. A
parser failure never advances to abstract-only analysis.

## Run and publication state

- `COMPLETE` means every selected priority paper completed every required stage.
- `PARTIAL` means at least one selected paper completed and one or more selected
  items failed. Publication is allowed only with a prominent partial state and
  the missing papers, stages, and error codes.
- `FAILED` covers invalid configuration, unavailable database, incompatible
  migrations, global arXiv failure, a missing required secret, zero completed
  selected papers, or publication transaction failure.

M1 ingestion is atomic per persistence batch. External requests complete and are
validated before the short database transaction. A failed write rolls back the
batch and does not advance its cursor. A PostgreSQL advisory lock prevents two
logical Daily runs from executing concurrently.

## Dependency behavior

Readiness fails when PostgreSQL is unavailable or the database revision is not at
the application migration head. Liveness does not claim database readiness.
Migrations are never run during FastAPI startup.

Daily arXiv discovery fails the run after an exhausted global arXiv dependency
failure. Empty, malformed, schema-invalid, or domain-invalid external or model
output is a failure, not an empty success. Required production secrets are
validated by the operation that needs them; the read API does not require the
DeepSeek key.

## Logging

Production logs are concise structured JSON. INFO is reserved for runtime or run
start summaries, final run results, and publication. WARNING records item-level
failures compatible with `PARTIAL`, exhausted transient operations, and incomplete
reports. ERROR records run-level, publication, database, migration, and required
dependency failures.

Logs never include secret values, authorization headers, database credentials,
full prompts, full model responses, paper text, or duplicate copies of the same
exception at multiple layers.
