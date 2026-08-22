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

export const papersQuery = (topic: string, limit: number, offset: number) =>
  queryOptions({
    queryKey: ["papers", { topic, limit, offset }],
    queryFn: () => getPapers({ topic, limit, offset }),
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

export const latestRunQuery = (topic: string) =>
  queryOptions({
    queryKey: ["runs", "latest", { topic }],
    queryFn: () => getLatestRun({ topic }),
  });

export const latestDailyQuery = (topic: string) =>
  queryOptions({
    queryKey: ["daily", "latest", { topic }],
    queryFn: () => getLatestDaily({ topic }),
  });

export const dailyQuery = (topic: string, logicalDate: string) =>
  queryOptions({
    queryKey: ["daily", logicalDate, { topic }],
    queryFn: () => getDaily(logicalDate, { topic }),
  });

export const dailyReportsQuery = (topic: string, limit = 20, offset = 0) =>
  queryOptions({
    queryKey: ["reports", "daily", { topic, limit, offset }],
    queryFn: () => getDailyReports({ topic, limit, offset }),
  });

export type GraphFilters = {
  entityType?: GraphEntityType;
  relationType?: GraphRelationType;
  provenance?: RelationProvenance;
  paperId?: string;
  entityId?: string;
};

export const knowledgeGraphQuery = (topic: string, filters: GraphFilters = {}) =>
  queryOptions({
    queryKey: ["graph", { topic, ...filters }],
    queryFn: () =>
      getKnowledgeGraph({
        topic,
        entity_type: filters.entityType,
        relation_type: filters.relationType,
        provenance: filters.provenance,
        paper_id: filters.paperId,
        entity_id: filters.entityId,
        max_nodes: 200,
        max_edges: 400,
      }),
  });

export const trendsQuery = (topic: string, windows?: TrendWindow[]) =>
  queryOptions({
    queryKey: ["trends", { topic, windows }],
    queryFn: () =>
      getTrends(
        windows
          ? { topic, window: windows, max_entities: 50 }
          : { topic, max_entities: 50 },
      ),
  });

export const lineageQuery = (topic: string, entityOrPaperId: string, maxDepth = 5) =>
  queryOptions({
    queryKey: ["lineages", entityOrPaperId, { topic, maxDepth }],
    queryFn: () =>
      getLineage(entityOrPaperId, {
        topic,
        max_depth: maxDepth,
        max_nodes: 100,
        max_edges: 200,
      }),
  });

export const runsQuery = (topic: string, limit = 50, offset = 0) =>
  queryOptions({
    queryKey: ["runs", { topic, limit, offset }],
    queryFn: () => getRuns({ topic, limit, offset }),
  });

export const runQuery = (topic: string, runId?: string) =>
  queryOptions({
    queryKey: ["runs", runId ?? "latest", "detail", { topic }],
    queryFn: () => (runId ? getRun(runId) : getLatestRun({ topic })),
  });
