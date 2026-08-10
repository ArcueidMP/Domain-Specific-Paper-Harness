"""Explicit opt-in real SPECTER2 Base and pgvector smoke test."""

from __future__ import annotations

import math
import os
import platform
from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from paper_harness.adapters.postgres import PostgresRepository, create_postgres_engine
from paper_harness.adapters.specter2 import (
    SPECTER2_DIMENSION,
    SPECTER2_EMBEDDING_SOURCE,
    SPECTER2_LOCAL_FILES_ONLY,
    SPECTER2_MAX_TOKENS,
    SPECTER2_MODEL_IDENTIFIER,
    SPECTER2_MODEL_PROVENANCE,
    SPECTER2_MODEL_REVISION,
    SPECTER2_PREPROCESSING_CONTRACT,
    SPECTER2_TOKENIZER_IDENTIFIER,
    SPECTER2_TOKENIZER_REVISION,
    SPECTER2_TRANSFORMERS_VERSION,
    SPECTER2_TRUST_REMOTE_CODE,
    SPECTER2_USE_SAFETENSORS,
    SPECTER2_WEIGHTS_ONLY,
    load_specter2_encoder,
)
from paper_harness.adapters.specter2.prepare import prepare_specter2_base
from paper_harness.domain.historical import (
    BackfillStatus,
    ExternalPaperStub,
    HistoricalBackfillRun,
    HistoricalCorpusEntry,
    ScientificEmbedding,
)
from paper_harness.domain.identity import (
    stable_embedding_id,
    stable_external_paper_id,
    stable_historical_backfill_id,
    stable_historical_corpus_entry_id,
)
from paper_harness.domain.models import TopicConfig
from paper_harness.ports.scientific_embedding import ScientificPaperText

pytestmark = [pytest.mark.integration, pytest.mark.live]


@pytest.fixture
def live_specter2_database() -> Generator[tuple[PostgresRepository, Engine]]:
    if os.environ.get("RUN_LIVE_SPECTER2_TEST") != "1":
        pytest.skip("set RUN_LIVE_SPECTER2_TEST=1 for the explicit SPECTER2 Base smoke test")
    if platform.python_version() != "3.13.13":
        pytest.fail("the real SPECTER2 Base smoke test requires exact CPython 3.13.13")
    database_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.fail("TEST_DATABASE_URL is required when RUN_LIVE_SPECTER2_TEST=1")

    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    config = Config(str(Path("alembic.ini").resolve()))
    command.upgrade(config, "head")
    engine = create_postgres_engine(database_url)
    try:
        yield PostgresRepository(engine), engine
    finally:
        engine.dispose()
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url


def test_real_specter2_base_model_and_pgvector_retrieval(
    live_specter2_database: tuple[PostgresRepository, Engine],
    tmp_path: Path,
    topic_config: TopicConfig,
) -> None:
    repository_root = Path.cwd().resolve()
    live_artifact_root = tmp_path.resolve()
    assert not live_artifact_root.is_relative_to(repository_root)

    prepared = prepare_specter2_base(
        live_artifact_root / "specter2_base",
        cache_dir=live_artifact_root / "huggingface-cache",
    )
    encoder = load_specter2_encoder(prepared.path)

    installed_transformers = version("transformers")
    assert installed_transformers == SPECTER2_TRANSFORMERS_VERSION
    assert tuple(int(part) for part in installed_transformers.split(".")[:3]) >= (5, 3, 0)
    assert encoder.transformers_version == SPECTER2_TRANSFORMERS_VERSION
    assert encoder.torch_version == "2.13.0"
    assert platform.python_version() == "3.13.13"
    assert encoder.model_identifier == SPECTER2_MODEL_IDENTIFIER == "allenai/specter2_base"
    assert encoder.model_revision == SPECTER2_MODEL_REVISION
    assert encoder.tokenizer_identifier == SPECTER2_TOKENIZER_IDENTIFIER
    assert encoder.tokenizer_revision == SPECTER2_TOKENIZER_REVISION
    assert encoder.dimension == SPECTER2_DIMENSION == 768
    assert encoder.max_input_length == SPECTER2_MAX_TOKENS == 512
    assert encoder.preprocessing_contract == SPECTER2_PREPROCESSING_CONTRACT
    assert encoder.model_provenance == SPECTER2_MODEL_PROVENANCE
    assert encoder.source == SPECTER2_EMBEDDING_SOURCE
    assert encoder.trust_remote_code is SPECTER2_TRUST_REMOTE_CODE is False
    assert encoder.local_files_only is SPECTER2_LOCAL_FILES_ONLY is True
    assert encoder.use_safetensors is SPECTER2_USE_SAFETENSORS is True
    assert encoder.weights_only is SPECTER2_WEIGHTS_ONLY is True
    assert encoder.artifact_path == prepared.path
    assert encoder.artifact_sha256 == prepared.model_safetensors_sha256
    assert encoder.artifact_generated_at == prepared.generated_at

    paper_inputs = (
        ScientificPaperText(
            key="agent-planning",
            title="Bounded Planning for Tool-Using Language Model Agents",
            abstract=(
                "We evaluate a bounded planner for language model agents that invoke tools "
                "under explicit search and execution limits."
            ),
        ),
        ScientificPaperText(
            key="protein-folding",
            title="Protein Folding with Geometric Molecular Representations",
            abstract=(
                "We predict three-dimensional protein structures using molecular geometry "
                "and amino-acid sequence representations."
            ),
        ),
    )
    first = encoder.encode(paper_inputs)
    second = encoder.encode(paper_inputs)
    assert len(first) == len(paper_inputs)
    assert all(len(item.vector) == SPECTER2_DIMENSION for item in first)
    assert all(math.isfinite(value) for item in first for value in item.vector)
    assert first == second

    repository, engine = live_specter2_database
    topic = replace(
        topic_config,
        id=uuid4(),
        slug=f"live-specter2-{uuid4().hex}",
        name="Live SPECTER2 Base Smoke",
    )
    repository.upsert_topic(topic)
    persisted_at = datetime.now(UTC).replace(microsecond=0)
    semantic_scholar_ids = ("1" * 40, "2" * 40)
    external_papers = tuple(
        ExternalPaperStub(
            id=stable_external_paper_id(semantic_scholar_id),
            semantic_scholar_id=semantic_scholar_id,
            title=paper.title,
            abstract=paper.abstract,
            year=2026,
            publication_date=date(2026, 1, index + 1),
            venue="Live smoke fixture",
            authors=("Paper Harness Smoke Test",),
            external_ids=(),
            arxiv_id=None,
            doi=None,
            citation_count=0,
            influential_citation_count=0,
            full_text_available=False,
            source="semantic_scholar",
            schema_version=1,
            created_at=persisted_at,
            updated_at=persisted_at,
        )
        for index, (semantic_scholar_id, paper) in enumerate(
            zip(semantic_scholar_ids, paper_inputs, strict=True)
        )
    )
    entries = tuple(
        HistoricalCorpusEntry(
            id=stable_historical_corpus_entry_id(topic.id, paper.id),
            topic_id=topic.id,
            external_paper_id=paper.id,
            local_paper_id=None,
            local_paper_version_id=None,
            representative_rank=None,
            first_seen_at=persisted_at,
            last_seen_at=persisted_at,
            schema_version=1,
        )
        for paper in external_papers
    )
    generated_by_key = {item.key: item for item in first}
    embeddings = tuple(
        ScientificEmbedding(
            id=stable_embedding_id(
                paper.id,
                model_identifier=encoder.model_identifier,
                model_revision=encoder.model_revision,
                tokenizer_identifier=encoder.tokenizer_identifier,
                tokenizer_revision=encoder.tokenizer_revision,
                dimension=encoder.dimension,
                preprocessing_contract=encoder.preprocessing_contract,
                model_provenance=encoder.model_provenance,
                source=encoder.source,
            ),
            paper_version_id=None,
            external_paper_id=paper.id,
            model_identifier=encoder.model_identifier,
            model_revision=encoder.model_revision,
            tokenizer_identifier=encoder.tokenizer_identifier,
            tokenizer_revision=encoder.tokenizer_revision,
            dimension=encoder.dimension,
            preprocessing_contract=encoder.preprocessing_contract,
            model_provenance=encoder.model_provenance,
            vector=generated_by_key[paper_input.key].vector,
            generated_at=prepared.generated_at,
            source=encoder.source,
            schema_version=1,
            created_at=persisted_at,
        )
        for paper, paper_input in zip(external_papers, paper_inputs, strict=True)
    )
    window_from = date(2025, 8, 1)
    window_to = date(2026, 2, 1)
    run = HistoricalBackfillRun(
        id=stable_historical_backfill_id(topic.id, window_from, window_to),
        topic_id=topic.id,
        window_from=window_from,
        window_to=window_to,
        query_plan=("live SPECTER2 Base smoke",),
        max_results_per_query=2,
        overall_timeout_seconds=600.0,
        embedding_model_identifier=encoder.model_identifier,
        embedding_model_revision=encoder.model_revision,
        embedding_tokenizer_identifier=encoder.tokenizer_identifier,
        embedding_tokenizer_revision=encoder.tokenizer_revision,
        embedding_dimension=encoder.dimension,
        embedding_preprocessing_contract=encoder.preprocessing_contract,
        embedding_model_provenance=encoder.model_provenance,
        embedding_source=encoder.source,
        status=BackfillStatus.RUNNING,
        next_query_index=0,
        discovered_count=0,
        persisted_count=0,
        representative_count=0,
        started_at=persisted_at,
        completed_at=None,
        error_code=None,
        error_detail=None,
        schema_version=1,
        created_at=persisted_at,
    )
    repository.start_historical_backfill(run)
    repository.persist_historical_backfill_page(
        run.id,
        expected_query_index=0,
        next_query_index=1,
        papers=external_papers,
        entries=entries,
        embeddings=embeddings,
        discovered_count=2,
        persisted_count=2,
        persisted_at=persisted_at,
    )

    matches = repository.search_historical_by_vector(
        topic.id,
        vector=first[0].vector,
        model_identifier=encoder.model_identifier,
        model_revision=encoder.model_revision,
        tokenizer_identifier=encoder.tokenizer_identifier,
        tokenizer_revision=encoder.tokenizer_revision,
        dimension=encoder.dimension,
        preprocessing_contract=encoder.preprocessing_contract,
        model_provenance=encoder.model_provenance,
        source=encoder.source,
        limit=2,
    )
    assert len(matches) == 2
    assert matches[0].external_paper.id == external_papers[0].id
    assert matches[0].score == pytest.approx(1.0)
    assert matches[0].score > matches[1].score

    with engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT model_identifier, model_revision, tokenizer_identifier, "
                "tokenizer_revision, dimension, preprocessing_contract, model_provenance, "
                "generated_at, source, "
                "vector_dims(vector) FROM scientific_embeddings "
                "WHERE external_paper_id = :external_paper_id"
            ),
            {"external_paper_id": external_papers[0].id},
        ).one()
    assert stored == (
        SPECTER2_MODEL_IDENTIFIER,
        SPECTER2_MODEL_REVISION,
        SPECTER2_TOKENIZER_IDENTIFIER,
        SPECTER2_TOKENIZER_REVISION,
        SPECTER2_DIMENSION,
        SPECTER2_PREPROCESSING_CONTRACT,
        SPECTER2_MODEL_PROVENANCE,
        prepared.generated_at,
        SPECTER2_EMBEDDING_SOURCE,
        SPECTER2_DIMENSION,
    )
