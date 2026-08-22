from __future__ import annotations

from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DOCKER_DIRECTORY = REPOSITORY_ROOT / "infra" / "docker"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_first_party_images_are_labeled_non_root_and_runtime_appropriate() -> None:
    api = _read(DOCKER_DIRECTORY / "Dockerfile.api")
    daily = _read(DOCKER_DIRECTORY / "Dockerfile.daily")

    for dockerfile in (api, daily):
        assert "FROM python:3.13.13-slim-bookworm" in dockerfile
        assert "python:3.13.13-slim-bookworm@sha256:" in dockerfile
        assert "ghcr.io/astral-sh/uv:0.12.3@sha256:" in dockerfile
        assert "org.opencontainers.image.source=" in dockerfile
        assert "org.opencontainers.image.version=" in dockerfile
        assert "APP_ENV=production" in dockerfile
        assert "USER 10001:10001" in dockerfile
        assert "STOPSIGNAL SIGTERM" in dockerfile

    assert "HEALTHCHECK" in api
    assert "/health/live" in api
    assert "node:24.15.0-bookworm-slim@sha256:" in api
    assert "HEALTHCHECK" not in daily


def test_grobid_service_keeps_the_verified_runtime_and_health_contract() -> None:
    dockerfile = _read(DOCKER_DIRECTORY / "Dockerfile.grobid")

    assert (
        "grobid/grobid:0.9.0-crf@sha256:"
        "24ba90eb1c959f65d812bcdb2cf79c677fa5fd7b95235de616b8bc9fa1317849"
    ) in dockerfile
    assert 'org.opencontainers.image.revision="b2251cb"' in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/api/health" in dockerfile
    assert "groupadd --system --gid 10001 paper-harness" in dockerfile
    assert "useradd --system --uid 10001 --gid paper-harness" in dockerfile
    assert "/opt/grobid/grobid-home/tmp /opt/grobid/logs" in dockerfile
    assert "install -d -o 10001 -g 10001 -m 0700" in dockerfile
    assert "USER 10001:10001" in dockerfile


def test_compose_exposes_only_loopback_ports_and_bounds_local_runtimes() -> None:
    compose = yaml.safe_load(_read(REPOSITORY_ROOT / "compose.yaml"))
    services = compose["services"]

    assert services["db"]["ports"] == ["127.0.0.1:${POSTGRES_PORT:-5432}:5432"]
    assert services["api"]["ports"] == ["127.0.0.1:${API_PORT:-8000}:8080"]
    assert services["grobid"]["ports"] == ["127.0.0.1:${GROBID_PORT:-8070}:8070"]

    for name in ("api", "daily", "grobid"):
        service = services[name]
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["pids_limit"] > 0
        assert service["cpus"] > 0
        assert service["mem_limit"]
        assert service["tmpfs"]

    assert services["grobid"]["user"] == "10001:10001"
    assert services["grobid"]["tmpfs"] == [
        "/tmp:rw,noexec,nosuid,size=256m,uid=10001,gid=10001,mode=0700",
        ("/opt/grobid/grobid-home/tmp:rw,noexec,nosuid,size=512m,uid=10001,gid=10001,mode=0700"),
        ("/opt/grobid/logs:rw,noexec,nosuid,size=64m,uid=10001,gid=10001,mode=0700"),
    ]


def test_docker_context_excludes_local_secrets_state_and_large_artifacts() -> None:
    excluded = {
        line.strip()
        for line in _read(REPOSITORY_ROOT / ".dockerignore").splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert {
        ".git",
        ".env",
        "**/.env.*",
        "*.tfstate",
        "*.tfplan",
        "*.tfvars",
        "*.pem",
        "*.key",
        "*service-account*.json",
        ".cache",
        "models",
        "downloads",
        "backups",
        "exports",
        "*.pdf",
    } <= excluded
