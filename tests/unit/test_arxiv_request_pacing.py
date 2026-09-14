# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false
"""Every arXiv wire attempt shares pacing, coordination, and one deadline."""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import date
from typing import Any

import pytest
import requests
from tests.unit.test_arxiv_client import _VALID_EMPTY_ATOM, _response

from paper_harness.adapters.arxiv.client import ArxivClient, BoundedArxivSession


def test_oai_atom_and_pdf_share_one_request_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    sent: list[float] = []

    def sleep(delay: float) -> None:
        clock[0] += delay

    def request(
        _session: requests.Session, _method: str, url: str, **_kwargs: Any
    ) -> requests.Response:
        sent.append(clock[0])
        if "oaipmh.arxiv.org" in url:
            return _response(
                200,
                content=(
                    b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
                    b'<error code="noRecordsMatch">No records.</error></OAI-PMH>'
                ),
            )
        if "/api/query" in url:
            return _response(200, content=_VALID_EMPTY_ATOM)
        return _response(200, content=b"%PDF-1.7\nA test PDF.\n%%EOF")

    monkeypatch.setattr(requests.Session, "request", request)
    client = ArxivClient(max_retries=0, sleep=sleep, monotonic=lambda: clock[0])
    client.list_updated_identifiers(day=date(2026, 1, 10), category="cs.AI")
    client.get_papers_by_ids(canonical_arxiv_ids=("2601.01234",))
    client.download_pdf(
        canonical_arxiv_id="2601.01234", version=1, pdf_url="https://arxiv.org/pdf/2601.01234v1"
    )
    assert sent == [0.0, 3.0, 6.0]


@pytest.mark.parametrize("failure", ("rate_limit", "timeout"))
def test_retries_never_bypass_the_three_second_floor(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    clock = [0.0]
    sent: list[float] = []

    def sleep(delay: float) -> None:
        clock[0] += delay

    def request(
        _session: requests.Session, _method: str, _url: str, **_kwargs: Any
    ) -> requests.Response:
        sent.append(clock[0])
        if len(sent) == 1:
            if failure == "timeout":
                raise requests.exceptions.ReadTimeout("fixture timeout")
            return _response(429, retry_after="1")
        return _response(200, content=_VALID_EMPTY_ATOM)

    monkeypatch.setattr(requests.Session, "request", request)
    session = BoundedArxivSession(
        request_timeout_seconds=10,
        max_retries=1,
        retry_backoff_seconds=1,
        max_retry_after_seconds=30,
        max_total_seconds=20,
        delay_seconds=3,
        sleep=sleep,
        monotonic=lambda: clock[0],
    )
    with session.operation():
        assert session.get("https://export.arxiv.org/api/query").status_code == 200
    assert sent == [0.0, 3.0]


@pytest.mark.parametrize("wait_seconds", (5.0, 8.0))
def test_gate_wait_reduces_http_budget_and_prevents_late_requests(
    monkeypatch: pytest.MonkeyPatch, wait_seconds: float
) -> None:
    clock = [0.0]
    observed_timeouts: list[float] = []
    held = [False]

    @contextmanager
    def gate(timeout_seconds: float) -> Generator[None]:
        assert timeout_seconds == 7.0
        held[0] = True
        clock[0] += wait_seconds
        try:
            yield
        finally:
            held[0] = False

    def request(
        _session: requests.Session, _method: str, _url: str, **kwargs: Any
    ) -> requests.Response:
        assert held[0]
        observed_timeouts.append(kwargs["timeout"])
        return _response(200, content=_VALID_EMPTY_ATOM)

    monkeypatch.setattr(requests.Session, "request", request)
    session = BoundedArxivSession(
        request_timeout_seconds=10,
        max_retries=0,
        retry_backoff_seconds=1,
        max_retry_after_seconds=30,
        max_total_seconds=7,
        sleep=lambda delay: None,
        monotonic=lambda: clock[0],
        request_gate=gate,
    )
    with session.operation():
        if wait_seconds < 7:
            assert session.get("https://export.arxiv.org/api/query").status_code == 200
        else:
            with pytest.raises(requests.exceptions.Timeout):
                session.get("https://export.arxiv.org/api/query")
    assert observed_timeouts == ([2.0] if wait_seconds < 7 else [])
    assert not held[0]


@pytest.mark.parametrize("fail_stream", (False, True))
def test_guard_owns_stream_consumption_and_close(
    monkeypatch: pytest.MonkeyPatch, fail_stream: bool
) -> None:
    held = [False]
    events: list[str] = []

    @contextmanager
    def gate(_timeout: float) -> Generator[None]:
        held[0] = True
        events.append("acquired")
        try:
            yield
        finally:
            events.append("released")
            held[0] = False

    class Response(requests.Response):
        def close(self) -> None:
            assert held[0]
            events.append("closed")

    response = Response()
    response.status_code = 200

    def request(
        _session: requests.Session, _method: str, _url: str, **_kwargs: Any
    ) -> requests.Response:
        assert held[0]
        events.append("headers")
        return response

    def consume(_response: requests.Response) -> str:
        assert held[0]
        events.append("body")
        if fail_stream:
            raise requests.exceptions.ReadTimeout("fixture stream timeout")
        return "complete"

    monkeypatch.setattr(requests.Session, "request", request)
    session = BoundedArxivSession(
        request_timeout_seconds=10,
        max_retries=0,
        retry_backoff_seconds=1,
        max_retry_after_seconds=30,
        max_total_seconds=20,
        sleep=lambda delay: None,
        monotonic=lambda: 0.0,
        request_gate=gate,
    )
    with session.operation():
        if fail_stream:
            with pytest.raises(requests.exceptions.ReadTimeout):
                session.consume_stream("https://arxiv.org/pdf/2601.01234v1", consume=consume)
        else:
            assert (
                session.consume_stream("https://arxiv.org/pdf/2601.01234v1", consume=consume)
                == "complete"
            )
    assert events == ["acquired", "headers", "body", "closed", "released"]
