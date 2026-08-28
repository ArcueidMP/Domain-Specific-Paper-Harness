"""Checks for the public source release and private runtime distribution boundary."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DOCKER_DIRECTORY = REPOSITORY_ROOT / "infra" / "docker"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_first_party_package_metadata_uses_apache_2_and_node_packages_remain_private() -> None:
    python_project = tomllib.loads(_read(REPOSITORY_ROOT / "pyproject.toml"))["project"]
    root_node = json.loads(_read(REPOSITORY_ROOT / "package.json"))
    web_node = json.loads(_read(REPOSITORY_ROOT / "apps" / "web" / "package.json"))

    assert python_project["license"] == "Apache-2.0"
    assert python_project["license-files"] == ["LICENSE"]
    for package in (root_node, web_node):
        assert package["license"] == "Apache-2.0"
        assert package["private"] is True


def test_first_party_license_is_labeled_and_retained_in_every_runtime_image() -> None:
    for name in ("Dockerfile.api", "Dockerfile.daily", "Dockerfile.grobid"):
        dockerfile = _read(DOCKER_DIRECTORY / name)

        assert 'org.opencontainers.image.licenses="Apache-2.0"' in dockerfile
        assert "LICENSE /opt/licenses/paper-harness/LICENSE" in dockerfile
        assert "THIRD_PARTY_NOTICES.md /opt/licenses/paper-harness/THIRD_PARTY_NOTICES.md" in (
            dockerfile
        )


def test_public_source_release_excludes_private_images_and_hosted_demo() -> None:
    notices = _read(REPOSITORY_ROOT / "THIRD_PARTY_NOTICES.md")
    normalized_notices = " ".join(notices.split()).lower()
    reuse_register = yaml.safe_load(_read(REPOSITORY_ROOT / "docs" / "reuse-register.yaml"))
    source_release = reuse_register["license_review"]["source_release"]

    assert source_release["first_party_license"] == "Apache-2.0"
    assert source_release["distribution"] == "public GitHub source archive"
    assert source_release["container_images"] == "private"
    assert source_release["hosted_public_demo"] == "not included"
    assert "public source release" in notices
    assert "container images remain private" in normalized_notices
    assert "hosted public demo" in normalized_notices


def test_documented_dev_script_forwards_the_custom_web_port_to_vite() -> None:
    dev_script = _read(REPOSITORY_ROOT / "scripts" / "dev.ps1")
    verify_script = _read(REPOSITORY_ROOT / "scripts" / "verify.ps1")

    assert '"pnpm", "--filter", "@paper-harness/web", "dev"' in dev_script
    assert '"pnpm", "dev", "--", "--host"' not in dev_script
    assert "[int]$PostgresPort = 15432" in verify_script
