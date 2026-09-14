# Current Status
## Current milestone

Recovery maintenance is deployed on 2026-09-14 following the owner's Supabase
upgrade. All three Daily Jobs are ready with coordinated arXiv requests and
DeepSeek V4.1 Flash. Ubuntu migration and storage cleanup are deferred. The
owner requested normal scheduled operation without a manual Daily/REPROCESS run.

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

301 combined focused unit/contract tests passed for arXiv transport, response
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
`sha256:79c0008f915cab5e22696e07df3ff4f4b33fc375270a738e9334a975851613f5`
and `LLM_MODEL=deepseek-flash`; all three report Ready.
Their original schedules are enabled at 20:00/20:20/20:40 in `Asia/Kuala_Lumpur`.
The latest September 14 scheduled executions failed at the arXiv dependency:
Broad LLM Agents exhausted HTTP 429 retries; Brain-Computer Interfaces and World
Models exhausted read timeouts. These historical failures do not establish a
failure of the subsequently upgraded Supabase plan. No manual execution was
added. The next scheduled invocations are September 15 at the same local times.
Private GROBID remains unchanged.

The deployed Daily image tag is `recovery-20260914-flash-arxiv-1`. Terraform
changed only the image and `LLM_MODEL` of the three existing Daily Jobs:
zero resources added or destroyed. The final plan reported no infrastructure
drift. Web/API, Migration, and GROBID retain their current images; the existing
read API accepts the new model identity.

Demo schema/roles, Demo secrets, GitHub OIDC, and public Demo resources remain
unprovisioned. Production secret versions and the IAP allowlist are unchanged.

## Current blockers

No infrastructure or credential blocker remains. The owner reports that Supabase
has been upgraded, and current database/API reads succeed; the old free-plan
quota notice is no longer treated as the active recovery blocker.

A bounded official arXiv metadata probe still returned HTTP 429 on September 14.
The pacing and timeout corrections do not guarantee upstream recovery. No new
production Daily run has been executed to establish end-to-end acceptance of
the deployed changes; the owner requested waiting for the next normal schedules.

## Next milestone

Review the results of the September 15 normal 20:00/20:20/20:40 schedules when
available, including arXiv availability and publication status. Ubuntu migration,
storage cleanup, and public Demo remain separate later work.
