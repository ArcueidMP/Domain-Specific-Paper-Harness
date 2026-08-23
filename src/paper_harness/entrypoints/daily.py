"""Cloud Run Daily Job entrypoint."""

from __future__ import annotations

import sys

from paper_harness.entrypoints.cli import app

_EXPLICIT_OPERATIONS = frozenset(
    {
        "run-pipeline",
        "ingest-arxiv",
        "analyze-papers",
        "historical-backfill",
        "search-related",
        "compare-papers",
        "publish-product",
        "generate-periodic-report",
    }
)
_OPERATOR_ONLY_OPERATIONS = frozenset(
    {
        "bootstrap-demo-schema",
        "sync-demo-schema",
    }
)


def main() -> None:
    arguments = sys.argv[1:]
    if arguments and arguments[0] in _OPERATOR_ONLY_OPERATIONS:
        raise SystemExit(
            f"{arguments[0]} is an operator-only command and is unavailable to the Daily Job"
        )
    if arguments and arguments[0] in _EXPLICIT_OPERATIONS:
        app(prog_name="paper-harness-daily", args=arguments)
    else:
        app(prog_name="paper-harness-daily", args=["run-pipeline", *arguments])


if __name__ == "__main__":
    main()
