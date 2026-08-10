import type {
  DailyRunEnvelope,
  KnowledgeGraph,
  Lineage,
  Report,
  RunDetail,
  TrendSnapshot,
} from "../api/client";

export const topicId = "cc6caeba-3832-42c4-8fbf-607a183490f8";
export const paperId = "00511b3e-1303-4e03-b846-d29fd641942d";
export const paperVersionId = "465c74ac-bdf8-42e2-8652-7fec30fce680";
export const historicalPaperId = "b431af71-5ea9-4903-a3ff-f611bdc50f32";
export const historicalPaperVersionId = "55e69ff3-643b-4699-9699-235b29bc71a1";
export const paperNodeId = "3229eb3c-c68f-47d1-b7b2-a71035d24e38";
export const historicalNodeId = "cf4e2d55-8644-40a4-ab39-aa137dfbe3cb";
export const methodNodeId = "a3de9207-edb9-4546-8c11-8bed403599af";
export const evidenceId = "a50b18f1-bf2c-4121-87ed-4c2d6b3d193b";
export const historicalEvidenceId = "aaec48b6-0ce0-43f1-95e7-1954129d79ca";
export const comparisonId = "638a6949-a4df-4ef9-b195-25309f576acd";
export const lineageId = "f87b55e3-1158-4109-bc41-6b75d3dd6956";
export const runId = "df0b73ea-cea0-4eb5-9501-e5680b472f85";
export const reportId = "a7673fa1-7de4-445b-b214-2232362eb584";

const report: Report = {
  id: reportId,
  run_id: runId,
  topic_id: topicId,
  logical_date: "2026-08-08",
  status: "PARTIAL",
  title: "Broad LLM agents daily report",
  summary: "One paper completed graph publication while one selected paper failed parsing.",
  source: "product_publication_pipeline",
  generated_at: "2026-08-08T05:04:00+08:00",
  schema_version: 1,
  created_at: "2026-08-08T05:04:00+08:00",
  failures: [
    {
      id: "c7276cc5-8134-49d6-a95e-2ee73c4025f8",
      report_id: reportId,
      paper_id: historicalPaperId,
      paper_version_id: historicalPaperVersionId,
      failed_stage: "PARSED",
      error_code: "GROBID_INVALID_TEI",
      retryable: false,
      error_detail: "The parser returned TEI without a body section.",
      schema_version: 1,
      created_at: "2026-08-08T05:04:00+08:00",
    },
  ],
  sections: [
    {
      id: "57b637e4-f809-48fd-abbb-a58e7b5a57ae",
      report_id: reportId,
      kind: "OVERVIEW",
      narrative: "The completed paper connects verified memory checks to agent planning.",
      evidence_ids: [evidenceId],
      schema_version: 1,
      created_at: "2026-08-08T05:04:00+08:00",
    },
  ],
  report_type: "DAILY",
  period_start: "2026-08-08",
  period_end: "2026-08-08",
  counts: { retrieved: 8, selected: 2, processed: 2, completed: 1, failed: 1 },
  highlighted_papers: [
    {
      paper_id: paperId,
      paper_version_id: paperVersionId,
      title: "Planning with Verifiable Agent Memory",
      reason: "Adds evidence-linked memory checks to planning.",
      evidence_ids: [evidenceId],
    },
  ],
  major_entities: [
    {
      graph_entity_id: methodNodeId,
      entity_type: "METHOD",
      label: "Source-grounded memory verification",
      distinct_paper_count: 2,
    },
  ],
  notable_comparisons: [
    {
      comparison_id: comparisonId,
      summary: "The method extends a historical validation protocol.",
      comparability_status: "PARTIALLY_COMPARABLE",
      evidence_ids: [evidenceId],
    },
  ],
  graph_changes: { entity_count: 3, edge_count: 2, new_entity_count: 1, inferred_edge_count: 1 },
  trend_snapshot_ids: ["a6ef960f-26d1-4559-b01e-081115c8490b"],
  lineage_highlights: [
    {
      lineage_snapshot_id: lineageId,
      root_paper_id: paperId,
      summary: "Memory verification lineage",
      uncertain: true,
    },
  ],
  evidence: [
    {
      id: evidenceId,
      paper_id: paperId,
      paper_version_id: paperVersionId,
      section: "3.2 Memory verification",
      excerpt: "Each memory entry is checked against its cited observation before planning.",
      evidence_type: "SUPPORTS",
      verification_status: "UNVERIFIED",
    },
  ],
  limitations: ["The report covers the currently retrieved corpus only."],
  missing_sections: ["LINEAGE"],
  narrative_mode: "DEEPSEEK",
  provider: "deepseek",
  configured_model: "deepseek-v4-flash",
  model_version: "deepseek-v4-flash-2026-08",
  prompt_version: "m4-report-v1",
  usage: {
    prompt_tokens: 400,
    completion_tokens: 120,
    total_tokens: 520,
    call_count: 1,
    duration_ms: 850,
    estimated_cost_usd: "0.0008",
  },
  verification_status: "UNVERIFIED",
};

const run = {
  id: runId,
  topic_id: topicId,
  source_run_id: "23767bfb-69b3-4f63-926f-6dc34d2a4572",
  logical_date: "2026-08-08",
  operation: "PRODUCT_PUBLICATION" as const,
  analysis_scope: "FULL_TEXT" as const,
  status: "PARTIAL" as const,
  started_at: "2026-08-08T05:03:00+08:00",
  completed_at: "2026-08-08T05:04:00+08:00",
  cursor_from: null,
  cursor_to: null,
  discovered_count: 8,
  normalized_count: 8,
  selected_count: 2,
  completed_count: 1,
  failed_count: 1,
  error_code: null,
  error_detail: null,
  schema_version: 1,
  created_at: "2026-08-08T05:03:00+08:00",
};

const runItems = [
  {
    id: "125790a2-7520-492f-a6ad-b3f10ce9075c",
    run_id: runId,
    paper_id: paperId,
    paper_version_id: paperVersionId,
    canonical_arxiv_id: "2608.01234",
    paper_title: "Planning with Verifiable Agent Memory",
    stage: "PUBLISHED" as const,
    status: "COMPLETED" as const,
    failed_stage: null,
    error_code: null,
    retryable: null,
    error_detail: null,
    schema_version: 1,
    created_at: "2026-08-08T05:03:00+08:00",
    updated_at: "2026-08-08T05:04:00+08:00",
  },
  {
    id: "8e21d299-e8db-4182-9e95-bb6281b2623e",
    run_id: runId,
    paper_id: historicalPaperId,
    paper_version_id: historicalPaperVersionId,
    canonical_arxiv_id: "2608.05678",
    paper_title: "A Failed Parser Fixture",
    stage: "PDF_DOWNLOADED" as const,
    status: "FAILED" as const,
    failed_stage: "PARSED" as const,
    error_code: "GROBID_INVALID_TEI",
    retryable: false,
    error_detail: "The parser returned TEI without a body section.",
    schema_version: 1,
    created_at: "2026-08-08T05:03:00+08:00",
    updated_at: "2026-08-08T05:03:20+08:00",
  },
];

export const dailyRun: DailyRunEnvelope = { run, items: runItems, report };
export const runDetail: RunDetail = { ...run, items: runItems, report };

const inferredEdge = {
  id: "c65cd240-bd5c-42f2-966f-29cda1d34587",
  source_entity_id: paperNodeId,
  target_entity_id: historicalNodeId,
  relation_type: "EXTENDS" as const,
  source_paper_version_id: paperVersionId,
  target_paper_version_id: historicalPaperVersionId,
  analysis_id: null,
  comparison_id: comparisonId,
  paper_relation_id: "ee9a1044-fb97-4df6-b959-1b6a507ac558",
  provenance: "LLM_INFERRED" as const,
  inferred: true,
  evidence_ids: [evidenceId, historicalEvidenceId],
  evidence: [
    {
      evidence_id: evidenceId,
      paper_id: paperId,
      paper_version_id: paperVersionId,
      role: "SOURCE" as const,
    },
    {
      evidence_id: historicalEvidenceId,
      paper_id: historicalPaperId,
      paper_version_id: historicalPaperVersionId,
      role: "TARGET" as const,
    },
  ],
  justification: "The new protocol extends historical memory validation.",
  model_provenance: {
    provider: "deepseek",
    configured_model: "deepseek-v4-flash",
    model_version: "deepseek-v4-flash-2026-08",
    prompt_version: "m3-comparison-v1",
  },
  confidence: 0.72,
  confidence_meaning:
    "A bounded model-reported support strength; it is not a probability or human verification.",
  verification_status: "UNVERIFIED" as const,
  generated_at: "2026-08-08T05:02:00+08:00",
  schema_version: 1,
  created_at: "2026-08-08T05:02:00+08:00",
};

export const knowledgeGraph: KnowledgeGraph = {
  topic_id: topicId,
  as_of: "2026-08-08",
  total_nodes: 3,
  total_edges: 2,
  total_mentions: 2,
  truncated: false,
  nodes: [
    {
      id: paperNodeId,
      topic_id: topicId,
      entity_type: "PAPER",
      paper_id: paperId,
      canonical_label: "Planning with Verifiable Agent Memory",
      normalized_key: `paper:${paperId}`,
      display_label: "Planning with Verifiable Agent Memory",
      aliases: [],
      provenance: "METADATA_EXPLICIT",
      inferred: false,
      source: "paper_metadata",
      mention_count: 1,
      mentions: [
        {
          id: "1478bdd9-427b-439d-bb3b-e0248fd5f132",
          paper_id: paperId,
          paper_version_id: paperVersionId,
          analysis_id: "8b28f2c7-f706-40e8-a0dc-696001298cab",
          comparison_id: null,
          observed_label: "Planning with Verifiable Agent Memory",
          provenance: "METADATA_EXPLICIT",
          inferred: false,
          evidence_ids: [],
          model_provenance: null,
          confidence: null,
          verification_status: "UNVERIFIED",
          generated_at: "2026-08-08T05:01:00+08:00",
          schema_version: 1,
          created_at: "2026-08-08T05:01:00+08:00",
        },
      ],
      schema_version: 1,
      created_at: "2026-08-08T05:01:00+08:00",
      updated_at: "2026-08-08T05:02:00+08:00",
    },
    {
      id: historicalNodeId,
      topic_id: topicId,
      entity_type: "PAPER",
      paper_id: historicalPaperId,
      canonical_label: "Historical Memory Checks for Tool-Using Agents",
      normalized_key: `paper:${historicalPaperId}`,
      display_label: "Historical Memory Checks for Tool-Using Agents",
      aliases: [],
      provenance: "METADATA_EXPLICIT",
      inferred: false,
      source: "paper_metadata",
      mention_count: 0,
      mentions: [],
      schema_version: 1,
      created_at: "2026-08-08T05:01:00+08:00",
      updated_at: "2026-08-08T05:02:00+08:00",
    },
    {
      id: methodNodeId,
      topic_id: topicId,
      entity_type: "METHOD",
      paper_id: null,
      canonical_label: "Source-grounded memory verification",
      normalized_key: "source-grounded memory verification",
      display_label: "Source-grounded memory verification",
      aliases: [],
      provenance: "DETERMINISTICALLY_DERIVED",
      inferred: false,
      source: "structured_analysis",
      mention_count: 1,
      mentions: [
        {
          id: "429961f7-f44e-4a56-8921-11521dd48f73",
          paper_id: paperId,
          paper_version_id: paperVersionId,
          analysis_id: "8b28f2c7-f706-40e8-a0dc-696001298cab",
          comparison_id: null,
          observed_label: "Source-grounded memory verification",
          provenance: "TEXT_EXPLICIT",
          inferred: false,
          evidence_ids: [evidenceId],
          model_provenance: null,
          confidence: null,
          verification_status: "UNVERIFIED",
          generated_at: "2026-08-08T05:01:00+08:00",
          schema_version: 1,
          created_at: "2026-08-08T05:01:00+08:00",
        },
      ],
      schema_version: 1,
      created_at: "2026-08-08T05:01:00+08:00",
      updated_at: "2026-08-08T05:02:00+08:00",
    },
  ],
  edges: [
    inferredEdge,
    {
      id: "05b4be58-958f-47d7-a353-4c4afc4f4d6d",
      source_entity_id: paperNodeId,
      target_entity_id: methodNodeId,
      relation_type: "USES_METHOD",
      source_paper_version_id: paperVersionId,
      target_paper_version_id: null,
      analysis_id: "8b28f2c7-f706-40e8-a0dc-696001298cab",
      comparison_id: null,
      paper_relation_id: null,
      provenance: "TEXT_EXPLICIT",
      inferred: false,
      evidence_ids: [evidenceId],
      evidence: [
        {
          evidence_id: evidenceId,
          paper_id: paperId,
          paper_version_id: paperVersionId,
          role: "SOURCE",
        },
      ],
      justification: "The method is explicit in the persisted analysis evidence.",
      model_provenance: null,
      confidence: null,
      confidence_meaning: null,
      verification_status: "UNVERIFIED",
      generated_at: "2026-08-08T05:01:00+08:00",
      schema_version: 1,
      created_at: "2026-08-08T05:01:00+08:00",
    },
  ],
};

function trend(window: "7D" | "30D" | "90D", included: number): TrendSnapshot {
  const sufficient = included >= 10 ? "SUFFICIENT" : included >= 3 ? "LIMITED" : "INSUFFICIENT";
  return {
    id:
      window === "7D"
        ? "a6ef960f-26d1-4559-b01e-081115c8490b"
        : window === "30D"
          ? "6ae2318c-566e-4113-936c-78df8e23180f"
          : "f06cbd25-e674-4fc8-b262-1401091cdbaf",
    topic_id: topicId,
    as_of_date: "2026-08-08",
    window,
    window_start: "2026-08-02",
    window_end: "2026-08-08",
    preceding_window_start: "2026-07-26",
    preceding_window_end: "2026-08-01",
    included_paper_count: included,
    preceding_paper_count: window === "30D" ? 0 : 8,
    paper_count_change: {
      current_count: included,
      preceding_count: window === "30D" ? 0 : 8,
      absolute_change: included - (window === "30D" ? 0 : 8),
      denominator_count: window === "30D" ? 0 : 8,
      relative_change: window === "30D" ? null : "0.5",
      growth_status: window === "30D" ? "ZERO_DENOMINATOR" : "AVAILABLE",
    },
    entity_counts: [
      {
        entity_id: methodNodeId,
        entity_type: "METHOD",
        label: "Source-grounded memory verification",
        change: {
          current_count: included,
          preceding_count: 2,
          absolute_change: included - 2,
          denominator_count: 2,
          relative_change: included < 3 ? null : "1.0",
          growth_status: included < 3 ? "LIMITED_SAMPLE" : "AVAILABLE",
        },
        newly_appearing: false,
        recurring: true,
      },
    ],
    total_entities: 1,
    truncated: false,
    relation_counts: [
      {
        relation_type: "USES_METHOD",
        change: {
          current_count: included,
          preceding_count: 2,
          absolute_change: included - 2,
          denominator_count: 2,
          relative_change: included < 3 ? null : "1.0",
          growth_status: included < 3 ? "LIMITED_SAMPLE" : "AVAILABLE",
        },
      },
    ],
    new_entity_ids: [],
    recurring_entity_ids: [methodNodeId],
    representative_papers: [
      {
        paper_id: paperId,
        paper_version_id: paperVersionId,
        activity_date: "2026-08-08",
        title: "Planning with Verifiable Agent Memory",
      },
    ],
    data_sufficiency: sufficient,
    preceding_data_sufficiency: "LIMITED",
    thresholds: { limited_paper_count: 3, sufficient_paper_count: 10, minimum_growth_denominator: 3 },
    aggregation_version: "m4-trends-v1",
    generated_at: "2026-08-08T05:03:00+08:00",
    schema_version: 1,
  };
}

export const sevenDayTrend = trend("7D", 12);
export const thirtyDayTrend = trend("30D", 2);
export const ninetyDayTrend = trend("90D", 6);

export const lineage: Lineage = {
  id: lineageId,
  topic_id: topicId,
  root_paper_id: paperId,
  as_of_date: "2026-08-08",
  nodes: [
    {
      graph_entity_id: historicalNodeId,
      paper_id: historicalPaperId,
      title: "Historical Memory Checks for Tool-Using Agents",
      publication_date: "2025-09-01",
      depth: 1,
    },
    {
      graph_entity_id: paperNodeId,
      paper_id: paperId,
      title: "Planning with Verifiable Agent Memory",
      publication_date: "2026-08-01",
      depth: 0,
    },
  ],
  edges: [inferredEdge],
  permitted_relation_types: ["CITES", "EXTENDS", "IMPROVES_ON"],
  max_depth: 5,
  max_nodes: 100,
  max_edges: 200,
  truncated: false,
  explicit_predecessor_available: false,
  verified_predecessor_available: false,
  corpus_scope: "CURRENTLY_RETRIEVED_CORPUS",
  limitations: ["Only the currently retrieved corpus is represented."],
  lineage_version: "m4-lineage-v1",
  generated_at: "2026-08-08T05:03:00+08:00",
  schema_version: 1,
};

export { report };
