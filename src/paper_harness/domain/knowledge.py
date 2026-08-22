"""Deterministic, provenance-aware knowledge graph, lineage, and trend models.

Entity identity is deliberately conservative. Labels are normalized with NFKC,
whitespace folding, case folding, and a small punctuation-variant translation;
punctuation is otherwise preserved. Two labels match only when their complete
normalized keys are exactly equal within the same topic and entity type.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from paper_harness.domain.analysis import (
    AnalysisBundle,
    ClaimType,
    VerificationStatus,
)
from paper_harness.domain.errors import DomainInvariantError
from paper_harness.domain.historical import (
    ComparisonBundle,
    ComparisonDimensionName,
    PaperRelationType,
    RelationProvenance,
)
from paper_harness.domain.identity import (
    stable_graph_edge_id,
    stable_graph_entity_id,
    stable_graph_entity_mention_id,
    stable_graph_paper_entity_id,
    stable_lineage_snapshot_id,
    stable_trend_snapshot_id,
)

# The canonical key is indexed together with topic/type in PostgreSQL. Keeping
# labels at 500 Unicode code points stays below the B-tree entry limit even for
# four-byte UTF-8 characters and rejects prose-shaped pseudo-entities early.
MAX_GRAPH_LABEL_LENGTH = 500
MAX_PAPER_TITLE_LENGTH = 4000
MAX_LINEAGE_DEPTH = 10
MAX_LINEAGE_NODES = 200
MAX_LINEAGE_EDGES = 1000
MAX_REPRESENTATIVE_PAPERS = 20
GRAPH_CONFIDENCE_MEANING = (
    "A bounded 0-to-1 model-reported support strength for this edge; it is not a "
    "probability or a human verification result."
)


class GraphEntityType(StrEnum):
    PAPER = "PAPER"
    RESEARCH_PROBLEM = "RESEARCH_PROBLEM"
    METHOD = "METHOD"
    TASK = "TASK"
    DATASET = "DATASET"
    BENCHMARK = "BENCHMARK"


class GraphRelationType(StrEnum):
    CITES = "CITES"
    SIMILAR_TO = "SIMILAR_TO"
    EXTENDS = "EXTENDS"
    COMPARES_WITH = "COMPARES_WITH"
    CONTRADICTS = "CONTRADICTS"
    IMPROVES_ON = "IMPROVES_ON"
    ADDRESSES = "ADDRESSES"
    USES_METHOD = "USES_METHOD"
    TARGETS_TASK = "TARGETS_TASK"
    USES_DATASET = "USES_DATASET"
    EVALUATES_ON = "EVALUATES_ON"


PAPER_GRAPH_RELATION_TYPES = frozenset(
    {
        GraphRelationType.CITES,
        GraphRelationType.SIMILAR_TO,
        GraphRelationType.EXTENDS,
        GraphRelationType.COMPARES_WITH,
        GraphRelationType.CONTRADICTS,
        GraphRelationType.IMPROVES_ON,
    }
)
DEFAULT_LINEAGE_RELATION_TYPES = (
    GraphRelationType.CITES,
    GraphRelationType.EXTENDS,
    GraphRelationType.IMPROVES_ON,
)

_ENTITY_RELATION_TARGET = {
    GraphRelationType.ADDRESSES: GraphEntityType.RESEARCH_PROBLEM,
    GraphRelationType.USES_METHOD: GraphEntityType.METHOD,
    GraphRelationType.TARGETS_TASK: GraphEntityType.TASK,
    GraphRelationType.USES_DATASET: GraphEntityType.DATASET,
    GraphRelationType.EVALUATES_ON: GraphEntityType.BENCHMARK,
}
_PAPER_RELATION_MAP = {item: GraphRelationType(item.value) for item in PaperRelationType}
_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainInvariantError(f"{name} must be timezone-aware")


def _require_text(value: str, name: str, *, maximum: int) -> None:
    if not value.strip():
        raise DomainInvariantError(f"{name} must not be empty")
    if "\x00" in value or len(value) > maximum:
        raise DomainInvariantError(f"{name} must be concise valid text")


def _require_unit_score(value: float, name: str) -> None:
    if not 0.0 <= value <= 1.0:
        raise DomainInvariantError(f"{name} must be between zero and one")


def _normalize_label(value: str, *, maximum: int, name: str) -> str:
    if "\x00" in value:
        raise DomainInvariantError(f"{name} contains a null character")
    normalized = unicodedata.normalize("NFKC", value).translate(_PUNCTUATION_TRANSLATION)
    normalized = " ".join(normalized.split())
    if not normalized:
        raise DomainInvariantError(f"{name} must not be empty")
    if len(normalized) > maximum:
        raise DomainInvariantError(f"{name} exceeds the persistence bound")
    return normalized


def normalize_entity_label(value: str) -> str:
    """Return the conservative display normalization used before key creation.

    Internal and terminal punctuation remains significant. Only compatibility
    Unicode forms, common dash/quote variants, and whitespace are normalized.
    """

    return _normalize_label(
        value,
        maximum=MAX_GRAPH_LABEL_LENGTH,
        name="graph entity label",
    )


def _normalize_paper_title(value: str) -> str:
    return _normalize_label(
        value,
        maximum=MAX_PAPER_TITLE_LENGTH,
        name="paper graph title",
    )


def normalized_entity_key(value: str) -> str:
    """Return the exact canonical key; punctuation is intentionally preserved."""

    return normalize_entity_label(value).casefold()


def graph_entity_keys_match(left: str, right: str) -> bool:
    """Match only exact canonical keys after the documented normalization."""

    return normalized_entity_key(left) == normalized_entity_key(right)


@dataclass(frozen=True, slots=True)
class GraphModelProvenance:
    provider: str
    configured_model: str
    model_version: str
    prompt_version: str

    def __post_init__(self) -> None:
        for value, name, maximum in (
            (self.provider, "graph model provider", 100),
            (self.configured_model, "configured graph model", 200),
            (self.model_version, "graph model version", 200),
            (self.prompt_version, "graph prompt version", 100),
        ):
            _require_text(value, name, maximum=maximum)


@dataclass(frozen=True, slots=True)
class GraphEntity:
    id: UUID
    topic_id: UUID
    entity_type: GraphEntityType
    paper_id: UUID | None
    canonical_label: str
    normalized_key: str
    display_label: str
    aliases: tuple[str, ...]
    provenance: RelationProvenance
    source: str
    schema_version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        normalizer = (
            _normalize_paper_title
            if self.entity_type is GraphEntityType.PAPER
            else normalize_entity_label
        )
        canonical_label = normalizer(self.canonical_label)
        display_label = normalizer(self.display_label)
        object.__setattr__(self, "canonical_label", canonical_label)
        _require_text(self.source, "graph entity source", maximum=100)
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise DomainInvariantError("graph entity update cannot precede creation")
        if self.entity_type is GraphEntityType.PAPER:
            if self.paper_id is None:
                raise DomainInvariantError("paper graph entity requires a paper ID")
            expected_key = f"paper:{self.paper_id}"
            expected_id = stable_graph_paper_entity_id(self.topic_id, self.paper_id)
        else:
            if self.paper_id is not None:
                raise DomainInvariantError("non-paper graph entity cannot carry a paper ID")
            expected_key = normalized_entity_key(canonical_label)
            if normalized_entity_key(display_label) != expected_key:
                display_label = canonical_label
            expected_id = stable_graph_entity_id(
                self.topic_id,
                self.entity_type.value,
                expected_key,
            )
        if self.normalized_key != expected_key:
            raise DomainInvariantError("graph entity normalized key is not canonical")
        if self.id != expected_id:
            raise DomainInvariantError("graph entity ID is not stable for its canonical key")
        object.__setattr__(self, "display_label", display_label)
        cleaned_aliases = tuple(dict.fromkeys(normalizer(alias) for alias in self.aliases))
        if self.entity_type is not GraphEntityType.PAPER:
            cleaned_aliases = tuple(
                alias for alias in cleaned_aliases if normalized_entity_key(alias) == expected_key
            )
        object.__setattr__(self, "aliases", cleaned_aliases)


def _validate_occurrence_provenance(
    *,
    provenance: RelationProvenance,
    evidence_ids: tuple[UUID, ...],
    model_provenance: GraphModelProvenance | None,
    confidence: float | None,
    verification_status: VerificationStatus,
) -> None:
    if len(set(evidence_ids)) != len(evidence_ids):
        raise DomainInvariantError("graph evidence IDs must be unique")
    if provenance in (RelationProvenance.TEXT_EXPLICIT, RelationProvenance.LLM_INFERRED) and not (
        evidence_ids
    ):
        raise DomainInvariantError("text-explicit and LLM-inferred graph records require evidence")
    if provenance is RelationProvenance.LLM_INFERRED:
        if model_provenance is None or confidence is None:
            raise DomainInvariantError(
                "LLM-inferred graph records require model provenance and confidence"
            )
    elif confidence is not None:
        raise DomainInvariantError("only LLM-inferred graph records may carry confidence")
    if model_provenance is not None and provenance not in (
        RelationProvenance.LLM_INFERRED,
        RelationProvenance.DETERMINISTICALLY_DERIVED,
    ):
        raise DomainInvariantError(
            "only inferred or AI-derived graph records may carry model provenance"
        )
    if confidence is not None:
        _require_unit_score(confidence, "graph confidence")
    if (
        provenance is RelationProvenance.HUMAN_VERIFIED
        and verification_status is not VerificationStatus.HUMAN_VERIFIED
    ):
        raise DomainInvariantError("human-verified provenance requires human verification status")


@dataclass(frozen=True, slots=True)
class GraphEntityMention:
    id: UUID
    entity_id: UUID
    paper_id: UUID
    paper_version_id: UUID
    analysis_id: UUID | None
    comparison_id: UUID | None
    observed_label: str
    provenance: RelationProvenance
    evidence_ids: tuple[UUID, ...]
    model_provenance: GraphModelProvenance | None
    confidence: float | None
    verification_status: VerificationStatus
    generated_at: datetime
    schema_version: int
    created_at: datetime
    pipeline_execution_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_text(
            self.observed_label,
            "graph observed label",
            maximum=MAX_PAPER_TITLE_LENGTH,
        )
        _validate_occurrence_provenance(
            provenance=self.provenance,
            evidence_ids=self.evidence_ids,
            model_provenance=self.model_provenance,
            confidence=self.confidence,
            verification_status=self.verification_status,
        )
        expected_id = stable_graph_entity_mention_id(
            self.entity_id,
            self.paper_version_id,
            analysis_id=self.analysis_id,
            comparison_id=self.comparison_id,
            pipeline_execution_id=self.pipeline_execution_id,
        )
        if self.id != expected_id:
            raise DomainInvariantError("graph entity mention ID is not stable for its owner")
        _require_aware(self.generated_at, "generated_at")
        _require_aware(self.created_at, "created_at")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class GraphEdge:
    id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    relation_type: GraphRelationType
    source_paper_version_id: UUID
    target_paper_version_id: UUID | None
    analysis_id: UUID | None
    comparison_id: UUID | None
    paper_relation_id: UUID | None
    provenance: RelationProvenance
    evidence_ids: tuple[UUID, ...]
    justification: str
    model_provenance: GraphModelProvenance | None
    confidence: float | None
    verification_status: VerificationStatus
    generated_at: datetime
    schema_version: int
    created_at: datetime
    pipeline_execution_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.source_entity_id == self.target_entity_id:
            raise DomainInvariantError("graph edges cannot be self-relations")
        _require_text(self.justification, "graph edge justification", maximum=2000)
        _validate_occurrence_provenance(
            provenance=self.provenance,
            evidence_ids=self.evidence_ids,
            model_provenance=self.model_provenance,
            confidence=self.confidence,
            verification_status=self.verification_status,
        )
        is_paper_relation = self.relation_type in PAPER_GRAPH_RELATION_TYPES
        if is_paper_relation:
            if (
                self.target_paper_version_id is None
                or self.paper_relation_id is None
                or self.comparison_id is None
                or self.analysis_id is not None
            ):
                raise DomainInvariantError(
                    "paper graph relation requires target version, comparison, and source relation"
                )
            if self.source_paper_version_id == self.target_paper_version_id:
                raise DomainInvariantError("paper graph relation requires distinct versions")
        elif (
            self.target_paper_version_id is not None
            or self.paper_relation_id is not None
            or (self.analysis_id is None) == (self.comparison_id is None)
        ):
            raise DomainInvariantError(
                "paper-to-entity edge requires exactly one analysis or comparison owner"
            )
        expected_id = stable_graph_edge_id(
            self.source_entity_id,
            self.target_entity_id,
            self.relation_type.value,
            self.source_paper_version_id,
            target_paper_version_id=self.target_paper_version_id,
            analysis_id=self.analysis_id,
            comparison_id=self.comparison_id,
            paper_relation_id=self.paper_relation_id,
            pipeline_execution_id=self.pipeline_execution_id,
        )
        if self.id != expected_id:
            raise DomainInvariantError("graph edge ID is not stable for its source occurrence")
        _require_aware(self.generated_at, "generated_at")
        _require_aware(self.created_at, "created_at")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class GraphReferenceSet:
    paper_version_ids: tuple[UUID, ...] = ()
    analysis_ids: tuple[UUID, ...] = ()
    comparison_ids: tuple[UUID, ...] = ()
    paper_relation_ids: tuple[UUID, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "paper_version_ids",
            "analysis_ids",
            "comparison_ids",
            "paper_relation_ids",
            "evidence_ids",
        ):
            values = getattr(self, field_name)
            object.__setattr__(self, field_name, tuple(sorted(set(values), key=str)))


class _Identified(Protocol):
    @property
    def id(self) -> UUID: ...


def _deduplicate_identified[IdentifiedT: _Identified](
    values: Iterable[IdentifiedT],
    name: str,
) -> tuple[IdentifiedT, ...]:
    by_id: dict[UUID, IdentifiedT] = {}
    for value in values:
        existing = by_id.get(value.id)
        if existing is not None and existing != value:
            raise DomainInvariantError(f"conflicting {name} records share a stable ID")
        by_id[value.id] = value
    return tuple(sorted(by_id.values(), key=lambda item: str(item.id)))


def _merge_graph_entities(values: Iterable[GraphEntity]) -> tuple[GraphEntity, ...]:
    grouped: dict[UUID, list[GraphEntity]] = defaultdict(list)
    for value in values:
        grouped[value.id].append(value)
    merged: list[GraphEntity] = []
    for entity_id, observations in grouped.items():
        first = observations[0]
        identity = (
            first.topic_id,
            first.entity_type,
            first.paper_id,
            first.normalized_key,
            first.provenance,
            first.source,
            first.schema_version,
        )
        if any(
            (
                item.topic_id,
                item.entity_type,
                item.paper_id,
                item.normalized_key,
                item.provenance,
                item.source,
                item.schema_version,
            )
            != identity
            for item in observations[1:]
        ):
            raise DomainInvariantError("conflicting graph entities share a stable ID")
        selected = max(
            observations,
            key=lambda item: (item.updated_at, item.display_label.casefold(), item.display_label),
        )
        aliases = {
            alias
            for item in observations
            for alias in (*item.aliases, item.display_label, item.canonical_label)
        }
        merged.append(
            GraphEntity(
                id=entity_id,
                topic_id=selected.topic_id,
                entity_type=selected.entity_type,
                paper_id=selected.paper_id,
                canonical_label=selected.canonical_label,
                normalized_key=selected.normalized_key,
                display_label=selected.display_label,
                aliases=tuple(sorted(aliases, key=lambda value: (value.casefold(), value))),
                provenance=selected.provenance,
                source=selected.source,
                schema_version=selected.schema_version,
                created_at=min(item.created_at for item in observations),
                updated_at=max(item.updated_at for item in observations),
            )
        )
    return tuple(sorted(merged, key=lambda item: str(item.id)))


@dataclass(frozen=True, slots=True)
class KnowledgeGraphBundle:
    topic_id: UUID
    entities: tuple[GraphEntity, ...]
    mentions: tuple[GraphEntityMention, ...]
    edges: tuple[GraphEdge, ...]
    references: GraphReferenceSet

    def __post_init__(self) -> None:
        entities = _merge_graph_entities(self.entities)
        mentions = _deduplicate_identified(self.mentions, "graph mention")
        edges = _deduplicate_identified(self.edges, "graph edge")
        object.__setattr__(self, "entities", entities)
        object.__setattr__(self, "mentions", mentions)
        object.__setattr__(self, "edges", edges)
        if not entities or not mentions:
            raise DomainInvariantError("knowledge graph bundle requires entities and mentions")
        if any(entity.topic_id != self.topic_id for entity in entities):
            raise DomainInvariantError("knowledge graph bundle cannot cross topic boundaries")
        entities_by_id = {entity.id: entity for entity in entities}
        mentioned_entity_ids: set[UUID] = set()
        for mention in mentions:
            entity = entities_by_id.get(mention.entity_id)
            if entity is None:
                raise DomainInvariantError("graph mention cannot reference an orphan entity")
            mentioned_entity_ids.add(entity.id)
            if mention.paper_version_id not in self.references.paper_version_ids:
                raise DomainInvariantError("graph mention references an unknown paper version")
            if (
                mention.analysis_id is not None
                and mention.analysis_id not in self.references.analysis_ids
            ):
                raise DomainInvariantError("graph mention references an unknown analysis")
            if (
                mention.comparison_id is not None
                and mention.comparison_id not in self.references.comparison_ids
            ):
                raise DomainInvariantError("graph mention references an unknown comparison")
            if not set(mention.evidence_ids).issubset(self.references.evidence_ids):
                raise DomainInvariantError("graph mention references unknown evidence")
            if entity.entity_type is GraphEntityType.PAPER:
                if entity.paper_id != mention.paper_id:
                    raise DomainInvariantError("paper graph mention has mismatched paper ownership")
            elif normalized_entity_key(mention.observed_label) != entity.normalized_key:
                raise DomainInvariantError("graph mention label does not match its entity key")
        if mentioned_entity_ids != set(entities_by_id):
            raise DomainInvariantError("knowledge graph bundle cannot contain orphan entities")
        for edge in edges:
            source = entities_by_id.get(edge.source_entity_id)
            target = entities_by_id.get(edge.target_entity_id)
            if source is None or target is None:
                raise DomainInvariantError("graph edge cannot reference an orphan entity")
            if source.entity_type is not GraphEntityType.PAPER:
                raise DomainInvariantError("initial graph edges must originate from a paper")
            if edge.source_paper_version_id not in self.references.paper_version_ids:
                raise DomainInvariantError("graph edge references an unknown source paper version")
            if (
                edge.analysis_id is not None
                and edge.analysis_id not in self.references.analysis_ids
            ):
                raise DomainInvariantError("graph edge references an unknown analysis")
            if (
                edge.comparison_id is not None
                and edge.comparison_id not in self.references.comparison_ids
            ):
                raise DomainInvariantError("graph edge references an unknown comparison")
            if (
                edge.paper_relation_id is not None
                and edge.paper_relation_id not in self.references.paper_relation_ids
            ):
                raise DomainInvariantError("graph edge references an unknown paper relation")
            if not set(edge.evidence_ids).issubset(self.references.evidence_ids):
                raise DomainInvariantError("graph edge references unknown evidence")
            if edge.relation_type in PAPER_GRAPH_RELATION_TYPES:
                if target.entity_type is not GraphEntityType.PAPER:
                    raise DomainInvariantError("paper relation must target a paper entity")
                assert edge.target_paper_version_id is not None
                if edge.target_paper_version_id not in self.references.paper_version_ids:
                    raise DomainInvariantError("graph edge references an unknown target version")
            elif target.entity_type is not _ENTITY_RELATION_TARGET[edge.relation_type]:
                raise DomainInvariantError("paper-to-entity relation has the wrong target type")
            if not self._has_paper_mention(edge, source=True):
                raise DomainInvariantError("graph edge source version lacks an owned paper mention")
            if edge.relation_type in PAPER_GRAPH_RELATION_TYPES and not self._has_paper_mention(
                edge, source=False
            ):
                raise DomainInvariantError("graph edge target version lacks an owned paper mention")

    def _has_paper_mention(self, edge: GraphEdge, *, source: bool) -> bool:
        entity_id = edge.source_entity_id if source else edge.target_entity_id
        version_id = edge.source_paper_version_id if source else edge.target_paper_version_id
        return any(
            mention.entity_id == entity_id
            and mention.paper_version_id == version_id
            and mention.analysis_id == edge.analysis_id
            and mention.comparison_id == edge.comparison_id
            for mention in self.mentions
        )


def namespace_knowledge_graph_bundle(
    bundle: KnowledgeGraphBundle,
    pipeline_execution_id: UUID,
) -> KnowledgeGraphBundle:
    """Salt publication occurrences while preserving shared graph entities."""

    mentions = tuple(
        replace(
            mention,
            id=stable_graph_entity_mention_id(
                mention.entity_id,
                mention.paper_version_id,
                analysis_id=mention.analysis_id,
                comparison_id=mention.comparison_id,
                pipeline_execution_id=pipeline_execution_id,
            ),
            pipeline_execution_id=pipeline_execution_id,
        )
        for mention in bundle.mentions
    )
    edges = tuple(
        replace(
            edge,
            id=stable_graph_edge_id(
                edge.source_entity_id,
                edge.target_entity_id,
                edge.relation_type.value,
                edge.source_paper_version_id,
                target_paper_version_id=edge.target_paper_version_id,
                analysis_id=edge.analysis_id,
                comparison_id=edge.comparison_id,
                paper_relation_id=edge.paper_relation_id,
                pipeline_execution_id=pipeline_execution_id,
            ),
            pipeline_execution_id=pipeline_execution_id,
        )
        for edge in bundle.edges
    )
    return KnowledgeGraphBundle(
        topic_id=bundle.topic_id,
        entities=bundle.entities,
        mentions=mentions,
        edges=edges,
        references=bundle.references,
    )


@dataclass(frozen=True, slots=True)
class GraphExtractionResult:
    bundle: KnowledgeGraphBundle
    omitted_entity_types: tuple[GraphEntityType, ...]

    def __post_init__(self) -> None:
        unique = tuple(sorted(set(self.omitted_entity_types), key=lambda item: item.value))
        object.__setattr__(self, "omitted_entity_types", unique)


def _model_provenance(
    provider: str,
    configured_model: str,
    model_version: str,
    prompt_version: str,
) -> GraphModelProvenance:
    return GraphModelProvenance(
        provider=provider,
        configured_model=configured_model,
        model_version=model_version,
        prompt_version=prompt_version,
    )


def _paper_entity(
    *,
    topic_id: UUID,
    paper_id: UUID,
    title: str,
    created_at: datetime,
) -> GraphEntity:
    label = _normalize_paper_title(title)
    return GraphEntity(
        id=stable_graph_paper_entity_id(topic_id, paper_id),
        topic_id=topic_id,
        entity_type=GraphEntityType.PAPER,
        paper_id=paper_id,
        canonical_label=label,
        normalized_key=f"paper:{paper_id}",
        display_label=label,
        aliases=(label,),
        provenance=RelationProvenance.METADATA_EXPLICIT,
        source="paper_metadata",
        schema_version=1,
        created_at=created_at,
        updated_at=created_at,
    )


def _named_entity(
    *,
    topic_id: UUID,
    entity_type: GraphEntityType,
    label: str,
    created_at: datetime,
) -> GraphEntity:
    if entity_type is GraphEntityType.PAPER:
        raise DomainInvariantError("named graph entities cannot use the paper type")
    display_label = normalize_entity_label(label)
    key = normalized_entity_key(display_label)
    return GraphEntity(
        id=stable_graph_entity_id(topic_id, entity_type.value, key),
        topic_id=topic_id,
        entity_type=entity_type,
        paper_id=None,
        canonical_label=display_label,
        normalized_key=key,
        display_label=display_label,
        aliases=(display_label,),
        provenance=RelationProvenance.DETERMINISTICALLY_DERIVED,
        source="canonical_entity_key_v1",
        schema_version=1,
        created_at=created_at,
        updated_at=created_at,
    )


def _mention(
    *,
    entity: GraphEntity,
    paper_id: UUID,
    paper_version_id: UUID,
    observed_label: str,
    provenance: RelationProvenance,
    evidence_ids: tuple[UUID, ...],
    model_provenance: GraphModelProvenance | None,
    verification_status: VerificationStatus,
    generated_at: datetime,
    created_at: datetime,
    analysis_id: UUID | None = None,
    comparison_id: UUID | None = None,
) -> GraphEntityMention:
    observed = (
        _normalize_paper_title(observed_label)
        if entity.entity_type is GraphEntityType.PAPER
        else normalize_entity_label(observed_label)
    )
    return GraphEntityMention(
        id=stable_graph_entity_mention_id(
            entity.id,
            paper_version_id,
            analysis_id=analysis_id,
            comparison_id=comparison_id,
        ),
        entity_id=entity.id,
        paper_id=paper_id,
        paper_version_id=paper_version_id,
        analysis_id=analysis_id,
        comparison_id=comparison_id,
        observed_label=observed,
        provenance=provenance,
        evidence_ids=evidence_ids,
        model_provenance=model_provenance,
        confidence=None,
        verification_status=verification_status,
        generated_at=generated_at,
        schema_version=1,
        created_at=created_at,
    )


def _entity_edge(
    *,
    paper_entity: GraphEntity,
    target_entity: GraphEntity,
    relation_type: GraphRelationType,
    paper_version_id: UUID,
    evidence_ids: tuple[UUID, ...],
    justification: str,
    model_provenance: GraphModelProvenance,
    verification_status: VerificationStatus,
    generated_at: datetime,
    created_at: datetime,
    analysis_id: UUID | None = None,
    comparison_id: UUID | None = None,
) -> GraphEdge:
    return GraphEdge(
        id=stable_graph_edge_id(
            paper_entity.id,
            target_entity.id,
            relation_type.value,
            paper_version_id,
            analysis_id=analysis_id,
            comparison_id=comparison_id,
        ),
        source_entity_id=paper_entity.id,
        target_entity_id=target_entity.id,
        relation_type=relation_type,
        source_paper_version_id=paper_version_id,
        target_paper_version_id=None,
        analysis_id=analysis_id,
        comparison_id=comparison_id,
        paper_relation_id=None,
        provenance=RelationProvenance.DETERMINISTICALLY_DERIVED,
        evidence_ids=evidence_ids,
        justification=justification,
        model_provenance=model_provenance,
        confidence=None,
        verification_status=verification_status,
        generated_at=generated_at,
        schema_version=1,
        created_at=created_at,
    )


def extract_analysis_graph(
    topic_id: UUID,
    analysis_bundle: AnalysisBundle,
    *,
    paper_title: str,
) -> GraphExtractionResult:
    """Project evidence-supported M2 analysis fields into an idempotent graph update."""

    analysis = analysis_bundle.analysis
    if analysis.verification_status is VerificationStatus.REJECTED:
        raise DomainInvariantError("rejected analysis cannot produce graph records")
    model = _model_provenance(
        analysis.provider,
        analysis.configured_model,
        analysis.model_version,
        analysis.prompt_version,
    )
    paper_entity = _paper_entity(
        topic_id=topic_id,
        paper_id=analysis.paper_id,
        title=paper_title,
        created_at=analysis.created_at,
    )
    entities = [paper_entity]
    mentions = [
        _mention(
            entity=paper_entity,
            paper_id=analysis.paper_id,
            paper_version_id=analysis.paper_version_id,
            observed_label=paper_title,
            provenance=RelationProvenance.METADATA_EXPLICIT,
            evidence_ids=(),
            model_provenance=None,
            verification_status=VerificationStatus.UNVERIFIED,
            generated_at=analysis.generated_at,
            created_at=analysis.created_at,
            analysis_id=analysis.id,
        )
    ]
    edges: list[GraphEdge] = []
    claims_by_type: dict[ClaimType, set[UUID]] = defaultdict(set)
    for claim in analysis_bundle.claims:
        if claim.verification_status is not VerificationStatus.REJECTED:
            claims_by_type[claim.claim_type].add(claim.id)
    evidence_by_type: dict[ClaimType, tuple[UUID, ...]] = {}
    for claim_type, claim_ids in claims_by_type.items():
        evidence_by_type[claim_type] = tuple(
            sorted(
                {
                    item.id
                    for item in analysis_bundle.evidence
                    if item.verification_status is not VerificationStatus.REJECTED
                    and set(item.supported_claim_ids).intersection(claim_ids)
                },
                key=str,
            )
        )
    candidates = (
        (
            GraphEntityType.RESEARCH_PROBLEM,
            GraphRelationType.ADDRESSES,
            analysis.research_problem,
            evidence_by_type.get(ClaimType.RESEARCH_PROBLEM, ()),
        ),
        (
            GraphEntityType.METHOD,
            GraphRelationType.USES_METHOD,
            analysis.method_summary,
            evidence_by_type.get(ClaimType.METHOD, ()),
        ),
    )
    omitted = {GraphEntityType.TASK, GraphEntityType.DATASET, GraphEntityType.BENCHMARK}
    for entity_type, relation_type, label, evidence_ids in candidates:
        if not evidence_ids:
            omitted.add(entity_type)
            continue
        try:
            label = normalize_entity_label(label)
        except DomainInvariantError as error:
            if "exceeds the persistence bound" not in str(error):
                raise
            omitted.add(entity_type)
            continue
        entity = _named_entity(
            topic_id=topic_id,
            entity_type=entity_type,
            label=label,
            created_at=analysis.created_at,
        )
        entities.append(entity)
        mentions.append(
            _mention(
                entity=entity,
                paper_id=analysis.paper_id,
                paper_version_id=analysis.paper_version_id,
                observed_label=label,
                provenance=RelationProvenance.DETERMINISTICALLY_DERIVED,
                evidence_ids=evidence_ids,
                model_provenance=model,
                verification_status=analysis.verification_status,
                generated_at=analysis.generated_at,
                created_at=analysis.created_at,
                analysis_id=analysis.id,
            )
        )
        edges.append(
            _entity_edge(
                paper_entity=paper_entity,
                target_entity=entity,
                relation_type=relation_type,
                paper_version_id=analysis.paper_version_id,
                evidence_ids=evidence_ids,
                justification=(
                    f"Deterministically projected from the validated {entity_type.value.lower()} "
                    "field of this analysis."
                ),
                model_provenance=model,
                verification_status=analysis.verification_status,
                generated_at=analysis.generated_at,
                created_at=analysis.created_at,
                analysis_id=analysis.id,
            )
        )
    references = GraphReferenceSet(
        paper_version_ids=(analysis.paper_version_id,),
        analysis_ids=(analysis.id,),
        evidence_ids=tuple(
            item.id
            for item in analysis_bundle.evidence
            if item.verification_status is not VerificationStatus.REJECTED
        ),
    )
    return GraphExtractionResult(
        bundle=KnowledgeGraphBundle(
            topic_id=topic_id,
            entities=tuple(entities),
            mentions=tuple(mentions),
            edges=tuple(edges),
            references=references,
        ),
        omitted_entity_types=tuple(omitted),
    )


_COMPARISON_ENTITY_MAP = {
    ComparisonDimensionName.RESEARCH_PROBLEM: (
        GraphEntityType.RESEARCH_PROBLEM,
        GraphRelationType.ADDRESSES,
    ),
    ComparisonDimensionName.METHOD: (GraphEntityType.METHOD, GraphRelationType.USES_METHOD),
    ComparisonDimensionName.TASK: (GraphEntityType.TASK, GraphRelationType.TARGETS_TASK),
    ComparisonDimensionName.DATASETS: (
        GraphEntityType.DATASET,
        GraphRelationType.USES_DATASET,
    ),
    ComparisonDimensionName.BENCHMARKS: (
        GraphEntityType.BENCHMARK,
        GraphRelationType.EVALUATES_ON,
    ),
}


def extract_comparison_graph(
    topic_id: UUID,
    comparison_bundle: ComparisonBundle,
    *,
    source_paper_title: str,
    target_paper_title: str,
) -> GraphExtractionResult:
    """Project evidence-supported M3 dimensions and relations into graph records."""

    comparison = comparison_bundle.comparison
    if comparison.verification_status is VerificationStatus.REJECTED:
        raise DomainInvariantError("rejected comparison cannot produce graph records")
    model = _model_provenance(
        comparison.provider,
        comparison.configured_model,
        comparison.model_version,
        comparison.prompt_version,
    )
    source_paper = _paper_entity(
        topic_id=topic_id,
        paper_id=comparison.source_paper_id,
        title=source_paper_title,
        created_at=comparison.created_at,
    )
    target_paper = _paper_entity(
        topic_id=topic_id,
        paper_id=comparison.target_paper_id,
        title=target_paper_title,
        created_at=comparison.created_at,
    )
    entities = [source_paper, target_paper]
    mentions = [
        _mention(
            entity=paper_entity,
            paper_id=paper_id,
            paper_version_id=paper_version_id,
            observed_label=title,
            provenance=RelationProvenance.METADATA_EXPLICIT,
            evidence_ids=(),
            model_provenance=None,
            verification_status=VerificationStatus.UNVERIFIED,
            generated_at=comparison.generated_at,
            created_at=comparison.created_at,
            comparison_id=comparison.id,
        )
        for paper_entity, paper_id, paper_version_id, title in (
            (
                source_paper,
                comparison.source_paper_id,
                comparison.source_paper_version_id,
                source_paper_title,
            ),
            (
                target_paper,
                comparison.target_paper_id,
                comparison.target_paper_version_id,
                target_paper_title,
            ),
        )
    ]
    edges: list[GraphEdge] = []
    materialized_types: set[GraphEntityType] = set()
    for dimension in comparison.dimensions:
        mapping = _COMPARISON_ENTITY_MAP.get(dimension.name)
        if mapping is None:
            continue
        entity_type, relation_type = mapping
        for paper_entity, paper_id, paper_version_id, value, evidence_ids in (
            (
                source_paper,
                comparison.source_paper_id,
                comparison.source_paper_version_id,
                dimension.source_value,
                dimension.source_evidence_ids,
            ),
            (
                target_paper,
                comparison.target_paper_id,
                comparison.target_paper_version_id,
                dimension.target_value,
                dimension.target_evidence_ids,
            ),
        ):
            if not evidence_ids:
                continue
            try:
                value = normalize_entity_label(value)
            except DomainInvariantError as error:
                if "exceeds the persistence bound" not in str(error):
                    raise
                continue
            entity = _named_entity(
                topic_id=topic_id,
                entity_type=entity_type,
                label=value,
                created_at=comparison.created_at,
            )
            materialized_types.add(entity_type)
            entities.append(entity)
            mentions.append(
                _mention(
                    entity=entity,
                    paper_id=paper_id,
                    paper_version_id=paper_version_id,
                    observed_label=value,
                    provenance=RelationProvenance.DETERMINISTICALLY_DERIVED,
                    evidence_ids=evidence_ids,
                    model_provenance=model,
                    verification_status=comparison.verification_status,
                    generated_at=comparison.generated_at,
                    created_at=comparison.created_at,
                    comparison_id=comparison.id,
                )
            )
            edges.append(
                _entity_edge(
                    paper_entity=paper_entity,
                    target_entity=entity,
                    relation_type=relation_type,
                    paper_version_id=paper_version_id,
                    evidence_ids=evidence_ids,
                    justification=(
                        f"Deterministically projected from the validated {dimension.name.value} "
                        "comparison dimension."
                    ),
                    model_provenance=model,
                    verification_status=comparison.verification_status,
                    generated_at=comparison.generated_at,
                    created_at=comparison.created_at,
                    comparison_id=comparison.id,
                )
            )
    for relation in comparison_bundle.relations:
        relation_type = _PAPER_RELATION_MAP[relation.relation_type]
        relation_model = model if relation.provenance is RelationProvenance.LLM_INFERRED else None
        edges.append(
            GraphEdge(
                id=stable_graph_edge_id(
                    source_paper.id,
                    target_paper.id,
                    relation_type.value,
                    relation.source_paper_version_id,
                    target_paper_version_id=relation.target_paper_version_id,
                    comparison_id=comparison.id,
                    paper_relation_id=relation.id,
                ),
                source_entity_id=source_paper.id,
                target_entity_id=target_paper.id,
                relation_type=relation_type,
                source_paper_version_id=relation.source_paper_version_id,
                target_paper_version_id=relation.target_paper_version_id,
                analysis_id=None,
                comparison_id=comparison.id,
                paper_relation_id=relation.id,
                provenance=relation.provenance,
                evidence_ids=relation.evidence_ids,
                justification=relation.justification,
                model_provenance=relation_model,
                confidence=relation.confidence,
                verification_status=relation.verification_status,
                generated_at=relation.generated_at,
                schema_version=relation.schema_version,
                created_at=relation.created_at,
            )
        )
    all_entity_types = set(_COMPARISON_ENTITY_MAP.values())
    expected_types = {entity_type for entity_type, _relation_type in all_entity_types}
    evidence_ids = {
        evidence_id
        for dimension in comparison.dimensions
        for evidence_id in dimension.source_evidence_ids + dimension.target_evidence_ids
    }
    return GraphExtractionResult(
        bundle=KnowledgeGraphBundle(
            topic_id=topic_id,
            entities=tuple(entities),
            mentions=tuple(mentions),
            edges=tuple(edges),
            references=GraphReferenceSet(
                paper_version_ids=(
                    comparison.source_paper_version_id,
                    comparison.target_paper_version_id,
                ),
                analysis_ids=(comparison.source_analysis_id, comparison.target_analysis_id),
                comparison_ids=(comparison.id,),
                paper_relation_ids=tuple(item.id for item in comparison_bundle.relations),
                evidence_ids=tuple(evidence_ids),
            ),
        ),
        omitted_entity_types=tuple(expected_types - materialized_types),
    )


def merge_knowledge_graph_bundles(
    bundles: Iterable[KnowledgeGraphBundle],
) -> KnowledgeGraphBundle:
    """Combine repeated extraction updates deterministically and reject conflicts."""

    values = tuple(bundles)
    if not values:
        raise DomainInvariantError("at least one knowledge graph bundle is required")
    topic_id = values[0].topic_id
    if any(bundle.topic_id != topic_id for bundle in values[1:]):
        raise DomainInvariantError("knowledge graph updates cannot cross topic boundaries")
    return KnowledgeGraphBundle(
        topic_id=topic_id,
        entities=tuple(entity for bundle in values for entity in bundle.entities),
        mentions=tuple(mention for bundle in values for mention in bundle.mentions),
        edges=tuple(edge for bundle in values for edge in bundle.edges),
        references=GraphReferenceSet(
            paper_version_ids=tuple(
                value for bundle in values for value in bundle.references.paper_version_ids
            ),
            analysis_ids=tuple(
                value for bundle in values for value in bundle.references.analysis_ids
            ),
            comparison_ids=tuple(
                value for bundle in values for value in bundle.references.comparison_ids
            ),
            paper_relation_ids=tuple(
                value for bundle in values for value in bundle.references.paper_relation_ids
            ),
            evidence_ids=tuple(
                value for bundle in values for value in bundle.references.evidence_ids
            ),
        ),
    )


class LineageCorpusScope(StrEnum):
    CURRENTLY_RETRIEVED_CORPUS = "CURRENTLY_RETRIEVED_CORPUS"


@dataclass(frozen=True, slots=True)
class LineagePaper:
    graph_entity_id: UUID
    paper_id: UUID
    title: str
    publication_date: date | None

    def __post_init__(self) -> None:
        _require_text(self.title, "lineage paper title", maximum=MAX_PAPER_TITLE_LENGTH)


@dataclass(frozen=True, slots=True)
class LineageNode:
    graph_entity_id: UUID
    paper_id: UUID
    title: str
    publication_date: date | None
    depth: int

    def __post_init__(self) -> None:
        _require_text(self.title, "lineage node title", maximum=MAX_PAPER_TITLE_LENGTH)
        if self.depth < 0:
            raise DomainInvariantError("lineage node depth cannot be negative")


@dataclass(frozen=True, slots=True)
class LineageSnapshot:
    id: UUID
    topic_id: UUID
    root_paper_id: UUID
    as_of_date: date
    nodes: tuple[LineageNode, ...]
    edges: tuple[GraphEdge, ...]
    permitted_relation_types: tuple[GraphRelationType, ...]
    max_depth: int
    max_nodes: int
    max_edges: int
    truncated: bool
    explicit_predecessor_available: bool
    verified_predecessor_available: bool
    corpus_scope: LineageCorpusScope
    limitations: tuple[str, ...]
    lineage_version: str
    generated_at: datetime
    schema_version: int
    pipeline_execution_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(sorted(self.nodes, key=_lineage_node_sort_key)))
        object.__setattr__(
            self,
            "permitted_relation_types",
            tuple(sorted(set(self.permitted_relation_types), key=lambda item: item.value)),
        )
        if not 1 <= self.max_depth <= MAX_LINEAGE_DEPTH:
            raise DomainInvariantError("lineage max depth is outside the supported bound")
        if not 1 <= self.max_nodes <= MAX_LINEAGE_NODES:
            raise DomainInvariantError("lineage max nodes is outside the supported bound")
        if not 1 <= self.max_edges <= MAX_LINEAGE_EDGES:
            raise DomainInvariantError("lineage max edges is outside the supported bound")
        if not self.nodes or len(self.nodes) > self.max_nodes:
            raise DomainInvariantError("lineage snapshot node count is invalid")
        if len({item.graph_entity_id for item in self.nodes}) != len(self.nodes):
            raise DomainInvariantError("lineage paper entities must be unique")
        if len({item.paper_id for item in self.nodes}) != len(self.nodes):
            raise DomainInvariantError("lineage papers must be unique")
        root_nodes = [item for item in self.nodes if item.paper_id == self.root_paper_id]
        if len(root_nodes) != 1 or root_nodes[0].depth != 0:
            raise DomainInvariantError("lineage root must appear exactly once at depth zero")
        if any(item.depth > self.max_depth for item in self.nodes):
            raise DomainInvariantError("lineage node exceeds the configured depth")
        permitted = tuple(sorted(set(self.permitted_relation_types), key=lambda item: item.value))
        if not permitted or any(item not in PAPER_GRAPH_RELATION_TYPES for item in permitted):
            raise DomainInvariantError("lineage permits only explicit paper relation types")
        node_ids = {item.graph_entity_id for item in self.nodes}
        nodes_by_id = {item.graph_entity_id: item for item in self.nodes}
        if len(self.edges) > self.max_edges:
            raise DomainInvariantError("lineage snapshot edge count exceeds its bound")
        if len({item.id for item in self.edges}) != len(self.edges):
            raise DomainInvariantError("lineage edges must be unique")
        for edge in self.edges:
            if (
                edge.relation_type not in permitted
                or edge.source_entity_id not in node_ids
                or edge.target_entity_id not in node_ids
            ):
                raise DomainInvariantError("lineage edge is outside the bounded snapshot")
            source_depth = nodes_by_id[edge.source_entity_id].depth
            target_depth = nodes_by_id[edge.target_entity_id].depth
            if target_depth != source_depth + 1:
                raise DomainInvariantError("lineage edges must point to the next predecessor depth")
        reachable = {root_nodes[0].graph_entity_id}
        for depth in range(self.max_depth):
            reachable.update(
                edge.target_entity_id
                for edge in self.edges
                if edge.source_entity_id in reachable
                and nodes_by_id[edge.source_entity_id].depth == depth
            )
        if reachable != node_ids:
            raise DomainInvariantError("lineage snapshot contains an unreachable node")
        _require_text(self.lineage_version, "lineage version", maximum=100)
        for limitation in self.limitations:
            _require_text(limitation, "lineage limitation", maximum=500)
        _require_aware(self.generated_at, "generated_at")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")
        expected_id = stable_lineage_snapshot_id(
            self.topic_id,
            self.root_paper_id,
            self.as_of_date,
            permitted_relation_types=tuple(item.value for item in permitted),
            max_depth=self.max_depth,
            max_nodes=self.max_nodes,
            max_edges=self.max_edges,
            lineage_version=self.lineage_version,
            pipeline_execution_id=self.pipeline_execution_id,
        )
        if self.id != expected_id:
            raise DomainInvariantError("lineage snapshot ID is not stable for its scope")


def namespace_lineage_snapshot(
    snapshot: LineageSnapshot,
    pipeline_execution_id: UUID,
) -> LineageSnapshot:
    return replace(
        snapshot,
        id=stable_lineage_snapshot_id(
            snapshot.topic_id,
            snapshot.root_paper_id,
            snapshot.as_of_date,
            permitted_relation_types=tuple(
                item.value for item in snapshot.permitted_relation_types
            ),
            max_depth=snapshot.max_depth,
            max_nodes=snapshot.max_nodes,
            max_edges=snapshot.max_edges,
            lineage_version=snapshot.lineage_version,
            pipeline_execution_id=pipeline_execution_id,
        ),
        pipeline_execution_id=pipeline_execution_id,
    )


def _lineage_paper_sort_key(item: LineagePaper) -> tuple[bool, date, str]:
    return (
        item.publication_date is None,
        item.publication_date or date.max,
        str(item.paper_id),
    )


def _lineage_node_sort_key(item: LineageNode) -> tuple[bool, date, str]:
    return (
        item.publication_date is None,
        item.publication_date or date.max,
        str(item.paper_id),
    )


def build_lineage_snapshot(
    topic_id: UUID,
    root_paper_id: UUID,
    *,
    as_of_date: date,
    papers: Iterable[LineagePaper],
    edges: Iterable[GraphEdge],
    generated_at: datetime,
    max_depth: int = 3,
    max_nodes: int = 50,
    max_edges: int = 200,
    permitted_relation_types: Iterable[GraphRelationType] = DEFAULT_LINEAGE_RELATION_TYPES,
    lineage_version: str = "lineage_v1",
) -> LineageSnapshot:
    """Build a cycle-safe bounded predecessor lineage from the supplied corpus."""

    if not 1 <= max_depth <= MAX_LINEAGE_DEPTH:
        raise DomainInvariantError("lineage max depth is outside the supported bound")
    if not 1 <= max_nodes <= MAX_LINEAGE_NODES:
        raise DomainInvariantError("lineage max nodes is outside the supported bound")
    if not 1 <= max_edges <= MAX_LINEAGE_EDGES:
        raise DomainInvariantError("lineage max edges is outside the supported bound")
    _require_aware(generated_at, "generated_at")
    paper_values = tuple(papers)
    papers_by_entity = {item.graph_entity_id: item for item in paper_values}
    if len(papers_by_entity) != len(paper_values) or len(
        {item.paper_id for item in paper_values}
    ) != len(paper_values):
        raise DomainInvariantError("lineage input papers must have unique entities and paper IDs")
    root_candidates = [item for item in paper_values if item.paper_id == root_paper_id]
    if len(root_candidates) != 1:
        raise DomainInvariantError("lineage root paper must be present exactly once")
    root = root_candidates[0]
    if root.publication_date is not None and root.publication_date > as_of_date:
        raise DomainInvariantError("lineage root publication date is after the snapshot date")
    permitted = tuple(sorted(set(permitted_relation_types), key=lambda item: item.value))
    if not permitted or any(item not in PAPER_GRAPH_RELATION_TYPES for item in permitted):
        raise DomainInvariantError("lineage permits only paper relation types")
    eligible_paper_ids = {
        item.graph_entity_id
        for item in paper_values
        if item.publication_date is None or item.publication_date <= as_of_date
    }
    chronology_excluded = 0
    eligible_edge_values: list[GraphEdge] = []
    for edge in edges:
        if (
            edge.relation_type not in permitted
            or edge.verification_status is VerificationStatus.REJECTED
            or edge.source_entity_id not in eligible_paper_ids
            or edge.target_entity_id not in eligible_paper_ids
        ):
            continue
        source_date = papers_by_entity[edge.source_entity_id].publication_date
        target_date = papers_by_entity[edge.target_entity_id].publication_date
        if source_date is not None and target_date is not None and target_date > source_date:
            chronology_excluded += 1
            continue
        eligible_edge_values.append(edge)
    eligible_edges = tuple(
        sorted(
            eligible_edge_values,
            key=lambda item: (
                item.relation_type.value,
                str(item.source_entity_id),
                str(item.target_entity_id),
                str(item.id),
            ),
        )
    )
    adjacency: dict[UUID, list[GraphEdge]] = defaultdict(list)
    for edge in eligible_edges:
        adjacency[edge.source_entity_id].append(edge)
    for entity_id, outgoing_edges in adjacency.items():
        adjacency[entity_id] = sorted(
            outgoing_edges,
            key=lambda edge: (
                _lineage_paper_sort_key(papers_by_entity[edge.target_entity_id]),
                edge.relation_type.value,
                str(edge.id),
            ),
        )
    selected_depths = {root.graph_entity_id: 0}
    tree_edges: list[GraphEdge] = []
    queue: deque[UUID] = deque((root.graph_entity_id,))
    truncated = False
    while queue:
        entity_id = queue.popleft()
        depth = selected_depths[entity_id]
        outgoing_edges = adjacency.get(entity_id, ())
        if depth >= max_depth:
            if any(edge.target_entity_id not in selected_depths for edge in outgoing_edges):
                truncated = True
            continue
        for edge in outgoing_edges:
            neighbor = edge.target_entity_id
            if neighbor in selected_depths:
                continue
            if len(selected_depths) >= max_nodes or len(tree_edges) >= max_edges:
                truncated = True
                continue
            selected_depths[neighbor] = depth + 1
            tree_edges.append(edge)
            queue.append(neighbor)
    selected_ids = set(selected_depths)
    forward_edges = tuple(
        edge
        for edge in eligible_edges
        if edge.source_entity_id in selected_ids
        and edge.target_entity_id in selected_ids
        and selected_depths[edge.target_entity_id] == selected_depths[edge.source_entity_id] + 1
    )
    tree_edge_ids = {edge.id for edge in tree_edges}
    additional_edges = tuple(edge for edge in forward_edges if edge.id not in tree_edge_ids)
    if len(tree_edges) + len(additional_edges) > max_edges:
        truncated = True
    selected_edges = tuple(tree_edges) + additional_edges[: max_edges - len(tree_edges)]
    nodes = tuple(
        sorted(
            (
                LineageNode(
                    graph_entity_id=entity_id,
                    paper_id=paper.paper_id,
                    title=paper.title,
                    publication_date=paper.publication_date,
                    depth=selected_depths[entity_id],
                )
                for entity_id in selected_ids
                for paper in (papers_by_entity[entity_id],)
            ),
            key=_lineage_node_sort_key,
        )
    )
    predecessor_relations = {
        GraphRelationType.CITES,
        GraphRelationType.EXTENDS,
        GraphRelationType.IMPROVES_ON,
    }
    predecessor_edges = tuple(
        edge
        for edge in selected_edges
        if edge.source_entity_id == root.graph_entity_id
        and edge.relation_type in predecessor_relations
    )
    explicit_provenance = {
        RelationProvenance.METADATA_EXPLICIT,
        RelationProvenance.TEXT_EXPLICIT,
        RelationProvenance.HUMAN_VERIFIED,
    }
    explicit_predecessor_available = any(
        edge.provenance in explicit_provenance for edge in predecessor_edges
    )
    verified_predecessor_available = any(
        edge.verification_status is VerificationStatus.HUMAN_VERIFIED
        or edge.provenance is RelationProvenance.HUMAN_VERIFIED
        for edge in predecessor_edges
    )
    limitations = [
        "Lineage among the currently retrieved corpus; global completeness is not claimed."
    ]
    if not verified_predecessor_available:
        limitations.append("No verified predecessor relation is currently available.")
    if any(node.publication_date is None for node in nodes):
        limitations.append("Some publication or version dates are unavailable.")
    if chronology_excluded:
        limitations.append(
            "Relations pointing to a later-dated paper were excluded from predecessor lineage."
        )
    if any(edge.provenance is RelationProvenance.LLM_INFERRED for edge in selected_edges):
        limitations.append(
            "AI-inferred edges are unverified unless their verification status says otherwise."
        )
    if truncated:
        limitations.append("Traversal was truncated by the configured depth, node, or edge bound.")
    return LineageSnapshot(
        id=stable_lineage_snapshot_id(
            topic_id,
            root_paper_id,
            as_of_date,
            permitted_relation_types=tuple(item.value for item in permitted),
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_edges=max_edges,
            lineage_version=lineage_version,
        ),
        topic_id=topic_id,
        root_paper_id=root_paper_id,
        as_of_date=as_of_date,
        nodes=nodes,
        edges=selected_edges,
        permitted_relation_types=permitted,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_edges=max_edges,
        truncated=truncated,
        explicit_predecessor_available=explicit_predecessor_available,
        verified_predecessor_available=verified_predecessor_available,
        corpus_scope=LineageCorpusScope.CURRENTLY_RETRIEVED_CORPUS,
        limitations=tuple(limitations),
        lineage_version=lineage_version,
        generated_at=generated_at,
        schema_version=1,
    )


class TrendWindow(StrEnum):
    SEVEN_DAYS = "7D"
    THIRTY_DAYS = "30D"
    NINETY_DAYS = "90D"

    @property
    def days(self) -> int:
        return {
            TrendWindow.SEVEN_DAYS: 7,
            TrendWindow.THIRTY_DAYS: 30,
            TrendWindow.NINETY_DAYS: 90,
        }[self]


class TrendDataSufficiency(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    LIMITED = "LIMITED"
    INSUFFICIENT = "INSUFFICIENT"


class TrendGrowthStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    ZERO_DENOMINATOR = "ZERO_DENOMINATOR"
    LIMITED_SAMPLE = "LIMITED_SAMPLE"


@dataclass(frozen=True, slots=True)
class TrendThresholds:
    limited_paper_count: int = 3
    sufficient_paper_count: int = 10
    minimum_growth_denominator: int = 3

    def __post_init__(self) -> None:
        if not 1 <= self.limited_paper_count <= self.sufficient_paper_count:
            raise DomainInvariantError("trend sufficiency thresholds are inconsistent")
        if self.minimum_growth_denominator < 1:
            raise DomainInvariantError("trend growth denominator threshold must be positive")


DEFAULT_TREND_THRESHOLDS = TrendThresholds()


@dataclass(frozen=True, slots=True)
class TrendChange:
    current_count: int
    preceding_count: int
    absolute_change: int
    denominator_count: int
    relative_change: Decimal | None
    growth_status: TrendGrowthStatus

    def __post_init__(self) -> None:
        if self.current_count < 0 or self.preceding_count < 0:
            raise DomainInvariantError("trend counts cannot be negative")
        if self.absolute_change != self.current_count - self.preceding_count:
            raise DomainInvariantError("trend absolute change is inconsistent")
        if self.denominator_count != self.preceding_count:
            raise DomainInvariantError("trend denominator must be the preceding count")
        if self.growth_status is TrendGrowthStatus.AVAILABLE:
            if self.preceding_count == 0 or self.relative_change is None:
                raise DomainInvariantError("available trend growth requires a nonzero denominator")
        elif self.relative_change is not None:
            raise DomainInvariantError("unavailable trend growth cannot carry a relative value")
        if self.growth_status is TrendGrowthStatus.ZERO_DENOMINATOR and self.denominator_count != 0:
            raise DomainInvariantError("zero-denominator trend status requires a zero denominator")


@dataclass(frozen=True, slots=True)
class TrendEntityCount:
    entity_id: UUID
    entity_type: GraphEntityType
    label: str
    change: TrendChange
    newly_appearing: bool
    recurring: bool

    def __post_init__(self) -> None:
        if self.entity_type is GraphEntityType.PAPER:
            raise DomainInvariantError("paper volume is stored separately from entity counts")
        _require_text(self.label, "trend entity label", maximum=MAX_GRAPH_LABEL_LENGTH)
        if self.newly_appearing != (
            self.change.current_count > 0 and self.change.preceding_count == 0
        ):
            raise DomainInvariantError("new entity flag is inconsistent with its counts")
        if self.recurring != (self.change.current_count > 0 and self.change.preceding_count > 0):
            raise DomainInvariantError("recurring entity flag is inconsistent with its counts")


@dataclass(frozen=True, slots=True)
class TrendRelationCount:
    relation_type: GraphRelationType
    change: TrendChange


@dataclass(frozen=True, slots=True)
class TrendPaperRecord:
    paper_id: UUID
    paper_version_id: UUID
    activity_date: date
    title: str

    def __post_init__(self) -> None:
        _require_text(self.title, "trend paper title", maximum=MAX_PAPER_TITLE_LENGTH)


@dataclass(frozen=True, slots=True)
class TrendSnapshot:
    id: UUID
    topic_id: UUID
    as_of_date: date
    window: TrendWindow
    window_start: date
    window_end: date
    preceding_window_start: date
    preceding_window_end: date
    included_paper_count: int
    preceding_paper_count: int
    paper_count_change: TrendChange
    entity_counts: tuple[TrendEntityCount, ...]
    relation_counts: tuple[TrendRelationCount, ...]
    new_entity_ids: tuple[UUID, ...]
    recurring_entity_ids: tuple[UUID, ...]
    representative_paper_ids: tuple[UUID, ...]
    data_sufficiency: TrendDataSufficiency
    preceding_data_sufficiency: TrendDataSufficiency
    thresholds: TrendThresholds
    aggregation_version: str
    generated_at: datetime
    schema_version: int
    pipeline_execution_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "new_entity_ids", tuple(sorted(set(self.new_entity_ids), key=str)))
        object.__setattr__(
            self,
            "recurring_entity_ids",
            tuple(sorted(set(self.recurring_entity_ids), key=str)),
        )
        object.__setattr__(
            self,
            "representative_paper_ids",
            tuple(dict.fromkeys(self.representative_paper_ids)),
        )
        expected_start = self.as_of_date - timedelta(days=self.window.days - 1)
        expected_preceding_end = expected_start - timedelta(days=1)
        expected_preceding_start = expected_preceding_end - timedelta(days=self.window.days - 1)
        if (
            self.window_start != expected_start
            or self.window_end != self.as_of_date
            or self.preceding_window_start != expected_preceding_start
            or self.preceding_window_end != expected_preceding_end
        ):
            raise DomainInvariantError("trend snapshot window bounds are not exact")
        if (
            self.included_paper_count != self.paper_count_change.current_count
            or self.preceding_paper_count != self.paper_count_change.preceding_count
        ):
            raise DomainInvariantError("trend paper comparison does not match snapshot totals")
        if self.data_sufficiency is not _data_sufficiency(
            self.included_paper_count, self.thresholds
        ) or self.preceding_data_sufficiency is not _data_sufficiency(
            self.preceding_paper_count, self.thresholds
        ):
            raise DomainInvariantError("trend data sufficiency is inconsistent with thresholds")
        if len({item.entity_id for item in self.entity_counts}) != len(self.entity_counts):
            raise DomainInvariantError("trend entity counts must be unique")
        if len({item.relation_type for item in self.relation_counts}) != len(self.relation_counts):
            raise DomainInvariantError("trend relation counts must be unique")
        if set(self.new_entity_ids).intersection(self.recurring_entity_ids):
            raise DomainInvariantError("new and recurring trend entity sets cannot overlap")
        _require_text(self.aggregation_version, "trend aggregation version", maximum=100)
        _require_aware(self.generated_at, "generated_at")
        if self.schema_version < 1:
            raise DomainInvariantError("schema_version must be positive")
        expected_id = stable_trend_snapshot_id(
            self.topic_id,
            self.as_of_date,
            self.window.value,
            self.aggregation_version,
            pipeline_execution_id=self.pipeline_execution_id,
        )
        if self.id != expected_id:
            raise DomainInvariantError("trend snapshot ID is not stable for its scope")


def namespace_trend_snapshot(
    snapshot: TrendSnapshot,
    pipeline_execution_id: UUID,
) -> TrendSnapshot:
    return replace(
        snapshot,
        id=stable_trend_snapshot_id(
            snapshot.topic_id,
            snapshot.as_of_date,
            snapshot.window.value,
            snapshot.aggregation_version,
            pipeline_execution_id=pipeline_execution_id,
        ),
        pipeline_execution_id=pipeline_execution_id,
    )


def _data_sufficiency(
    paper_count: int,
    thresholds: TrendThresholds,
) -> TrendDataSufficiency:
    if paper_count >= thresholds.sufficient_paper_count:
        return TrendDataSufficiency.SUFFICIENT
    if paper_count >= thresholds.limited_paper_count:
        return TrendDataSufficiency.LIMITED
    return TrendDataSufficiency.INSUFFICIENT


def _trend_change(
    current_count: int,
    preceding_count: int,
    *,
    current_paper_count: int,
    preceding_paper_count: int,
    thresholds: TrendThresholds,
) -> TrendChange:
    absolute_change = current_count - preceding_count
    if preceding_count == 0:
        return TrendChange(
            current_count=current_count,
            preceding_count=preceding_count,
            absolute_change=absolute_change,
            denominator_count=0,
            relative_change=None,
            growth_status=TrendGrowthStatus.ZERO_DENOMINATOR,
        )
    if (
        preceding_count < thresholds.minimum_growth_denominator
        or current_paper_count < thresholds.limited_paper_count
        or preceding_paper_count < thresholds.limited_paper_count
    ):
        return TrendChange(
            current_count=current_count,
            preceding_count=preceding_count,
            absolute_change=absolute_change,
            denominator_count=preceding_count,
            relative_change=None,
            growth_status=TrendGrowthStatus.LIMITED_SAMPLE,
        )
    with localcontext() as context:
        context.prec = 28
        relative_change = (Decimal(absolute_change) / Decimal(preceding_count)).quantize(
            Decimal("0.000001"),
            rounding=ROUND_HALF_EVEN,
        )
    return TrendChange(
        current_count=current_count,
        preceding_count=preceding_count,
        absolute_change=absolute_change,
        denominator_count=preceding_count,
        relative_change=relative_change,
        growth_status=TrendGrowthStatus.AVAILABLE,
    )


def _in_window(value: date, start: date, end: date) -> bool:
    return start <= value <= end


def aggregate_trend_snapshots(
    topic_id: UUID,
    *,
    as_of_date: date,
    papers: Iterable[TrendPaperRecord],
    entities: Iterable[GraphEntity],
    mentions: Iterable[GraphEntityMention],
    edges: Iterable[GraphEdge],
    mention_activity_dates: Mapping[UUID, date],
    edge_activity_dates: Mapping[UUID, date],
    generated_at: datetime,
    thresholds: TrendThresholds = DEFAULT_TREND_THRESHOLDS,
    representative_limit: int = 5,
    aggregation_version: str = "trend_v1",
    windows: Iterable[TrendWindow] = tuple(TrendWindow),
) -> tuple[TrendSnapshot, ...]:
    """Aggregate exact 7/30/90-day snapshots from persisted structured records."""

    _require_aware(generated_at, "generated_at")
    if not 1 <= representative_limit <= MAX_REPRESENTATIVE_PAPERS:
        raise DomainInvariantError("representative paper limit is outside the supported bound")
    window_values = tuple(sorted(set(windows), key=lambda item: item.days))
    if not window_values:
        raise DomainInvariantError("at least one trend window is required")
    paper_values = tuple(papers)
    papers_by_version = {item.paper_version_id: item for item in paper_values}
    if len(papers_by_version) != len(paper_values):
        raise DomainInvariantError("trend paper versions must be unique")
    entity_values = tuple(entities)
    entities_by_id = {item.id: item for item in entity_values}
    if len(entities_by_id) != len(entity_values):
        raise DomainInvariantError("trend graph entities must be unique")
    if any(entity.topic_id != topic_id for entity in entity_values):
        raise DomainInvariantError("trend graph entities cannot cross topic boundaries")
    all_mention_values = tuple(mentions)
    mention_ids = {item.id for item in all_mention_values}
    if len(mention_ids) != len(all_mention_values):
        raise DomainInvariantError("trend graph mentions must be unique")
    if not mention_ids.issubset(mention_activity_dates):
        raise DomainInvariantError("trend aggregation is missing mention activity dates")
    if any(type(value) is not date for value in mention_activity_dates.values()):
        raise DomainInvariantError("trend mention activity dates must be dates")
    all_edge_values = tuple(edges)
    edge_ids = {item.id for item in all_edge_values}
    if len(edge_ids) != len(all_edge_values):
        raise DomainInvariantError("trend graph edges must be unique")
    if not edge_ids.issubset(edge_activity_dates):
        raise DomainInvariantError("trend aggregation is missing edge activity dates")
    if any(type(value) is not date for value in edge_activity_dates.values()):
        raise DomainInvariantError("trend edge activity dates must be dates")
    mention_values = tuple(
        mention
        for mention in all_mention_values
        if mention.verification_status is not VerificationStatus.REJECTED
    )
    edge_values = tuple(
        edge
        for edge in all_edge_values
        if edge.verification_status is not VerificationStatus.REJECTED
    )
    if any(mention.entity_id not in entities_by_id for mention in mention_values):
        raise DomainInvariantError("trend mention references an unknown graph entity")
    if any(
        edge.source_entity_id not in entities_by_id or edge.target_entity_id not in entities_by_id
        for edge in edge_values
    ):
        raise DomainInvariantError("trend edge references an unknown graph entity")
    if any(mention.paper_version_id not in papers_by_version for mention in mention_values):
        raise DomainInvariantError("trend mention references an unknown paper version")
    if any(edge.source_paper_version_id not in papers_by_version for edge in edge_values):
        raise DomainInvariantError("trend edge references an unknown source paper version")
    snapshots = tuple(
        _aggregate_trend_window(
            topic_id=topic_id,
            as_of_date=as_of_date,
            window=window,
            papers=paper_values,
            papers_by_version=papers_by_version,
            entities_by_id=entities_by_id,
            mentions=mention_values,
            edges=edge_values,
            mention_activity_dates=mention_activity_dates,
            edge_activity_dates=edge_activity_dates,
            generated_at=generated_at,
            thresholds=thresholds,
            representative_limit=representative_limit,
            aggregation_version=aggregation_version,
        )
        for window in window_values
    )
    return snapshots


def _aggregate_trend_window(
    *,
    topic_id: UUID,
    as_of_date: date,
    window: TrendWindow,
    papers: tuple[TrendPaperRecord, ...],
    papers_by_version: Mapping[UUID, TrendPaperRecord],
    entities_by_id: Mapping[UUID, GraphEntity],
    mentions: tuple[GraphEntityMention, ...],
    edges: tuple[GraphEdge, ...],
    mention_activity_dates: Mapping[UUID, date],
    edge_activity_dates: Mapping[UUID, date],
    generated_at: datetime,
    thresholds: TrendThresholds,
    representative_limit: int,
    aggregation_version: str,
) -> TrendSnapshot:
    window_start = as_of_date - timedelta(days=window.days - 1)
    preceding_end = window_start - timedelta(days=1)
    preceding_start = preceding_end - timedelta(days=window.days - 1)
    current_paper_ids = {
        item.paper_id for item in papers if _in_window(item.activity_date, window_start, as_of_date)
    }
    preceding_paper_ids = {
        item.paper_id
        for item in papers
        if _in_window(item.activity_date, preceding_start, preceding_end)
    }
    current_entity_papers: dict[UUID, set[UUID]] = defaultdict(set)
    preceding_entity_papers: dict[UUID, set[UUID]] = defaultdict(set)
    for mention in mentions:
        entity = entities_by_id[mention.entity_id]
        if entity.entity_type is GraphEntityType.PAPER:
            continue
        paper = papers_by_version[mention.paper_version_id]
        activity_date = mention_activity_dates[mention.id]
        if _in_window(activity_date, window_start, as_of_date):
            current_entity_papers[entity.id].add(paper.paper_id)
        elif _in_window(activity_date, preceding_start, preceding_end):
            preceding_entity_papers[entity.id].add(paper.paper_id)
    current_relation_keys: dict[GraphRelationType, set[tuple[UUID, UUID, GraphRelationType]]] = (
        defaultdict(set)
    )
    preceding_relation_keys: dict[GraphRelationType, set[tuple[UUID, UUID, GraphRelationType]]] = (
        defaultdict(set)
    )
    relation_paper_scores: dict[UUID, set[tuple[UUID, UUID, GraphRelationType]]] = defaultdict(set)
    for edge in edges:
        paper = papers_by_version[edge.source_paper_version_id]
        activity_date = edge_activity_dates[edge.id]
        key = (edge.source_entity_id, edge.target_entity_id, edge.relation_type)
        if _in_window(activity_date, window_start, as_of_date):
            current_relation_keys[edge.relation_type].add(key)
            relation_paper_scores[paper.paper_id].add(key)
        elif _in_window(activity_date, preceding_start, preceding_end):
            preceding_relation_keys[edge.relation_type].add(key)
    current_count = len(current_paper_ids)
    preceding_count = len(preceding_paper_ids)
    entity_counts = tuple(
        sorted(
            (
                TrendEntityCount(
                    entity_id=entity_id,
                    entity_type=entities_by_id[entity_id].entity_type,
                    label=entities_by_id[entity_id].display_label,
                    change=(
                        change := _trend_change(
                            len(current_entity_papers.get(entity_id, set())),
                            len(preceding_entity_papers.get(entity_id, set())),
                            current_paper_count=current_count,
                            preceding_paper_count=preceding_count,
                            thresholds=thresholds,
                        )
                    ),
                    newly_appearing=(change.current_count > 0 and change.preceding_count == 0),
                    recurring=change.current_count > 0 and change.preceding_count > 0,
                )
                for entity_id in set(current_entity_papers) | set(preceding_entity_papers)
            ),
            key=lambda item: (
                -item.change.current_count,
                -item.change.preceding_count,
                item.entity_type.value,
                normalized_entity_key(item.label),
                str(item.entity_id),
            ),
        )
    )
    relation_counts = tuple(
        TrendRelationCount(
            relation_type=relation_type,
            change=_trend_change(
                len(current_relation_keys.get(relation_type, set())),
                len(preceding_relation_keys.get(relation_type, set())),
                current_paper_count=current_count,
                preceding_paper_count=preceding_count,
                thresholds=thresholds,
            ),
        )
        for relation_type in sorted(
            set(current_relation_keys) | set(preceding_relation_keys),
            key=lambda item: item.value,
        )
    )
    current_entity_scores: dict[UUID, set[UUID]] = defaultdict(set)
    for mention in mentions:
        paper = papers_by_version[mention.paper_version_id]
        entity = entities_by_id[mention.entity_id]
        if entity.entity_type is not GraphEntityType.PAPER and _in_window(
            mention_activity_dates[mention.id], window_start, as_of_date
        ):
            current_entity_scores[paper.paper_id].add(entity.id)
    latest_activity_by_paper: dict[UUID, date] = {}
    for paper in papers:
        if paper.paper_id in current_paper_ids:
            latest_activity_by_paper[paper.paper_id] = max(
                paper.activity_date,
                latest_activity_by_paper.get(paper.paper_id, date.min),
            )
    for mention in mentions:
        activity_date = mention_activity_dates[mention.id]
        if _in_window(activity_date, window_start, as_of_date):
            paper_id = papers_by_version[mention.paper_version_id].paper_id
            latest_activity_by_paper[paper_id] = max(
                activity_date,
                latest_activity_by_paper.get(paper_id, date.min),
            )
    for edge in edges:
        activity_date = edge_activity_dates[edge.id]
        if _in_window(activity_date, window_start, as_of_date):
            paper_id = papers_by_version[edge.source_paper_version_id].paper_id
            latest_activity_by_paper[paper_id] = max(
                activity_date,
                latest_activity_by_paper.get(paper_id, date.min),
            )
    representative_paper_ids = set(latest_activity_by_paper)
    representatives = tuple(
        sorted(
            representative_paper_ids,
            key=lambda paper_id: (
                -len(current_entity_scores.get(paper_id, set())),
                -len(relation_paper_scores.get(paper_id, set())),
                -latest_activity_by_paper[paper_id].toordinal(),
                str(paper_id),
            ),
        )[:representative_limit]
    )
    new_entity_ids = tuple(
        sorted((item.entity_id for item in entity_counts if item.newly_appearing), key=str)
    )
    recurring_entity_ids = tuple(
        sorted((item.entity_id for item in entity_counts if item.recurring), key=str)
    )
    return TrendSnapshot(
        id=stable_trend_snapshot_id(
            topic_id,
            as_of_date,
            window.value,
            aggregation_version,
        ),
        topic_id=topic_id,
        as_of_date=as_of_date,
        window=window,
        window_start=window_start,
        window_end=as_of_date,
        preceding_window_start=preceding_start,
        preceding_window_end=preceding_end,
        included_paper_count=current_count,
        preceding_paper_count=preceding_count,
        paper_count_change=_trend_change(
            current_count,
            preceding_count,
            current_paper_count=current_count,
            preceding_paper_count=preceding_count,
            thresholds=thresholds,
        ),
        entity_counts=entity_counts,
        relation_counts=relation_counts,
        new_entity_ids=new_entity_ids,
        recurring_entity_ids=recurring_entity_ids,
        representative_paper_ids=representatives,
        data_sufficiency=_data_sufficiency(current_count, thresholds),
        preceding_data_sufficiency=_data_sufficiency(preceding_count, thresholds),
        thresholds=thresholds,
        aggregation_version=aggregation_version,
        generated_at=generated_at,
        schema_version=1,
    )


__all__ = [
    "DEFAULT_LINEAGE_RELATION_TYPES",
    "DEFAULT_TREND_THRESHOLDS",
    "GRAPH_CONFIDENCE_MEANING",
    "MAX_GRAPH_LABEL_LENGTH",
    "MAX_LINEAGE_DEPTH",
    "MAX_LINEAGE_EDGES",
    "MAX_LINEAGE_NODES",
    "MAX_REPRESENTATIVE_PAPERS",
    "PAPER_GRAPH_RELATION_TYPES",
    "GraphEdge",
    "GraphEntity",
    "GraphEntityMention",
    "GraphEntityType",
    "GraphExtractionResult",
    "GraphModelProvenance",
    "GraphReferenceSet",
    "GraphRelationType",
    "KnowledgeGraphBundle",
    "LineageCorpusScope",
    "LineageNode",
    "LineagePaper",
    "LineageSnapshot",
    "TrendChange",
    "TrendDataSufficiency",
    "TrendEntityCount",
    "TrendGrowthStatus",
    "TrendPaperRecord",
    "TrendRelationCount",
    "TrendSnapshot",
    "TrendThresholds",
    "TrendWindow",
    "aggregate_trend_snapshots",
    "build_lineage_snapshot",
    "extract_analysis_graph",
    "extract_comparison_graph",
    "graph_entity_keys_match",
    "merge_knowledge_graph_bundles",
    "normalize_entity_label",
    "normalized_entity_key",
]
