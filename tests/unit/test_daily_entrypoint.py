# pyright: reportPrivateUsage=false

from __future__ import annotations

import sys
from unittest.mock import Mock

import pytest

import paper_harness.entrypoints.daily as daily_module
from paper_harness.entrypoints.cli import app as cli_app


def test_explicit_daily_operations_match_every_registered_cli_command() -> None:
    registered_operations = frozenset(
        command.name for command in cli_app.registered_commands if command.name is not None
    )

    assert len(registered_operations) == len(cli_app.registered_commands)
    assert registered_operations == daily_module._EXPLICIT_OPERATIONS


@pytest.mark.parametrize("operation", sorted(daily_module._EXPLICIT_OPERATIONS))
def test_main_passes_every_explicit_operation_through_exactly(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    invocation = Mock()
    arguments = [operation, "--help"]
    monkeypatch.setattr(daily_module, "app", invocation)
    monkeypatch.setattr(sys, "argv", ["paper-harness-daily", *arguments])

    daily_module.main()

    invocation.assert_called_once_with(
        prog_name="paper-harness-daily",
        args=arguments,
    )


def test_main_defaults_options_to_the_full_daily_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation = Mock()
    monkeypatch.setattr(daily_module, "app", invocation)
    monkeypatch.setattr(sys, "argv", ["paper-harness-daily", "--help"])

    daily_module.main()

    invocation.assert_called_once_with(
        prog_name="paper-harness-daily",
        args=["run-pipeline", "--help"],
    )
