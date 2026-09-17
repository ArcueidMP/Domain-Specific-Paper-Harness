# Current Status
## Current milestone

The September 17 report-section persistence fix is deployed to all three Daily
Jobs. Recovery execution `paper-harness-daily-gk69k` completed successfully;
Broad LLM Agents now has a verified PARTIAL report with eight completed papers
and two retained analysis failures. World Models also published PARTIAL; BCI
remains blocked on OAI transport. Ubuntu migration and storage cleanup remain
deferred.

## Completed capabilities

Publication deduplication is topic-scoped while compatible analysis remains
shared. Daily discovery uses arXiv OAI-PMH continuation and atomic metadata
checkpoints; incomplete harvests cannot advance the cursor. Reprocessing
preserves exact published baseline versions and supports explicit execution
resumption.

Graph, trend, and lineage computation failures retain structured diagnostics in
reports, the API, the UI, and bounded warning logs. Safe source metadata remains
publishable; Demo snapshots redact diagnostic details.

Historical identifier checks use indexed Unicode-normalized lookup keys and
return at most one conflict. Original identifier spellings are preserved;
normalized uniqueness protects concurrent writes. Metadata-only refreshes skip
dependent rekey reads, while actual identity promotion and alias merging retain
atomic rekeying.

The arXiv correction serializes complete OAI-PMH, Atom, and PDF requests
across topic runtimes through the existing PostgreSQL database. Requests and
retries respect a three-second gap and bounded acquisition, network, and operation
timeouts. Duplicate normalized provider authors preserve their first spelling
and order; repository and immutable version constraints remain strict.

Production model configuration selects DeepSeek V4.1 Flash as `deepseek-flash`.
All five operation schemas retain validation, bounded retries, and explicit
model provenance. Existing research records are not rewritten.

## Verification

The new PostgreSQL regression reproduced the production constraint failure
before the fix in three partial-outline cases. All five outline variants now
publish and round-trip through the daily and weekly report paths. All 34 product
repository integration tests and 32 report unit tests passed, together with
Ruff, full Pyright, repository hygiene, and diff checks. PostgreSQL constraints
and migrations remain unchanged. No canonical milestone rerun was needed.

The production Daily image built successfully, contains the corrected section
mapping, and passed CLI/runtime checks with networking disabled. It runs CPython
3.13.13 and `deepseek-flash` and loads the pinned 768-dimensional SPECTER2 artifact
at revision `3447645e1def9117997203454fa4495937bfbd83`. The reviewed Terraform plan
changed only the images of the three existing Daily Jobs; apply completed and
the final plan found no drift.

September 17 Cloud Run logs and read-only database queries agree on all three
topic results. PostgreSQL remains reachable at version 17.6, migration
`0009_identifier_lookup`, with `default_transaction_read_only=off`. Broad LLM
Agents ingested 419 papers and retained eight successful core analyses plus
two `LLM_OUTPUT_INVALID` items. Its publication failure marked the eight ready
items failed at `PUBLISHED`; it did not erase their source analyses or evidence.

Supabase's original PostgreSQL error at 20:28:38 Asia/Singapore is SQLSTATE
`23514`: an inserted `report_sections` row had `kind=COMPARISONS, position=1`,
violating `ck_report_sections_canonical_order`, which requires position 2.
The domain permits a canonically ordered subset of sections, but
`_insert_normalized_report` previously used `enumerate(report.sections)` and
compressed positions when an earlier section was absent. The constraint definition
was verified directly. Persistence now assigns each section its canonical
enum position, preserving omitted sections and the strict database check.
The recovery execution completed the original pipeline
`c34abef7-1a5e-59cf-bae9-02c350fb8b8b` and publication run
`8ff6ffaa-94b0-464d-a353-78f6c4d43fe7` as PARTIAL at 23:19:53 Asia/Singapore.
The eight successful source analyses retain their original 20:19 completion
time. Cloud logs record zero arXiv and zero GROBID operations during recovery.
The report contains ten paper cards, 118 evidence links, three trend links, and
eight lineage highlights. Direct database reads, the authenticated Chrome report
view, and HTTP 200 from the private report API agree on eight completed and two
failed items. Both unavailable analyses remain visibly marked
`LLM_OUTPUT_INVALID`; no unavailable analysis is presented as evidence.

World Models ingested 139 papers and published four successful analyses with six
explicit `LLM_OUTPUT_INVALID` failures. Its historical backfill also recorded an
integrity failure, while usable inputs supported partial publication. BCI saved
ten ingestion records before `bounded arXiv OAI transport failed with
ConnectionError`. The latest DAILY reports are September 17 for Broad LLM Agents
and World Models, and September 11 for BCI.

The additional IAP accessor passed Terraform format/validation and a reviewed
plan containing one IAM binding update. Apply completed with zero resources
added or destroyed; a fresh Google IAP policy read confirmed the added account
and both existing accessors. The production tfvars allowlist is synchronized
outside Git. Cloud Run IAP remains enabled with the IAP service identity as
the only service invoker.

The September 14 rollout passed 301 combined focused unit/contract tests for
arXiv transport, response
normalization, runtime coordination, model configuration, provenance, cost
estimates, and Terraform topology. Seven PostgreSQL integration tests passed for
cross-runtime locking, timeout/release behavior, pool capacity, and author
version persistence. Ruff, full Pyright, repository hygiene, Terraform format
and validation, and diff checks passed. No canonical milestone rerun was needed.

Five bounded synthetic calls through the normal DeepSeek adapter passed:
analysis, crawler planning, selection, comparison, and report generation.
All returned `deepseek-flash` and validated in one call each (5,094 total tokens).
This establishes transport/schema compatibility, not full-paper scientific quality;
no production analysis was persisted.

The production database is reachable, remains at `0009_identifier_lookup`, and
reports `default_transaction_read_only=off`. The new request gate also acquired
and released its session lock against production without executing a pipeline
or changing persisted data. At 21:37 Asia/Singapore on September
14, current Cloud Run request logs recorded HTTP 200 for topics, latest report,
report history, paper lists, graph, and trends. Post-deployment database readiness
also passed, and anonymous readiness returned HTTP 302 to the authentication
boundary. Private runtime checks confirmed Ready resources, no public principal,
and the configured owner in the IAP allowlist. Browser rendering was not retested;
current evidence comes from database probes, service logs, and configuration checks.

The Daily production image built successfully from the frozen dependencies.
With networking disabled, it passed the CLI check and loaded CPython 3.13.13,
`deepseek-flash`, and the pinned 768-dimensional SPECTER2 artifact at revision
`3447645e1def9117997203454fa4495937bfbd83`. The owner's uploaded image matched the
verified local manifest. No Web/API source or schema change was required.

The September 9 identifier migration retained all 9,014 raw identifier records
and passed 97 focused PostgreSQL integration tests. The earlier storage audit
measured 492,219,539 application bytes plus 15,273,634 system-template bytes
(507,493,173 total). It identified a redundant 9.49 MB trend ownership index,
about 22.97 MB of losslessly encodable coordinate keys, and 9.48 MB of citation
excerpts duplicating retained passages. These remain deferred candidates;
no research data, indexes, or system templates were removed.

## Deployment

The [private Web/API](https://paper-harness-web-nxdmkbsdtq-as.a.run.app) is at
revision `paper-harness-web-00014-vp8`; production migration is
`0009_identifier_lookup`. Web/API and Migration use image digest
`sha256:831808eb58131e32ff18ee4e110ef072d2e85f8bad7b15690b2c3e72e47f52e8`.
The September 9 migration execution `paper-harness-migration-b2rtn` succeeded.
No production migration is required for this recovery change.

All three Daily Jobs use
`sha256:b3f403edf91a953e122354a6fa5e52b952bbacaa12600e27e79e62bca93e01d7`
and `LLM_MODEL=deepseek-flash`; all three report Ready.
Their original schedules are enabled at 20:00/20:20/20:40 in `Asia/Kuala_Lumpur`.
September 17 scheduled results (Asia/Kuala_Lumpur):

- `paper-harness-daily-ch8v6`: initial publication failed at 20:28:38 on the
  section-order constraint. Recovery `paper-harness-daily-gk69k` resumed the same
  logical-date pipeline and published PARTIAL at 23:19:53 (eight completed, two
  failed); the Cloud Run execution completed successfully at 23:19:58.
- `paper-harness-daily-brain-computer-interfaces-g8rvd`: OAI transport failed at
  20:25:15; its incomplete discovery cursor was retained.
- `paper-harness-daily-world-models-vqqfj`: PARTIAL publication completed at
  21:05:10 with four completed and six failed items.

Private GROBID remains unchanged.

The deployed Daily image tag is `report-sections-20260917`. Registry identity
matches the verified local manifest. Terraform changed only the image of the
three existing Daily Jobs: zero resources added or destroyed, all three Ready.
Web/API, Migration, GROBID, secrets, schedules, and IAP retain their configuration.

Demo schema/roles, Demo secrets, GitHub OIDC, and public Demo resources remain
unprovisioned. Production secret versions are unchanged. The IAP allowlist now
contains the owner and two additional approved Google accounts.

## Current blockers

The section-order publication blocker is resolved and production acceptance
passed. No migration was needed.

BCI remains blocked on arXiv OAI transport. The September 15 Atom 429 failure is
historical; two topics completed ingestion on September 17. Item-level DeepSeek
schema/domain failures remain explicit in persisted analysis results and require
separate investigation if their frequency is unacceptable. Historical-backfill
integrity failures for Broad LLM Agents and World Models remain separate
diagnostic follow-ups.

No current Supabase connectivity, read-only, or authentication blocker was found.

## Next milestone

Verify BCI discovery recovery and investigate the remaining typed DeepSeek
item failures and historical-backfill integrity diagnostics. Ubuntu migration,
storage cleanup, and public Demo remain later work.
