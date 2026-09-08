# Current Status
## Current milestone

The maintenance update from commit `7193f30` is deployed and accepted on
2026-09-08. Source publication is tracked in
[PR #14](https://github.com/ArcueidMP/Domain-Specific-Paper-Harness/pull/14).

## Completed capabilities

Publication deduplication is topic-scoped while compatible analysis remains
shared. Daily discovery uses arXiv OAI-PMH continuation and atomic metadata
checkpoints; incomplete harvests cannot advance the cursor. Reprocessing
preserves exact published baseline versions and supports explicit execution
resumption.

Graph, trend, and lineage computation failures retain structured diagnostics in
reports, the API, the UI, and bounded warning logs. Safe source metadata remains
publishable; Demo snapshots redact diagnostic details.

DeepSeek remains `deepseek-v4-flash`; the temporary V4.1 beta was not adopted.
The compatibility evidence is in `docs/MODEL_COMPATIBILITY.md`.

## Verification

Local verification passed 983 non-live Python tests, subsequent 41 focused
recovery checks, 35 frontend tests, two Playwright flows, Ruff, Pyright, and
OpenAPI/TypeScript drift and hygiene checks. GitHub's required Python, frontend,
and infrastructure checks passed for the source revision.

Both production images built and pushed successfully. The Daily image passed
network-disabled CLI and SPECTER2 loading/768-dimensional embedding checks.
A production logical backup was restored into isolated PostgreSQL and upgraded
from 0006 to 0008. Its 1,436 papers, 1,542 versions, 675 analyses, 14,954 evidence
records, and 176 reports matched the source and remained intact after migration.

All three new REPROCESS executions for 2026-09-08 completed successfully in Cloud
Run. Their OAI checkpoints exhausted 353/20/174 records with no pending IDs or
tokens. The original 10/1/10 exact-version baselines and all three shared
watermarks were preserved. Authenticated API readback matched the new reports.

## Deployment

The [private Web/API](https://paper-harness-web-nxdmkbsdtq-as.a.run.app) is at
revision `paper-harness-web-00013-ws8`; production migration is
`0008_enrichment_failures`. Web/API and Migration use image digest
`sha256:b4e66af2cb24600d8c7dd6a365d6f999f0fba665275d0bc026ff852a1e67fffd`;
all three Daily Jobs use
`sha256:f279eef2bb06d8de04115505338068bb03533ba2b1039ceaff63b0db742576c4`.
Terraform updated existing resources only.

| Topic | Publication | Metadata cards | Completed source analyses | Core failures |
| --- | --- | --- | --- | --- |
| Broad LLM Agents | COMPLETE | 10 | 10 | None |
| Brain-Computer Interfaces | PARTIAL | 1 | 0 | PDF HTTP 404 |
| World Models | PARTIAL | 10 | 8 | PDF size bound; model output validation |

Graph, trend, and lineage artifacts were verified where usable inputs exist.
The original schedules are enabled at 20:00/20:20/20:40 in
`Asia/Kuala_Lumpur`. Authenticated readiness returned HTTP 200 with ready database
and current migrations; anonymous readiness returned HTTP 302. IAP, the exact
two-account allowlist, and private Daily-only GROBID invocation remain intact.

Demo schema/roles, Demo secrets, GitHub OIDC, and public Demo resources remain
unprovisioned. Production secrets and approved model settings were unchanged.

## Current blockers

No deployment blocker remains. Individual unavailable PDFs and invalid model
outputs are explicitly retained in PARTIAL reports; they are not hidden as
successful analyses.

## Next milestone

Maintain the scheduled v0.1.x product. Public Demo provisioning remains a separate
later rollout.
