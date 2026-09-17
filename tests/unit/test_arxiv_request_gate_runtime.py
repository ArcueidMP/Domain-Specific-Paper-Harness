# pyright: reportPrivateUsage=false
"""Production arXiv constructors reuse their repository's configured engine."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from paper_harness.domain.analysis import AnalysisScope
from paper_harness.domain.models import TopicConfig
from paper_harness.entrypoints import runtime

_DATABASE_URL = "postgresql+psycopg://fixture:fixture@localhost:5432/fixture"


@pytest.mark.parametrize("operation", ("ingestion", "analysis"))
def test_standalone_arxiv_constructor_uses_the_same_database_engine_as_its_repository(
    monkeypatch: pytest.MonkeyPatch,
    topic_config: TopicConfig,
    operation: str,
) -> None:
    engine = MagicMock()
    engine_constructor = MagicMock(return_value=engine)
    repository = MagicMock()
    repository_constructor = MagicMock(return_value=repository)
    request_gate = object()
    gate_constructor = MagicMock(return_value=request_gate)
    arxiv = object()
    arxiv_constructor = MagicMock(return_value=arxiv)
    result = object()
    execute = MagicMock(return_value=result)
    use_case_constructor = MagicMock(return_value=SimpleNamespace(execute=execute))
    monkeypatch.setenv("DATABASE_URL", _DATABASE_URL)
    monkeypatch.setattr(runtime, "load_topic_config", MagicMock(return_value=topic_config))
    monkeypatch.setattr(runtime, "create_postgres_engine", engine_constructor)
    monkeypatch.setattr(runtime, "PostgresRepository", repository_constructor)
    monkeypatch.setattr(runtime, "PostgresArxivRequestGate", gate_constructor)
    monkeypatch.setattr(runtime, "ArxivClient", arxiv_constructor)

    if operation == "ingestion":
        monkeypatch.setattr(runtime, "IngestArxiv", use_case_constructor)
        actual = runtime.execute_arxiv_ingestion(
            topic_config=Path("unused.yaml"), logical_date=date(2026, 9, 14)
        )
    else:
        monkeypatch.setattr(
            runtime.DeepSeekSettings,
            "from_environment",
            staticmethod(lambda: SimpleNamespace(provider="deepseek", model="deepseek-flash")),
        )
        monkeypatch.setattr(runtime, "DeepSeekClient", MagicMock(return_value=object()))
        monkeypatch.setattr(runtime, "_grobid_parser", MagicMock(return_value=None))
        monkeypatch.setattr(runtime, "AnalyzePapers", use_case_constructor)
        actual = runtime.execute_structured_analysis(
            topic_config=Path("unused.yaml"),
            paper_ids=(),
            analysis_scope=AnalysisScope.ABSTRACT_ONLY,
            logical_date=date(2026, 9, 14),
        )

    assert actual is result
    engine_constructor.assert_called_once_with(_DATABASE_URL)
    repository_constructor.assert_called_once_with(engine)
    repository.check_ready.assert_called_once()
    gate_constructor.assert_called_once_with(engine)
    arxiv_constructor.assert_called_once_with(request_gate=request_gate)
    assert use_case_constructor.call_args.kwargs["arxiv"] is arxiv
    assert use_case_constructor.call_args.kwargs["repository"] is repository


def test_daily_repository_and_gate_engine_are_created_once(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = MagicMock()
    repository = MagicMock()
    engine_constructor = MagicMock(return_value=engine)
    repository_constructor = MagicMock(return_value=repository)
    monkeypatch.setenv("DATABASE_URL", _DATABASE_URL)
    monkeypatch.setattr(runtime, "create_postgres_engine", engine_constructor)
    monkeypatch.setattr(runtime, "PostgresRepository", repository_constructor)

    actual_repository, actual_engine = runtime._ready_repository_with_engine("daily pipeline")

    assert actual_repository is repository
    assert actual_engine is engine
    engine_constructor.assert_called_once_with(_DATABASE_URL)
    repository_constructor.assert_called_once_with(engine)
    repository.check_ready.assert_called_once()
