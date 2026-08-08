# pyright: reportPrivateUsage=false

from __future__ import annotations

import pytest

from paper_harness.domain.analysis import AnalysisScope
from paper_harness.entrypoints.runtime import _grobid_parser


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
