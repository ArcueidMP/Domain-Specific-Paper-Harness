# Contributing

Domain-Specific Paper Harness is maintained as a public showcase and a
reproducible reference implementation. Issues and pull requests are welcome and
are reviewed on a best-effort basis. The project does not promise a public
roadmap or a fixed response time.

## Development environment

The supported development path is Windows with PowerShell. Linux contributors
can use the equivalent manual commands documented in the README and exercised
by CI. macOS is not currently verified.

Use the repository's pinned toolchain:

- CPython 3.13.13, resolved through uv 0.12.3 in CI
- Node.js 24 (24.15.0 in CI) and pnpm 11.0.9 through Corepack
- Terraform 1.15.8 for infrastructure checks
- Docker with Compose v2 for local PostgreSQL and image checks

Do not substitute another Python minor release. Install the locked dependencies
from the repository root:

```powershell
uv python install 3.13.13
uv sync --frozen --all-extras --python 3.13.13
corepack prepare pnpm@11.0.9 --activate
corepack pnpm install --frozen-lockfile
```

See the README for database setup, migrations, local services, and the manual
Linux equivalents.

## Contribution boundaries

- Keep all source code, comments, documentation, commits, and product copy in
  English. `README.zh-CN.md` is the sole documentation exception and must stay
  synchronized with the English `README.md`.
- Preserve topic isolation. A topic's categories, inclusion terms, exclusions,
  cursor, selection, reports, graph, trends, and lineage must not affect another
  topic.
- Keep daily discovery arXiv-only. Historical research must stay within the
  documented Semantic Scholar and local-corpus boundaries.
- Preserve explicit provenance, publication states, immutable publication
  revisions, and availability-first `PARTIAL` and `NO_UPDATE` behavior.
- Do not add implicit provider, parser, model, storage, or analysis fallbacks.
- Never commit credentials, local environment files, production data, PDFs,
  model weights, Terraform state, or database exports.
- Update tests and current-state documentation when a change affects behavior.

## Checks and pull requests

Run focused checks for the boundary you changed while iterating. Typical checks
include:

```powershell
uv run --frozen --python 3.13.13 ruff check .
uv run --frozen --python 3.13.13 ruff format --check .
uv run --frozen --python 3.13.13 pyright
uv run --frozen --python 3.13.13 pytest tests/unit/test_config_and_query.py
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm test
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform validate
```

Use `scripts/verify.ps1` once when a coherent change is ready for final review;
it is the canonical Windows verification entry point. Live external-service
tests remain explicitly opt-in and must not be required for ordinary pull
requests.

Open a focused pull request that explains the behavior change, its verification,
and any deployment or data implications. The project does not require a
Contributor License Agreement, Developer Certificate of Origin sign-off, or an
additional approval workflow beyond the repository's normal pull-request and CI
rules.

Security-sensitive findings must follow [SECURITY.md](SECURITY.md), not a public
issue.
