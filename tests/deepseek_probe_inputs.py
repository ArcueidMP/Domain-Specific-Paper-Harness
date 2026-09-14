# pyright: reportPrivateUsage=false
"""Synthetic inputs shared by deterministic tests and explicit live adapter probes."""

from tests.unit.test_deepseek_client import _request as analysis_request
from tests.unit.test_deepseek_m3 import (
    _comparison_request as comparison_request,
)
from tests.unit.test_deepseek_m3 import (
    _crawler_request as crawler_request,
)
from tests.unit.test_deepseek_m3 import (
    _selection_request as selection_request,
)
from tests.unit.test_deepseek_reports import _request as report_request

__all__ = [
    "analysis_request",
    "comparison_request",
    "crawler_request",
    "report_request",
    "selection_request",
]
