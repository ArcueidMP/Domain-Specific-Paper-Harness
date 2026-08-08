"""Cloud Run Daily Job entrypoint."""

from __future__ import annotations

import sys

from paper_harness.entrypoints.cli import app


def main() -> None:
    app(prog_name="paper-harness-daily", args=["ingest-arxiv", *sys.argv[1:]])


if __name__ == "__main__":
    main()
