# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeRepository

from paper_harness.entrypoints.api import create_app
from paper_harness.ports.repository import RepositoryPort


@pytest.fixture
def production_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    static_directory = tmp_path / "static"
    assets_directory = static_directory / "assets"
    assets_directory.mkdir(parents=True)
    (static_directory / "index.html").write_text(
        "<!doctype html><title>Paper Harness</title><main>application shell</main>",
        encoding="utf-8",
    )
    (assets_directory / "application.css").write_text(
        "body { color: #172033; }\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PAPER_HARNESS_STATIC_DIR", str(static_directory))
    return TestClient(create_app(cast(RepositoryPort, FakeRepository())))


def test_production_static_mount_serves_assets_and_client_routes(
    production_client: TestClient,
) -> None:
    asset = production_client.get("/assets/application.css")
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("text/css")
    assert asset.text.strip() == "body { color: #172033; }"

    for path in ("/", "/papers", "/reports/daily/2026-08-10"):
        response = production_client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "application shell" in response.text


@pytest.mark.parametrize(
    "path",
    (
        "/api",
        "/api/",
        "/api/v1/not-a-route",
        "/API/v1/not-a-route",
        "/health",
        "/health/",
        "/health/not-a-route",
    ),
)
def test_production_static_mount_never_rewrites_service_routes_to_the_spa(
    production_client: TestClient,
    path: str,
) -> None:
    response = production_client.get(path)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Not Found"}


def test_known_api_and_health_routes_take_precedence_over_the_static_mount(
    production_client: TestClient,
) -> None:
    liveness = production_client.get("/health/live")
    assert liveness.status_code == 200
    assert liveness.json() == {"status": "alive"}

    topics = production_client.get("/api/v1/topics")
    assert topics.status_code == 200
    assert topics.json() == {"items": [], "total": 0}
