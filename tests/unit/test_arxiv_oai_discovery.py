from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from tests.fakes import FakeArxiv, FakeRepository

from paper_harness.adapters.arxiv.oai import identifier_page_url, parse_identifier_page
from paper_harness.application.ingest_arxiv import IngestArxiv, IngestionResumeError
from paper_harness.domain.models import (
    IngestionCursor,
    PipelineExecutionMode,
    RunStatus,
    TopicConfig,
)
from paper_harness.ports.arxiv import (
    ArxivDiscoveryIncompleteError,
    ArxivIdentifierPage,
    ArxivPaperRecord,
    ArxivResponseError,
    ArxivTokenExpiredError,
    ArxivUnavailableError,
)

DAY = date(2026, 1, 10)
NOW = datetime(2026, 1, 10, 12, tzinfo=UTC)


def _feed(*identifiers: str, token: str = "") -> bytes:
    headers = "".join(
        f"<header><identifier>oai:arXiv.org:{identifier}</identifier>"
        f"<datestamp>{DAY}</datestamp><setSpec>cs:cs:AI</setSpec></header>"
        for identifier in identifiers
    )
    return (
        '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
        f"<ListIdentifiers>{headers}<resumptionToken>{token}</resumptionToken>"
        "</ListIdentifiers></OAI-PMH>"
    ).encode()


def test_oai_query_uses_exact_update_day_category_and_opaque_token() -> None:
    parameters = parse_qs(urlsplit(identifier_page_url(DAY, "q-bio.NC", None)).query)
    assert parameters == {
        "verb": ["ListIdentifiers"],
        "metadataPrefix": ["arXiv"],
        "set": ["q-bio:q-bio:NC"],
        "from": [str(DAY)],
        "until": [str(DAY)],
    }
    assert parse_qs(urlsplit(identifier_page_url(DAY, "cs.AI", "a+b=/token")).query) == {
        "verb": ["ListIdentifiers"],
        "resumptionToken": ["a+b=/token"],
    }


def test_oai_deduplication_never_changes_provider_continuation() -> None:
    page = parse_identifier_page(
        _feed("2601.00003", "2601.00001", "2601.00001", token="next"), day=DAY, category="cs.AI"
    )
    assert page.canonical_arxiv_ids == ("2601.00001", "2601.00003")
    assert page.resumption_token == "next"
    deleted = _feed("2601.00001", token="after-deletions").replace(
        b"<header>", b'<header status="deleted">'
    )
    deleted_page = parse_identifier_page(deleted, day=DAY, category="cs.AI")
    assert deleted_page.canonical_arxiv_ids == ()
    assert deleted_page.resumption_token == "after-deletions"


@pytest.mark.parametrize(
    "content",
    [
        _feed("invalid"),
        _feed(),
        _feed("2601.00001").replace(b"2026-01-10", b"2026-01-09"),
        _feed("2601.00001").replace(b"cs:cs:AI", b"cs:cs:CL"),
        b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"/>',
        b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///secret">]><x/>',
    ],
)
def test_invalid_oai_identity_scope_or_document_is_not_exhaustion(content: bytes) -> None:
    with pytest.raises(ArxivResponseError):
        parse_identifier_page(content, day=DAY, category="cs.AI")


def test_expired_token_is_distinct_from_no_records() -> None:
    prefix = b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
    with pytest.raises(ArxivTokenExpiredError):
        parse_identifier_page(
            prefix + b'<error code="badResumptionToken"/></OAI-PMH>', day=DAY, category="cs.AI"
        )
    assert parse_identifier_page(
        prefix + b'<error code="noRecordsMatch"/></OAI-PMH>', day=DAY, category="cs.AI"
    ) == ArxivIdentifierPage((), None)


class PagedArxiv(FakeArxiv):
    def __init__(self, records: tuple[ArxivPaperRecord, ...], *, page_size: int = 2) -> None:
        super().__init__(records)
        self.page_size = page_size
        self.expire_once = False
        self.fail_lookup_at: int | None = None

    def list_updated_identifiers(
        self,
        *,
        day: date,
        category: str,
        resumption_token: str | None = None,
        timeout_seconds: float | None = None,
    ) -> ArxivIdentifierPage:
        self.discovery_calls.append((day, category, resumption_token))
        if day != DAY or category != "cs.AI":
            return ArxivIdentifierPage((), None)
        if self.expire_once and resumption_token is not None:
            self.expire_once = False
            raise ArxivTokenExpiredError("expired")
        offset = int(resumption_token or 0)
        identifiers = tuple(record.canonical_arxiv_id for record in self.records)
        end = min(offset + self.page_size, len(identifiers))
        return ArxivIdentifierPage(
            identifiers[offset:end], str(end) if end < len(identifiers) else None
        )

    def get_papers_by_ids(
        self, *, canonical_arxiv_ids: tuple[str, ...], timeout_seconds: float | None = None
    ) -> tuple[ArxivPaperRecord, ...]:
        if self.fail_lookup_at == len(self.id_calls):
            self.fail_lookup_at = None
            raise ArxivUnavailableError("temporary metadata failure")
        return super().get_papers_by_ids(canonical_arxiv_ids=canonical_arxiv_ids)


def _records(record: ArxivPaperRecord, count: int) -> tuple[ArxivPaperRecord, ...]:
    return tuple(
        replace(
            record,
            canonical_arxiv_id=f"2601.{index:05}",
            updated_at=NOW - timedelta(hours=1),
            pdf_url=f"https://arxiv.org/pdf/2601.{index:05}v1",
            source_url=f"https://arxiv.org/abs/2601.{index:05}v1",
        )
        for index in range(1, count + 1)
    )


def _repository(topic: TopicConfig) -> FakeRepository:
    repository = FakeRepository()
    repository.cursor = IngestionCursor(topic.id, NOW - timedelta(hours=2), 1, NOW, NOW)
    return repository


@pytest.mark.parametrize("count", [2, 5])
def test_discovery_splits_surplus_records_and_exact_cap_without_losing_ties(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord, count: int
) -> None:
    topic = replace(topic_config, categories=("cs.AI",), overlap_hours=1, max_results=2)
    records = _records(arxiv_record_v1, count)
    arxiv = PagedArxiv(tuple(reversed(records)) + (records[0],), page_size=3)
    repository = _repository(topic)
    run = IngestArxiv(arxiv=arxiv, repository=repository, clock=lambda: NOW).execute(topic)
    assert run.status is RunStatus.COMPLETE
    assert run.normalized_count == count
    assert all(len(batch) <= 2 for batch in arxiv.id_calls)
    assert repository.cursor is not None and repository.cursor.watermark == NOW


def test_saturated_scan_keeps_cursor_and_resumes_after_expired_token(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    topic = replace(topic_config, categories=("cs.AI",), overlap_hours=1, max_results=1)
    arxiv = PagedArxiv(_records(arxiv_record_v1, 5))
    repository = _repository(topic)
    prior_cursor = repository.cursor
    with pytest.raises(ArxivDiscoveryIncompleteError):
        IngestArxiv(arxiv=arxiv, repository=repository, clock=lambda: NOW, max_pages=1).execute(
            topic
        )
    assert repository.cursor == prior_cursor
    assert repository.run is not None
    assert repository.run.normalized_count == 2
    assert repository.ingestion_progress[repository.run.id].resumption_token == "2"
    arxiv.expire_once = True
    run = IngestArxiv(
        arxiv=arxiv, repository=repository, clock=lambda: NOW + timedelta(days=1)
    ).execute(topic, logical_date=DAY, resume_existing=True)
    assert run.cursor_to == NOW
    assert run.status is RunStatus.COMPLETE and run.normalized_count == 5
    assert repository.cursor is not None and repository.cursor.watermark == NOW
    assert (DAY, "cs.AI", "2") in arxiv.discovery_calls


def test_changed_topic_scope_fails_resume_without_losing_saved_progress(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    topic = replace(topic_config, categories=("cs.AI",), overlap_hours=1, max_results=1)
    arxiv = PagedArxiv(_records(arxiv_record_v1, 3))
    repository = _repository(topic)
    prior_cursor = repository.cursor
    with pytest.raises(ArxivDiscoveryIncompleteError):
        IngestArxiv(arxiv=arxiv, repository=repository, clock=lambda: NOW, max_pages=1).execute(
            topic
        )
    assert repository.run is not None
    progress = repository.ingestion_progress[repository.run.id]
    previous_calls = len(arxiv.discovery_calls)

    with pytest.raises(IngestionResumeError, match="topic scope changed"):
        IngestArxiv(arxiv=arxiv, repository=repository, clock=lambda: NOW).execute(
            replace(topic, include_terms=("a different research domain",)),
            logical_date=DAY,
            resume_existing=True,
        )

    assert repository.run.status is RunStatus.FAILED
    assert repository.run.error_code == "INGESTION_RESUME_CONFLICT"
    assert repository.ingestion_progress[repository.run.id] == progress
    assert repository.cursor == prior_cursor
    assert len(arxiv.discovery_calls) == previous_calls


def test_metadata_failure_retains_the_unconsumed_page_suffix(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    topic = replace(topic_config, categories=("cs.AI",), overlap_hours=1, max_results=1)
    arxiv = PagedArxiv(_records(arxiv_record_v1, 3), page_size=3)
    arxiv.fail_lookup_at = 1
    repository = _repository(topic)
    use_case = IngestArxiv(arxiv=arxiv, repository=repository, clock=lambda: NOW)
    with pytest.raises(ArxivUnavailableError):
        use_case.execute(topic)
    assert repository.run is not None
    assert repository.ingestion_progress[repository.run.id].pending_ids == (
        "2601.00002",
        "2601.00003",
    )
    run = use_case.execute(topic, resume_existing=True)
    assert run.normalized_count == 3
    assert arxiv.id_calls.count(("2601.00001",)) == 1


def test_oai_announcement_date_includes_old_version_timestamps(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    topic = replace(topic_config, categories=("cs.AI",), overlap_hours=1)
    old_record = replace(
        arxiv_record_v1, updated_at=NOW - timedelta(days=90), submitted_at=NOW - timedelta(days=100)
    )
    arxiv = PagedArxiv((old_record,))
    run = IngestArxiv(arxiv=arxiv, repository=_repository(topic), clock=lambda: NOW).execute(topic)
    assert run.normalized_count == 1


def test_time_budget_retains_metadata_suffix_for_fixed_window_resume(
    topic_config: TopicConfig,
    arxiv_record_v1: ArxivPaperRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topic = replace(topic_config, categories=("cs.AI",), overlap_hours=1, max_results=1)
    arxiv = PagedArxiv(_records(arxiv_record_v1, 3), page_size=3)
    repository = _repository(topic)
    ticks = [0.0]
    original = arxiv.get_papers_by_ids

    def slow_lookup(
        *, canonical_arxiv_ids: tuple[str, ...], timeout_seconds: float | None = None
    ) -> tuple[ArxivPaperRecord, ...]:
        assert timeout_seconds is not None and timeout_seconds <= 1
        records = original(canonical_arxiv_ids=canonical_arxiv_ids)
        ticks[0] += 2
        return records

    monkeypatch.setattr(arxiv, "get_papers_by_ids", slow_lookup)
    with pytest.raises(ArxivDiscoveryIncompleteError, match="time budget"):
        IngestArxiv(
            arxiv=arxiv,
            repository=repository,
            clock=lambda: NOW,
            monotonic=lambda: ticks[0],
            max_total_seconds=1,
        ).execute(topic)
    assert repository.run is not None and repository.run.normalized_count == 1
    assert repository.ingestion_progress[repository.run.id].pending_ids == (
        "2601.00002",
        "2601.00003",
    )
    monkeypatch.setattr(arxiv, "get_papers_by_ids", original)
    resumed = IngestArxiv(
        arxiv=arxiv, repository=repository, clock=lambda: NOW + timedelta(days=1)
    ).execute(topic, logical_date=DAY, resume_existing=True)
    assert resumed.cursor_to == NOW
    assert resumed.normalized_count == 3


def test_historic_reprocess_ignores_newer_current_versions_without_recent_lookahead(
    topic_config: TopicConfig, arxiv_record_v1: ArxivPaperRecord
) -> None:
    topic = replace(
        topic_config, categories=("cs.AI",), overlap_hours=1, initial_lookback_days=1, max_results=1
    )
    historical = replace(arxiv_record_v1, updated_at=NOW - timedelta(hours=1))
    recent = tuple(
        replace(record, updated_at=NOW + timedelta(days=30))
        for record in _records(arxiv_record_v1, 105)
    )
    arxiv = PagedArxiv(recent + (historical,), page_size=20)
    repository = _repository(topic)
    prior = repository.cursor
    run = IngestArxiv(
        arxiv=arxiv, repository=repository, clock=lambda: NOW + timedelta(days=31)
    ).execute(
        topic,
        logical_date=DAY,
        pipeline_execution_mode=PipelineExecutionMode.REPROCESS,
        pipeline_selection_limit=1,
        pipeline_execution_id=uuid4(),
    )
    assert run.normalized_count == 1
    assert repository.cursor == prior
    assert max(day for day, _category, _token in arxiv.discovery_calls) == DAY
