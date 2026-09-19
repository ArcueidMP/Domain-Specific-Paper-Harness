# Current Status
## Current milestone

Graph and Trends usability corrections were deployed and accepted on September
19. The owner pushed the verified `graph-trends-20260918` Web/API image;
Terraform updated the existing private service and the final plan found no drift.

## Completed capabilities

The deployed M1-M5 baseline includes private multi-topic discovery, grounded
analysis, historical comparison, graph/trend/lineage/report publication, and
resumable topic-scoped execution. The September 17 report-section persistence
fix remains the accepted production baseline.

The deployed correction adds paginated search across a topic's published graph nodes and
canonical arXiv IDs, connected endpoint sampling, separate selection and
neighborhood navigation, zoom/focus controls, and bounded zoom-aware labels.
Trends now use individual rows, two-line labels, full labels on hover or focus,
and a shared scale with explicit current/preceding counts. No dependencies,
database schema, research identities, or Daily pipeline behavior were changed.

## Verification

Twenty backend API/OpenAPI tests, thirty-five PostgreSQL integration tests,
nine focused frontend unit tests, and four Chromium scenarios passed. Browser
coverage includes search outside the overview, canvas preservation on selection,
a sixty-node graph, existing
report/graph/trend/lineage/failure flows, and ten long trend labels across all
three windows at desktop and mobile widths. Rendered screenshots were reviewed.
Ruff checks/format checks, full Pyright, frontend lint/typecheck/production
build, repository hygiene, and diff whitespace checks passed.

PostgreSQL checks cover published-only search, literal wildcard handling,
pagination, connected endpoint bounds, existing graph publication behavior,
and two populated topics with identical paper titles. Clean Alembic upgrades
to `0009_identifier_lookup` passed in disposable local databases.

The built Linux/amd64 image passed an offline Python 3.13.13, non-root, static
asset, and OpenAPI check. With disposable PostgreSQL fixtures, its live/readiness,
topics, graph, node search, trends, and SPA routes returned HTTP 200. Chrome
confirmed the image's node search, neighborhood, and Trends views. No canonical
milestone verification was rerun.

Production Chrome acceptance found 60 of 2,581 nodes with 33 of 2,557 relations
in the overview, replacing the previous disconnected prefix. ALFWorld was absent
from that overview but found by topic-wide search (93 matches); selecting its
exact node loaded three nodes and two relations. Focus and return-to-overview
controls worked. Each 7/30/90-day Trends window displayed ten rows without label
overlap: labels occupied at most 36 pixels, with 84-pixel minimum rows. The
longest tested production label contained 489 characters.

The new revision is Ready with IAP enabled. Private runtime checks passed;
anonymous readiness redirects to Google authentication with HTTP 302. Cloud Run
request logs confirm HTTP 200 for authenticated readiness, graph, node search,
and Trends requests on the new revision. Terraform applied zero additions, one
in-place service update, and zero deletions; a fresh plan reported no changes.

## Deployment

The deployed Web/API tag is `graph-trends-20260918`, with manifest
`sha256:08d81f68e6876c4d37ad72211c18ba0dfd246142d3a6587885cffc403f976cba`.
The registry identity matches the locally verified image. This correction
required no database migration or Daily/GROBID image rollout.

The accepted [private Web/API](https://paper-harness-web-nxdmkbsdtq-as.a.run.app)
revision is `paper-harness-web-00015-gkq`. Migration retains image
`sha256:831808eb58131e32ff18ee4e110ef072d2e85f8bad7b15690b2c3e72e47f52e8`.
The production database migration remains `0009_identifier_lookup`.

All three Daily Jobs retain the September 17 image
`sha256:b3f403edf91a953e122354a6fa5e52b952bbacaa12600e27e79e62bca93e01d7`
and `deepseek-flash`. Their schedules remain 20:00/20:20/20:40 in
`Asia/Kuala_Lumpur`. Private GROBID and the IAP allowlist are unchanged.
The last accepted September 17 reports were PARTIAL for Broad LLM Agents
(eight completed, two failed) and World Models (four completed, six failed).
Public Demo infrastructure remains unprovisioned.

## Current blockers

No blocker remains for the Graph/Trends correction. The image push, deployment,
private-access checks, database readiness, and browser acceptance are complete.

Previously recorded production follow-ups remain: BCI OAI transport recovery,
typed DeepSeek output failures, and historical-backfill integrity diagnostics.
These are separate from the Graph/Trends correction.

## Next milestone

Return to the separate Daily pipeline follow-ups listed above. Ubuntu migration,
storage cleanup, and public Demo remain deferred.
