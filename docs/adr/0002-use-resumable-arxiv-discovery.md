# ADR 0002: Use resumable arXiv identifier discovery

Status: Accepted

## Context

The Atom adapter selected a bounded prefix of a topic query, filtered it by
paper update time, and returned only a tuple. Ingestion could not distinguish
an exhausted window from a truncated prefix and advanced the same watermark
for both. Increasing overlap or the result cap does not establish coverage.

The [official Atom manual](https://info.arxiv.org/help/api/user-manual.html)
documents submission-date filtering and update-time sorting, but does not
provide the stable update-window continuation needed here. Relying on
undocumented date filtering or page order would preserve the original gap.

The [official OAI-PMH interface](https://info.arxiv.org/help/oa/index.html)
supports metadata datestamps, category sets, and continuation tokens. Its
datestamps represent metadata changes or availability; they are distinct from
paper submission/version timestamps. Tokens expire daily.

## Decision

Daily discovery harvests OAI identifiers one UTC day and category at a time.
arxiv.py continues to retrieve metadata and explicit arXiv versions for those
identifiers. Local topic filtering owns inclusion and exclusion decisions.
This is one arXiv-only discovery path, with no fallback provider.

An ingestion-owned PostgreSQL checkpoint stores the fixed window's position,
continuation token, and pending metadata IDs. Accepted papers and their next
checkpoint commit together. A metadata batch is bounded by the topic's
`max_results` and the existing exact-ID lookup bound. That cap never truncates
the completed window's paper set or replaces the separate analysis limit.

Each invocation has a 900-second discovery budget and a 100-page budget, with
bounded individual requests. A budget failure preserves progress for a retry
of the same logical date. An expired token replays only the current day/category
with idempotent writes. The shared cursor advances only after all continuations
and pending identities have been consumed. Normal topic selection deduplicates
publication only within the active topic.

## Consequences

The persistence contract adds an explicit ingestion checkpoint table. Empty
OAI pages and oversized identifier pages remain bounded operations; malformed
responses cannot be interpreted as successful exhaustion. Discovery can now
save more records than a metadata batch contains without silently losing the
remaining records.

OAI exposes current metadata, not arbitrary historical snapshots. Same-date
reprocessing retains its declared window and must not relabel a later version
as an earlier one. Its previously published baseline is seeded from the local
topic-owned exact versions, so a newer remote version cannot erase the original
cards. An explicit resume execution UUID continues an interrupted REPROCESS
revision; a fresh `--reprocess` still creates a new revision. The existing
overlap remains an explicit replay policy.
Incomplete harvests are visible and resumable rather than successful NO_UPDATE
publications. Checkpoints are operational data and are excluded from Demo.
