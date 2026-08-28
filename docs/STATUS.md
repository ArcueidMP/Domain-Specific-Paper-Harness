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

## Verification

The public-source release passed tracked-file hygiene, Ruff, Pyright, generated
OpenAPI and TypeScript contract checks, dependency-license reviews, 931 Python
tests with four explicit live tests skipped, 34 frontend unit tests, two
Playwright flows, a clean Alembic upgrade and revision check, Terraform format
and validation, Docker Compose validation, and all three runtime image builds.
The documented Windows keyless Quick Start also migrated a clean database and
returned successful Web, liveness, readiness, and topic responses.

## Deployment

The private Web/API remains protected by Google Cloud IAP. Three topic-specific
Daily Jobs and private GROBID remain deployed. Preparing the source release does
not change IAP, production secrets, database migration state, Scheduler, or any
deployed runtime.

No Demo schema or roles, Demo database secrets, GitHub OIDC identity, public
Demo API, or Cloudflare resource are provisioned in production.

## Current blockers

There is no known implementation, verification, production, or data blocker for
the public source distribution. Demo provisioning remains a separate future
rollout.

## Next milestone

Publish and maintain the v0.1.x source release. The Demo database bootstrap and
public runtime remain a separate later milestone.
