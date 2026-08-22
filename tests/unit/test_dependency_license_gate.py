"""Tests for the credential-free dependency license gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import check_dependency_licenses as license_gate
from scripts.check_dependency_licenses import (
    LicenseRecord,
    flatten_pnpm_inventory,
    license_is_allowed,
    normalized_license,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class FakeMetadata:
    def __init__(
        self,
        values: dict[str, str] | None = None,
        classifiers: list[str] | None = None,
    ) -> None:
        self._values = values or {}
        self._classifiers = classifiers or []

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def get_all(self, name: str) -> list[str] | None:
        return self._classifiers if name == "Classifier" else []


@pytest.mark.parametrize(
    "expression",
    [
        "MIT",
        "Apache-2.0 OR BSD-2-Clause",
        "LGPL-3.0-only",
        "Apache-2.0 WITH LLVM-exception AND BSD-3-Clause",
        "MIT AND ISC",
        "UNKNOWN",
        "A-New-Permissive-License-1.0",
        "GPL-2.0-only WITH Classpath-exception-2.0",
        "GPL-3.0-only OR MIT",
        "LicenseRef-Proprietary OR Apache-2.0",
    ],
)
def test_current_distribution_license_policy_accepts_reviewed_expressions(
    expression: str,
) -> None:
    assert license_is_allowed(expression)


@pytest.mark.parametrize(
    "expression",
    [
        "GPL-3.0-only",
        "AGPL-3.0-or-later",
        "SSPL-1.0",
        "LicenseRef-Proprietary",
        "GPL-3.0-only AND MIT",
        "AGPL-3.0-only WITH Classpath-exception-2.0",
    ],
)
def test_license_policy_rejects_known_incompatible_expressions(expression: str) -> None:
    assert not license_is_allowed(expression)


def test_license_normalization_prefers_spdx_and_supports_classifier_fallback() -> None:
    spdx = FakeMetadata(
        {
            "License-Expression": "MIT AND PSF-2.0",
            "License": "unexpected legacy text",
        }
    )
    assert normalized_license(spdx) == "MIT AND PSF-2.0"

    classifier = FakeMetadata(classifiers=["License :: OSI Approved :: Apache Software License"])
    assert normalized_license(classifier) == "Apache-2.0"

    unknown = FakeMetadata()
    assert normalized_license(unknown) == "UNKNOWN"


def test_pnpm_inventory_uses_only_package_version_and_license() -> None:
    secret_path = "C:/Users/example/private-token-value/node_modules/react"
    records = flatten_pnpm_inventory(
        {
            "MIT": [
                {
                    "name": "react",
                    "versions": ["19.2.0"],
                    "license": "MIT",
                    "paths": [secret_path],
                    "author": "ignored",
                }
            ]
        }
    )

    assert records == (LicenseRecord("node", "react", "19.2.0", "MIT"),)
    assert secret_path not in records[0].render()


def test_cli_suppresses_dependency_metadata_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "not-a-real-secret-value"

    def fail_inventory(_repository_root: Path) -> tuple[LicenseRecord, ...]:
        raise RuntimeError(secret)

    monkeypatch.setattr(license_gate, "python_license_inventory", fail_inventory)

    assert license_gate.main(["--python-only"]) == 1
    output = capsys.readouterr().out
    assert output == "python:dependency-metadata-error=RuntimeError\n"
    assert secret not in output


def test_ci_and_canonical_verification_run_both_license_inventories() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    verification = (REPOSITORY_ROOT / "scripts" / "verify.ps1").read_text(encoding="utf-8")

    assert "uv sync --frozen --all-extras --python 3.13.13" in workflow
    assert "scripts/check_dependency_licenses.py --python-only" in workflow
    assert "scripts/check_dependency_licenses.py --node-only" in workflow
    assert "Python dependency license policy" in verification
    assert "Frontend dependency license policy" in verification


def test_notices_keep_container_and_os_package_review_limits_explicit() -> None:
    notices = (REPOSITORY_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert "OS-package SBOM" in notices
    assert "not generated" in notices
    assert "GROBID" in notices
    assert "accepted limitations" in notices
    assert "publication blocker" in notices
