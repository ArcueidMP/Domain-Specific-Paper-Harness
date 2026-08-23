# ADR 0001: Isolate Public Demo Data in a PostgreSQL Schema

## Status

Accepted for implementation; not yet provisioned in production.

## Context

The private product must remain behind IAP while a future public interactive
Demo exposes a broad, read-only view of canonical publications. A second managed
database would add a recurring resource, while direct public access to the
authoritative production schema would violate the required isolation boundary.

## Decision

Use the existing PostgreSQL instance with an independently migrated `demo`
schema. A non-inheriting synchronization role receives explicit column-level
reads from `public` and owns `demo`; a separate non-inheriting runtime role has
Demo `SELECT` only. Production runtimes continue to use their unchanged
credential and default `public` schema.

Populate `demo` after successful main-branch CI through a deterministic,
server-side, transactional snapshot of all canonical publication history and
current periodic reports. Exclude raw parsed content, embeddings, ingestion
cursors, and historical-backfill bookkeeping. Redact free-form diagnostics but
retain stable errors, provenance, and structured usage metrics. Synchronization
failure preserves the prior snapshot and cannot block production.

## Consequences

- No additional managed database or fixed database charge is required.
- Production and Demo share database availability and storage capacity.
- Schema/role permission tests are mandatory because isolation is logical, not
  physical.
- Every new persistence table must be explicitly classified as copied or
  excluded before verification passes.
- Cloudflare hosting, CORS, and a public Demo API remain separate follow-up work;
  production IAP is unchanged.
