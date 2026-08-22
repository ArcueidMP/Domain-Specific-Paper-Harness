# pyright: reportPrivateUsage=false

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from tests.fakes import FakeRepository, fake_pipeline_execution_contract

from paper_harness.domain.analysis import AnalysisScope
from paper_harness.domain.errors import DomainInvariantError, DuplicateDailyRunError
from paper_harness.domain.identity import stable_pipeline_execution_id
from paper_harness.domain.models import (
    PipelineExecution,
    PipelineExecutionContract,
    PipelineExecutionMode,
    RunStatus,
    TopicConfig,
)
from paper_harness.entrypoints.runtime import _locked_pipeline_execution_lifecycle
from paper_harness.ports.repository import RepositoryError

TOPIC_ID = UUID("4b7db6d4-349c-5c06-bc41-f84091580fcb")
STARTED_AT = datetime(2026, 8, 10, 5, tzinfo=UTC)
CONTRACT_FIELD_NAMES = tuple(field.name for field in fields(PipelineExecutionContract))


def _execution() -> PipelineExecution:
    return PipelineExecution(
        id=stable_pipeline_execution_id(TOPIC_ID, date(2026, 8, 10)),
        topic_id=TOPIC_ID,
        logical_date=date(2026, 8, 10),
        execution_mode=PipelineExecutionMode.NORMAL,
        analysis_scope=AnalysisScope.FULL_TEXT,
        selection_limit=1,
        contract=fake_pipeline_execution_contract(),
        status=RunStatus.RUNNING,
        deadline_at=STARTED_AT + timedelta(hours=8),
        started_at=STARTED_AT,
        completed_at=None,
        error_code=None,
        error_detail=None,
        schema_version=1,
        created_at=STARTED_AT,
    )


def test_reprocess_execution_uses_a_fresh_publishable_identity() -> None:
    execution = replace(
        _execution(),
        id=UUID("3b301c07-9aa3-4a3d-b13a-e7b3ba4db146"),
        execution_mode=PipelineExecutionMode.REPROCESS,
    )

    assert execution.execution_mode is PipelineExecutionMode.REPROCESS


def test_normal_execution_still_requires_the_canonical_identity() -> None:
    with pytest.raises(DomainInvariantError, match="not stable"):
        replace(
            _execution(),
            id=UUID("3b301c07-9aa3-4a3d-b13a-e7b3ba4db146"),
        )


def _changed_contract(field_name: str) -> PipelineExecutionContract:
    contract = fake_pipeline_execution_contract()
    value: object = getattr(contract, field_name)
    if isinstance(value, str):
        changed: object = f"{value}-changed"
    elif isinstance(value, int):
        changed = value + 1
    elif isinstance(value, float):
        changed = value + 1.0
    elif isinstance(value, tuple):
        changed = (*cast(tuple[str, ...], value), "changed")
    else:  # pragma: no cover - the assertion documents the closed contract schema.
        raise AssertionError(f"unsupported execution contract field {field_name}")
    return replace(contract, **{field_name: changed})


def _topic(*, include_terms: tuple[str, ...] = ("LLM agent",)) -> TopicConfig:
    return TopicConfig(
        id=TOPIC_ID,
        slug="broad-llm-agents",
        name="Broad LLM Agents",
        description="Broad LLM-agent research.",
        categories=("cs.AI",),
        include_terms=include_terms,
        exclude_terms=(),
        overlap_hours=48,
        initial_lookback_days=7,
        max_results=500,
        representative_full_text_count=100,
    )


@pytest.mark.parametrize(
    ("error_code", "error_detail", "message"),
    [
        ("E" * 81, "failure", "error code"),
        ("FAILED", "x" * 1001, "error detail"),
    ],
)
def test_failed_execution_rejects_failure_data_beyond_database_bounds(
    error_code: str,
    error_detail: str,
    message: str,
) -> None:
    with pytest.raises(DomainInvariantError, match=message):
        replace(
            _execution(),
            status=RunStatus.FAILED,
            completed_at=STARTED_AT + timedelta(minutes=1),
            error_code=error_code,
            error_detail=error_detail,
        )


@pytest.mark.parametrize("field_name", CONTRACT_FIELD_NAMES)
def test_running_execution_replay_refreshes_every_mutable_contract_field(
    field_name: str,
) -> None:
    repository = FakeRepository()
    execution = _execution()
    repository.start_pipeline_execution(execution)
    changed_contract = _changed_contract(field_name)

    refreshed = repository.start_pipeline_execution(replace(execution, contract=changed_contract))

    assert refreshed.contract == changed_contract
    assert repository.get_pipeline_execution(execution.id) == refreshed


@pytest.mark.parametrize("field_name", CONTRACT_FIELD_NAMES)
def test_failed_execution_restart_refreshes_every_mutable_contract_field(
    field_name: str,
) -> None:
    repository = FakeRepository()
    execution = _execution()
    repository.start_pipeline_execution(execution)
    repository.fail_pipeline_execution(
        execution.id,
        completed_at=STARTED_AT + timedelta(minutes=1),
        error_code="DAILY_PIPELINE_FAILED",
        error_detail="retryable execution failure",
    )
    changed_contract = _changed_contract(field_name)

    restarted = repository.restart_pipeline_execution(
        execution.id,
        started_at=STARTED_AT + timedelta(hours=1),
        deadline_at=STARTED_AT + timedelta(hours=9),
        contract=changed_contract,
    )

    assert restarted.status is RunStatus.RUNNING
    assert restarted.contract == changed_contract
    assert repository.get_pipeline_execution(execution.id) == restarted


def test_duplicate_outer_lock_cannot_fail_the_active_execution_and_it_can_complete() -> None:
    repository = FakeRepository()
    topic = _topic()
    execution = _execution()
    repository.upsert_topic(topic)
    repository.start_pipeline_execution(execution)
    repository.pipeline_locked = True

    with (
        pytest.raises(DuplicateDailyRunError, match="another daily pipeline"),
        _locked_pipeline_execution_lifecycle(repository, topic, execution),
    ):
        raise AssertionError("duplicate execution must not enter its lifecycle")

    assert repository.get_pipeline_execution(execution.id) == execution
    repository.pipeline_locked = False
    with _locked_pipeline_execution_lifecycle(repository, topic, execution) as active:
        assert active.status is RunStatus.RUNNING
        repository.complete_pipeline_execution(
            active.id,
            status=RunStatus.COMPLETE,
            completed_at=STARTED_AT + timedelta(minutes=2),
        )

    completed = repository.get_pipeline_execution(execution.id)
    assert completed is not None
    assert completed.status is RunStatus.COMPLETE


@pytest.mark.parametrize("existing_status", [RunStatus.RUNNING, RunStatus.FAILED])
def test_changed_failed_run_inputs_refresh_before_topic_mutation(
    existing_status: RunStatus,
) -> None:
    repository = FakeRepository()
    original_topic = _topic()
    execution = _execution()
    repository.upsert_topic(original_topic)
    repository.start_pipeline_execution(execution)
    if existing_status is RunStatus.FAILED:
        repository.fail_pipeline_execution(
            execution.id,
            completed_at=STARTED_AT + timedelta(minutes=1),
            error_code="DAILY_PIPELINE_FAILED",
            error_detail="retryable execution failure",
        )
    changed_topic = _topic(include_terms=("changed topic policy",))
    changed_request = replace(
        execution,
        contract=replace(
            execution.contract,
            topic_include_terms=changed_topic.include_terms,
        ),
        started_at=STARTED_AT + timedelta(hours=1),
        deadline_at=STARTED_AT + timedelta(hours=9),
    )

    with _locked_pipeline_execution_lifecycle(
        repository,
        changed_topic,
        changed_request,
    ) as active:
        assert active.contract == changed_request.contract
        repository.complete_pipeline_execution(
            active.id,
            status=RunStatus.COMPLETE,
            completed_at=STARTED_AT + timedelta(hours=2),
        )

    assert repository.topic is not None
    assert repository.topic.config == changed_topic
    persisted = repository.get_pipeline_execution(execution.id)
    assert persisted is not None
    assert persisted.status is RunStatus.COMPLETE
    assert persisted.contract == changed_request.contract


def test_failed_execution_restarts_under_the_outer_lock_and_completes() -> None:
    repository = FakeRepository()
    topic = _topic()
    execution = _execution()
    repository.upsert_topic(topic)
    repository.start_pipeline_execution(execution)
    repository.fail_pipeline_execution(
        execution.id,
        completed_at=STARTED_AT + timedelta(minutes=1),
        error_code="DAILY_PIPELINE_FAILED",
        error_detail="retryable execution failure",
    )
    request = replace(
        execution,
        started_at=STARTED_AT + timedelta(hours=1),
        deadline_at=STARTED_AT + timedelta(hours=9),
    )

    with _locked_pipeline_execution_lifecycle(repository, topic, request) as restarted:
        assert restarted.status is RunStatus.RUNNING
        assert restarted.started_at == request.started_at
        assert restarted.deadline_at == request.deadline_at
        repository.complete_pipeline_execution(
            restarted.id,
            status=RunStatus.COMPLETE,
            completed_at=STARTED_AT + timedelta(hours=2),
        )

    completed = repository.get_pipeline_execution(execution.id)
    assert completed is not None
    assert completed.status is RunStatus.COMPLETE


def test_pipeline_failure_normalizes_persistence_bounds() -> None:
    repository = FakeRepository()
    execution = _execution()
    repository.start_pipeline_execution(execution)

    failed = repository.fail_pipeline_execution(
        execution.id,
        completed_at=STARTED_AT + timedelta(minutes=1),
        error_code=f"  {'E' * 100}  ",
        error_detail=f"  {'detail' * 250}  ",
    )

    assert failed.error_code == "E" * 80
    assert failed.error_detail == ("detail" * 250)[:1000]


@pytest.mark.parametrize(
    ("error_code", "error_detail"),
    [("   ", "diagnostic"), ("FAILED", "   ")],
)
def test_pipeline_failure_rejects_empty_normalized_provenance(
    error_code: str,
    error_detail: str,
) -> None:
    repository = FakeRepository()
    execution = _execution()
    repository.start_pipeline_execution(execution)

    with pytest.raises(RepositoryError, match="stable code and detail"):
        repository.fail_pipeline_execution(
            execution.id,
            completed_at=STARTED_AT + timedelta(minutes=1),
            error_code=error_code,
            error_detail=error_detail,
        )

    assert repository.get_pipeline_execution(execution.id) == execution
