from __future__ import annotations

import httpx
import pytest

from paper_harness.adapters.http_retry import HttpRetryPolicy, send_with_retry


def test_total_deadline_is_enforced_after_a_slow_response_body_operation() -> None:
    clock = [0.0]
    response = httpx.Response(200, content=b"bounded")

    def send(_timeout: float) -> httpx.Response:
        clock[0] = 6.0
        return response

    with pytest.raises(httpx.TimeoutException, match="total timeout"):
        send_with_retry(
            send,
            policy=HttpRetryPolicy(
                max_retries=0,
                request_timeout_seconds=5,
                total_timeout_seconds=5,
            ),
            monotonic=lambda: clock[0],
        )
    assert response.is_closed
