"""Deterministic, server-side snapshots for the isolated public demo schema.

The snapshot policy is intentionally permissive: every available artifact reachable
from a canonical publication or a current periodic report is retained. Only raw
parser material, embeddings, ingestion cursors, and backfill execution bookkeeping
are excluded. Free-form failure diagnostics are redacted while stable error codes
and all structured metrics remain unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from sqlalchemy import Connection, Engine, MetaData, text

from .models import Base
from .repository import EXPECTED_DATABASE_REVISION

DEMO_REDACTED_DIAGNOSTIC: Final = "Diagnostic detail omitted from the public demo."

_SCHEMA_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_SYSTEM_SCHEMAS = frozenset({"information_schema", "pg_catalog", "pg_toast"})

# This list is explicit so a new persistence table cannot silently enter a public
# snapshot. The synchronizer validates it against the SQLAlchemy metadata on every
# construction.
DEMO_EXCLUDED_TABLES: Final = frozenset(
    {
        "citation_contexts",
        "historical_backfill_runs",
        "historical_corpus_entries",
        "ingestion_cursors",
        "parsed_passages",
        "parsed_references",
        "parsed_sections",
        "scientific_embeddings",
    }
)

DEMO_INCLUDED_TABLES: Final = (
    "authors",
    "external_paper_stubs",
    "papers",
    "topics",
    "external_paper_identifiers",
    "graph_entities",
    "paper_versions",
    "pipeline_executions",
    "topic_papers",
    "daily_runs",
    "paper_source_identities",
    "paper_version_authors",
    "parsed_papers",
    "lineage_snapshots",
    "paper_analyses",
    "reports",
    "run_items",
    "trend_snapshots",
    "analysis_claims",
    "evidence",
    "lineage_nodes",
    "product_run_paper_inputs",
    "report_entity_highlights",
    "report_failures",
    "report_lineage_highlights",
    "report_paper_highlights",
    "report_sections",
    "report_trend_links",
    "search_sessions",
    "trend_metrics",
    "trend_representative_papers",
    "comparisons",
    "evidence_claims",
    "search_actions",
    "comparison_dimensions",
    "graph_entity_mentions",
    "paper_relations",
    "product_run_comparison_inputs",
    "report_comparison_highlights",
    "search_candidates",
    "comparison_evidence_links",
    "graph_edges",
    "graph_mention_evidence_links",
    "relation_evidence_links",
    "report_evidence_links",
    "search_candidate_discoveries",
    "graph_edge_evidence_links",
    "lineage_edges",
)

_NULL_DIAGNOSTIC_TABLES: Final = frozenset(
    {
        "daily_runs",
        "pipeline_executions",
        "run_items",
        "search_actions",
        "search_sessions",
    }
)

_TABLE_PREDICATES: Final = MappingProxyType(
    {
        "authors": "src.id IN (SELECT id FROM pg_temp.demo_snapshot_authors)",
        "external_paper_stubs": (
            "src.id IN (SELECT id FROM pg_temp.demo_snapshot_external_papers)"
        ),
        "papers": "src.id IN (SELECT id FROM pg_temp.demo_snapshot_papers)",
        "topics": "src.id IN (SELECT id FROM pg_temp.demo_snapshot_topics)",
        "external_paper_identifiers": (
            "src.external_paper_id IN (SELECT id FROM pg_temp.demo_snapshot_external_papers)"
        ),
        "graph_entities": ("src.id IN (SELECT id FROM pg_temp.demo_snapshot_graph_entities)"),
        "paper_versions": ("src.id IN (SELECT id FROM pg_temp.demo_snapshot_paper_versions)"),
        "pipeline_executions": (
            "src.id IN (SELECT id FROM pg_temp.demo_snapshot_pipeline_executions)"
        ),
        "topic_papers": (
            "src.topic_id IN (SELECT id FROM pg_temp.demo_snapshot_topics) "
            "AND src.paper_id IN (SELECT id FROM pg_temp.demo_snapshot_papers)"
        ),
        "daily_runs": "src.id IN (SELECT id FROM pg_temp.demo_snapshot_runs)",
        "paper_source_identities": (
            "src.paper_version_id IN (SELECT id FROM pg_temp.demo_snapshot_paper_versions)"
        ),
        "paper_version_authors": (
            "src.paper_version_id IN "
            "(SELECT id FROM pg_temp.demo_snapshot_paper_versions) "
            "AND src.author_id IN (SELECT id FROM pg_temp.demo_snapshot_authors)"
        ),
        "parsed_papers": (
            "src.id IN ("
            "SELECT a.parsed_paper_id FROM {source}.paper_analyses AS a "
            "WHERE a.id IN (SELECT id FROM pg_temp.demo_snapshot_analyses)"
            ")"
        ),
        "lineage_snapshots": ("src.id IN (SELECT id FROM pg_temp.demo_snapshot_lineages)"),
        "paper_analyses": ("src.id IN (SELECT id FROM pg_temp.demo_snapshot_analyses)"),
        "reports": "src.id IN (SELECT id FROM pg_temp.demo_snapshot_reports)",
        "run_items": "src.run_id IN (SELECT id FROM pg_temp.demo_snapshot_runs)",
        "trend_snapshots": ("src.id IN (SELECT id FROM pg_temp.demo_snapshot_trends)"),
        "analysis_claims": ("src.analysis_id IN (SELECT id FROM pg_temp.demo_snapshot_analyses)"),
        "evidence": "src.id IN (SELECT id FROM pg_temp.demo_snapshot_evidence)",
        "lineage_nodes": ("src.snapshot_id IN (SELECT id FROM pg_temp.demo_snapshot_lineages)"),
        "product_run_paper_inputs": ("src.run_id IN (SELECT id FROM pg_temp.demo_snapshot_runs)"),
        "report_entity_highlights": (
            "src.report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports)"
        ),
        "report_failures": ("src.report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports)"),
        "report_lineage_highlights": (
            "src.report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports)"
        ),
        "report_paper_highlights": (
            "src.report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports)"
        ),
        "report_sections": ("src.report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports)"),
        "report_trend_links": ("src.report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports)"),
        "search_sessions": ("src.id IN (SELECT id FROM pg_temp.demo_snapshot_search_sessions)"),
        "trend_metrics": ("src.snapshot_id IN (SELECT id FROM pg_temp.demo_snapshot_trends)"),
        "trend_representative_papers": (
            "src.snapshot_id IN (SELECT id FROM pg_temp.demo_snapshot_trends)"
        ),
        "comparisons": ("src.id IN (SELECT id FROM pg_temp.demo_snapshot_comparisons)"),
        "evidence_claims": ("src.evidence_id IN (SELECT id FROM pg_temp.demo_snapshot_evidence)"),
        "search_actions": (
            "src.session_id IN (SELECT id FROM pg_temp.demo_snapshot_search_sessions)"
        ),
        "comparison_dimensions": (
            "src.comparison_id IN (SELECT id FROM pg_temp.demo_snapshot_comparisons)"
        ),
        "graph_entity_mentions": (
            "src.id IN (SELECT id FROM pg_temp.demo_snapshot_graph_mentions)"
        ),
        "paper_relations": ("src.id IN (SELECT id FROM pg_temp.demo_snapshot_relations)"),
        "product_run_comparison_inputs": (
            "src.run_id IN (SELECT id FROM pg_temp.demo_snapshot_runs) "
            "AND src.comparison_id IN "
            "(SELECT id FROM pg_temp.demo_snapshot_comparisons)"
        ),
        "report_comparison_highlights": (
            "src.report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports)"
        ),
        "search_candidates": (
            "src.session_id IN (SELECT id FROM pg_temp.demo_snapshot_search_sessions)"
        ),
        "comparison_evidence_links": (
            "src.comparison_id IN (SELECT id FROM pg_temp.demo_snapshot_comparisons) "
            "AND src.evidence_id IN (SELECT id FROM pg_temp.demo_snapshot_evidence)"
        ),
        "graph_edges": "src.id IN (SELECT id FROM pg_temp.demo_snapshot_graph_edges)",
        "graph_mention_evidence_links": (
            "src.mention_id IN (SELECT id FROM pg_temp.demo_snapshot_graph_mentions) "
            "AND src.evidence_id IN (SELECT id FROM pg_temp.demo_snapshot_evidence)"
        ),
        "relation_evidence_links": (
            "src.relation_id IN (SELECT id FROM pg_temp.demo_snapshot_relations) "
            "AND src.evidence_id IN (SELECT id FROM pg_temp.demo_snapshot_evidence)"
        ),
        "report_evidence_links": (
            "src.report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports) "
            "AND src.evidence_id IN (SELECT id FROM pg_temp.demo_snapshot_evidence)"
        ),
        "search_candidate_discoveries": (
            "src.session_id IN (SELECT id FROM pg_temp.demo_snapshot_search_sessions)"
        ),
        "graph_edge_evidence_links": (
            "src.graph_edge_id IN (SELECT id FROM pg_temp.demo_snapshot_graph_edges) "
            "AND src.evidence_id IN (SELECT id FROM pg_temp.demo_snapshot_evidence)"
        ),
        "lineage_edges": ("src.snapshot_id IN (SELECT id FROM pg_temp.demo_snapshot_lineages)"),
    }
)


class DemoSnapshotError(RuntimeError):
    """The demo snapshot policy or database boundary could not be satisfied."""


@dataclass(frozen=True, slots=True)
class DemoTableSpec:
    """One explicit server-side table copy rule."""

    name: str
    columns: tuple[str, ...]
    predicate_sql: str
    redactions: tuple[tuple[str, str], ...] = ()

    @property
    def source_columns(self) -> tuple[str, ...]:
        redacted = {column for column, _expression in self.redactions}
        return tuple(column for column in self.columns if column not in redacted)


@dataclass(frozen=True, slots=True)
class DemoSnapshotManifest:
    """Complete allow/exclude classification for the current persistence schema."""

    tables: tuple[DemoTableSpec, ...]
    excluded_tables: frozenset[str]
    all_tables_in_dependency_order: tuple[str, ...]

    def table(self, name: str) -> DemoTableSpec:
        try:
            return next(item for item in self.tables if item.name == name)
        except StopIteration as error:
            raise KeyError(name) from error


@dataclass(frozen=True, slots=True)
class DemoSnapshotResult:
    """Non-sensitive result suitable for structured CLI output."""

    source_revision: str
    target_revision: str
    table_counts: tuple[tuple[str, int], ...]

    @property
    def total_rows(self) -> int:
        return sum(count for _table, count in self.table_counts)


def default_demo_snapshot_manifest(
    metadata: MetaData | None = None,
) -> DemoSnapshotManifest:
    """Build and validate the explicit policy against current ORM metadata."""

    current_metadata = Base.metadata if metadata is None else metadata
    model_tables = frozenset(current_metadata.tables)
    classified = frozenset(DEMO_INCLUDED_TABLES) | DEMO_EXCLUDED_TABLES
    overlap = frozenset(DEMO_INCLUDED_TABLES) & DEMO_EXCLUDED_TABLES
    if overlap:
        raise DemoSnapshotError(
            "demo snapshot table policy includes and excludes the same tables: "
            + ", ".join(sorted(overlap))
        )
    if classified != model_tables:
        missing = sorted(model_tables - classified)
        stale = sorted(classified - model_tables)
        detail: list[str] = []
        if missing:
            detail.append("unclassified=" + ",".join(missing))
        if stale:
            detail.append("unknown=" + ",".join(stale))
        raise DemoSnapshotError("demo snapshot table policy drift: " + "; ".join(detail))

    dependency_order = tuple(table.name for table in current_metadata.sorted_tables)
    included = frozenset(DEMO_INCLUDED_TABLES)
    specs: list[DemoTableSpec] = []
    for table_name in dependency_order:
        if table_name not in included:
            continue
        table = current_metadata.tables[table_name]
        redactions: tuple[tuple[str, str], ...] = ()
        if table_name in _NULL_DIAGNOSTIC_TABLES:
            redactions = (("error_detail", "NULL"),)
        elif table_name == "report_failures":
            redactions = (("error_detail", f"'{DEMO_REDACTED_DIAGNOSTIC}'"),)
        specs.append(
            DemoTableSpec(
                name=table_name,
                columns=tuple(column.name for column in table.columns),
                predicate_sql=_TABLE_PREDICATES[table_name],
                redactions=redactions,
            )
        )
    return DemoSnapshotManifest(
        tables=tuple(specs),
        excluded_tables=DEMO_EXCLUDED_TABLES,
        all_tables_in_dependency_order=dependency_order,
    )


class DemoSnapshotSynchronizer:
    """Replace the demo schema with one atomic, canonical public snapshot."""

    def __init__(
        self,
        engine: Engine,
        *,
        source_schema: str = "public",
        target_schema: str = "demo",
        manifest: DemoSnapshotManifest | None = None,
    ) -> None:
        self._engine = engine
        self._source_schema = _validate_schema(source_schema, target=False)
        self._target_schema = _validate_schema(target_schema, target=True)
        if self._source_schema == self._target_schema:
            raise ValueError("demo snapshot source and target schemas must differ")
        self._manifest = manifest or default_demo_snapshot_manifest()

    def synchronize(self) -> DemoSnapshotResult:
        """Run the whole snapshot in one transaction and return counts only."""

        with self._engine.begin() as connection:
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            source_revision = _read_revision(connection, self._source_schema)
            target_revision = _read_revision(connection, self._target_schema)
            if (
                source_revision != EXPECTED_DATABASE_REVISION
                or target_revision != EXPECTED_DATABASE_REVISION
            ):
                raise DemoSnapshotError(
                    "demo snapshot requires source and target schemas at the application revision"
                )

            for statement in build_demo_selection_statements(self._source_schema):
                connection.execute(text(statement))
            for table_name in reversed(self._manifest.all_tables_in_dependency_order):
                connection.execute(
                    text(f"DELETE FROM {_qualified(self._target_schema, table_name)}")
                )
            for table_spec in self._manifest.tables:
                connection.execute(
                    text(
                        build_demo_insert_statement(
                            table_spec,
                            source_schema=self._source_schema,
                            target_schema=self._target_schema,
                        )
                    )
                )

            counts = tuple(
                (
                    table_spec.name,
                    int(
                        connection.execute(
                            text(
                                "SELECT count(*) FROM "
                                + _qualified(self._target_schema, table_spec.name)
                            )
                        ).scalar_one()
                    ),
                )
                for table_spec in self._manifest.tables
            )
            return DemoSnapshotResult(
                source_revision=source_revision,
                target_revision=target_revision,
                table_counts=counts,
            )


def _validate_schema(value: str, *, target: bool) -> str:
    normalized = value.strip()
    if not _SCHEMA_IDENTIFIER.fullmatch(normalized):
        raise ValueError("demo snapshot schema names must be safe lowercase identifiers")
    if normalized in _SYSTEM_SCHEMAS or normalized.startswith("pg_"):
        raise ValueError("demo snapshot schema names cannot target PostgreSQL system schemas")
    if target and normalized == "public":
        raise ValueError("the demo snapshot target schema cannot be public")
    return normalized


def _quote_identifier(value: str) -> str:
    return f'"{value}"'


def _qualified(schema: str, table: str) -> str:
    return f"{_quote_identifier(schema)}.{_quote_identifier(table)}"


def _read_revision(connection: Connection, schema: str) -> str:
    revision = connection.execute(
        text(f"SELECT version_num FROM {_qualified(schema, 'alembic_version')}")
    ).scalar_one_or_none()
    if not isinstance(revision, str) or not revision.strip():
        raise DemoSnapshotError(f"schema {schema!r} does not have one Alembic revision")
    return revision


def build_demo_insert_statement(
    table_spec: DemoTableSpec,
    *,
    source_schema: str,
    target_schema: str,
) -> str:
    redactions = dict(table_spec.redactions)
    columns = ", ".join(_quote_identifier(column) for column in table_spec.columns)
    expressions = ", ".join(
        redactions.get(column, f"src.{_quote_identifier(column)}") for column in table_spec.columns
    )
    predicate = table_spec.predicate_sql.format(
        source=_quote_identifier(source_schema),
    )
    return (
        f"INSERT INTO {_qualified(target_schema, table_spec.name)} ({columns}) "
        f"SELECT {expressions} FROM {_qualified(source_schema, table_spec.name)} AS src "
        f"WHERE {predicate}"
    )


def build_demo_selection_statements(source_schema: str) -> tuple[str, ...]:
    source = _quote_identifier(source_schema)
    key_names = (
        "publication_runs",
        "reports",
        "runs",
        "pipeline_executions",
        "topics",
        "trends",
        "lineages",
        "graph_entities",
        "search_sessions",
        "search_actions",
        "comparisons",
        "graph_mentions",
        "graph_edges",
        "relations",
        "analyses",
        "evidence",
        "paper_versions",
        "papers",
        "external_papers",
        "authors",
    )
    create_keys = tuple(
        f"CREATE TEMP TABLE demo_snapshot_{name} (id uuid PRIMARY KEY) ON COMMIT DROP"
        for name in key_names
    )

    select_keys = (
        f"""
        INSERT INTO pg_temp.demo_snapshot_publication_runs (id)
        SELECT r.id
        FROM {source}.daily_runs AS r
        WHERE r.operation = 'PRODUCT_PUBLICATION'
          AND r.status IN ('COMPLETE', 'PARTIAL')
          AND r.pipeline_execution_mode <> 'SMOKE'
          AND NOT EXISTS (
              SELECT 1
              FROM {source}.daily_runs AS newer
              WHERE newer.topic_id = r.topic_id
                AND newer.logical_date = r.logical_date
                AND newer.operation = 'PRODUCT_PUBLICATION'
                AND newer.status IN ('COMPLETE', 'PARTIAL')
                AND newer.pipeline_execution_mode <> 'SMOKE'
                AND (
                    newer.started_at > r.started_at
                    OR (newer.started_at = r.started_at AND newer.id > r.id)
                )
          )
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_reports (id)
        SELECT report.id
        FROM {source}.reports AS report
        WHERE (report.report_type = 'DAILY'
               AND report.run_id IN (SELECT id FROM pg_temp.demo_snapshot_publication_runs))
           OR (report.report_type IN ('WEEKLY', 'MONTHLY')
               AND report.status IN ('COMPLETE', 'PARTIAL'))
        """,
        """
        INSERT INTO pg_temp.demo_snapshot_runs (id)
        SELECT id FROM pg_temp.demo_snapshot_publication_runs
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_runs (id)
        SELECT child.id
        FROM {source}.daily_runs AS child
        WHERE child.pipeline_execution_id IN (
            SELECT publication.pipeline_execution_id
            FROM {source}.daily_runs AS publication
            WHERE publication.id IN (SELECT id FROM pg_temp.demo_snapshot_publication_runs)
              AND publication.pipeline_execution_id IS NOT NULL
        )
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_runs (id)
        SELECT publication.source_run_id
        FROM {source}.daily_runs AS publication
        WHERE publication.id IN (SELECT id FROM pg_temp.demo_snapshot_publication_runs)
          AND publication.source_run_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_topics (id)
        SELECT topic_id FROM {source}.daily_runs
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_runs)
        UNION
        SELECT topic_id FROM {source}.reports
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_reports)
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_trends (id)
        SELECT snapshot.id FROM {source}.trend_snapshots AS snapshot
        WHERE snapshot.publication_run_id IN (
            SELECT id FROM pg_temp.demo_snapshot_publication_runs
        )
        UNION
        SELECT link.snapshot_id FROM {source}.report_trend_links AS link
        WHERE link.report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports)
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_lineages (id)
        SELECT snapshot.id FROM {source}.lineage_snapshots AS snapshot
        WHERE snapshot.publication_run_id IN (
            SELECT id FROM pg_temp.demo_snapshot_publication_runs
        )
        UNION
        SELECT highlight.lineage_snapshot_id
        FROM {source}.report_lineage_highlights AS highlight
        WHERE highlight.report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports)
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_runs (id)
        SELECT publication_run_id FROM {source}.trend_snapshots
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_trends)
        UNION
        SELECT publication_run_id FROM {source}.lineage_snapshots
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_lineages)
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_pipeline_executions (id)
        SELECT pipeline_execution_id FROM {source}.daily_runs
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_runs)
          AND pipeline_execution_id IS NOT NULL
        UNION
        SELECT pipeline_execution_id FROM {source}.trend_snapshots
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_trends)
          AND pipeline_execution_id IS NOT NULL
        UNION
        SELECT pipeline_execution_id FROM {source}.lineage_snapshots
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_lineages)
          AND pipeline_execution_id IS NOT NULL
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_search_sessions (id)
        SELECT id FROM {source}.search_sessions
        WHERE pipeline_execution_id IN (
            SELECT id FROM pg_temp.demo_snapshot_pipeline_executions
        )
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_graph_mentions (id)
        SELECT id FROM {source}.graph_entity_mentions
        WHERE publication_run_id IN (SELECT id FROM pg_temp.demo_snapshot_publication_runs)
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_graph_edges (id)
        SELECT id FROM {source}.graph_edges
        WHERE publication_run_id IN (SELECT id FROM pg_temp.demo_snapshot_publication_runs)
        UNION
        SELECT graph_edge_id FROM {source}.lineage_edges
        WHERE snapshot_id IN (SELECT id FROM pg_temp.demo_snapshot_lineages)
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_runs (id)
        SELECT publication_run_id FROM {source}.graph_edges
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_graph_edges)
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_pipeline_executions (id)
        SELECT pipeline_execution_id FROM {source}.graph_edges
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_graph_edges)
          AND pipeline_execution_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_runs (id)
        SELECT child.id FROM {source}.daily_runs AS child
        WHERE child.pipeline_execution_id IN (
            SELECT id FROM pg_temp.demo_snapshot_pipeline_executions
        )
        UNION
        SELECT selected.source_run_id FROM {source}.daily_runs AS selected
        WHERE selected.id IN (SELECT id FROM pg_temp.demo_snapshot_runs)
          AND selected.source_run_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_pipeline_executions (id)
        SELECT pipeline_execution_id FROM {source}.daily_runs
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_runs)
          AND pipeline_execution_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_search_sessions (id)
        SELECT id FROM {source}.search_sessions
        WHERE pipeline_execution_id IN (
            SELECT id FROM pg_temp.demo_snapshot_pipeline_executions
        )
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_comparisons (id)
        SELECT id FROM {source}.comparisons
        WHERE search_session_id IN (SELECT id FROM pg_temp.demo_snapshot_search_sessions)
        UNION
        SELECT comparison_id FROM {source}.product_run_comparison_inputs
        WHERE run_id IN (SELECT id FROM pg_temp.demo_snapshot_runs)
        UNION
        SELECT comparison_id FROM {source}.report_comparison_highlights
        WHERE report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports)
        UNION
        SELECT comparison_id FROM {source}.graph_entity_mentions
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_graph_mentions)
          AND comparison_id IS NOT NULL
        UNION
        SELECT comparison_id FROM {source}.graph_edges
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_graph_edges)
          AND comparison_id IS NOT NULL
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_search_sessions (id)
        SELECT search_session_id FROM {source}.comparisons
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_comparisons)
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_pipeline_executions (id)
        SELECT pipeline_execution_id FROM {source}.search_sessions
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_search_sessions)
          AND pipeline_execution_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_topics (id)
        SELECT topic_id FROM {source}.search_sessions
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_search_sessions)
        UNION
        SELECT topic_id FROM {source}.daily_runs
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_runs)
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_search_actions (id)
        SELECT id FROM {source}.search_actions
        WHERE session_id IN (SELECT id FROM pg_temp.demo_snapshot_search_sessions)
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_relations (id)
        SELECT id FROM {source}.paper_relations
        WHERE comparison_id IN (SELECT id FROM pg_temp.demo_snapshot_comparisons)
        UNION
        SELECT paper_relation_id FROM {source}.graph_edges
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_graph_edges)
          AND paper_relation_id IS NOT NULL
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_analyses (id)
        SELECT analysis_id FROM {source}.product_run_paper_inputs
        WHERE run_id IN (SELECT id FROM pg_temp.demo_snapshot_runs)
          AND analysis_id IS NOT NULL
        UNION
        SELECT source_analysis_id FROM {source}.search_sessions
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_search_sessions)
        UNION
        SELECT source_analysis_id FROM {source}.comparisons
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_comparisons)
        UNION
        SELECT target_analysis_id FROM {source}.comparisons
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_comparisons)
          AND target_analysis_id IS NOT NULL
        UNION
        SELECT analysis_id FROM {source}.graph_entity_mentions
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_graph_mentions)
          AND analysis_id IS NOT NULL
        UNION
        SELECT analysis_id FROM {source}.graph_edges
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_graph_edges)
          AND analysis_id IS NOT NULL
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_evidence (id)
        SELECT id FROM {source}.evidence
        WHERE analysis_id IN (SELECT id FROM pg_temp.demo_snapshot_analyses)
        UNION
        SELECT evidence_id FROM {source}.report_evidence_links
        WHERE report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports)
        UNION
        SELECT evidence_id FROM {source}.comparison_evidence_links
        WHERE comparison_id IN (SELECT id FROM pg_temp.demo_snapshot_comparisons)
        UNION
        SELECT evidence_id FROM {source}.graph_mention_evidence_links
        WHERE mention_id IN (SELECT id FROM pg_temp.demo_snapshot_graph_mentions)
        UNION
        SELECT evidence_id FROM {source}.graph_edge_evidence_links
        WHERE graph_edge_id IN (SELECT id FROM pg_temp.demo_snapshot_graph_edges)
        UNION
        SELECT evidence_id FROM {source}.relation_evidence_links
        WHERE relation_id IN (SELECT id FROM pg_temp.demo_snapshot_relations)
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_analyses (id)
        SELECT analysis_id FROM {source}.evidence
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_evidence)
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_graph_entities (id)
        SELECT entity_id FROM {source}.graph_entity_mentions
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_graph_mentions)
        UNION
        SELECT source_entity_id FROM {source}.graph_edges
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_graph_edges)
        UNION
        SELECT target_entity_id FROM {source}.graph_edges
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_graph_edges)
        UNION
        SELECT graph_entity_id FROM {source}.report_entity_highlights
        WHERE report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports)
        UNION
        SELECT graph_entity_id FROM {source}.lineage_nodes
        WHERE snapshot_id IN (SELECT id FROM pg_temp.demo_snapshot_lineages)
        UNION
        SELECT entity_id FROM {source}.trend_metrics
        WHERE snapshot_id IN (SELECT id FROM pg_temp.demo_snapshot_trends)
          AND entity_id IS NOT NULL
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_paper_versions (id)
        SELECT paper_version_id FROM {source}.run_items
        WHERE run_id IN (SELECT id FROM pg_temp.demo_snapshot_runs)
        UNION
        SELECT paper_version_id FROM {source}.product_run_paper_inputs
        WHERE run_id IN (SELECT id FROM pg_temp.demo_snapshot_runs)
        UNION
        SELECT paper_version_id FROM {source}.report_paper_highlights
        WHERE report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports)
        UNION
        SELECT paper_version_id FROM {source}.report_failures
        WHERE report_id IN (SELECT id FROM pg_temp.demo_snapshot_reports)
        UNION
        SELECT paper_version_id FROM {source}.paper_analyses
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_analyses)
        UNION
        SELECT source_paper_version_id FROM {source}.search_sessions
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_search_sessions)
        UNION
        SELECT source_paper_version_id FROM {source}.comparisons
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_comparisons)
        UNION
        SELECT target_paper_version_id FROM {source}.comparisons
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_comparisons)
          AND target_paper_version_id IS NOT NULL
        UNION
        SELECT source_paper_version_id FROM {source}.paper_relations
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_relations)
        UNION
        SELECT target_paper_version_id FROM {source}.paper_relations
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_relations)
        UNION
        SELECT paper_version_id FROM {source}.graph_entity_mentions
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_graph_mentions)
        UNION
        SELECT source_paper_version_id FROM {source}.graph_edges
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_graph_edges)
          AND source_paper_version_id IS NOT NULL
        UNION
        SELECT target_paper_version_id FROM {source}.graph_edges
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_graph_edges)
          AND target_paper_version_id IS NOT NULL
        UNION
        SELECT paper_version_id FROM {source}.evidence
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_evidence)
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_paper_versions (id)
        SELECT candidate.local_paper_version_id
        FROM {source}.search_candidates AS candidate
        WHERE candidate.session_id IN (SELECT id FROM pg_temp.demo_snapshot_search_sessions)
          AND candidate.local_paper_version_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_papers (id)
        SELECT paper_id FROM {source}.paper_versions
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_paper_versions)
        UNION
        SELECT paper_id FROM {source}.graph_entities
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_graph_entities)
          AND paper_id IS NOT NULL
        UNION
        SELECT root_paper_id FROM {source}.lineage_snapshots
        WHERE id IN (SELECT id FROM pg_temp.demo_snapshot_lineages)
        UNION
        SELECT paper_id FROM {source}.lineage_nodes
        WHERE snapshot_id IN (SELECT id FROM pg_temp.demo_snapshot_lineages)
          AND paper_id IS NOT NULL
        UNION
        SELECT paper_id FROM {source}.trend_representative_papers
        WHERE snapshot_id IN (SELECT id FROM pg_temp.demo_snapshot_trends)
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_paper_versions (id)
        SELECT id FROM {source}.paper_versions
        WHERE paper_id IN (SELECT id FROM pg_temp.demo_snapshot_papers)
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_external_papers (id)
        SELECT external_paper_id FROM {source}.search_candidates
        WHERE session_id IN (SELECT id FROM pg_temp.demo_snapshot_search_sessions)
          AND external_paper_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """,
        f"""
        INSERT INTO pg_temp.demo_snapshot_authors (id)
        SELECT author_id FROM {source}.paper_version_authors
        WHERE paper_version_id IN (SELECT id FROM pg_temp.demo_snapshot_paper_versions)
        ON CONFLICT DO NOTHING
        """,
    )
    return create_keys + tuple(_compact_sql(statement) for statement in select_keys)


def _compact_sql(statement: str) -> str:
    return " ".join(statement.split())
