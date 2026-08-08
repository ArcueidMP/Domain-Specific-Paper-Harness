from __future__ import annotations

from typer.testing import CliRunner

from paper_harness.entrypoints.cli import app


def test_cli_accepts_string_logical_date_option() -> None:
    result = CliRunner().invoke(app, ["ingest-arxiv", "--help"])
    assert result.exit_code == 0
    assert "--logical-date" in result.stdout
