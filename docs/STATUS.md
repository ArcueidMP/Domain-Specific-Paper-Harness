# Current Status
## Current milestone

Egress maintenance is deployed on 2026-09-09. The existing multi-topic
production baseline remains implemented; this change repairs historical
identifier lookup and unnecessary dependent-record reads.

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
atomic rekeying. DeepSeek remains `deepseek-v4-flash`.

## Verification

97 focused PostgreSQL integration tests passed, including query result bounds,
Unicode identity handling, concurrent conflict rollback, migrations, API,
publication, schema isolation, and Demo snapshots. Ruff, full Pyright, repository
hygiene, and diff checks passed. Both production images built and pushed; the
Daily image loaded its pinned SPECTER2 artifact with networking disabled.

A scoped production backup was restored into isolated PostgreSQL and upgraded
from 0008 to 0009. All 9,014 raw identifier records matched the source and remained
unchanged after both local and production migration. The production Daily image
refreshed three existing production metadata records in rolled-back transactions:
all conflict queries returned zero rows, dependent SELECT counts were zero, and
metadata/identifiers remained identical.

The new Web/API returned HTTP 200 for authenticated readiness, topics, latest
Daily report, and report history. A fresh browser reload displayed the existing
2026-09-08 report. Anonymous readiness returned HTTP 302; IAP and its two-account
allowlist remain intact. No new Daily or REPROCESS execution was started.

## Deployment

The [private Web/API](https://paper-harness-web-nxdmkbsdtq-as.a.run.app) is at
revision `paper-harness-web-00014-vp8`; production migration is
`0009_identifier_lookup`. Web/API and Migration use image digest
`sha256:831808eb58131e32ff18ee4e110ef072d2e85f8bad7b15690b2c3e72e47f52e8`.
Migration execution `paper-harness-migration-b2rtn` succeeded. Terraform updated
existing resources only.
The final Terraform plan reported no infrastructure drift.

| Topic | Publication | Metadata cards | Completed source analyses | Core failures |
| --- | --- | --- | --- | --- |
| Broad LLM Agents | COMPLETE | 10 | 10 | None |
| Brain-Computer Interfaces | PARTIAL | 1 | 0 | PDF HTTP 404 |
| World Models | PARTIAL | 10 | 8 | PDF size bound; model output validation |

All three Daily Jobs use
`sha256:4c7458010f308c528bcad9adfc7eb4ee76a983a0f922cd6d5408b536da750bd7`.
Their original schedules are enabled at 20:00/20:20/20:40 in `Asia/Kuala_Lumpur`.
Their latest executions remain the preceding 2026-09-08 runs; no manual Daily
execution was added. Private GROBID remains unchanged.

Demo schema/roles, Demo secrets, GitHub OIDC, and public Demo resources remain
unprovisioned. Production secrets and approved model settings were unchanged.

## Current blockers

Supabase reports an egress restriction: 17.40 GB used against 5 GB during the
2026-08-10 to 2026-09-10 billing cycle. The provider says restrictions lift after
the next cycle begins, with possible delay. Existing authenticated product reads
worked during verification; provider-wide recovery is not established.

The dashboard also reports database size at 101% (0.506/0.5 GB); the direct
PostgreSQL measurement after migration was 492,219,539 bytes. The readings have
different observation times. Stored paper text, evidence, indexes, and report
history require capacity regardless of billing-cycle reset. No research data
was deleted and no paid plan was activated.

## Next milestone

Let the normal scheduled runs publish tonight or after the provider cycle resets;
the owner requested no manual reprocessing. Review ongoing database capacity
without deleting research records or activating a paid plan implicitly.
Public Demo remains a separate later rollout.
