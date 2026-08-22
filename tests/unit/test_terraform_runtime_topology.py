"""Focused contracts for independently staged Terraform runtime boundaries."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_DIRECTORY = REPOSITORY_ROOT / "infra" / "terraform"


def _read(name: str) -> str:
    return (TERRAFORM_DIRECTORY / name).read_text(encoding="utf-8")


def _named_block(source: str, header: str) -> str:
    """Return one HCL block while ignoring braces inside quoted strings."""
    start = source.index(header)
    opening_brace = source.index("{", start + len(header))
    depth = 0
    in_string = False
    escaped = False

    for index in range(opening_brace, len(source)):
        character = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]

    raise AssertionError(f"Unterminated HCL block: {header}")


def _resource_block(source: str, resource_type: str, name: str) -> str:
    return _named_block(source, f'resource "{resource_type}" "{name}"')


def _variable_block(source: str, name: str) -> str:
    return _named_block(source, f'variable "{name}"')


def _run_variable_plan(
    directory: Path, variables: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    terraform = shutil.which("terraform")
    if terraform is None:
        pytest.skip("Terraform is not installed on this test host")
    directory.mkdir()
    (directory / "variables.tf").write_text(_read("variables.tf"), encoding="utf-8")
    arguments = [
        terraform,
        f"-chdir={directory}",
        "plan",
        "-input=false",
        "-lock=false",
        "-refresh=false",
        "-no-color",
        *[f"-var={name}={value}" for name, value in variables.items()],
    ]
    return subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _partial_runtime_variables() -> dict[str, str]:
    digest = f"asia-southeast1-docker.pkg.dev/test-project1/paper-harness/image@sha256:{'a' * 64}"
    return {
        "project_id": "test-project1",
        "owner_email": "owner@example.com",
        "deploy_migration_resources": "true",
        "migration_image": digest,
        "migration_database_secret_version": "1",
        "deploy_runtime_resources": "true",
        "web_api_image": digest,
        "database_secret_version": "1",
        "deploy_analysis_resources": "true",
        "grobid_image": digest,
    }


def test_staged_infrastructure_defaults_are_safe_and_required_resources_are_declared() -> None:
    variables = _read("variables.tf")
    services = _read("services.tf")
    iam = _read("iam.tf")
    runtime = _read("runtime.tf")

    for variable_name in (
        "deploy_migration_resources",
        "deploy_runtime_resources",
        "deploy_analysis_resources",
        "deploy_daily_resources",
        "deploy_scheduler",
    ):
        block = _variable_block(variables, variable_name)
        assert re.search(r"(?m)^\s*default\s*=\s*false\s*$", block)

    required_services = _named_block(services, "locals")
    service_names = set(re.findall(r'"([a-z]+\.googleapis\.com)"', required_services))
    assert {
        "artifactregistry.googleapis.com",
        "cloudresourcemanager.googleapis.com",
        "cloudscheduler.googleapis.com",
        "iam.googleapis.com",
        "iap.googleapis.com",
        "logging.googleapis.com",
        "run.googleapis.com",
        "secretmanager.googleapis.com",
    } <= service_names

    required_unconditional_resources = (
        (services, "google_project_service_identity", "iap"),
        (services, "google_artifact_registry_repository", "containers"),
        (services, "google_secret_manager_secret", "database_url"),
        (services, "google_secret_manager_secret", "deepseek_api_key"),
        (services, "google_secret_manager_secret", "semantic_scholar_api_key"),
        (iam, "google_service_account", "web"),
        (iam, "google_service_account", "daily"),
        (iam, "google_service_account", "scheduler"),
        (iam, "google_service_account", "migration"),
        (iam, "google_secret_manager_secret_iam_binding", "database_accessors"),
    )
    for source, resource_type, name in required_unconditional_resources:
        block = _resource_block(source, resource_type, name)
        assert "count =" not in block

    conditional_resources = (
        (runtime, "google_cloud_run_v2_job", "migration"),
        (runtime, "google_cloud_run_v2_service", "web"),
        (runtime, "google_cloud_run_v2_service_iam_binding", "iap_invoker"),
        (runtime, "google_iap_web_cloud_run_service_iam_binding", "owner"),
        (runtime, "google_cloud_run_v2_service", "grobid"),
        (runtime, "google_cloud_run_v2_service_iam_binding", "grobid_invokers"),
        (runtime, "google_cloud_run_v2_job", "daily"),
        (runtime, "google_cloud_run_v2_job_iam_binding", "scheduler_invokers"),
        (runtime, "google_cloud_scheduler_job", "daily"),
        (iam, "google_service_account", "grobid"),
        (iam, "google_secret_manager_secret_iam_binding", "deepseek_accessors"),
        (iam, "google_secret_manager_secret_iam_binding", "semantic_scholar_accessors"),
    )
    for source, resource_type, name in conditional_resources:
        block = _resource_block(source, resource_type, name)
        assert "count = var." in block


def test_web_and_grobid_can_be_enabled_without_daily_or_api_key_versions() -> None:
    variables = _read("variables.tf")
    runtime = _read("runtime.tf")

    analysis_gate = _variable_block(variables, "deploy_analysis_resources")
    assert "validation {" not in analysis_gate

    grobid = _resource_block(runtime, "google_cloud_run_v2_service", "grobid")
    assert "count = var.deploy_analysis_resources ? 1 : 0" in grobid
    for forbidden_dependency in (
        "deploy_runtime_resources",
        "deploy_daily_resources",
        "deepseek_secret_version",
        "google_cloud_run_v2_service.web",
        "google_cloud_run_v2_job.daily",
    ):
        assert forbidden_dependency not in grobid

    web = _resource_block(runtime, "google_cloud_run_v2_service", "web")
    assert "count = var.deploy_runtime_resources ? 1 : 0" in web
    assert "deploy_daily_resources" not in web

    grobid_invokers = _resource_block(
        runtime, "google_cloud_run_v2_service_iam_binding", "grobid_invokers"
    )
    assert "count = var.deploy_analysis_resources ? 1 : 0" in grobid_invokers
    assert 'members  = ["serviceAccount:${google_service_account.daily.email}"]' in (
        grobid_invokers
    )
    assert "allUsers" not in grobid_invokers
    assert "allAuthenticatedUsers" not in grobid_invokers


def test_variable_validation_accepts_independently_staged_web_and_daily() -> None:
    digest = f"asia-southeast1-docker.pkg.dev/test-project1/paper-harness/image@sha256:{'d' * 64}"
    web_only = {
        "project_id": "test-project1",
        "owner_email": "owner@example.com",
        "deploy_runtime_resources": "true",
        "web_api_image": digest,
        "database_secret_version": "1",
    }
    daily_without_web = {
        "project_id": "test-project1",
        "owner_email": "owner@example.com",
        "deploy_analysis_resources": "true",
        "grobid_image": digest,
        "deploy_daily_resources": "true",
        "daily_image": digest,
        "database_secret_version": "1",
        "deepseek_secret_version": "2",
        "semantic_scholar_secret_version": "3",
    }
    with tempfile.TemporaryDirectory(
        prefix="terraform-topology-", dir=REPOSITORY_ROOT / "data"
    ) as root:
        web_result = _run_variable_plan(Path(root) / "web-only", web_only)
        daily_result = _run_variable_plan(Path(root) / "daily-without-web", daily_without_web)

    assert web_result.returncode == 0, web_result.stdout + web_result.stderr
    assert daily_result.returncode == 0, daily_result.stdout + daily_result.stderr


def test_variable_validation_rejects_incomplete_daily_and_scheduler_topologies() -> None:
    digest = f"asia-southeast1-docker.pkg.dev/test-project1/paper-harness/daily@sha256:{'b' * 64}"
    incomplete_daily = {
        **_partial_runtime_variables(),
        "deploy_daily_resources": "true",
        "daily_image": digest,
        "deepseek_secret_version": "2",
    }
    with tempfile.TemporaryDirectory(
        prefix="terraform-topology-", dir=REPOSITORY_ROOT / "data"
    ) as root:
        daily_result = _run_variable_plan(Path(root) / "incomplete-daily", incomplete_daily)

        assert daily_result.returncode != 0
        assert "deploy_daily_resources requires GROBID" in (
            daily_result.stdout + daily_result.stderr
        )

        scheduler_without_daily = {
            **_partial_runtime_variables(),
            "deploy_scheduler": "true",
        }
        scheduler_result = _run_variable_plan(
            Path(root) / "scheduler-without-daily", scheduler_without_daily
        )

        assert scheduler_result.returncode != 0
        assert "deploy_scheduler requires deploy_daily_resources=true" in (
            scheduler_result.stdout + scheduler_result.stderr
        )


def test_variable_validation_accepts_complete_daily_topology() -> None:
    digest = f"asia-southeast1-docker.pkg.dev/test-project1/paper-harness/daily@sha256:{'c' * 64}"
    complete_daily = {
        **_partial_runtime_variables(),
        "deploy_daily_resources": "true",
        "daily_image": digest,
        "deepseek_secret_version": "2",
        "semantic_scholar_secret_version": "3",
    }
    with tempfile.TemporaryDirectory(
        prefix="terraform-topology-", dir=REPOSITORY_ROOT / "data"
    ) as root:
        result = _run_variable_plan(Path(root) / "complete-daily", complete_daily)

    assert result.returncode == 0, result.stdout + result.stderr


def test_daily_is_the_only_consumer_gate_for_complete_pipeline_inputs() -> None:
    variables = _read("variables.tf")
    runtime = _read("runtime.tf")
    iam = _read("iam.tf")

    daily_gate = _variable_block(variables, "deploy_daily_resources")
    for required_input in (
        "var.deploy_analysis_resources",
        "var.deepseek_secret_version != null",
        "var.semantic_scholar_secret_version != null",
    ):
        assert required_input in daily_gate
    assert "var.deploy_runtime_resources" not in daily_gate
    assert "attach_semantic_scholar_secret_to_daily" not in variables
    for secret_version in ("deepseek_secret_version", "semantic_scholar_secret_version"):
        assert f"var.{secret_version} != null" in daily_gate
        assert f'can(regex("^[1-9][0-9]*$", var.{secret_version}))' in daily_gate

    daily = _resource_block(runtime, "google_cloud_run_v2_job", "daily")
    assert "count = var.deploy_daily_resources ? 1 : 0" in daily
    for required_environment_name in (
        "GROBID_AUTH_MODE",
        "DEEPSEEK_API_KEY",
        "SEMANTIC_SCHOLAR_API_KEY",
    ):
        assert required_environment_name in daily

    for binding_name in ("deepseek_accessors", "semantic_scholar_accessors"):
        binding = _resource_block(iam, "google_secret_manager_secret_iam_binding", binding_name)
        assert "count = var.deploy_daily_resources ? 1 : 0" in binding


def test_daily_job_arguments_match_the_direct_cli_contract() -> None:
    runtime = _read("runtime.tf")
    daily = _resource_block(runtime, "google_cloud_run_v2_job", "daily")

    assert 'command = ["paper-harness-daily"]' in daily
    args_match = re.search(r"(?ms)^\s*args\s*=\s*\[(.*?)^\s*\]", daily)
    assert args_match is not None
    args = tuple(re.findall(r'"([^"]*)"', args_match.group(1)))
    assert args == (
        "run-pipeline",
        "--topic-config",
        "/app/configs/topics/broad-llm-agents.yaml",
        "--analysis-scope",
        "full_text",
        "--narrative-mode",
        "deepseek",
        "--max-selected-papers",
        "10",
        "--backfill-max-queries",
        "8",
        "--backfill-per-query-limit",
        "100",
        "--backfill-timeout-seconds",
        "1800",
        "--max-search-steps",
        "12",
        "--max-search-queries",
        "4",
        "--max-search-queue-size",
        "100",
        "--max-citation-depth",
        "2",
        "--max-search-candidates",
        "100",
        "--max-selected-candidates",
        "5",
        "--search-operation-timeout-seconds",
        "60",
        "--search-overall-timeout-seconds",
        "300",
        "--max-comparisons-per-paper",
        "3",
        "--pipeline-timeout-seconds",
        "28800",
    )
    assert "--execution-mode" not in args
    assert "--execution-key" not in args


def test_scheduler_and_outputs_follow_the_daily_boundary() -> None:
    variables = _read("variables.tf")
    outputs = _read("outputs.tf")
    example = _read("terraform.tfvars.example")

    scheduler_gate = _variable_block(variables, "deploy_scheduler")
    assert "!var.deploy_scheduler || var.deploy_daily_resources" in scheduler_gate

    daily_output = _named_block(outputs, 'output "daily_job_name"')
    assert (
        "var.deploy_daily_resources ? google_cloud_run_v2_job.daily[0].name : null" in daily_output
    )

    topology = _named_block(outputs, 'output "deployment_topology"')
    assert "daily_deployed                   = var.deploy_daily_resources" in topology
    for field in (
        "daily_image",
        "daily_timeout_seconds",
        "deepseek_secret_version",
        "semantic_scholar_secret_version",
    ):
        assert re.search(
            rf"(?m)^\s*{field}\s*=\s*var\.deploy_daily_resources\s*\?",
            topology,
        )

    assert "deploy_daily_resources = false" in example
    assert "GROBID is independently deployable" in example
    assert "Keep it disabled while" in example
