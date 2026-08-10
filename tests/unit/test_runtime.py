# pyright: reportPrivateUsage=false

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from paper_harness.domain.analysis import AnalysisScope
from paper_harness.entrypoints import runtime as runtime_module
from paper_harness.entrypoints.runtime import (
    _grobid_parser,
    _scholarly_retry_policy,
    execute_historical_backfill,
)
from paper_harness.ports.scholarly_search import ScholarlySearchConfigurationError
from paper_harness.ports.scientific_embedding import ScientificEmbeddingConfigurationError


def test_full_text_parser_rejects_unknown_application_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "prodution")
    monkeypatch.setenv("GROBID_URL", "http://grobid:8070")
    monkeypatch.setenv("GROBID_AUTH_MODE", "none")

    with pytest.raises(ValueError, match="APP_ENV must be"):
        _grobid_parser(AnalysisScope.FULL_TEXT)


def test_google_identity_audience_must_match_the_private_grobid_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("GROBID_URL", "https://paper-harness-grobid.example.run.app")
    monkeypatch.setenv("GROBID_AUDIENCE", "https://another-service.example.run.app")
    monkeypatch.setenv("GROBID_AUTH_MODE", "google_identity")

    with pytest.raises(ValueError, match="exactly match"):
        _grobid_parser(AnalysisScope.FULL_TEXT)


def test_production_full_text_parser_accepts_matching_google_identity_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("GROBID_URL", "https://paper-harness-grobid.example.run.app/")
    monkeypatch.setenv("GROBID_AUDIENCE", "https://paper-harness-grobid.example.run.app")
    monkeypatch.setenv("GROBID_AUTH_MODE", "google_identity")

    assert _grobid_parser(AnalysisScope.FULL_TEXT) is not None


def test_abstract_only_scope_does_not_require_unused_grobid_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROBID_URL", raising=False)
    monkeypatch.delenv("GROBID_AUDIENCE", raising=False)
    monkeypatch.delenv("GROBID_AUTH_MODE", raising=False)

    assert _grobid_parser(AnalysisScope.ABSTRACT_ONLY) is None


def test_historical_backfill_fails_before_database_work_without_semantic_scholar_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(
        ScholarlySearchConfigurationError,
        match="SEMANTIC_SCHOLAR_API_KEY",
    ):
        execute_historical_backfill(
            topic_config=Path("unused.yaml"),
            through=date(2026, 8, 9),
        )


def test_historical_backfill_requires_the_prepared_specter2_base_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "test-key")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(
        ScientificEmbeddingConfigurationError,
        match="artifact directory does not exist",
    ):
        execute_historical_backfill(
            topic_config=Path("unused.yaml"),
            through=date(2026, 8, 9),
        )


def test_specter2_model_path_can_be_selected_explicitly_for_local_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[str | Path] = []
    sentinel = object()

    def fake_load(model_path: str | Path = Path("/opt/models/specter2_base")) -> object:
        selected.append(model_path)
        return sentinel

    monkeypatch.setenv("SPECTER2_MODEL_PATH", "D:/models/specter2_base")
    monkeypatch.setattr(runtime_module, "load_specter2_encoder", fake_load)

    assert runtime_module._specter2_embeddings() is sentinel
    assert selected == ["D:/models/specter2_base"]


def test_related_work_semantic_scholar_policy_uses_the_operator_timeout() -> None:
    policy = _scholarly_retry_policy(7)

    assert policy.request_timeout_seconds == 7
    assert policy.total_timeout_seconds == 7
