"""Read projections exposed by the M1 API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from paper_harness.domain.models import (
    DailyRun,
    Paper,
    PaperSourceIdentity,
    PaperVersion,
    RunItem,
    TopicConfig,
)


@dataclass(frozen=True, slots=True)
class StoredTopic:
    config: TopicConfig
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PaperDetail:
    paper: Paper
    versions: tuple[PaperVersion, ...]
    source_identities: tuple[PaperSourceIdentity, ...]
    topic_slugs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunDetail:
    run: DailyRun
    items: tuple[RunItem, ...]
