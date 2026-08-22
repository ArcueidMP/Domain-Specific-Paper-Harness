"""Focused repository hygiene tests for M5."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from scripts.check_repository_hygiene import (
    check_generated_artifacts,
    check_tracked_repository,
)


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(repository: Path, relative_path: str, content: str) -> Path:
    path = repository / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def git_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--initial-branch", "main")
    _git(repository, "config", "user.email", "hygiene@example.invalid")
    _git(repository, "config", "user.name", "Hygiene Test")
    _write(repository, "README.md", "fixture repository\n")
    _git(repository, "add", "README.md")
    return repository


def test_tracked_hygiene_allows_documented_examples_and_tei_fixtures(
    git_repository: Path,
) -> None:
    _write(git_repository, ".env.example", "DATABASE_URL=<required>\n")
    _write(
        git_repository,
        "tests/contract/fixtures/grobid_fulltext_0_9_0.tei.xml",
        "<TEI />\n",
    )
    _write(
        git_repository,
        "tests/contract/fixtures/provider_variation.tei.xml",
        "<TEI />\n",
    )
    _git(git_repository, "add", ".env.example", "tests")

    assert check_tracked_repository(git_repository) == ()


def test_tracked_hygiene_rejects_credentials_and_generated_artifacts(
    git_repository: Path,
) -> None:
    _write(git_repository, ".env.production", "PLACEHOLDER=value\n")
    _write(git_repository, "secrets/service-account-prod.json", "{}\n")
    _write(git_repository, "apps/web/dist/index.html", "generated\n")
    _git(git_repository, "add", "--all")

    findings = check_tracked_repository(git_repository)

    assert {(finding.path, finding.rule) for finding in findings} == {
        (".env.production", "environment file must remain untracked"),
        (
            "apps/web/dist/index.html",
            "generated, cached, model, download, or local-data directory is tracked",
        ),
        ("secrets/service-account-prod.json", "service-account key document is tracked"),
    }


def test_tracked_hygiene_checks_modified_tracked_content(
    git_repository: Path,
) -> None:
    _write(git_repository, "README.md", "sk-" + ("x" * 32))

    findings = check_tracked_repository(git_repository)

    assert {(finding.path, finding.rule) for finding in findings} == {
        ("README.md", "provider API key")
    }


def test_generated_hygiene_accepts_clean_openapi_and_frontend_build(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    openapi = _write(
        repository,
        "apps/api/openapi.json",
        json.dumps({"openapi": "3.1.0", "paths": {}}),
    )
    _write(repository, "apps/web/dist/index.html", '<main id="root"></main>\n')
    _write(repository, "apps/web/dist/assets/app.js", "console.log('ready');\n")

    assert check_generated_artifacts(repository, (openapi, Path("apps/web/dist"))) == ()


def test_generated_hygiene_rejects_source_map(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _write(repository, "apps/web/dist/index.html", '<main id="root"></main>\n')
    _write(repository, "apps/web/dist/assets/app.js", "console.log('ready');\n")
    _write(repository, "apps/web/dist/assets/app.js.map", "{}\n")

    findings = check_generated_artifacts(repository, (Path("apps/web/dist"),))

    assert {(finding.path, finding.rule) for finding in findings} == {
        ("apps/web/dist/assets/app.js.map", "production source map is present")
    }


def test_generated_hygiene_rejects_malformed_openapi(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    openapi = _write(repository, "apps/api/openapi.json", json.dumps({"description": "x"}))

    findings = check_generated_artifacts(repository, (openapi,))

    assert {(finding.path, finding.rule) for finding in findings} == {
        ("apps/api/openapi.json", "OpenAPI artifact lacks an openapi document key")
    }
