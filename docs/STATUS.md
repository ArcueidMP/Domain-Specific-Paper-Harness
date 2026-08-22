# Current Status
## Current milestone

M5 and the private production MVP are complete. The multi-topic expansion is
deployed and accepted for the 2026-08-22 logical date.

## Completed capabilities

Broad LLM Agents, Brain-Computer Interfaces, and World Models now run as
independent TopicConfigs through the same pipeline, PostgreSQL database, Daily
image, FastAPI contract, and React product. Each topic has isolated discovery
cursor, runs, reports, graph, trends, lineage, frontend selection, Cloud Run
Job, and Scheduler target.

Migration `0006_topic_reprocessing` adds explicit additive same-date
reprocessing. A successful revision becomes the public topic/date result while
earlier publications remain immutable for audit. Reprocessing uses its logical
date lookback without advancing the scheduled cursor. Failed historical
backfills resume from the last committed query boundary.

## Verification

The final canonical `scripts/verify.ps1 -PostgresPort 55433` invocation passed:
frozen dependencies, license and repository hygiene, Ruff, formatting,
Pyright, OpenAPI generation, frontend lint/typecheck/build, 33 Vitest tests,
two Playwright tests, Terraform format/validation, clean Alembic upgrade to
`0006`, 878 pytest tests with four explicit live opt-ins skipped, PostgreSQL
integration, and all three runtime image builds. Live arXiv requests returned
candidates for all three topics, and the authenticated production browser
verified topic switching and published reports.

## Deployment

Production database migration execution `paper-harness-migration-jrnml`
completed successfully at the `0006_topic_reprocessing` head. The private
Web/API remains available at
`https://paper-harness-web-nxdmkbsdtq-as.a.run.app` with the three-topic
selector. Terraform state owns topic-keyed Daily Job, invoker, and Scheduler
resources; all three topic Jobs share one Daily image. GROBID and existing
secrets were reused unchanged.

The 2026-08-22 reprocessed publications are:

- Broad LLM Agents: execution `paper-harness-daily-pm6g9`, `PARTIAL`, 7 of 10
  selected papers completed; DAILY report, graph, three trend windows, and
  seven lineage snapshots persisted.
- Brain-Computer Interfaces: execution
  `paper-harness-daily-brain-computer-interfaces-kj6hp`, `PARTIAL`, 6 of 10
  completed; DAILY report, graph, three trend windows, and six lineage
  snapshots persisted.
- World Models: execution `paper-harness-daily-world-models-94pmn`, `PARTIAL`,
  6 of 10 completed; DAILY report, graph, three trend windows, and six lineage
  snapshots persisted.

Schedulers are enabled in `Asia/Kuala_Lumpur` at 05:00, 05:20, and 05:40 for
Broad LLM Agents, Brain-Computer Interfaces, and World Models respectively.

## Current blockers

No external blocker remains. Item-level PDF, DeepSeek output, and comparison
failures remain visible in the corresponding honest PARTIAL reports.

## Next milestone

Operate the three staggered schedules and review item-level diagnostics through
the private product.
