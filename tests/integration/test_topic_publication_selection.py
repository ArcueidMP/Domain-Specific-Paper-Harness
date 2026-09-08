# pyright: reportPrivateUsage=false

"""Publication deduplication is topic-scoped while paper analysis remains shared."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from tests.fakes import FakeArxiv
from tests.integration.test_m4_postgres_repository import NOW, _prepare_complete_source

from paper_harness.adapters.postgres import PostgresRepository
from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.application.publish_product import PublishProduct
from paper_harness.domain.models import RunStatus, TopicConfig
from paper_harness.domain.reports import ReportNarrativeMode
from paper_harness.entrypoints.runtime import _pipeline_selection, _selection_candidates
from paper_harness.ports.arxiv import ArxivPaperRecord

pytestmark = pytest.mark.integration


def test_published_version_is_excluded_only_from_its_own_topics_selection(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    arxiv_record_v1 = replace(arxiv_record_v1, updated_at=NOW)
    _, logical_date = _prepare_complete_source(postgres_repository, topic_config, arxiv_record_v1)
    publisher = PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=1, minutes=10),
    )
    published = publisher.execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
    )
    assert published.status is RunStatus.COMPLETE
    replayed = publisher.execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
    )
    assert replayed.id == published.id

    other_topic = replace(
        topic_config,
        id=UUID("eadc31d0-a4cd-49a9-8a1c-f74c6d7f4838"),
        slug="overlapping-research",
        name="Overlapping Research",
    )
    other_ingestion = IngestArxiv(
        arxiv=FakeArxiv((arxiv_record_v1,)),
        repository=postgres_repository,
        clock=lambda: NOW + timedelta(days=1, minutes=20),
    ).execute(other_topic, logical_date=logical_date)
    source_ingestion = postgres_repository.get_run_for_date(topic_config.id, logical_date)
    assert source_ingestion is not None

    for topic, ingestion, expected_count in (
        (topic_config, source_ingestion, 0),
        (other_topic, other_ingestion, 1),
    ):
        detail = postgres_repository.get_run(ingestion.id)
        assert detail is not None
        candidates = _selection_candidates(postgres_repository, detail)
        assert len(candidates) == 1
        selection = _pipeline_selection(
            postgres_repository,
            detail,
            topic=topic,
            candidates=candidates,
            limit=1,
        )
        assert len(selection.selected) == expected_count
        assert selection.selected == (candidates if expected_count else ())

    detail = postgres_repository.get_run(other_ingestion.id)
    assert detail is not None
    candidate = _selection_candidates(postgres_repository, detail)[0]
    source_analysis = postgres_repository.get_paper_analysis(
        candidate.paper_id,
        paper_version_id=candidate.paper_version_id,
    )
    assert source_analysis is not None
    analysis = source_analysis.analysis
    assert postgres_repository.get_reusable_analyzed_paper_version_ids(
        (candidate.paper_version_id,),
        analysis_scope=analysis.analysis_scope,
        provider=analysis.provider,
        configured_model=analysis.configured_model,
        prompt_version=analysis.prompt_version,
        parser_name=source_analysis.parser_name,
        parser_version=source_analysis.parser_version,
    ) == frozenset({candidate.paper_version_id})
