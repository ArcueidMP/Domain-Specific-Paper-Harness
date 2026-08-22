"""Stable identifiers for persisted domain objects."""

from __future__ import annotations

import re
from datetime import date
from uuid import UUID, uuid5

from paper_harness.domain.errors import DomainInvariantError

PAPER_NAMESPACE = UUID("cbd3c150-b93e-4af8-95ba-e17e77ccb12a")
PAPER_VERSION_NAMESPACE = UUID("3e7688e7-0ca6-46d9-a4d2-e3b416a82cf3")
SOURCE_IDENTITY_NAMESPACE = UUID("34bd2824-7bd2-4df8-a09e-784db57d20cb")
AUTHOR_NAMESPACE = UUID("b741f3e4-28ce-48b5-b2c7-9080e3da6690")
PARSED_PAPER_NAMESPACE = UUID("ce05af6a-1963-43f2-980d-1a165f2a3a50")
PARSED_SECTION_NAMESPACE = UUID("30ea24b3-4d3e-4ef4-a092-bb13982f6d39")
PARSED_PASSAGE_NAMESPACE = UUID("36387192-ea62-4216-8981-f9d4311ef581")
PARSED_REFERENCE_NAMESPACE = UUID("fbef25cb-8d31-46a5-bce5-bec9bfd3746e")
CITATION_CONTEXT_NAMESPACE = UUID("cd7f287b-b2d8-4e6b-b77e-8f77e8b4b151")
ANALYSIS_NAMESPACE = UUID("5b37c831-4524-41e4-9e69-4a7316699ae6")
CLAIM_NAMESPACE = UUID("42b72c7b-665a-4d11-9f45-d2154fb79257")
EVIDENCE_NAMESPACE = UUID("09955308-cb9d-4b0a-85dd-82235fa8080d")
REPORT_NAMESPACE = UUID("ff9abc00-438e-42ec-999e-3e7f8c213a98")
EXTERNAL_PAPER_NAMESPACE = UUID("f34ca3d0-3701-41ca-93fa-e488cbf8e872")
SEARCH_SESSION_NAMESPACE = UUID("dd16d4c8-1b37-46dd-8a92-90b113f8551c")
SEARCH_ACTION_NAMESPACE = UUID("99079ed9-c1dc-41c5-b117-827a847e0030")
SEARCH_CANDIDATE_NAMESPACE = UUID("ba4925ae-68ff-478c-9cd3-aeff5380c539")
CANDIDATE_DISCOVERY_NAMESPACE = UUID("182097c2-17f5-42ca-8d83-c4f65834e7a1")
HISTORICAL_CORPUS_NAMESPACE = UUID("8815c52b-a885-4030-857a-6137e224088e")
HISTORICAL_BACKFILL_NAMESPACE = UUID("25850b6f-f170-4436-855a-cf95cad8aabb")
EMBEDDING_NAMESPACE = UUID("cf44753b-3e97-4380-ad64-8aad2ac810a1")
COMPARISON_NAMESPACE = UUID("056381f3-78bf-4929-af6f-983800a8dde9")
COMPARISON_DIMENSION_NAMESPACE = UUID("b9157c1a-8407-4304-a3b6-ff7eec448fb0")
PAPER_RELATION_NAMESPACE = UUID("e5799c68-7f25-48e8-a86b-65f6594bbd97")
GRAPH_ENTITY_NAMESPACE = UUID("ec6392cf-c38b-4fdb-b205-59c9bf80f8fe")
GRAPH_ENTITY_MENTION_NAMESPACE = UUID("d73b3650-d074-4d28-991c-bd97e8fc4899")
GRAPH_EDGE_NAMESPACE = UUID("09c7e488-4471-430e-87f7-6d9312918710")
LINEAGE_SNAPSHOT_NAMESPACE = UUID("048c51ec-5f00-4245-bb51-5e66123a32f4")
TREND_SNAPSHOT_NAMESPACE = UUID("eb58cde7-537d-4677-aa2f-b71b5fe3d6a2")
PIPELINE_EXECUTION_NAMESPACE = UUID("0357d07f-0ab6-48d1-a941-a5b4786db14f")

_ARXIV_VERSIONED_ID = re.compile(
    r"^(?P<canonical>(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7}))v(?P<version>[1-9]\d*)$",
    re.IGNORECASE,
)
_ARXIV_CANONICAL_ID = re.compile(
    r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})$",
    re.IGNORECASE,
)


def parse_arxiv_identifier(value: str) -> tuple[str, int]:
    """Return a canonical arXiv work identifier and explicit positive version."""

    cleaned = value.strip()
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        cleaned = cleaned.rstrip("/").rsplit("/", maxsplit=1)[-1]
    match = _ARXIV_VERSIONED_ID.fullmatch(cleaned)
    if match is None:
        raise DomainInvariantError(f"arXiv identifier must include an explicit version: {value!r}")
    canonical = match.group("canonical")
    return canonical.lower(), int(match.group("version"))


def validate_canonical_arxiv_id(value: str) -> str:
    """Validate and normalize an unversioned canonical arXiv work identifier."""

    cleaned = value.strip().lower()
    if _ARXIV_CANONICAL_ID.fullmatch(cleaned) is None:
        raise DomainInvariantError(f"invalid canonical arXiv identifier: {value!r}")
    return cleaned


def stable_paper_id(canonical_arxiv_id: str) -> UUID:
    return uuid5(PAPER_NAMESPACE, validate_canonical_arxiv_id(canonical_arxiv_id))


def stable_paper_version_id(canonical_arxiv_id: str, version: int) -> UUID:
    if version < 1:
        raise DomainInvariantError("paper version must be positive")
    canonical = validate_canonical_arxiv_id(canonical_arxiv_id)
    return uuid5(PAPER_VERSION_NAMESPACE, f"arxiv:{canonical}:v{version}")


def stable_source_identity_id(source: str, external_id: str, source_version: str) -> UUID:
    return uuid5(SOURCE_IDENTITY_NAMESPACE, f"{source}:{external_id}:{source_version}")


def normalize_author_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise DomainInvariantError("author name must not be empty")
    return normalized


def stable_author_id(name: str) -> UUID:
    return uuid5(AUTHOR_NAMESPACE, normalize_author_name(name).casefold())


def stable_parsed_paper_id(paper_version_id: UUID, parser_name: str, parser_version: str) -> UUID:
    return uuid5(PARSED_PAPER_NAMESPACE, f"{paper_version_id}:{parser_name}:{parser_version}")


def stable_parsed_section_id(parsed_paper_id: UUID, index: int) -> UUID:
    return uuid5(PARSED_SECTION_NAMESPACE, f"{parsed_paper_id}:{index}")


def stable_parsed_passage_id(parsed_paper_id: UUID, source_id: str) -> UUID:
    return uuid5(PARSED_PASSAGE_NAMESPACE, f"{parsed_paper_id}:{source_id}")


def stable_parsed_reference_id(parsed_paper_id: UUID, source_id: str) -> UUID:
    return uuid5(PARSED_REFERENCE_NAMESPACE, f"{parsed_paper_id}:{source_id}")


def stable_citation_context_id(
    parsed_paper_id: UUID, passage_id: UUID, reference_source_id: str, ordinal: int
) -> UUID:
    return uuid5(
        CITATION_CONTEXT_NAMESPACE,
        f"{parsed_paper_id}:{passage_id}:{reference_source_id}:{ordinal}",
    )


def stable_analysis_id(
    paper_version_id: UUID,
    analysis_scope: str,
    parsed_paper_id: UUID | None,
    provider: str,
    configured_model: str,
    model_version: str,
    prompt_version: str,
) -> UUID:
    parsed_identity = "abstract" if parsed_paper_id is None else str(parsed_paper_id)
    return uuid5(
        ANALYSIS_NAMESPACE,
        f"{paper_version_id}:{analysis_scope}:{parsed_identity}:{provider}:"
        f"{configured_model}:{model_version}:{prompt_version}",
    )


def stable_claim_id(analysis_id: UUID, claim_key: str) -> UUID:
    return uuid5(CLAIM_NAMESPACE, f"{analysis_id}:{claim_key}")


def stable_evidence_id(analysis_id: UUID, evidence_key: str) -> UUID:
    return uuid5(EVIDENCE_NAMESPACE, f"{analysis_id}:{evidence_key}")


def stable_report_id(run_id: UUID) -> UUID:
    return uuid5(REPORT_NAMESPACE, str(run_id))


def stable_periodic_report_id(
    topic_id: UUID,
    report_type: str,
    period_start: date,
    period_end: date,
) -> UUID:
    """Return a stable identity for a run-independent weekly or monthly report."""

    type_value = report_type.strip()
    if type_value not in {"WEEKLY", "MONTHLY"}:
        raise DomainInvariantError("periodic report identity requires weekly or monthly scope")
    if period_start > period_end:
        raise DomainInvariantError("periodic report identity has a reversed period")
    return uuid5(
        REPORT_NAMESPACE,
        f"periodic:{topic_id}:{type_value}:{period_start.isoformat()}:{period_end.isoformat()}",
    )


def stable_external_paper_id(
    semantic_scholar_id: str,
    *,
    arxiv_id: str | None = None,
    doi: str | None = None,
) -> UUID:
    value = semantic_scholar_id.strip()
    if not value:
        raise DomainInvariantError("Semantic Scholar paper ID must not be empty")
    if arxiv_id is not None:
        identity = f"arxiv:{validate_canonical_arxiv_id(arxiv_id)}"
    elif value:
        identity = f"semantic_scholar:{value.casefold()}"
    elif doi is not None and doi.strip():
        identity = f"doi:{doi.strip().casefold()}"
    else:
        raise DomainInvariantError("external paper needs an approved stable identity")
    return uuid5(EXTERNAL_PAPER_NAMESPACE, identity)


def stable_search_session_id(
    source_paper_version_id: UUID,
    objective: str,
    limits_identity: str,
    prompt_version: str,
) -> UUID:
    normalized = " ".join(objective.split())
    if not normalized:
        raise DomainInvariantError("search objective must not be empty")
    if not limits_identity.strip() or not prompt_version.strip():
        raise DomainInvariantError("search policy identity and prompt version are required")
    return uuid5(
        SEARCH_SESSION_NAMESPACE,
        f"{source_paper_version_id}:{normalized}:{limits_identity}:{prompt_version}",
    )


def stable_pipeline_execution_id(
    topic_id: UUID,
    logical_date: date,
) -> UUID:
    """Identify the one Daily pipeline execution for a topic and logical date."""

    return uuid5(
        PIPELINE_EXECUTION_NAMESPACE,
        _encode_identity_parts(str(topic_id), logical_date.isoformat(), "NORMAL", "canonical"),
    )


def stable_search_action_id(session_id: UUID, step: int) -> UUID:
    if step < 1:
        raise DomainInvariantError("search action step must be positive")
    return uuid5(SEARCH_ACTION_NAMESPACE, f"{session_id}:{step}")


def stable_search_candidate_id(session_id: UUID, semantic_scholar_id: str) -> UUID:
    value = semantic_scholar_id.strip()
    if not value:
        raise DomainInvariantError("candidate Semantic Scholar ID must not be empty")
    return uuid5(SEARCH_CANDIDATE_NAMESPACE, f"{session_id}:{value}")


def stable_candidate_discovery_id(
    candidate_id: UUID,
    origin: str,
    action_id: UUID | None,
    relation_depth: int,
) -> UUID:
    if relation_depth < 0:
        raise DomainInvariantError("candidate discovery depth cannot be negative")
    return uuid5(
        CANDIDATE_DISCOVERY_NAMESPACE,
        f"{candidate_id}:{origin}:{action_id or 'local'}:{relation_depth}",
    )


def stable_historical_corpus_entry_id(topic_id: UUID, external_paper_id: UUID) -> UUID:
    return uuid5(HISTORICAL_CORPUS_NAMESPACE, f"{topic_id}:{external_paper_id}")


def stable_historical_backfill_id(topic_id: UUID, window_from: date, window_to: date) -> UUID:
    if window_from > window_to:
        raise DomainInvariantError("historical backfill window is invalid")
    return uuid5(
        HISTORICAL_BACKFILL_NAMESPACE,
        f"{topic_id}:{window_from.isoformat()}:{window_to.isoformat()}",
    )


def stable_embedding_id(
    owner_id: UUID,
    *,
    model_identifier: str,
    model_revision: str,
    tokenizer_identifier: str,
    tokenizer_revision: str,
    dimension: int,
    preprocessing_contract: str,
    model_provenance: str,
    source: str,
) -> UUID:
    identity = (
        model_identifier,
        model_revision,
        tokenizer_identifier,
        tokenizer_revision,
        preprocessing_contract,
        model_provenance,
        source,
    )
    if any(not value.strip() for value in identity) or dimension < 1:
        raise DomainInvariantError("complete embedding contract provenance is required")
    values = (str(owner_id), *identity[:4], str(dimension), *identity[4:])
    encoded = "".join(f"{len(value)}:{value}" for value in values)
    return uuid5(EMBEDDING_NAMESPACE, encoded)


def stable_comparison_id(
    search_session_id: UUID,
    source_paper_version_id: UUID,
    source_analysis_id: UUID,
    target_paper_version_id: UUID,
    target_analysis_id: UUID,
    provider: str,
    configured_model: str,
    model_version: str,
    prompt_version: str,
) -> UUID:
    return uuid5(
        COMPARISON_NAMESPACE,
        f"{search_session_id}:{source_paper_version_id}:{source_analysis_id}:"
        f"{target_paper_version_id}:{target_analysis_id}:"
        f"{provider}:{configured_model}:{model_version}:{prompt_version}",
    )


def stable_comparison_dimension_id(comparison_id: UUID, name: str) -> UUID:
    return uuid5(COMPARISON_DIMENSION_NAMESPACE, f"{comparison_id}:{name}")


def stable_paper_relation_id(
    comparison_id: UUID,
    source_paper_version_id: UUID,
    target_paper_version_id: UUID,
    relation_type: str,
    provenance: str,
    model_version: str | None,
    prompt_version: str | None,
) -> UUID:
    return uuid5(
        PAPER_RELATION_NAMESPACE,
        f"{comparison_id}:{source_paper_version_id}:{target_paper_version_id}:"
        f"{relation_type}:{provenance}:{model_version or 'none'}:{prompt_version or 'none'}",
    )


def _encode_identity_parts(*parts: str) -> str:
    """Length-prefix identity parts so embedded delimiters cannot cause collisions."""

    return "".join(f"{len(part)}:{part}" for part in parts)


def stable_graph_entity_id(topic_id: UUID, entity_type: str, normalized_key: str) -> UUID:
    entity_type_value = entity_type.strip()
    key_value = normalized_key.strip()
    if not entity_type_value or not key_value:
        raise DomainInvariantError("graph entity type and normalized key are required")
    return uuid5(
        GRAPH_ENTITY_NAMESPACE,
        _encode_identity_parts(str(topic_id), entity_type_value, key_value),
    )


def stable_graph_paper_entity_id(topic_id: UUID, paper_id: UUID) -> UUID:
    return stable_graph_entity_id(topic_id, "PAPER", f"paper:{paper_id}")


def stable_graph_entity_mention_id(
    entity_id: UUID,
    paper_version_id: UUID,
    *,
    analysis_id: UUID | None = None,
    comparison_id: UUID | None = None,
    pipeline_execution_id: UUID | None = None,
) -> UUID:
    if (analysis_id is None) == (comparison_id is None):
        raise DomainInvariantError(
            "graph entity mention requires exactly one analysis or comparison owner"
        )
    owner_kind = "analysis" if analysis_id is not None else "comparison"
    owner_id = analysis_id if analysis_id is not None else comparison_id
    assert owner_id is not None
    parts = (str(entity_id), str(paper_version_id), owner_kind, str(owner_id))
    if pipeline_execution_id is not None:
        parts = (*parts, "pipeline_execution", str(pipeline_execution_id))
    return uuid5(GRAPH_ENTITY_MENTION_NAMESPACE, _encode_identity_parts(*parts))


def stable_graph_edge_id(
    source_entity_id: UUID,
    target_entity_id: UUID,
    relation_type: str,
    source_paper_version_id: UUID,
    *,
    target_paper_version_id: UUID | None = None,
    analysis_id: UUID | None = None,
    comparison_id: UUID | None = None,
    paper_relation_id: UUID | None = None,
    pipeline_execution_id: UUID | None = None,
) -> UUID:
    relation_value = relation_type.strip()
    if source_entity_id == target_entity_id:
        raise DomainInvariantError("graph edges cannot be self-relations")
    if not relation_value:
        raise DomainInvariantError("graph edge relation type is required")
    if analysis_id is None and comparison_id is None:
        raise DomainInvariantError("graph edge requires an analysis or comparison owner")
    if analysis_id is not None and comparison_id is not None:
        raise DomainInvariantError("graph edge cannot have both analysis and comparison owners")
    if paper_relation_id is not None and comparison_id is None:
        raise DomainInvariantError("paper-relation graph edge requires a comparison owner")
    parts = (
        str(source_entity_id),
        str(target_entity_id),
        relation_value,
        str(source_paper_version_id),
        str(target_paper_version_id or "none"),
        str(analysis_id or "none"),
        str(comparison_id or "none"),
        str(paper_relation_id or "none"),
    )
    if pipeline_execution_id is not None:
        parts = (*parts, "pipeline_execution", str(pipeline_execution_id))
    return uuid5(GRAPH_EDGE_NAMESPACE, _encode_identity_parts(*parts))


def stable_lineage_snapshot_id(
    topic_id: UUID,
    root_paper_id: UUID,
    as_of_date: date,
    *,
    permitted_relation_types: tuple[str, ...],
    max_depth: int,
    max_nodes: int,
    max_edges: int,
    lineage_version: str,
    pipeline_execution_id: UUID | None = None,
) -> UUID:
    relation_values = tuple(sorted(set(permitted_relation_types)))
    if not relation_values:
        raise DomainInvariantError("lineage requires at least one permitted relation type")
    if max_depth < 1 or max_nodes < 1 or max_edges < 1 or not lineage_version.strip():
        raise DomainInvariantError("lineage identity bounds and version must be positive")
    parts = (
        str(topic_id),
        str(root_paper_id),
        as_of_date.isoformat(),
        *relation_values,
        str(max_depth),
        str(max_nodes),
        str(max_edges),
        lineage_version,
    )
    if pipeline_execution_id is not None:
        parts = (*parts, "pipeline_execution", str(pipeline_execution_id))
    return uuid5(LINEAGE_SNAPSHOT_NAMESPACE, _encode_identity_parts(*parts))


def stable_trend_snapshot_id(
    topic_id: UUID,
    as_of_date: date,
    window: str,
    aggregation_version: str,
    pipeline_execution_id: UUID | None = None,
) -> UUID:
    window_value = window.strip()
    version_value = aggregation_version.strip()
    if not window_value or not version_value:
        raise DomainInvariantError("trend window and aggregation version are required")
    parts = (
        str(topic_id),
        as_of_date.isoformat(),
        window_value,
        version_value,
    )
    if pipeline_execution_id is not None:
        parts = (*parts, "pipeline_execution", str(pipeline_execution_id))
    return uuid5(TREND_SNAPSHOT_NAMESPACE, _encode_identity_parts(*parts))
