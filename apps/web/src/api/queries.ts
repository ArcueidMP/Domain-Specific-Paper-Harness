import { queryOptions } from "@tanstack/react-query";

import {
  getComparison,
  getLatestRun,
  getPaper,
  getPaperAnalysis,
  getPaperEvidence,
  getPaperRelatedWork,
  getPapers,
  getTopics,
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
