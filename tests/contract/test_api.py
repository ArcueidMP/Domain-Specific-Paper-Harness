# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeArxiv, FakeRepository

from paper_harness.application.analyze_papers import build_analysis_bundle
from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.application.read_models import AnalysisDetail, PaperDetail
from paper_harness.domain.analysis import (
    AnalysisPassage,
    AnalysisRequest,
    AnalysisScope,
    ClaimType,
    EvidenceType,
    GeneratedAnalysis,
    GeneratedClaim,
    GeneratedEvidence,
    ModelUsage,
)
from paper_harness.domain.identity import stable_paper_id, stable_paper_version_id
from paper_harness.domain.models import Paper, PaperVersion, TopicConfig
from paper_harness.entrypoints.api import create_app
from paper_harness.ports.arxiv import ArxivPaperRecord
from paper_harness.ports.repository import MigrationIncompatibleError


def _paper(record: ArxivPaperRecord) -> Paper:
    return Paper(
        id=stable_paper_id(record.canonical_arxiv_id),
        canonical_arxiv_id=record.canonical_arxiv_id,
        title=record.title,
        abstract=record.abstract,
        current_version=record.version,
        first_submitted_at=record.submitted_at,
        latest_updated_at=record.updated_at,
        primary_category=record.primary_category,
        categories=record.categories,
        authors=record.authors,
        pdf_url=record.pdf_url,
        schema_version=1,
        created_at=datetime(2026, 1, 10, 5, tzinfo=UTC),
    )


def _paper_version(record: ArxivPaperRecord, paper: Paper) -> PaperVersion:
    return PaperVersion(
        id=stable_paper_version_id(record.canonical_arxiv_id, record.version),
        paper_id=paper.id,
        canonical_arxiv_id=record.canonical_arxiv_id,
        version=record.version,
        title=record.title,
        abstract=record.abstract,
        submitted_at=record.submitted_at,
        updated_at=record.updated_at,
        primary_category=record.primary_category,
        categories=record.categories,
        authors=record.authors,
        pdf_url=record.pdf_url,
        source_url=record.source_url,
        schema_version=1,
        created_at=datetime(2026, 1, 10, 5, tzinfo=UTC),
    )


def _analysis_detail(record: ArxivPaperRecord, paper: Paper) -> AnalysisDetail:
    version = _paper_version(record, paper)
    generated_at = datetime(2026, 1, 10, 5, 1, tzinfo=UTC)
    request = AnalysisRequest(
        paper_id=paper.id,
        paper_version_id=version.id,
        canonical_arxiv_id=record.canonical_arxiv_id,
        arxiv_version=record.version,
        title=record.title,
        scope=AnalysisScope.ABSTRACT_ONLY,
        passages=(AnalysisPassage(id="abstract", section="Abstract", text=record.abstract),),
    )
    generated = GeneratedAnalysis(
        provider="deepseek",
        configured_model="deepseek-v4-flash",
        model_version="DeepSeek-V4-Flash-2026-04-24",
        prompt_version="m2-analysis-v1",
        generated_at=generated_at,
        summary="The paper evaluates a tool-using language model agent.",
        research_problem="Tool-using agents require reliable evaluation.",
        method_summary="The authors evaluate a tool-using agent.",
        key_contributions=("A focused agent evaluation.",),
        limitations=("The abstract does not describe every benchmark.",),
        claims=(
            GeneratedClaim(
                key="method_1",
                claim_type=ClaimType.METHOD,
                text="The paper evaluates a tool-using language model agent.",
            ),
        ),
        evidence=(
            GeneratedEvidence(
                key="evidence_1",
                claim_keys=("method_1",),
                passage_id="abstract",
                excerpt="evaluate a tool-using language model agent",
                evidence_type=EvidenceType.SUPPORTS,
            ),
        ),
        usage=ModelUsage(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            call_count=1,
            duration_ms=800,
            estimated_cost_usd=None,
        ),
    )
    bundle = build_analysis_bundle(request, generated, created_at=generated_at)
    return AnalysisDetail(
        analysis=bundle.analysis,
        arxiv_version=record.version,
        claims=bundle.claims,
        evidence=bundle.evidence,
    )


def test_m1_read_api_exposes_persisted_topics_papers_and_latest_run(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    now = datetime(2026, 1, 10, 5, tzinfo=UTC)
    repository = FakeRepository()
    repository.papers = (_paper(arxiv_record_v1),)
    IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)), repository=repository, clock=lambda: now
    ).execute(topic_config, logical_date=date(2026, 1, 10))
    app = create_app(repository)
    client = TestClient(app)

    assert client.get("/health/live").json() == {"status": "alive"}
    assert client.get("/health/ready").json() == {
        "status": "ready",
        "database": "ready",
        "migrations": "current",
    }
    topics = client.get("/api/v1/topics").json()
    assert topics["total"] == 1
    assert topics["items"][0]["slug"] == "broad-llm-agents"
    papers = client.get("/api/v1/papers?limit=20&offset=0").json()
    assert papers["total"] == 1
    assert papers["items"][0]["canonical_arxiv_id"] == "2601.01234"
    run = client.get("/api/v1/runs/latest").json()
    assert run["status"] == "COMPLETE"
    assert run["analysis_scope"] is None
    assert run["items"][0]["stage"] == "NORMALIZED"


def test_readiness_reports_incompatible_migration() -> None:
    repository = FakeRepository()
    repository.ready_error = MigrationIncompatibleError("database revision is behind")
    response = TestClient(create_app(repository)).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MIGRATION_INCOMPATIBLE"


def test_m2_analysis_and_evidence_contracts_expose_scope_provenance_and_links(
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    repository = FakeRepository()
    paper = _paper(arxiv_record_v1)
    version = _paper_version(arxiv_record_v1, paper)
    repository.paper_detail = PaperDetail(
        paper=paper,
        versions=(version,),
        source_identities=(),
        topic_slugs=("broad-llm-agents",),
    )
    repository.analysis_detail = _analysis_detail(arxiv_record_v1, paper)
    app = create_app(repository)
    client = TestClient(app)

    analysis_response = client.get(
        f"/api/v1/papers/{paper.id}/analysis?paper_version_id={version.id}"
    )
    assert analysis_response.status_code == 200
    analysis = analysis_response.json()
    assert analysis["paper_version_id"] == str(version.id)
    assert analysis["arxiv_version"] == 1
    assert analysis["analysis_scope"] == "ABSTRACT_ONLY"
    assert analysis["parsed_paper_id"] is None
    assert analysis["parser_name"] is None
    assert analysis["parser_version"] is None
    assert analysis["provider"] == "deepseek"
    assert analysis["configured_model"] == "deepseek-v4-flash"
    assert analysis["prompt_version"] == "m2-analysis-v1"
    assert analysis["verification_status"] == "UNVERIFIED"
    assert analysis["claims"][0]["claim_type"] == "METHOD"

    evidence_response = client.get(
        f"/api/v1/papers/{paper.id}/evidence?analysis_id={analysis['id']}"
        f"&paper_version_id={version.id}&scope=ABSTRACT_ONLY"
    )
    assert evidence_response.status_code == 200
    evidence = evidence_response.json()
    assert evidence["total"] == 1
    assert evidence["items"][0]["section"] == "Abstract"
    assert evidence["items"][0]["extraction_source"] == "arxiv_abstract"
    assert evidence["items"][0]["supported_claim_ids"] == [analysis["claims"][0]["id"]]
    mismatched_scope = client.get(
        f"/api/v1/papers/{paper.id}/evidence?analysis_id={analysis['id']}&scope=FULL_TEXT"
    )
    assert mismatched_scope.status_code == 404
    assert mismatched_scope.json()["detail"]["code"] == "ANALYSIS_NOT_FOUND"

    openapi = app.openapi()
    for path in (
        "/api/v1/papers/{paper_id}/analysis",
        "/api/v1/papers/{paper_id}/evidence",
    ):
        responses = openapi["paths"][path]["get"]["responses"]
        for status_code in ("404", "503"):
            assert responses[status_code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ApiErrorResponse"
            }


def test_analysis_404_and_empty_evidence_are_distinct_for_an_existing_paper(
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    repository = FakeRepository()
    paper = _paper(arxiv_record_v1)
    repository.paper_detail = PaperDetail(
        paper=paper,
        versions=(_paper_version(arxiv_record_v1, paper),),
        source_identities=(),
        topic_slugs=(),
    )
    client = TestClient(create_app(repository))

    missing_analysis = client.get(f"/api/v1/papers/{paper.id}/analysis")
    assert missing_analysis.status_code == 404
    assert missing_analysis.json()["detail"]["code"] == "ANALYSIS_NOT_FOUND"
    missing_evidence = client.get(
        f"/api/v1/papers/{paper.id}/evidence?analysis_id=00000000-0000-0000-0000-000000000000"
    )
    assert missing_evidence.status_code == 404
    assert missing_evidence.json()["detail"]["code"] == "ANALYSIS_NOT_FOUND"


def test_read_api_starts_without_deepseek_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert TestClient(create_app(FakeRepository())).get("/health/live").status_code == 200


def test_unknown_paper_analysis_returns_paper_not_found() -> None:
    paper_id = UUID("e54e4c7c-e0b1-4c0b-a416-67a63b949b67")
    response = TestClient(create_app(FakeRepository())).get(f"/api/v1/papers/{paper_id}/analysis")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PAPER_NOT_FOUND"


def test_checked_in_openapi_is_generated_from_fastapi() -> None:
    expected = json.loads(Path("apps/api/openapi.json").read_text(encoding="utf-8"))
    assert create_app(FakeRepository()).openapi() == expected
