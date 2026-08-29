"""Focused least-privilege and direct-operations infrastructure checks."""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_DIRECTORY = REPOSITORY_ROOT / "infra" / "terraform"
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _resource_block(source: str, resource_type: str, name: str) -> str:
    header = f'resource "{resource_type}" "{name}"'
    start = source.index(header)
    following = source.find('\nresource "', start + len(header))
    return source[start:] if following < 0 else source[start:following]


def test_terraform_has_no_public_or_project_wide_runtime_bindings() -> None:
    terraform = "\n".join(_read(path) for path in sorted(TERRAFORM_DIRECTORY.glob("*.tf")))

    assert "allUsers" not in terraform
    assert "allAuthenticatedUsers" not in terraform
    assert "roles/owner" not in terraform
    assert "roles/editor" not in terraform
    assert "google_project_iam" not in terraform


def test_secret_access_is_scoped_to_the_runtime_that_uses_each_secret() -> None:
    iam = _read(TERRAFORM_DIRECTORY / "iam.tf")

    assert 'role      = "roles/secretmanager.secretAccessor"' in iam
    database_binding = iam.split(
        'resource "google_secret_manager_secret_iam_binding" "database_accessors"',
        maxsplit=1,
    )[1].split(
        'resource "google_secret_manager_secret_iam_binding" "deepseek_accessors"',
        maxsplit=1,
    )[0]
    for account in ("daily", "migration", "web"):
        assert f"google_service_account.{account}.email" in database_binding
    assert "google_service_account.grobid" not in database_binding
    assert "google_service_account.scheduler" not in database_binding

    for binding_name in ("deepseek_accessors", "semantic_scholar_accessors"):
        binding = iam.split(
            f'resource "google_secret_manager_secret_iam_binding" "{binding_name}"',
            maxsplit=1,
        )[1].split("resource ", maxsplit=1)[0]
        assert 'members   = ["serviceAccount:${google_service_account.daily.email}"]' in binding


def test_web_and_grobid_access_remain_identity_gated() -> None:
    variables = _read(TERRAFORM_DIRECTORY / "variables.tf")
    runtime = _read(TERRAFORM_DIRECTORY / "runtime.tf")

    assert "iap_enabled         = true" in runtime
    assert 'role     = "roles/run.invoker"' in runtime
    assert "google_project_service_identity.iap.email" in runtime
    assert 'role                   = "roles/iap.httpsResourceAccessor"' in runtime
    assert 'variable "additional_iap_user_emails"' in variables
    assert '["user:${var.owner_email}"]' in runtime
    assert '"user:${email}"' in runtime
    assert "var.additional_iap_user_emails" in runtime
    assert 'members  = ["serviceAccount:${google_service_account.daily.email}"]' in runtime


def test_known_runtime_units_are_bounded_and_scheduler_has_no_automatic_retry() -> None:
    runtime = _read(TERRAFORM_DIRECTORY / "runtime.tf")

    for resource_type, name in (
        ("google_cloud_run_v2_job", "migration"),
        ("google_cloud_run_v2_service", "web"),
        ("google_cloud_run_v2_service", "grobid"),
        ("google_cloud_run_v2_job", "daily"),
    ):
        block = _resource_block(runtime, resource_type, name)
        assert re.search(r"\bdeletion_protection\s*=\s*true\b", block)

    for service_name in ("web", "grobid"):
        block = _resource_block(runtime, "google_cloud_run_v2_service", service_name)
        assert re.search(r"\bmin_instance_count\s*=\s*0\b", block)

    for job_name in ("migration", "daily"):
        block = _resource_block(runtime, "google_cloud_run_v2_job", job_name)
        assert re.search(r"\bmax_retries\s*=\s*0\b", block)

    scheduler = _resource_block(runtime, "google_cloud_scheduler_job", "daily")
    assert "for_each = var.deploy_scheduler ? local.daily_topics : {}" in scheduler
    assert "schedule         = each.value.schedule" in scheduler
    assert "time_zone        = var.schedule_time_zone" in scheduler
    assert "paused           = var.scheduler_paused" in scheduler
    assert re.search(r"\bretry_count\s*=\s*0\b", scheduler)


def test_operator_scripts_use_direct_commands() -> None:
    deploy = _read(SCRIPTS_DIRECTORY / "deploy.ps1")
    migration = _read(SCRIPTS_DIRECTORY / "run-production-migration.ps1")
    daily = _read(SCRIPTS_DIRECTORY / "run-production-daily.ps1")
    scheduler = _read(SCRIPTS_DIRECTORY / "verify-scheduler.ps1")

    assert '"init"' in deploy
    assert '$Action = if ($Apply) { "apply" } else { "plan" }' in deploy
    assert (
        '$ActionArguments = @("-chdir=$TerraformDirectory", $Action) + $CommonArguments' in deploy
    )
    assert "-Arguments $ActionArguments" in deploy
    assert "run jobs execute" in migration
    assert '"run", "jobs", "execute"' in daily
    assert "PIPELINE_LOGICAL_DATE" in daily
    assert "PIPELINE_REPROCESS=true" in daily
    assert "--update-env-vars=" in daily
    for action in ("describe", "run"):
        assert f"scheduler jobs {action}" in scheduler
    for action in ("pause", "resume"):
        assert f"scheduler jobs {action}" not in scheduler


def test_local_helpers_delegate_contracts_and_keep_state_management_bounded() -> None:
    run_daily = _read(SCRIPTS_DIRECTORY / "run-daily.ps1")
    build_images = _read(SCRIPTS_DIRECTORY / "build-images.ps1")
    private_runtime = _read(SCRIPTS_DIRECTORY / "verify-private-runtime.ps1")
    bootstrap_state = _read(SCRIPTS_DIRECTORY / "bootstrap-terraform-state.ps1")

    assert "ValueFromRemainingArguments" in run_daily
    assert "OperationArgument" not in run_daily
    assert "SEMANTIC_SCHOLAR_API_KEY" not in run_daily
    assert "DEEPSEEK_API_KEY" not in run_daily

    assert "PushExisting" in build_images
    assert 'ValidateSet("web-api", "daily", "grobid")' in build_images
    assert "Get-Command gcloud" not in build_images

    assert ").Count -gt 0" in private_runtime
    assert '-cnotcontains "user:$OwnerEmail"' in private_runtime
    assert '"allUsers"' in private_runtime
    assert '"allAuthenticatedUsers"' in private_runtime

    assert "$RetentionSeconds -lt 604800" in bootstrap_state
    assert 'retentionDurationSeconds") -ne "604800"' not in bootstrap_state
    assert "Read-Host" not in bootstrap_state
