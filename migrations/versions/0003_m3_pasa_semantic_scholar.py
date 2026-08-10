"""Add M3 historical search, embeddings, comparisons, and relations.

Revision ID: 0003_m3_pasa_semantic_scholar
Revises: 0002_m2_structured_analysis
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0003_m3_pasa_semantic_scholar"
down_revision: str | None = "0002_m2_structured_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_paper_analyses_m3_ownership",
        "paper_analyses",
        ["id", "paper_id", "paper_version_id", "analysis_scope"],
    )
    op.create_unique_constraint(
        "uq_evidence_paper_version_ownership",
        "evidence",
        ["id", "paper_id", "paper_version_id"],
    )

    op.create_table(
        "external_paper_stubs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("semantic_scholar_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("venue", sa.Text(), nullable=True),
        sa.Column("authors", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("arxiv_id", sa.String(length=64), nullable=True),
        sa.Column("doi", sa.Text(), nullable=True),
        sa.Column("citation_count", sa.Integer(), nullable=False),
        sa.Column("influential_citation_count", sa.Integer(), nullable=False),
        sa.Column("full_text_available", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "publication_year IS NULL OR publication_year BETWEEN 1000 AND 9999",
            name="ck_external_paper_stubs_year",
        ),
        sa.CheckConstraint(
            "citation_count >= 0 AND influential_citation_count >= 0 "
            "AND influential_citation_count <= citation_count",
            name="ck_external_paper_stubs_citations",
        ),
        sa.CheckConstraint(
            "(full_text_available AND arxiv_id IS NOT NULL) OR "
            "(NOT full_text_available AND arxiv_id IS NULL)",
            name="ck_external_paper_stubs_full_text",
        ),
        sa.CheckConstraint("source = 'semantic_scholar'", name="ck_external_paper_stubs_source"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_external_paper_stubs_update_order"),
        sa.CheckConstraint("schema_version > 0", name="ck_external_paper_stubs_schema_version"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("semantic_scholar_id"),
        sa.UniqueConstraint(
            "id",
            "semantic_scholar_id",
            name="uq_external_paper_stubs_ownership",
        ),
    )
    op.create_index(
        "ix_external_paper_stubs_arxiv_id",
        "external_paper_stubs",
        ["arxiv_id"],
        unique=True,
    )
    op.create_index(
        "ix_external_paper_stubs_doi",
        "external_paper_stubs",
        ["doi"],
        unique=True,
    )
    op.create_index(
        "ix_external_paper_stubs_publication_date",
        "external_paper_stubs",
        ["publication_date"],
        unique=False,
    )
    op.create_index(
        "ix_external_paper_stubs_lexical",
        "external_paper_stubs",
        [
            sa.text(
                "to_tsvector('english'::regconfig, "
                "(COALESCE(title, ''::text) || ' '::text) || "
                "COALESCE(abstract, ''::text))"
            )
        ],
        unique=False,
        postgresql_using="gin",
    )

    op.create_table(
        "external_paper_identifiers",
        sa.Column("external_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identifier_type", sa.String(length=40), nullable=False),
        sa.Column("identifier_value", sa.String(length=512), nullable=False),
        sa.ForeignKeyConstraint(
            ["external_paper_id"],
            ["external_paper_stubs.id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("external_paper_id", "identifier_type"),
        sa.UniqueConstraint(
            "identifier_type",
            "identifier_value",
            name="uq_external_paper_identifiers_external",
        ),
    )

    op.create_table(
        "historical_backfill_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("window_from", sa.Date(), nullable=False),
        sa.Column("window_to", sa.Date(), nullable=False),
        sa.Column("query_plan", postgresql.ARRAY(sa.String(length=500)), nullable=False),
        sa.Column("max_results_per_query", sa.Integer(), nullable=False),
        sa.Column("overall_timeout_seconds", sa.Float(), nullable=False),
        sa.Column("embedding_model_identifier", sa.String(length=300), nullable=False),
        sa.Column("embedding_model_revision", sa.String(length=128), nullable=False),
        sa.Column("embedding_tokenizer_identifier", sa.String(length=300), nullable=False),
        sa.Column("embedding_tokenizer_revision", sa.String(length=128), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("embedding_preprocessing_contract", sa.String(length=1000), nullable=False),
        sa.Column("embedding_model_provenance", sa.String(length=1000), nullable=False),
        sa.Column("embedding_source", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("next_query_index", sa.Integer(), nullable=False),
        sa.Column("discovered_count", sa.Integer(), nullable=False),
        sa.Column("persisted_count", sa.Integer(), nullable=False),
        sa.Column("representative_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_detail", sa.String(length=1000), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE', 'FAILED')",
            name="ck_historical_backfill_status",
        ),
        sa.CheckConstraint("window_from <= window_to", name="ck_historical_backfill_window_order"),
        sa.CheckConstraint(
            "cardinality(query_plan) BETWEEN 1 AND 40 "
            "AND next_query_index <= cardinality(query_plan) "
            "AND max_results_per_query BETWEEN 1 AND 500 "
            "AND overall_timeout_seconds BETWEEN 1 AND 7200",
            name="ck_historical_backfill_plan_bounds",
        ),
        sa.CheckConstraint(
            "next_query_index >= 0 AND discovered_count >= 0 AND persisted_count >= 0 "
            "AND representative_count >= 0 AND persisted_count <= discovered_count "
            "AND representative_count <= persisted_count",
            name="ck_historical_backfill_counts",
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL)",
            name="ck_historical_backfill_completion",
        ),
        sa.CheckConstraint(
            "status <> 'COMPLETE' OR next_query_index = cardinality(query_plan)",
            name="ck_historical_backfill_complete_cursor",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_historical_backfill_completion_order",
        ),
        sa.CheckConstraint(
            "embedding_dimension = 768",
            name="ck_historical_backfill_embedding_dimension",
        ),
        sa.CheckConstraint(
            "(status = 'FAILED' AND error_code IS NOT NULL) OR "
            "(status <> 'FAILED' AND error_code IS NULL AND error_detail IS NULL)",
            name="ck_historical_backfill_failure",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_historical_backfill_schema_version"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "topic_id", "window_from", "window_to", name="uq_historical_backfill_window"
        ),
    )

    op.create_table(
        "historical_corpus_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("local_paper_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("local_paper_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("representative_rank", sa.Integer(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "(local_paper_id IS NULL AND local_paper_version_id IS NULL) OR "
            "(local_paper_id IS NOT NULL AND local_paper_version_id IS NOT NULL)",
            name="ck_historical_corpus_local_owner",
        ),
        sa.CheckConstraint(
            "representative_rank IS NULL OR representative_rank > 0",
            name="ck_historical_corpus_representative_rank",
        ),
        sa.CheckConstraint("last_seen_at >= first_seen_at", name="ck_historical_corpus_seen_order"),
        sa.CheckConstraint("schema_version > 0", name="ck_historical_corpus_schema_version"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["external_paper_id"],
            ["external_paper_stubs.id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["local_paper_version_id", "local_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_historical_corpus_local_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_id", "external_paper_id", name="uq_historical_corpus_paper"),
    )
    op.create_index(
        "uq_historical_corpus_representative",
        "historical_corpus_entries",
        ["topic_id", "representative_rank"],
        unique=True,
        postgresql_where=sa.text("representative_rank IS NOT NULL"),
    )

    op.create_table(
        "search_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_analysis_scope", sa.String(length=32), nullable=False),
        sa.Column("requested_year_from", sa.Integer(), nullable=False),
        sa.Column("effective_year_to", sa.Integer(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("crawler_queries", postgresql.ARRAY(sa.String(length=500)), nullable=True),
        sa.Column("crawler_use_recommendations", sa.Boolean(), nullable=True),
        sa.Column("crawler_expand_references", sa.Boolean(), nullable=True),
        sa.Column("crawler_expand_citations", sa.Boolean(), nullable=True),
        sa.Column("crawler_decision_reason", sa.Text(), nullable=True),
        sa.Column("crawler_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("max_queries", sa.Integer(), nullable=False),
        sa.Column("max_queue_size", sa.Integer(), nullable=False),
        sa.Column("max_citation_depth", sa.Integer(), nullable=False),
        sa.Column("max_candidates", sa.Integer(), nullable=False),
        sa.Column("max_selected_candidates", sa.Integer(), nullable=False),
        sa.Column("per_operation_timeout_seconds", sa.Float(), nullable=False),
        sa.Column("overall_timeout_seconds", sa.Float(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.String(length=40), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_detail", sa.String(length=1000), nullable=True),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("configured_model", sa.String(length=200), nullable=True),
        sa.Column("model_version", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("call_count", sa.Integer(), nullable=True),
        sa.Column("model_duration_ms", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE', 'FAILED')",
            name="ck_search_sessions_status",
        ),
        sa.CheckConstraint(
            "source_analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT')",
            name="ck_search_sessions_source_analysis_scope",
        ),
        sa.CheckConstraint(
            "requested_year_from BETWEEN 1000 AND 9999 "
            "AND effective_year_to BETWEEN 1000 AND 9999 "
            "AND requested_year_from <= effective_year_to",
            name="ck_search_sessions_year_scope",
        ),
        sa.CheckConstraint(
            "stop_reason IS NULL OR stop_reason IN ('QUEUE_EXHAUSTED', 'MAX_STEPS', "
            "'MAX_QUERIES', 'MAX_QUEUE_SIZE', 'MAX_CANDIDATES', "
            "'MAX_SELECTED_CANDIDATES', 'OVERALL_TIMEOUT', 'FAILED')",
            name="ck_search_sessions_stop_reason",
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL AND stop_reason IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL AND stop_reason IS NOT NULL)",
            name="ck_search_sessions_completion",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_search_sessions_completion_order",
        ),
        sa.CheckConstraint(
            "(status = 'FAILED' AND stop_reason = 'FAILED' AND error_code IS NOT NULL) OR "
            "(status <> 'FAILED' AND stop_reason IS DISTINCT FROM 'FAILED' "
            "AND error_code IS NULL AND error_detail IS NULL)",
            name="ck_search_sessions_failure",
        ),
        sa.CheckConstraint(
            "max_steps BETWEEN 1 AND 100 AND max_queries BETWEEN 1 AND 40 "
            "AND max_queue_size BETWEEN 1 AND 2000 AND max_citation_depth BETWEEN 0 AND 5 "
            "AND max_candidates BETWEEN 1 AND 5000 "
            "AND max_selected_candidates BETWEEN 1 AND 100 "
            "AND max_selected_candidates <= max_candidates",
            name="ck_search_sessions_limits",
        ),
        sa.CheckConstraint(
            "per_operation_timeout_seconds BETWEEN 1 AND 600 "
            "AND overall_timeout_seconds BETWEEN per_operation_timeout_seconds AND 3600",
            name="ck_search_sessions_timeouts",
        ),
        sa.CheckConstraint(
            "(crawler_queries IS NULL AND crawler_use_recommendations IS NULL "
            "AND crawler_expand_references IS NULL AND crawler_expand_citations IS NULL "
            "AND crawler_decision_reason IS NULL AND crawler_generated_at IS NULL) OR "
            "(crawler_queries IS NOT NULL "
            "AND cardinality(crawler_queries) BETWEEN 1 AND max_queries "
            "AND crawler_use_recommendations IS NOT NULL "
            "AND crawler_expand_references IS NOT NULL "
            "AND crawler_expand_citations IS NOT NULL "
            "AND crawler_decision_reason IS NOT NULL AND crawler_generated_at IS NOT NULL "
            "AND provider IS NOT NULL)",
            name="ck_search_sessions_crawler_plan",
        ),
        sa.CheckConstraint(
            "(provider IS NULL AND configured_model IS NULL AND model_version IS NULL "
            "AND prompt_version IS NULL AND prompt_tokens IS NULL "
            "AND completion_tokens IS NULL AND total_tokens IS NULL "
            "AND call_count IS NULL AND model_duration_ms IS NULL "
            "AND estimated_cost_usd IS NULL) OR "
            "(provider IS NOT NULL AND configured_model IS NOT NULL "
            "AND model_version IS NOT NULL AND prompt_version IS NOT NULL "
            "AND prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL "
            "AND total_tokens IS NOT NULL AND call_count IS NOT NULL "
            "AND model_duration_ms IS NOT NULL)",
            name="ck_search_sessions_model_provenance",
        ),
        sa.CheckConstraint(
            "prompt_tokens IS NULL OR (prompt_tokens >= 0 AND completion_tokens >= 0 "
            "AND total_tokens = prompt_tokens + completion_tokens "
            "AND total_tokens <= 1000000 AND completion_tokens <= 16000 "
            "AND call_count BETWEEN 1 AND 4 "
            "AND model_duration_ms BETWEEN 0 AND 1800000 "
            "AND (estimated_cost_usd IS NULL "
            "OR estimated_cost_usd BETWEEN 0 AND 9999999999.99999999))",
            name="ck_search_sessions_model_usage",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_search_sessions_schema_version"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_paper_version_id", "source_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_search_sessions_source_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_analysis_id",
                "source_paper_id",
                "source_paper_version_id",
                "source_analysis_scope",
            ],
            [
                "paper_analyses.id",
                "paper_analyses.paper_id",
                "paper_analyses.paper_version_id",
                "paper_analyses.analysis_scope",
            ],
            name="fk_search_sessions_source_analysis",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "source_paper_id",
            "source_paper_version_id",
            "source_analysis_id",
            "source_analysis_scope",
            name="uq_search_sessions_source_ownership",
        ),
    )

    op.create_table(
        "search_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("tool", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("query", sa.String(length=500), nullable=True),
        sa.Column("target_semantic_scholar_id", sa.String(length=128), nullable=True),
        sa.Column("target_arxiv_id", sa.String(length=64), nullable=True),
        sa.Column("positive_paper_ids", postgresql.ARRAY(sa.String(length=128)), nullable=False),
        sa.Column("year_from", sa.Integer(), nullable=True),
        sa.Column("year_to", sa.Integer(), nullable=True),
        sa.Column("requested_limit", sa.Integer(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("relation_depth", sa.Integer(), nullable=False),
        sa.Column("decision_reason", sa.String(length=1000), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("error_detail", sa.String(length=1000), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "tool IN ('search_papers', 'get_paper', 'get_references', 'get_citations', "
            "'get_recommendations', 'read_arxiv_paper')",
            name="ck_search_actions_tool",
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'FAILED')",
            name="ck_search_actions_status",
        ),
        sa.CheckConstraint(
            "step >= 1 AND requested_limit BETWEEN 1 AND 1000 "
            "AND result_count BETWEEN 0 AND requested_limit "
            "AND relation_depth BETWEEN 0 AND 5 AND duration_ms BETWEEN 0 AND 600000",
            name="ck_search_actions_bounds",
        ),
        sa.CheckConstraint(
            "(year_from IS NULL OR year_from BETWEEN 1000 AND 9999) "
            "AND (year_to IS NULL OR year_to BETWEEN 1000 AND 9999) "
            "AND (year_from IS NULL OR year_to IS NULL OR year_from <= year_to)",
            name="ck_search_actions_years",
        ),
        sa.CheckConstraint(
            "(tool = 'search_papers' AND query IS NOT NULL "
            "AND target_semantic_scholar_id IS NULL AND target_arxiv_id IS NULL "
            "AND cardinality(positive_paper_ids) = 0) OR "
            "(tool IN ('get_paper', 'get_references', 'get_citations') AND query IS NULL "
            "AND target_semantic_scholar_id IS NOT NULL AND target_arxiv_id IS NULL "
            "AND cardinality(positive_paper_ids) = 0) OR "
            "(tool = 'get_recommendations' AND query IS NULL "
            "AND target_semantic_scholar_id IS NULL AND target_arxiv_id IS NULL "
            "AND cardinality(positive_paper_ids) > 0) OR "
            "(tool = 'read_arxiv_paper' AND query IS NULL "
            "AND target_semantic_scholar_id IS NULL AND target_arxiv_id IS NOT NULL "
            "AND cardinality(positive_paper_ids) = 0)",
            name="ck_search_actions_tool_parameters",
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL AND error_code IS NULL "
            "AND retryable IS NULL AND error_detail IS NULL AND result_count = 0 "
            "AND duration_ms = 0) OR "
            "(status = 'COMPLETED' AND completed_at IS NOT NULL AND error_code IS NULL "
            "AND retryable IS NULL AND error_detail IS NULL) OR "
            "(status = 'FAILED' AND completed_at IS NOT NULL AND error_code IS NOT NULL "
            "AND retryable IS NOT NULL)",
            name="ck_search_actions_lifecycle",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name="ck_search_actions_completion_order",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_search_actions_schema_version"),
        sa.ForeignKeyConstraint(["session_id"], ["search_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "step", name="uq_search_actions_session_step"),
        sa.UniqueConstraint("id", "session_id", name="uq_search_actions_ownership"),
    )

    op.create_index(
        "ix_search_sessions_source_started",
        "search_sessions",
        ["source_paper_id", "started_at", "id"],
        unique=False,
    )

    op.create_table(
        "search_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("semantic_scholar_id", sa.String(length=128), nullable=False),
        sa.Column("local_paper_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("local_paper_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("discovered_by_action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origins", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("relation_depth", sa.Integer(), nullable=False),
        sa.Column("semantic_scholar_score", sa.Float(), nullable=False),
        sa.Column("lexical_score", sa.Float(), nullable=False),
        sa.Column("vector_score", sa.Float(), nullable=False),
        sa.Column("entity_overlap_score", sa.Float(), nullable=False),
        sa.Column("citation_score", sa.Float(), nullable=False),
        sa.Column("recommendation_score", sa.Float(), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("decision_reason", sa.String(length=1000), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("configured_model", sa.String(length=200), nullable=True),
        sa.Column("model_version", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(local_paper_id IS NULL AND local_paper_version_id IS NULL) OR "
            "(local_paper_id IS NOT NULL AND local_paper_version_id IS NOT NULL)",
            name="ck_search_candidates_local_owner",
        ),
        sa.CheckConstraint(
            "cardinality(origins) > 0 AND relation_depth BETWEEN 0 AND 5 AND rank > 0",
            name="ck_search_candidates_bounds",
        ),
        sa.CheckConstraint(
            "origins <@ ARRAY['SEARCH', 'REFERENCES', 'CITATIONS', 'RECOMMENDATIONS', "
            "'LOCAL_LEXICAL', 'LOCAL_VECTOR']::varchar[]",
            name="ck_search_candidates_origins",
        ),
        sa.CheckConstraint(
            "semantic_scholar_score BETWEEN 0 AND 1 AND lexical_score BETWEEN 0 AND 1 "
            "AND vector_score BETWEEN 0 AND 1 AND entity_overlap_score BETWEEN 0 AND 1 "
            "AND citation_score BETWEEN 0 AND 1 AND recommendation_score BETWEEN 0 AND 1 "
            "AND final_score BETWEEN 0 AND 1",
            name="ck_search_candidates_scores",
        ),
        sa.CheckConstraint(
            "decision IN ('PENDING', 'SELECTED', 'REJECTED')",
            name="ck_search_candidates_decision",
        ),
        sa.CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_search_candidates_verification",
        ),
        sa.CheckConstraint(
            "(decision = 'PENDING' AND provider IS NULL AND configured_model IS NULL "
            "AND model_version IS NULL AND prompt_version IS NULL AND generated_at IS NULL) OR "
            "(decision <> 'PENDING' AND provider IS NOT NULL AND configured_model IS NOT NULL "
            "AND model_version IS NOT NULL AND prompt_version IS NOT NULL "
            "AND generated_at IS NOT NULL)",
            name="ck_search_candidates_model_provenance",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_search_candidates_schema_version"),
        sa.ForeignKeyConstraint(["session_id"], ["search_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["external_paper_id", "semantic_scholar_id"],
            ["external_paper_stubs.id", "external_paper_stubs.semantic_scholar_id"],
            name="fk_search_candidates_external_identity",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["local_paper_version_id", "local_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_search_candidates_local_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["discovered_by_action_id", "session_id"],
            ["search_actions.id", "search_actions.session_id"],
            name="fk_search_candidates_discovery_action",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "external_paper_id", name="uq_search_candidates_paper"),
        sa.UniqueConstraint("id", "session_id", name="uq_search_candidates_ownership"),
    )

    op.create_table(
        "search_candidate_discoveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("relation_depth", sa.Integer(), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "origin IN ('SEARCH', 'REFERENCES', 'CITATIONS', 'RECOMMENDATIONS', "
            "'LOCAL_LEXICAL', 'LOCAL_VECTOR')",
            name="ck_search_candidate_discoveries_origin",
        ),
        sa.CheckConstraint(
            "relation_depth BETWEEN 0 AND 5",
            name="ck_search_candidate_discoveries_depth",
        ),
        sa.CheckConstraint(
            "(origin IN ('SEARCH', 'REFERENCES', 'CITATIONS', 'RECOMMENDATIONS') "
            "AND action_id IS NOT NULL) OR "
            "(origin IN ('LOCAL_LEXICAL', 'LOCAL_VECTOR') AND action_id IS NULL)",
            name="ck_search_candidate_discoveries_action",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "session_id"],
            ["search_candidates.id", "search_candidates.session_id"],
            name="fk_search_candidate_discoveries_candidate",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["action_id", "session_id"],
            ["search_actions.id", "search_actions.session_id"],
            name="fk_search_candidate_discoveries_action",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_id",
            "action_id",
            "origin",
            name="uq_search_candidate_discoveries_origin",
            postgresql_nulls_not_distinct=True,
        ),
    )

    op.create_index(
        "ix_search_candidates_session_rank",
        "search_candidates",
        ["session_id", "rank", "id"],
        unique=False,
    )

    op.create_table(
        "scientific_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("external_paper_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_identifier", sa.String(length=300), nullable=False),
        sa.Column("model_revision", sa.String(length=128), nullable=False),
        sa.Column("tokenizer_identifier", sa.String(length=300), nullable=False),
        sa.Column("tokenizer_revision", sa.String(length=128), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("preprocessing_contract", sa.String(length=1000), nullable=False),
        sa.Column("model_provenance", sa.String(length=1000), nullable=False),
        sa.Column("vector", Vector(768), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(paper_version_id IS NOT NULL AND external_paper_id IS NULL) OR "
            "(paper_version_id IS NULL AND external_paper_id IS NOT NULL)",
            name="ck_scientific_embeddings_owner",
        ),
        sa.CheckConstraint("dimension = 768", name="ck_scientific_embeddings_dimension"),
        sa.CheckConstraint("schema_version > 0", name="ck_scientific_embeddings_schema_version"),
        sa.ForeignKeyConstraint(["paper_version_id"], ["paper_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["external_paper_id"],
            ["external_paper_stubs.id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "paper_version_id",
            "external_paper_id",
            "model_identifier",
            "model_revision",
            "tokenizer_identifier",
            "tokenizer_revision",
            "dimension",
            "preprocessing_contract",
            "model_provenance",
            "source",
            name="uq_scientific_embeddings_owner_model",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_scientific_embeddings_model",
        "scientific_embeddings",
        [
            "model_identifier",
            "model_revision",
            "tokenizer_identifier",
            "tokenizer_revision",
        ],
    )

    op.create_table(
        "comparisons",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("search_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_analysis_scope", sa.String(length=32), nullable=False),
        sa.Column("target_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_analysis_scope", sa.String(length=32), nullable=False),
        sa.Column("comparability_status", sa.String(length=32), nullable=False),
        sa.Column("comparability_reason", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("configured_model", sa.String(length=200), nullable=False),
        sa.Column("model_version", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("call_count", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_paper_version_id <> target_paper_version_id",
            name="ck_comparisons_distinct_versions",
        ),
        sa.CheckConstraint(
            "source_analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT') "
            "AND target_analysis_scope IN ('ABSTRACT_ONLY', 'FULL_TEXT')",
            name="ck_comparisons_analysis_scopes",
        ),
        sa.CheckConstraint(
            "comparability_status IN ('DIRECTLY_COMPARABLE', 'PARTIALLY_COMPARABLE', "
            "'NOT_DIRECTLY_COMPARABLE', 'INSUFFICIENT_EVIDENCE')",
            name="ck_comparisons_comparability",
        ),
        sa.CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_comparisons_verification",
        ),
        sa.CheckConstraint(
            "prompt_tokens BETWEEN 0 AND 1000000 AND completion_tokens BETWEEN 0 AND 16000 "
            "AND total_tokens BETWEEN 0 AND 1000000 "
            "AND total_tokens = prompt_tokens + completion_tokens "
            "AND call_count BETWEEN 1 AND 4 AND duration_ms BETWEEN 0 AND 1800000 "
            "AND (estimated_cost_usd IS NULL OR estimated_cost_usd >= 0)",
            name="ck_comparisons_usage",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_comparisons_schema_version"),
        sa.ForeignKeyConstraint(
            [
                "search_session_id",
                "source_paper_id",
                "source_paper_version_id",
                "source_analysis_id",
                "source_analysis_scope",
            ],
            [
                "search_sessions.id",
                "search_sessions.source_paper_id",
                "search_sessions.source_paper_version_id",
                "search_sessions.source_analysis_id",
                "search_sessions.source_analysis_scope",
            ],
            name="fk_comparisons_search_session_source",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_paper_version_id", "source_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_comparisons_source_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_paper_version_id", "target_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_comparisons_target_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_analysis_id",
                "source_paper_id",
                "source_paper_version_id",
                "source_analysis_scope",
            ],
            [
                "paper_analyses.id",
                "paper_analyses.paper_id",
                "paper_analyses.paper_version_id",
                "paper_analyses.analysis_scope",
            ],
            name="fk_comparisons_source_analysis",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            [
                "target_analysis_id",
                "target_paper_id",
                "target_paper_version_id",
                "target_analysis_scope",
            ],
            [
                "paper_analyses.id",
                "paper_analyses.paper_id",
                "paper_analyses.paper_version_id",
                "paper_analyses.analysis_scope",
            ],
            name="fk_comparisons_target_analysis",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "search_session_id",
            "source_paper_version_id",
            "source_analysis_id",
            "target_paper_version_id",
            "target_analysis_id",
            "provider",
            "configured_model",
            "model_version",
            "prompt_version",
            name="uq_comparisons_provenance",
        ),
        sa.UniqueConstraint("id", "source_paper_id", name="uq_comparisons_source_ownership"),
        sa.UniqueConstraint("id", "target_paper_id", name="uq_comparisons_target_ownership"),
        sa.UniqueConstraint(
            "id",
            "source_paper_id",
            "source_paper_version_id",
            "target_paper_id",
            "target_paper_version_id",
            name="uq_comparisons_relation_ownership",
        ),
    )
    op.create_index("ix_comparisons_generated_at", "comparisons", ["generated_at"], unique=False)

    op.create_table(
        "comparison_dimensions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("comparison_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_value", sa.Text(), nullable=False),
        sa.Column("target_value", sa.Text(), nullable=False),
        sa.Column("assessment", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position BETWEEN 0 AND 13", name="ck_comparison_dimensions_position"),
        sa.CheckConstraint(
            "name IN ('RESEARCH_PROBLEM', 'TASK', 'METHOD', 'ARCHITECTURE', 'DATASETS', "
            "'BENCHMARKS', 'BASELINES', 'METRICS', 'REPORTED_RESULTS', "
            "'COMPUTE_OR_INFERENCE_BUDGET', 'CLAIMED_NOVELTY', 'LIMITATIONS', "
            "'CODE_AVAILABILITY', 'RESULT_COMPARABILITY')",
            name="ck_comparison_dimensions_name",
        ),
        sa.CheckConstraint(
            "(name = 'RESEARCH_PROBLEM' AND position = 0) OR "
            "(name = 'TASK' AND position = 1) OR "
            "(name = 'METHOD' AND position = 2) OR "
            "(name = 'ARCHITECTURE' AND position = 3) OR "
            "(name = 'DATASETS' AND position = 4) OR "
            "(name = 'BENCHMARKS' AND position = 5) OR "
            "(name = 'BASELINES' AND position = 6) OR "
            "(name = 'METRICS' AND position = 7) OR "
            "(name = 'REPORTED_RESULTS' AND position = 8) OR "
            "(name = 'COMPUTE_OR_INFERENCE_BUDGET' AND position = 9) OR "
            "(name = 'CLAIMED_NOVELTY' AND position = 10) OR "
            "(name = 'LIMITATIONS' AND position = 11) OR "
            "(name = 'CODE_AVAILABILITY' AND position = 12) OR "
            "(name = 'RESULT_COMPARABILITY' AND position = 13)",
            name="ck_comparison_dimensions_canonical_order",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_comparison_dimensions_schema_version"),
        sa.ForeignKeyConstraint(["comparison_id"], ["comparisons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("comparison_id", "name", name="uq_comparison_dimensions_name"),
        sa.UniqueConstraint("comparison_id", "position", name="uq_comparison_dimensions_position"),
        sa.UniqueConstraint("id", "comparison_id", name="uq_comparison_dimensions_ownership"),
    )

    op.create_table(
        "comparison_evidence_links",
        sa.Column("comparison_dimension_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_role", sa.String(length=8), nullable=False),
        sa.Column("comparison_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "evidence_role IN ('SOURCE', 'TARGET')",
            name="ck_comparison_evidence_links_role",
        ),
        sa.ForeignKeyConstraint(
            ["comparison_dimension_id", "comparison_id"],
            ["comparison_dimensions.id", "comparison_dimensions.comparison_id"],
            name="fk_comparison_evidence_links_dimension",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "evidence_paper_id", "evidence_paper_version_id"],
            ["evidence.id", "evidence.paper_id", "evidence.paper_version_id"],
            name="fk_comparison_evidence_links_evidence",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "evidence_analysis_id"],
            ["evidence.id", "evidence.analysis_id"],
            name="fk_comparison_evidence_links_evidence_analysis",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("comparison_dimension_id", "evidence_id", "evidence_role"),
    )

    op.create_table(
        "paper_relations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("comparison_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("provenance", sa.String(length=32), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("model_version", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_paper_version_id <> target_paper_version_id",
            name="ck_paper_relations_distinct_versions",
        ),
        sa.CheckConstraint(
            "relation_type IN ('CITES', 'SIMILAR_TO', 'EXTENDS', 'COMPARES_WITH', "
            "'CONTRADICTS', 'IMPROVES_ON')",
            name="ck_paper_relations_type",
        ),
        sa.CheckConstraint(
            "provenance IN ('METADATA_EXPLICIT', 'TEXT_EXPLICIT', "
            "'DETERMINISTICALLY_DERIVED', 'LLM_INFERRED', 'HUMAN_VERIFIED')",
            name="ck_paper_relations_provenance",
        ),
        sa.CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'HUMAN_VERIFIED', 'REJECTED')",
            name="ck_paper_relations_verification",
        ),
        sa.CheckConstraint(
            "(provenance = 'LLM_INFERRED' AND provider IS NOT NULL "
            "AND model_version IS NOT NULL AND prompt_version IS NOT NULL "
            "AND confidence BETWEEN 0 AND 1) OR "
            "(provenance <> 'LLM_INFERRED' AND provider IS NULL "
            "AND model_version IS NULL AND prompt_version IS NULL AND confidence IS NULL)",
            name="ck_paper_relations_model_provenance",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_paper_relations_schema_version"),
        sa.ForeignKeyConstraint(
            [
                "comparison_id",
                "source_paper_id",
                "source_paper_version_id",
                "target_paper_id",
                "target_paper_version_id",
            ],
            [
                "comparisons.id",
                "comparisons.source_paper_id",
                "comparisons.source_paper_version_id",
                "comparisons.target_paper_id",
                "comparisons.target_paper_version_id",
            ],
            name="fk_paper_relations_comparison_ownership",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_paper_version_id", "source_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_paper_relations_source_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_paper_version_id", "target_paper_id"],
            ["paper_versions.id", "paper_versions.paper_id"],
            name="fk_paper_relations_target_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "comparison_id",
            "source_paper_version_id",
            "target_paper_version_id",
            "relation_type",
            "provenance",
            name="uq_paper_relations_provenance",
        ),
        sa.UniqueConstraint("id", "comparison_id", name="uq_paper_relations_ownership"),
    )

    op.create_table(
        "relation_evidence_links",
        sa.Column("relation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("comparison_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["relation_id", "comparison_id"],
            ["paper_relations.id", "paper_relations.comparison_id"],
            name="fk_relation_evidence_links_relation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "evidence_paper_id", "evidence_paper_version_id"],
            ["evidence.id", "evidence.paper_id", "evidence.paper_version_id"],
            name="fk_relation_evidence_links_evidence",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("relation_id", "evidence_id"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_m3_data = bool(
        connection.scalar(
            sa.text(
                "SELECT EXISTS ("
                "SELECT 1 FROM external_paper_stubs "
                "UNION ALL SELECT 1 FROM external_paper_identifiers "
                "UNION ALL SELECT 1 FROM historical_backfill_runs "
                "UNION ALL SELECT 1 FROM historical_corpus_entries "
                "UNION ALL SELECT 1 FROM search_sessions "
                "UNION ALL SELECT 1 FROM search_actions "
                "UNION ALL SELECT 1 FROM search_candidates "
                "UNION ALL SELECT 1 FROM search_candidate_discoveries "
                "UNION ALL SELECT 1 FROM scientific_embeddings "
                "UNION ALL SELECT 1 FROM comparisons "
                "UNION ALL SELECT 1 FROM comparison_dimensions "
                "UNION ALL SELECT 1 FROM comparison_evidence_links "
                "UNION ALL SELECT 1 FROM paper_relations "
                "UNION ALL SELECT 1 FROM relation_evidence_links"
                ")"
            )
        )
    )
    allow_data_loss = (
        context.get_x_argument(as_dictionary=True).get("allow_m3_data_loss", "false").lower()
        == "true"
    )
    if has_m3_data and not allow_data_loss:
        raise RuntimeError(
            "M3 downgrade refused because historical-search, comparison, or relation data "
            "exists. Create and verify a PostgreSQL backup, then explicitly rerun with "
            "'-x allow_m3_data_loss=true' only when destructive rollback is intended."
        )

    op.drop_table("relation_evidence_links")
    op.drop_table("paper_relations")
    op.drop_table("comparison_evidence_links")
    op.drop_table("comparison_dimensions")
    op.drop_index("ix_comparisons_generated_at", table_name="comparisons")
    op.drop_table("comparisons")
    op.drop_index("ix_scientific_embeddings_model", table_name="scientific_embeddings")
    op.drop_table("scientific_embeddings")
    op.drop_table("search_candidate_discoveries")
    op.drop_index("ix_search_candidates_session_rank", table_name="search_candidates")
    op.drop_table("search_candidates")
    op.drop_table("search_actions")
    op.drop_index("ix_search_sessions_source_started", table_name="search_sessions")
    op.drop_table("search_sessions")
    op.drop_index("uq_historical_corpus_representative", table_name="historical_corpus_entries")
    op.drop_table("historical_corpus_entries")
    op.drop_table("historical_backfill_runs")
    op.drop_table("external_paper_identifiers")
    op.drop_index("ix_external_paper_stubs_doi", table_name="external_paper_stubs")
    op.drop_index("ix_external_paper_stubs_arxiv_id", table_name="external_paper_stubs")
    op.drop_index("ix_external_paper_stubs_lexical", table_name="external_paper_stubs")
    op.drop_index(
        "ix_external_paper_stubs_publication_date",
        table_name="external_paper_stubs",
    )
    op.drop_table("external_paper_stubs")
    op.drop_constraint("uq_evidence_paper_version_ownership", "evidence", type_="unique")
    op.drop_constraint("uq_paper_analyses_m3_ownership", "paper_analyses", type_="unique")
