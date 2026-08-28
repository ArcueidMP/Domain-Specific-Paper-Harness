# Current Status
## Current milestone

The production Daily ingestion availability correction and off-peak scheduling
change are deployed. The first automatic 20:00/20:20/20:40 run is awaiting
acceptance on 2026-08-29. The optional public-Demo isolation code remains
implemented but is not provisioned in the production database or cloud project.

## Completed capabilities

Production remains an IAP-protected availability-first multi-topic product.
Daily discovery now uses a 168-hour overlap that covers delayed arXiv
announcements. Canonically published versions are excluded from normal
selection, zero-result days publish `COMPLETE / NO_UPDATE`, and an ambiguous
external-paper identifier rolls back only its optional enrichment transaction
without overwriting identifiers or blocking independently usable source data.

The codebase also supports an independently migrated PostgreSQL `demo` schema,
least-privilege sync/read roles, deterministic redacted snapshots, operator CLI
commands, and an independent post-CI GitHub OIDC workflow. These Demo resources
have not been bootstrapped or provisioned in production.

## Verification

Focused ingestion, historical-backfill, related-work, runtime, Terraform, and
deployment-script checks passed locally. Pull requests #7 through #10 passed the
Python, frontend, and infrastructure CI jobs before squash merge. Terraform
plans for the ingestion rollout and off-peak schedule contained only the
explicitly targeted Cloud Run Jobs and Scheduler updates, with no additions or
deletions. Terraform state was refreshed after each targeted apply.

## Deployment

The private Web/API remains revision `paper-harness-web-00012-tp7` at
`https://paper-harness-web-nxdmkbsdtq-as.a.run.app`. All three Daily Jobs use
`daily@sha256:3e7d855e54697d0ad9feebf2c5d207e3eec5af3f4a18a1a63f14bcd97185bb89`.
Enabled Scheduler times in `Asia/Kuala_Lumpur` are Broad LLM Agents at 20:00,
Brain-Computer Interfaces at 20:20, and World Models at 20:40. IAP, GROBID,
secrets, migration state, and the production `DATABASE_URL` were not changed.

No `demo` schema/roles, Demo database secrets, GitHub OIDC identity, public API,
or Cloudflare resource exists in production.

## Current blockers

The first off-peak scheduled executions have not yet occurred. Recent manual
reprocessing was rate-limited by arXiv HTTP 429, so production acceptance must
use the next bounded normal executions rather than repeated manual retries.

Demo provisioning still requires the database capability check, owner-provided
sync/read credentials, fixed Secret Manager versions, and the first snapshot.

## Next milestone

Accept the 2026-08-29 automatic Daily executions and confirm the 168-hour
windows, honest terminal publications, and off-peak start times. Then begin the
Demo rollout at database capability inspection and Phase 0/1 bootstrap rather
than skipping directly to automation or the public runtime.
