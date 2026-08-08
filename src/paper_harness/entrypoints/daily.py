"""Cloud Run Daily Job entrypoint."""

from __future__ import annotations

import sys

from paper_harness.entrypoints.cli import app


def main() -> None:
    arguments = sys.argv[1:]
    if arguments and arguments[0] in {"ingest-arxiv", "analyze-papers"}:
        app(prog_name="paper-harness-daily", args=arguments)
    else:
        app(prog_name="paper-harness-daily", args=["ingest-arxiv", *arguments])


if __name__ == "__main__":
    main()
