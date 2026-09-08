"""Exercise the production Daily script against a local gcloud argument recorder."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = "paper-harness-test"
JOB = "paper-harness-daily-test"
REGION = "asia-southeast1"
LOGICAL_DATE = "2026-09-08"
EXECUTION_ID = "3b301c07-9aa3-4a3d-b13a-e7b3ba4db146"
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run-production-daily.ps1"


@pytest.fixture
def powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is required to exercise the production Daily script")
    return executable


def _run_script(
    powershell: str, tmp_path: Path, *arguments: str
) -> tuple[subprocess.CompletedProcess[bytes], list[list[str]]]:
    recorder = tmp_path / "gcloud_recorder.py"
    log = tmp_path / "gcloud_argv.jsonl"
    recorder.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "with Path(os.environ['DAILY_TEST_GCLOUD_LOG']).open('a', encoding='utf-8') as output:\n"
        "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:] == ['config', 'get-value', 'project']:\n"
        f"    print({PROJECT!r})\n"
        "elif sys.argv[1:4] == ['run', 'jobs', 'describe']:\n"
        f"    print({JOB!r})\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        stub = tmp_path / "gcloud.cmd"
        stub.write_text(f'@echo off\n"{sys.executable}" "{recorder}" %*\n', encoding="utf-8")
    else:
        stub = tmp_path / "gcloud"
        stub.write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(recorder))} "$@"\n',
            encoding="utf-8",
        )
        stub.chmod(0o700)
    environment = {
        **os.environ,
        "PATH": str(tmp_path),
        "DAILY_TEST_GCLOUD_LOG": str(log),
    }
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-ProjectId",
            PROJECT,
            "-Region",
            REGION,
            "-JobName",
            JOB,
            *arguments,
        ],
        env=environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    calls = (
        [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        if log.exists()
        else []
    )
    return result, calls


def test_daily_resume_script_forwards_the_original_execution_and_date(
    powershell: str, tmp_path: Path
) -> None:
    result, calls = _run_script(
        powershell,
        tmp_path,
        "-Reprocess",
        "-LogicalDate",
        LOGICAL_DATE,
        "-ResumeExecutionId",
        EXECUTION_ID,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [
        ["config", "get-value", "project"],
        [
            "run",
            "jobs",
            "describe",
            JOB,
            f"--project={PROJECT}",
            f"--region={REGION}",
            "--format=value(name)",
        ],
        [
            "run",
            "jobs",
            "execute",
            JOB,
            f"--project={PROJECT}",
            f"--region={REGION}",
            "--tasks=1",
            "--wait",
            "--update-env-vars="
            f"PIPELINE_LOGICAL_DATE={LOGICAL_DATE},PIPELINE_REPROCESS=true,"
            f"PIPELINE_RESUME_EXECUTION_ID={EXECUTION_ID}",
        ],
    ]


@pytest.mark.parametrize(
    "prerequisites",
    [(), ("-Reprocess",), ("-LogicalDate", LOGICAL_DATE)],
    ids=["missing-both", "missing-logical-date", "missing-reprocess"],
)
def test_daily_resume_script_rejects_missing_prerequisites_before_gcloud(
    powershell: str, tmp_path: Path, prerequisites: tuple[str, ...]
) -> None:
    result, calls = _run_script(
        powershell, tmp_path, *prerequisites, "-ResumeExecutionId", EXECUTION_ID
    )

    assert result.returncode != 0
    assert b"ResumeExecutionId requires Reprocess and the original LogicalDate" in result.stderr
    assert calls == []
