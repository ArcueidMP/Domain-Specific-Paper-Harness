"""Validated YAML topic configuration adapter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Self
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from paper_harness.domain.models import MAX_REPRESENTATIVE_FULL_TEXT_COUNT, TopicConfig

_CATEGORY = re.compile(r"^[a-z-]+(?:\.[A-Z]{2})?$")


class ArxivTopicDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    categories: tuple[str, ...] = Field(min_length=1)
    include_terms: tuple[str, ...] = Field(min_length=1)
    exclude_terms: tuple[str, ...] = ()

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values) or any(
            _CATEGORY.fullmatch(value) is None for value in values
        ):
            raise ValueError("arXiv categories must be unique canonical category identifiers")
        return values

    @field_validator("include_terms", "exclude_terms")
    @classmethod
    def validate_terms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("arXiv query terms must be unique")
        for value in values:
            if not value.strip() or len(value) > 120 or any(char in value for char in '"\\\r\n'):
                raise ValueError(
                    "arXiv query terms must be plain, non-empty text under 121 characters"
                )
        return values


class DiscoveryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    overlap_hours: int = Field(ge=1, le=24 * 14)
    initial_lookback_days: int = Field(ge=1, le=180)
    max_results: int = Field(ge=1, le=5000)


class TopicDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    topic_id: UUID
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    arxiv: ArxivTopicDocument
    discovery: DiscoveryDocument
    representative_full_text_count: int = Field(ge=1, le=MAX_REPRESENTATIVE_FULL_TEXT_COUNT)

    def to_domain(self) -> TopicConfig:
        return TopicConfig(
            id=self.topic_id,
            slug=self.slug,
            name=self.name,
            description=self.description,
            categories=self.arxiv.categories,
            include_terms=self.arxiv.include_terms,
            exclude_terms=self.arxiv.exclude_terms,
            overlap_hours=self.discovery.overlap_hours,
            initial_lookback_days=self.discovery.initial_lookback_days,
            max_results=self.discovery.max_results,
            representative_full_text_count=self.representative_full_text_count,
            schema_version=self.schema_version,
        )

    @classmethod
    def from_raw(cls, raw: Any) -> Self:
        return cls.model_validate(raw)


def load_topic_config(path: Path) -> TopicConfig:
    with path.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    return TopicDocument.from_raw(raw).to_domain()
