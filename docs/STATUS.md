# Current Status
## Current milestone

The private production MVP is deployed. The current milestone is the verified
Apache-2.0 v0.1.0 public source release, with no production runtime change and
no public Demo provisioning.

## Completed capabilities

Production is an IAP-protected, availability-first, multi-topic product. Daily
discovery uses a 168-hour overlap for delayed arXiv announcements, excludes
canonically published versions from normal selection, and publishes terminal
`COMPLETE`, honest `PARTIAL`, or transparent `NO_UPDATE` results without making
optional enrichment a publication blocker.

The codebase supports an independently migrated PostgreSQL `demo` schema,
least-privilege sync/read roles, deterministic redacted snapshots, operator CLI
commands, and an independent post-CI GitHub OIDC workflow. These Demo resources
are implemented but are not provisioned in production.

The private Web/API IAP binding supports the owner plus an explicit set of
additional approved Google accounts. Production identities stay in an ignored
Terraform variables file and are not committed to the public repository.

## Verification

The public-source release passed tracked-file hygiene, Ruff, Pyright, generated
OpenAPI and TypeScript contract checks, dependency-license reviews, 931 Python
tests with four explicit live tests skipped, 34 frontend unit tests, two
Playwright flows, a clean Alembic upgrade and revision check, Terraform format
and validation, Docker Compose validation, and all three runtime image builds.
The documented Windows keyless Quick Start also migrated a clean database and
returned successful Web, liveness, readiness, and topic responses.
Focused IAP/Terraform tests passed, and the production IAM-only plan applied as
one in-place binding update with no additions, deletions, or Cloud Run revision.

## Deployment

The private Web/API remains protected by Google Cloud IAP. Three topic-specific
Daily Jobs and private GROBID remain deployed. The public source release did not
change production secrets, database migration state, Scheduler, or any deployed
runtime.

The IAP allowlist is Terraform-managed and currently contains the owner and one
additional approved collaborator. Their identities are intentionally omitted
from public documentation.

No Demo schema or roles, Demo database secrets, GitHub OIDC identity, public
Demo API, or Cloudflare resource are provisioned in production.

## Current blockers

There is no known implementation, verification, production, or data blocker for
the public source distribution. Demo provisioning remains a separate future
rollout.

## Next milestone

Publish and maintain the v0.1.x source release. The Demo database bootstrap and
public runtime remain a separate later milestone.
