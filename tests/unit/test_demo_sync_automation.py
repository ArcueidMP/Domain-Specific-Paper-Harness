"""Focused contracts for optional, non-blocking Demo snapshot automation."""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_DIRECTORY = REPOSITORY_ROOT / "infra" / "terraform"
WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "demo-data-sync.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_demo_sync_runs_only_after_successful_main_ci_without_gating_ci() -> None:
    workflow = _read(WORKFLOW)

    assert "workflow_run:" in workflow
    assert "workflows: [CI]" in workflow
    assert "branches: [main]" in workflow
    assert "workflow_run.conclusion == 'success'" in workflow
    assert "workflow_run.event == 'push'" in workflow
    assert "workflow_run.head_branch == 'main'" in workflow
    assert "workflow_run.head_repository.full_name == github.repository" in workflow
    assert "vars.GCP_PROJECT_ID != ''" in workflow
    assert "vars.GCP_DEMO_SYNC_WORKLOAD_IDENTITY_PROVIDER != ''" in workflow
    assert "vars.GCP_DEMO_SYNC_SERVICE_ACCOUNT != ''" in workflow
    assert "vars.GCP_DEMO_SYNC_DATABASE_SECRET_ID != ''" in workflow
    assert "vars.GCP_DEMO_SYNC_DATABASE_SECRET_VERSION != ''" in workflow
    assert "DEMO_SYNC_ENABLED" not in workflow
    assert "id-token: write" in workflow
    assert "sync-demo-schema" in workflow
    assert "continue-on-error" not in workflow
    assert "workflow_dispatch" not in workflow
    assert "demo-data-sync.yml" not in _read(REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml")


def test_demo_sync_actions_are_immutable_and_no_credentials_are_stored() -> None:
    workflow = _read(WORKFLOW)

    uses = re.findall(r"(?m)^\s*- uses:\s*(\S+)", workflow)
    assert uses
    assert all(re.search(r"@[0-9a-f]{40}$", action) for action in uses)
    assert "service-account" not in workflow.lower()
    assert "GCP_DEMO_SYNC_DATABASE_SECRET_ID" in workflow
    assert "GCP_DEMO_SYNC_DATABASE_SECRET_VERSION" in workflow
    assert '"DATABASE_SCHEMA": "demo"' in workflow
    assert '"APP_ENV": "production"' in workflow
    assert "versions/latest:access" not in workflow
    assert "versions/{secret_version}:access" in workflow
    assert "positive integer" in workflow
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" in workflow


def test_terraform_demo_resources_are_opt_in_and_oidc_is_narrow() -> None:
    variables = _read(TERRAFORM_DIRECTORY / "variables.tf")
    services = _read(TERRAFORM_DIRECTORY / "services.tf")
    automation = _read(TERRAFORM_DIRECTORY / "demo_automation.tf")
    outputs = _read(TERRAFORM_DIRECTORY / "outputs.tf")

    flag = re.search(
        r'variable "deploy_demo_sync_automation"\s*\{(?P<body>.*?)\n\}',
        variables,
        re.DOTALL,
    )
    assert flag is not None
    assert re.search(r"(?m)^\s*default\s*=\s*false\s*$", flag.group("body"))
    assert "github_repository must use owner/name form" in variables
    assert '"iamcredentials.googleapis.com"' in services
    assert services.count("var.deploy_demo_sync_automation ? 1 : 0") == 2
    assert automation.count("var.deploy_demo_sync_automation ? 1 : 0") == 5
    assert 'assertion.ref == "refs/heads/main"' in automation
    assert "demo-data-sync.yml@refs/heads/main" in automation
    assert 'role      = "roles/secretmanager.secretAccessor"' in automation
    assert "google_project_iam" not in automation
    assert 'output "demo_sync_automation"' in outputs


def test_demo_read_secret_is_not_available_to_the_sync_identity() -> None:
    services = _read(TERRAFORM_DIRECTORY / "services.tf")
    automation = _read(TERRAFORM_DIRECTORY / "demo_automation.tf")

    assert 'resource "google_secret_manager_secret" "demo_read_database_url"' in services
    assert "demo_read_database_url" not in automation
    assert "private_key" not in automation
    assert "service_account_key" not in automation
