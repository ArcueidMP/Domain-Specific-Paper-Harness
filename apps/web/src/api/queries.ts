import { queryOptions } from "@tanstack/react-query";

import {
  getComparison,
  getDaily,
  getDailyReports,
  getKnowledgeGraph,
  getLatestRun,
  getLatestDaily,
  getLineage,
  getPaper,
  getPaperAnalysis,
  getPaperEvidence,
  getPaperRelatedWork,
  getPapers,
  getRun,
  getRuns,
  getTopics,
  getTrends,
} from "./client";

import type {
  GraphEntityType,
  GraphRelationType,
  RelationProvenance,
  TrendWindow,
} from "./client";

export const topicsQuery = () =>
  queryOptions({
    queryKey: ["topics"],
    queryFn: getTopics,
  });

export const papersQuery = (limit: number, offset: number) =>
  queryOptions({
    queryKey: ["papers", { limit, offset }],
    queryFn: () => getPapers({ limit, offset }),
  });

export const paperQuery = (paperId: string) =>
  queryOptions({
    queryKey: ["papers", paperId],
    queryFn: () => getPaper(paperId),
  });

export const paperAnalysisQuery = (paperId: string, paperVersionId: string) =>
  queryOptions({
    queryKey: ["papers", paperId, "analysis", paperVersionId],
    queryFn: () => getPaperAnalysis(paperId, paperVersionId),
  });

export const paperEvidenceQuery = (
  paperId: string,
  analysisId: string,
  paperVersionId: string,
  scope: "ABSTRACT_ONLY" | "FULL_TEXT",
) =>
  queryOptions({
    queryKey: ["papers", paperId, "evidence", analysisId],
    queryFn: () => getPaperEvidence(paperId, analysisId, paperVersionId, scope),
  });

export const paperRelatedWorkQuery = (paperId: string, paperVersionId: string) =>
  queryOptions({
    queryKey: ["papers", paperId, "related", paperVersionId],
    queryFn: () => getPaperRelatedWork(paperId, paperVersionId),
  });

export const comparisonQuery = (comparisonId: string) =>
  queryOptions({
    queryKey: ["comparisons", comparisonId],
    queryFn: () => getComparison(comparisonId),
  });

export const latestRunQuery = () =>
  queryOptions({
    queryKey: ["runs", "latest"],
    queryFn: getLatestRun,
  });

export const latestDailyQuery = () =>
  queryOptions({
    queryKey: ["daily", "latest"],
    queryFn: () => getLatestDaily(),
  });

export const dailyQuery = (logicalDate: string) =>
  queryOptions({
    queryKey: ["daily", logicalDate],
    queryFn: () => getDaily(logicalDate),
  });

export const dailyReportsQuery = (limit = 20, offset = 0) =>
  queryOptions({
    queryKey: ["reports", "daily", { limit, offset }],
    queryFn: () => getDailyReports({ limit, offset }),
  });

export type GraphFilters = {
  entityType?: GraphEntityType;
  relationType?: GraphRelationType;
  provenance?: RelationProvenance;
  paperId?: string;
  entityId?: string;
};

export const knowledgeGraphQuery = (filters: GraphFilters = {}) =>
  queryOptions({
    queryKey: ["graph", filters],
    queryFn: () =>
      getKnowledgeGraph({
        entity_type: filters.entityType,
        relation_type: filters.relationType,
        provenance: filters.provenance,
        paper_id: filters.paperId,
        entity_id: filters.entityId,
        max_nodes: 200,
        max_edges: 400,
      }),
  });

export const trendsQuery = (windows?: TrendWindow[]) =>
  queryOptions({
    queryKey: ["trends", { windows }],
    queryFn: () =>
      getTrends(windows ? { window: windows, max_entities: 50 } : { max_entities: 50 }),
  });

export const lineageQuery = (entityOrPaperId: string, maxDepth = 5) =>
  queryOptions({
    queryKey: ["lineages", entityOrPaperId, { maxDepth }],
    queryFn: () =>
      getLineage(entityOrPaperId, { max_depth: maxDepth, max_nodes: 100, max_edges: 200 }),
  });

export const runsQuery = (limit = 50, offset = 0) =>
  queryOptions({
    queryKey: ["runs", { limit, offset }],
    queryFn: () => getRuns({ limit, offset }),
  });

export const runQuery = (runId?: string) =>
  queryOptions({
    queryKey: ["runs", runId ?? "latest", "detail"],
    queryFn: () => (runId ? getRun(runId) : getLatestRun()),
  });
