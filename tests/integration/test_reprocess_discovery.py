# pyright: reportPrivateUsage=false

"""Historical reprocessing retains its published exact versions as discovery changes."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from tests.fakes import FakeArxiv, fake_pipeline_execution_contract
from tests.integration.test_m4_postgres_repository import NOW, _prepare_complete_source

from paper_harness.adapters.postgres import PostgresRepository
from paper_harness.application.ingest_arxiv import IngestArxiv
from paper_harness.application.publish_product import PublishProduct
from paper_harness.domain.analysis import AnalysisScope
from paper_harness.domain.identity import stable_paper_id
from paper_harness.domain.models import (
    PipelineExecution,
    PipelineExecutionMode,
    RunStatus,
    TopicConfig,
)
from paper_harness.domain.reports import ReportNarrativeMode
from paper_harness.entrypoints.runtime import _pipeline_selection, _selection_candidates
from paper_harness.ports.arxiv import ArxivPaperRecord

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("identifier_still_listed", [False, True])
def test_reprocess_seeds_published_v1_when_live_discovery_only_knows_later_v2(
    postgres_repository: PostgresRepository,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
    identifier_still_listed: bool,
) -> None:
    record = replace(arxiv_record_v1, updated_at=NOW)
    _, logical_date = _prepare_complete_source(postgres_repository, topic_config, record)
    published = PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=1, minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
    )
    assert published.status is RunStatus.COMPLETE
    paper_id = stable_paper_id(record.canonical_arxiv_id)
    original = postgres_repository.get_paper(paper_id)
    assert original is not None
    published_v1 = original.versions[0]
    assert published_v1.version == 1

    later_time = NOW + timedelta(days=4)
    revised_record = replace(
        record,
        version=2,
        title="A Later Revised LLM Agent",
        abstract="A revised LLM agent with additional evaluation results.",
        updated_at=later_time,
        pdf_url=f"https://arxiv.org/pdf/{record.canonical_arxiv_id}v2",
        source_url=f"https://arxiv.org/abs/{record.canonical_arxiv_id}v2",
    )
    IngestArxiv(
        arxiv=FakeArxiv((revised_record,), discovery_day=later_time.date()),
        repository=postgres_repository,
        clock=lambda: later_time + timedelta(minutes=1),
    ).execute(topic_config, logical_date=later_time.date())
    current = postgres_repository.get_paper(paper_id)
    assert current is not None and current.paper.current_version == 2
    cursor_before = postgres_repository.get_ingestion_cursor(topic_config.id)

    reprocess_started = later_time + timedelta(minutes=10)
    execution_id = uuid4()
    postgres_repository.start_pipeline_execution(
        PipelineExecution(
            id=execution_id,
            topic_id=topic_config.id,
            logical_date=logical_date,
            execution_mode=PipelineExecutionMode.REPROCESS,
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
            selection_limit=1,
            contract=fake_pipeline_execution_contract(),
            status=RunStatus.RUNNING,
            deadline_at=reprocess_started + timedelta(hours=8),
            started_at=reprocess_started,
            completed_at=None,
            error_code=None,
            error_detail=None,
            schema_version=1,
            created_at=reprocess_started,
        )
    )
    historical_arxiv = FakeArxiv(
        (revised_record,),
        discovery_day=logical_date if identifier_still_listed else later_time.date(),
    )
    ingestion = IngestArxiv(
        arxiv=historical_arxiv,
        repository=postgres_repository,
        clock=lambda: reprocess_started,
    ).execute(
        topic_config,
        logical_date=logical_date,
        pipeline_execution_mode=PipelineExecutionMode.REPROCESS,
        pipeline_selection_limit=1,
        pipeline_execution_id=execution_id,
    )
    assert ingestion.status is RunStatus.COMPLETE
    detail = postgres_repository.get_run(ingestion.id)
    assert detail is not None
    candidates = _selection_candidates(postgres_repository, detail)
    assert tuple(candidate.paper_version_id for candidate in candidates) == (published_v1.id,)
    assert candidates[0].title == published_v1.title
    assert candidates[0].abstract == published_v1.abstract
    assert candidates[0].updated_at == published_v1.updated_at
    selection = _pipeline_selection(
        postgres_repository,
        detail,
        topic=topic_config,
        candidates=candidates,
        limit=1,
    )
    assert selection.selected == candidates
    assert postgres_repository.get_ingestion_cursor(topic_config.id) == cursor_before
    after = postgres_repository.get_paper(paper_id)
    assert after is not None and after.paper.current_version == 2
    assert (
        next(version for version in after.versions if version.id == published_v1.id) == published_v1
    )
    assert after.versions == current.versions
