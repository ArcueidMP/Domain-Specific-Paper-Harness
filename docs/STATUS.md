# Current Status
## Current milestone

The private production MVP is deployed. The current maintenance revision fixes
topic isolation, discovery completeness, and optional computation diagnostics.
These source changes are verified locally; they have not been pushed or deployed.

## Completed capabilities

Published-version deduplication now requires the active topic, while compatible
paper analyses remain reusable across topics.

Daily discovery uses official arXiv OAI-PMH identifiers and arxiv.py metadata.
Atomic checkpoints preserve pending batches and expired-token recovery; only
complete harvests advance the shared cursor. Historical reprocessing preserves
its local exact-version baseline, and an explicit execution UUID resumes an
interrupted reprocessing revision.

Graph, trend, and lineage computation failures persist with stage, code, scope,
and concise diagnostic. They remain visible in reports, the API, the UI, and
bounded warning logs without advancing failed processing stages or blocking
usable source metadata. Demo snapshots redact their diagnostic details.

DeepSeek remains `deepseek-v4-flash`. The September 8 compatibility assessment
rejected the unavailable stable V4.1 identifier and the short-lived beta as an
unattended production default; see `docs/MODEL_COMPATIBILITY.md`.

The codebase supports an independently migrated PostgreSQL `demo` schema,
least-privilege sync/read roles, deterministic redacted snapshots, operator CLI
commands, and an independent post-CI GitHub OIDC workflow. These Demo resources
are implemented but are not provisioned in production.

The private Web/API IAP binding supports the owner plus an explicit set of
additional approved Google accounts. Production identities stay in an ignored
Terraform variables file and are not committed to the public repository.

## Verification

The integrated non-live Python regression passed 983 tests with four live tests
deselected. Subsequent focused recovery checks passed 41 tests, including the
additional changed-topic resume failure case. PostgreSQL verification used a
dedicated disposable local instance and covered clean migration, populated
0006-to-0008 upgrades, migration/model parity, exact readiness, topic isolation,
durable discovery recovery, diagnostic readback, rollback, and Demo isolation.

Frontend lint, typecheck, production build, all 35 unit tests, and both
Playwright flows passed. Local Vitest used its forks pool because the workstation
threads pool could not start. Ruff, Pyright, generated OpenAPI/TypeScript drift
checks, and repository hygiene were checked for the maintenance changes.

## Deployment

The private Web/API remains protected by Google Cloud IAP. Three topic-specific
Daily Jobs and private GROBID remain deployed. This maintenance work did not
change production secrets, Scheduler, database migration state, or runtime
images. Migrations `0007_ingestion_progress` and `0008_enrichment_failures` are
verified locally and await an explicit production rollout with the matching code.

The IAP allowlist is Terraform-managed and currently contains the owner and one
additional approved collaborator. Their identities are intentionally omitted
from public documentation.

No Demo schema or roles, Demo database secrets, GitHub OIDC identity, public
Demo API, or Cloudflare resource are provisioned in production.

## Current blockers

There is no blocker for the verified local maintenance changes. Production
rollout and Demo provisioning are outside this task.

## Next milestone

Review and explicitly roll out the maintenance revision with its additive
migrations. The Demo database bootstrap and public runtime remain a separate
later milestone.
