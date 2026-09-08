# pyright: reportPrivateUsage=false

"""Upgrade populated publication data and verify the new persistence boundaries."""

from __future__ import annotations

from argparse import Namespace
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, insert, text
from sqlalchemy.exc import IntegrityError
from tests.integration.test_m4_postgres_repository import NOW, _prepare_complete_source

from paper_harness.adapters.postgres import PostgresRepository
from paper_harness.adapters.postgres.models import ReportEnrichmentFailureRow
from paper_harness.adapters.postgres.repository import EXPECTED_DATABASE_REVISION
from paper_harness.application.publish_product import PublishProduct
from paper_harness.domain.models import TopicConfig
from paper_harness.domain.reports import ReportNarrativeMode
from paper_harness.ports.arxiv import ArxivDiscoveryProgress, ArxivPaperRecord
from paper_harness.ports.repository import MigrationIncompatibleError

pytestmark = pytest.mark.integration


def _legacy_rows(engine: Engine) -> tuple[tuple[object, ...], ...]:
    tables = ("papers", "paper_versions", "paper_analyses", "evidence", "reports", "run_items")
    with engine.connect() as connection:
        return tuple(
            tuple(connection.scalars(text(f"SELECT row_to_json(t) FROM {table} t ORDER BY id")))
            for table in tables
        )


def test_populated_m6_upgrade_preserves_publications_and_enforces_new_boundaries(
    postgres_repository: PostgresRepository,
    postgres_engine: Engine,
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
) -> None:
    record = replace(arxiv_record_v1, updated_at=NOW)
    _, logical_date = _prepare_complete_source(postgres_repository, topic_config, record)
    PublishProduct(
        repository=postgres_repository,
        llm=None,
        clock=lambda: NOW + timedelta(days=1, minutes=10),
    ).execute(
        topic_config,
        narrative_mode=ReportNarrativeMode.STRUCTURED_ONLY,
        logical_date=logical_date,
    )
    publication = postgres_repository.get_product_run(
        logical_date=logical_date, topic_slug=topic_config.slug
    )
    assert publication is not None and publication.report is not None
    paper_item = publication.items[0].item
    report_id = publication.report.report.id
    config = Config(str(Path("alembic.ini").resolve()))
    baseline = _legacy_rows(postgres_engine)
    assert all(baseline)

    try:
        command.downgrade(config, "0006_topic_reprocessing")
        assert _legacy_rows(postgres_engine) == baseline
        with pytest.raises(MigrationIncompatibleError):
            postgres_repository.check_ready()
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT to_regclass('arxiv_discovery_progress')")) is None
            assert (
                connection.scalar(text("SELECT to_regclass('report_enrichment_failures')")) is None
            )

        command.upgrade(config, "head")
        command.check(config)
        postgres_repository.check_ready()
        assert _legacy_rows(postgres_engine) == baseline
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM arxiv_discovery_progress")) == 0
            assert connection.scalar(text("SELECT count(*) FROM report_enrichment_failures")) == 0

        next_day = NOW + timedelta(days=2)
        ingestion = postgres_repository.start_ingestion_run(
            topic_id=topic_config.id,
            logical_date=next_day.date(),
            started_at=next_day,
            cursor_from=next_day - timedelta(days=1),
            cursor_to=next_day,
        )
        progress = ArxivDiscoveryProgress(
            query="Migration fixture discovery",
            categories=topic_config.categories,
            day=next_day.date(),
            resumption_token="fixture-resumption-token",
            pending_ids=(record.canonical_arxiv_id,),
        )
        postgres_repository.persist_ingestion_progress(
            topic=topic_config,
            run_id=ingestion.id,
            progress=progress,
            records=(),
            persisted_at=next_day,
        )
        assert postgres_repository.get_ingestion_progress(ingestion.id) == progress
        invalid_progress_updates = (
            "complete = true",
            "category_index = cardinality(categories)",
            "pending_ids = array_fill('2601.01234'::text, ARRAY[10001])",
        )
        with postgres_engine.begin() as connection:
            for assignment in invalid_progress_updates:
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        text(
                            f"UPDATE arxiv_discovery_progress SET {assignment} WHERE run_id = :id"
                        ),
                        {"id": ingestion.id},
                    )
        with pytest.raises(RuntimeError, match="Ingestion downgrade refused"):
            command.downgrade(config, "0006_topic_reprocessing")
        postgres_repository.check_ready()
        assert postgres_repository.get_ingestion_progress(ingestion.id) == progress
        postgres_repository.persist_ingestion_progress(
            topic=topic_config,
            run_id=ingestion.id,
            progress=replace(progress, pending_ids=(), resumption_token=None, complete=True),
            records=(),
            persisted_at=next_day,
        )

        failure = {
            "id": uuid4(),
            "report_id": report_id,
            "failed_stage": "GRAPH_EXTRACTION",
            "paper_id": paper_item.paper_id,
            "paper_version_id": paper_item.paper_version_id,
            "error_code": "GRAPH_OUTPUT_INVALID",
            "retryable": False,
            "error_detail": "Migration fixture diagnostic.",
            "schema_version": 1,
            "created_at": next_day,
        }
        trend_failure = {
            **failure,
            "id": uuid4(),
            "failed_stage": "TREND_AGGREGATION",
            "paper_id": None,
            "paper_version_id": None,
        }
        with postgres_engine.begin() as connection:
            connection.execute(insert(ReportEnrichmentFailureRow), [failure, trend_failure])
            invalid_failures = (
                {**failure, "id": uuid4(), "paper_id": uuid4()},
                {**failure, "id": uuid4(), "report_id": uuid4()},
                {**failure, "id": uuid4(), "paper_id": None, "paper_version_id": None},
                {**trend_failure, "id": uuid4()},
            )
            for invalid in invalid_failures:
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(insert(ReportEnrichmentFailureRow).values(**invalid))
        with pytest.raises(RuntimeError, match="allow_enrichment_data_loss"):
            command.downgrade(config, "0007_ingestion_progress")
        postgres_repository.check_ready()
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM report_enrichment_failures")) == 2

        config.cmd_opts = Namespace(x=["allow_enrichment_data_loss=true"])
        command.downgrade(config, "0006_topic_reprocessing")
        command.upgrade(config, "head")
        postgres_repository.check_ready()
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                EXPECTED_DATABASE_REVISION
            )
            assert connection.scalar(text("SELECT count(*) FROM report_enrichment_failures")) == 0
    finally:
        command.upgrade(config, "head")
