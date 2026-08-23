# Current Status
## Current milestone

The optional public-Demo data-isolation foundation is implemented and locally
verified. It is not provisioned in the production database or cloud project.

## Completed capabilities

Production remains an IAP-protected availability-first multi-topic product.
Source-analysis failures publish transparent metadata cards; optional enrichment
does not block Daily publication; zero-result days publish `COMPLETE / NO_UPDATE`.

The codebase now supports an independently migrated PostgreSQL `demo` schema,
least-privilege sync/read roles, a deterministic canonical-publication snapshot,
free-form diagnostic redaction, explicit table classification, atomic rollback,
operator CLI commands, and an independent post-CI GitHub OIDC workflow. Raw
parsed content, embeddings, cursors, and backfill bookkeeping are excluded.

## Verification

Focused verification passed: 70 Demo/schema/CLI/automation unit tests and 71
existing-plus-Demo PostgreSQL integration tests. Ruff, Ruff format, Pyright,
repository hygiene, patch whitespace, Terraform format, and Terraform validation
also passed. Dual-schema clean Alembic migration, role isolation, canonical
revision selection, idempotent synchronization, API reads, redaction, and
rollback on revision mismatch were exercised against a disposable pgvector
database. The prior production baseline canonical passed before this change;
`scripts/verify.ps1` was not rerun because this optional boundary has not been
provisioned or accepted in production.

## Deployment

The private Web/API remains revision `paper-harness-web-00012-tp7` at
`https://paper-harness-web-nxdmkbsdtq-as.a.run.app`. Existing Daily Jobs,
Schedulers, IAP, GROBID, secrets, migration state, and production `DATABASE_URL`
were not changed. No `demo` schema/roles, OIDC identity, Demo secret value,
public API, or Cloudflare resource has been created.

## Current blockers

Demo provisioning requires a read-only capability check proving that the
managed PostgreSQL owner can create a schema, login roles, and column grants;
owner/sync/read credentials and fixed Secret Manager versions are also external
inputs. No code blocker remains, and no fallback weakens these boundaries.

## Next milestone

Inspect the production database capabilities, run the idempotent Demo bootstrap
and first canonical snapshot, apply the optional OIDC/secret-container Terraform
resources, then plan the separate Cloudflare Pages and public read-API rollout.
