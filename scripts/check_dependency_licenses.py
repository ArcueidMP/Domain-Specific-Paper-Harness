"""Report dependency licenses and block only known incompatible terms."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Protocol

try:
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
except ImportError:  # pragma: no cover - exercised by the CLI before dependency sync
    Requirement = None  # type: ignore[assignment,misc]
    default_environment = None  # type: ignore[assignment]
    canonicalize_name = None  # type: ignore[assignment]


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIRST_PARTY_DISTRIBUTION = "domain-specific-paper-harness"
FIRST_PARTY_EXTRAS = frozenset({"specter2"})

LICENSE_OPERATORS = frozenset({"AND", "OR", "WITH"})
GPL_LINKING_EXCEPTIONS = frozenset({"Classpath-exception-2.0"})
INCOMPATIBLE_LICENSE_PREFIXES = (
    "AGPL-",
    "BUSL-",
    "GPL-",
    "LicenseRef-Proprietary",
    "SSPL-",
)
INCOMPATIBLE_LICENSE_IDENTIFIERS = frozenset(
    {
        "Commons-Clause",
        "Elastic-License",
        "PolyForm",
        "SSPL",
    }
)

CLASSIFIER_LICENSES = {
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: BSD License": "BSD",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
}
LEGACY_LICENSES = {
    "Apache 2.0 License": "Apache-2.0",
    "Apache License 2.0": "Apache-2.0",
    "BSD License": "BSD",
    "ISC License": "ISC",
}


@dataclass(frozen=True, order=True)
class LicenseRecord:
    ecosystem: str
    package: str
    version: str
    license: str

    def render(self) -> str:
        separator = "==" if self.ecosystem == "python" else "@"
        return f"{self.ecosystem}:{self.package}{separator}{self.version} license={self.license}"


class DistributionLike(Protocol):
    version: str
    requires: list[str] | None
    metadata: LicenseMetadata


class LicenseMetadata(Protocol):
    def get(self, name: str) -> str | None: ...

    def get_all(self, name: str) -> list[str] | None: ...


def _normalize_package_name(name: str) -> str:
    if canonicalize_name is None:
        return re.sub(r"[-_.]+", "-", name).lower()
    return str(canonicalize_name(name))


def normalized_license(distribution_metadata: LicenseMetadata) -> str:
    expression = distribution_metadata.get("License-Expression")
    if expression and expression.strip():
        return expression.strip()

    legacy = distribution_metadata.get("License")
    if legacy and legacy.strip():
        value = legacy.strip()
        return LEGACY_LICENSES.get(value, value)

    for classifier in distribution_metadata.get_all("Classifier") or []:
        if classifier in CLASSIFIER_LICENSES:
            return CLASSIFIER_LICENSES[classifier]
    return "UNKNOWN"


def license_is_allowed(license_expression: str) -> bool:
    if not license_expression.strip():
        return True

    for alternative in re.split(
        r"(?<![A-Za-z0-9.+-])OR(?![A-Za-z0-9.+-])",
        license_expression,
        flags=re.IGNORECASE,
    ):
        identifiers = {
            token
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", alternative)
            if token.upper() not in LICENSE_OPERATORS
        }
        incompatible = {
            identifier
            for identifier in identifiers
            if identifier in INCOMPATIBLE_LICENSE_IDENTIFIERS
            or identifier.startswith(INCOMPATIBLE_LICENSE_PREFIXES)
        }
        if not incompatible:
            return True
        if (
            incompatible
            and all(identifier.startswith("GPL-") for identifier in incompatible)
            and identifiers & GPL_LINKING_EXCEPTIONS
        ):
            return True
    return False


def _require_packaging() -> None:
    if Requirement is None or default_environment is None or canonicalize_name is None:
        raise RuntimeError("packaging")


def python_license_inventory(repository_root: Path) -> tuple[LicenseRecord, ...]:
    """Return the installed production dependency closure, including SPECTER2."""

    _require_packaging()
    active_extras: dict[str, set[str]] = {
        _normalize_package_name(FIRST_PARTY_DISTRIBUTION): set(FIRST_PARTY_EXTRAS)
    }
    pending = [_normalize_package_name(FIRST_PARTY_DISTRIBUTION)]
    processed_extras: dict[str, frozenset[str]] = {}
    distributions: dict[str, DistributionLike] = {}
    environment = default_environment()

    while pending:
        package_name = pending.pop()
        extras = frozenset(active_extras[package_name])
        if processed_extras.get(package_name) == extras:
            continue
        try:
            distribution = metadata.distribution(package_name)
        except metadata.PackageNotFoundError as exc:
            raise RuntimeError(package_name) from exc
        distributions[package_name] = distribution
        processed_extras[package_name] = extras

        for raw_requirement in distribution.requires or []:
            requirement = Requirement(raw_requirement)
            marker_extras = {"", *extras}
            applies = requirement.marker is None or any(
                requirement.marker.evaluate({**environment, "extra": extra})
                for extra in marker_extras
            )
            if not applies:
                continue
            dependency_name = _normalize_package_name(requirement.name)
            dependency_extras = active_extras.setdefault(dependency_name, set())
            previous = frozenset(dependency_extras)
            dependency_extras.update(requirement.extras)
            if dependency_name not in processed_extras or previous != dependency_extras:
                pending.append(dependency_name)

    records: list[LicenseRecord] = []
    for package_name, distribution in distributions.items():
        if package_name == _normalize_package_name(FIRST_PARTY_DISTRIBUTION):
            continue
        display_name = distribution.metadata.get("Name") or package_name
        records.append(
            LicenseRecord(
                ecosystem="python",
                package=display_name,
                version=distribution.version,
                license=normalized_license(distribution.metadata),
            )
        )
    return tuple(
        sorted(
            records,
            key=lambda record: (_normalize_package_name(record.package), record.version),
        )
    )


def _pnpm_command() -> list[str]:
    arguments = ["corepack", "pnpm", "licenses", "list", "--prod", "--json"]
    if os.name != "nt":
        return arguments
    command_processor = os.environ.get("COMSPEC", "cmd.exe")
    return [command_processor, "/d", "/s", "/c", subprocess.list2cmdline(arguments)]


def flatten_pnpm_inventory(payload: object) -> tuple[LicenseRecord, ...]:
    if not isinstance(payload, dict):
        raise RuntimeError("pnpm-license-metadata")
    records: set[LicenseRecord] = set()
    for group_license, packages in payload.items():
        if not isinstance(group_license, str) or not isinstance(packages, list):
            raise RuntimeError("pnpm-license-metadata")
        for package in packages:
            if not isinstance(package, dict):
                raise RuntimeError("pnpm-license-metadata")
            name = package.get("name")
            versions = package.get("versions")
            package_license = package.get("license", group_license)
            if (
                not isinstance(name, str)
                or not isinstance(versions, list)
                or not versions
                or not isinstance(package_license, str)
            ):
                raise RuntimeError("pnpm-license-metadata")
            for version in versions:
                if not isinstance(version, str):
                    raise RuntimeError("pnpm-license-metadata")
                records.add(
                    LicenseRecord(
                        ecosystem="node",
                        package=name,
                        version=version,
                        license=package_license.strip() or "UNKNOWN",
                    )
                )
    return tuple(sorted(records, key=lambda record: (record.package.casefold(), record.version)))


def node_license_inventory(repository_root: Path) -> tuple[LicenseRecord, ...]:
    """Read pnpm's frozen install metadata and return the production closure."""

    if not (repository_root / "node_modules" / ".pnpm").is_dir():
        raise RuntimeError("pnpm-install")

    environment = os.environ.copy()
    environment["CI"] = "true"
    environment["COREPACK_ENABLE_NETWORK"] = "0"
    environment["npm_config_offline"] = "true"
    result = subprocess.run(
        _pnpm_command(),
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    if result.returncode != 0:
        raise RuntimeError("pnpm-license-metadata")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("pnpm-license-metadata") from exc
    records = flatten_pnpm_inventory(payload)

    with (repository_root / "apps" / "web" / "package.json").open(encoding="utf-8") as stream:
        web_manifest = json.load(stream)
    direct_dependencies = web_manifest.get("dependencies", {})
    if not isinstance(direct_dependencies, dict):
        raise RuntimeError("web-production-dependencies")
    installed_names = {record.package for record in records}
    missing = sorted(set(direct_dependencies) - installed_names)
    if missing:
        raise RuntimeError(missing[0])
    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--python-only", action="store_true")
    mode.add_argument("--node-only", action="store_true")
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    return parser


def main(arguments: list[str] | None = None) -> int:
    options = build_parser().parse_args(arguments)
    records: list[LicenseRecord] = []
    failed = False

    if not options.node_only:
        try:
            records.extend(python_license_inventory(options.repository_root.resolve()))
        except Exception as error:
            print(f"python:dependency-metadata-error={type(error).__name__}")
            failed = True

    if not options.python_only:
        try:
            records.extend(node_license_inventory(options.repository_root.resolve()))
        except Exception as error:
            print(f"node:dependency-metadata-error={type(error).__name__}")
            failed = True

    for record in sorted(records):
        print(record.render())
        if not license_is_allowed(record.license):
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
