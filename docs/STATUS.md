# Current Status
## Current milestone

The availability-first Daily publication regression is implemented and
accepted in the private multi-topic production MVP.

## Completed capabilities

Every selected paper with valid arXiv metadata remains visible as a Daily card.
Source-analysis failure produces `ANALYSIS_UNAVAILABLE`; related work,
comparison, graph, trend, lineage, and generated narrative are optional,
explicit availability states. Zero relevant papers publish `COMPLETE / NO_UPDATE`
for the current logical date. Normal scheduled runs retain canonical
deduplication, while same-date reprocessing preserves the widest terminal
paper-version baseline.

## Verification

The final canonical `scripts/verify.ps1` invocation passed: frozen dependencies,
license and repository hygiene, Ruff, formatting, Pyright, generated OpenAPI,
frontend lint/typecheck/build, 34 Vitest tests, two Playwright smoke tests,
Docker Compose configuration, Terraform format/init/validation, a clean Alembic
upgrade to `0006` with no schema drift, 886 pytest tests with four explicit live
opt-ins skipped, and the Web/API, Daily, and pinned GROBID image builds. Focused
publication/search verification and production reads also passed, including
exact card/failure ownership and safe metadata-only report inputs.

## Deployment

The private Web/API is revision `paper-harness-web-00012-tp7` at
`https://paper-harness-web-nxdmkbsdtq-as.a.run.app`. All Daily Jobs use image
`sha256:15c4430c0a6ea3201e1359fd4b6bcb1937dacc2c389f4c4cb34ebc31a6984df2`.
IAP, the owner allowlist, GROBID, secrets, migrations, and Scheduler resources
were unchanged and verified non-public.

The accepted 2026-08-23 results are:

- Broad LLM Agents: run `5896655c-1eba-40e6-9998-9dfcb6f8ff4a`, `COMPLETE`,
  10 selected, 10 completed, 10 Daily cards.
- Brain-Computer Interfaces: run `ded45b40-ef69-4011-b651-09058facef25`,
  `PARTIAL`, 4 selected, 3 completed, one metadata-only analysis failure, and 4
  Daily cards.
- World Models was not rerun; its earlier 2026-08-23 `PARTIAL` revision remains
  visible. Its Job uses the current Daily image for future schedules.

Schedulers remain enabled in `Asia/Kuala_Lumpur` at 05:00, 05:20, and 05:40.

## Current blockers

No external or production blocker remains.

## Next milestone

Operate the staggered topic schedules and review transparent item-level
availability diagnostics through the private product.
