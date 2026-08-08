# Failure Policy

## Principles

Failures are explicit, attributable to one operation or pipeline stage, and
persisted when a run record exists. Retrying repeats the same operation; it never
changes the provider, parser, model, analysis scope, source, or persistence
backend. There is no implicit fallback.

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

M1 implements discovery and normalization. The current M2 structured-analysis
operation starts selected items at `SELECTED` and terminates successfully at
`EVIDENCE_EXTRACTED`; the M3 and M4 stages are not simulated or marked complete.
An item advances only after its required durable write commits. Every item
failure records the failed stage, a stable error code, retryability of that exact
operation, and concise diagnostic detail.

`FULL_TEXT` and `ABSTRACT_ONLY` are selected before a run. A failed PDF download
or GROBID parse records a full-text item failure and never changes the scope.
Abstract-only analysis never invokes GROBID.

## Structured-analysis dependency failures

The structured-analysis operation validates its DeepSeek settings before opening
a run. It also validates GROBID URL and identity settings before full-text work.
The read API does not load or require either dependency.

- DeepSeek authentication failure marks the current item and the whole analysis
  run `FAILED`, then returns the authentication error to the operator. No later
  selected item is attempted.
- DeepSeek transport exhaustion, request rejection, empty output, malformed
  JSON, schema violation, domain violation, unsupported finish reason, or
  inconsistent usage is an `ANALYZED` item failure. Invalid content is not
  repaired and is not retried as content.
- A deterministic invalid or rejected arXiv PDF is a `PDF_DOWNLOADED` item
  failure. Exhausted arXiv dependency unavailability aborts the run globally;
  no later selected paper is attempted.
- GROBID authentication, request, availability, bounded-size, content-type, XML,
  TEI, coordinate, reference, or citation-target failure is a `PARSED` item
  failure. No alternate parser or abstract fallback is called.
- Unknown passages, non-verbatim excerpts, invalid claim links, or other bundle
  ownership violations are an `EVIDENCE_EXTRACTED` item failure. No partial
  analysis or evidence bundle is persisted.

## Transactions, run state, and reports

M1 ingestion validates external results before a short database transaction. A
failed batch write rolls back both records and cursor advancement. A PostgreSQL
advisory lock prevents duplicate logical ingestion or analysis runs.

M2 persists a parsed paper in its own short transaction after parsing succeeds.
It persists each analysis, its claims, evidence, ownership links, and terminal
item transition atomically. A failure rolls back that complete bundle.

After every selected item is terminal, one locked transaction derives the run
state and, where allowed, publishes its deterministic report:

- `COMPLETE`: every selected item reached `EVIDENCE_EXTRACTED`; a report is
  published without failures.
- `PARTIAL`: at least one selected item completed and at least one failed; a
  report is published with every failed paper version, stage, stable error code,
  retryability value, and concise detail.
- `FAILED`: no selected item completed, or a fatal run-level failure occurred;
  no report is published.

A publication-transaction database failure rolls back the state/report update
and surfaces a run-level repository error. The application then makes a separate
best-effort `FAILED` transition, so it never leaves a report that claims
publication succeeded. If the database is still unavailable for that second
write, the persisted run can unavoidably remain `RUNNING`; operator
reconciliation is then required before another logical-date attempt.

## Destructive M2 downgrade procedure

Downgrading `0002_m2_structured_analysis` to `0001_m1_ingestion` permanently
drops parsed papers, sections, passages, references, citation contexts,
analyses, claims, evidence, reports, and their links. It also deletes structured-
analysis run rows because the M1 schema cannot represent them.

The migration refuses the downgrade when any structured-analysis run, parsed
paper, paper analysis, or report exists. Do not bypass that refusal until all of
these steps succeed:

1. Stop every writer and record the source database identity, Alembic revision,
   UTC time, and row counts for every M2 table plus structured-analysis runs.
2. Create a PostgreSQL custom-format export with `pg_dump --format=custom` into
   a secure location outside the repository. Do not print or store its connection
   URI in Git or logs.
3. Record the export's SHA-256 checksum and confirm its catalog is readable with
   `pg_restore --list <backup-file>`.
4. Restore the export into a separate empty PostgreSQL 15+ database with pgvector.
   Never use the source database as the restore-verification target.
5. Point the application at that isolated restore, run `alembic current
   --check-heads`, and compare the restored M2/structured-run row counts with the
   recorded source counts. Exercise a read of analysis, evidence, and report data
   before declaring the backup restorable.
6. Retain the export, checksum, row-count comparison, and restore-verification
   result according to the operator's backup policy.

Only after that verification, and only when destructive rollback is intended,
may the operator run:

```powershell
D:\Tools\uv\uv.exe run --frozen --python 3.13.13 alembic `
  -x allow_m2_data_loss=true downgrade 0001_m1_ingestion
```

The flag authorizes data loss; it does not create, validate, or restore a backup.
Production currently has no configured database and no M2 production data. This
procedure becomes mandatory before any future production downgrade containing
M2 data.

## API readiness and presentation

Liveness reports only process health. Readiness fails when PostgreSQL is
unavailable or Alembic is not at the application migration head. FastAPI never
runs migrations or analysis during startup.

The API returns persisted scope, provenance, verification state, report status,
and item failures. The React latest-run view displays a prominent `PARTIAL`
state and item-level stage, error code, retryability, and detail. It does not
present `UNVERIFIED` model output as human-verified.

## Logging

Production logs are concise structured JSON. INFO is reserved for runtime or run
start summaries, final run results, and publication. WARNING records item-level
failures compatible with `PARTIAL`, exhausted transient operations, and
incomplete reports. ERROR records run-level, publication, database, migration,
and required-dependency failures.

Logs never include secret values, authorization headers, database credentials,
full prompts, full model responses, paper text, or duplicate copies of one
exception at several layers.
