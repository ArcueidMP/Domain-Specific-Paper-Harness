import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

const api = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string | undefined;

  constructor(status: number, detail?: string, code?: string) {
    super(detail ?? `Request failed with status ${status}.`);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
  }
}

function readErrorDetail(
  error: unknown,
): Partial<components["schemas"]["ApiErrorDetail"]> {
  if (typeof error !== "object" || error === null || !("detail" in error)) {
    return {};
  }

  const detail = error.detail;
  if (typeof detail === "string") {
    return { message: detail };
  }
  if (typeof detail !== "object" || detail === null) {
    return {};
  }

  const code = "code" in detail && typeof detail.code === "string" ? detail.code : undefined;
  const message =
    "message" in detail && typeof detail.message === "string" ? detail.message : undefined;
  return { code, message };
}

function requestError(status: number, error: unknown): ApiRequestError {
  const detail = readErrorDetail(error);
  return new ApiRequestError(status, detail.message, detail.code);
}

export async function getTopics() {
  const { data, error, response } = await api.GET("/api/v1/topics");

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export type TopicsResponse = Awaited<ReturnType<typeof getTopics>>;
export type TopicSummary = TopicsResponse["items"][number];

type PaperQuery = NonNullable<paths["/api/v1/papers"]["get"]["parameters"]["query"]>;

export async function getPapers(query: PaperQuery = {}) {
  const { data, error, response } = await api.GET("/api/v1/papers", {
    params: { query },
  });

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export type PapersResponse = Awaited<ReturnType<typeof getPapers>>;
export type PaperSummary = PapersResponse["items"][number];

export async function getPaper(paperId: string) {
  const { data, error, response } = await api.GET("/api/v1/papers/{paper_id}", {
    params: { path: { paper_id: paperId } },
  });

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export type PaperDetail = Awaited<ReturnType<typeof getPaper>>;

export async function getPaperAnalysis(paperId: string, paperVersionId: string) {
  const { data, error, response } = await api.GET("/api/v1/papers/{paper_id}/analysis", {
    params: {
      path: { paper_id: paperId },
      query: { paper_version_id: paperVersionId },
    },
  });

  if (response.status === 404) {
    const detail = readErrorDetail(error);
    if (detail.code === "ANALYSIS_NOT_FOUND") {
      return null;
    }
    throw requestError(response.status, error);
  }

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export type PaperAnalysis = components["schemas"]["PaperAnalysisResponse"];
export type AnalysisClaim = components["schemas"]["AnalysisClaimResponse"];

export async function getPaperEvidence(
  paperId: string,
  analysisId: string,
  paperVersionId: string,
  scope: components["schemas"]["AnalysisScope"],
) {
  const { data, error, response } = await api.GET("/api/v1/papers/{paper_id}/evidence", {
    params: {
      path: { paper_id: paperId },
      query: {
        analysis_id: analysisId,
        paper_version_id: paperVersionId,
        scope,
      },
    },
  });

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export type EvidenceItem = components["schemas"]["EvidenceResponse"];
export type EvidenceList = components["schemas"]["EvidenceListResponse"];

export async function getPaperRelatedWork(paperId: string, paperVersionId: string) {
  const { data, error, response } = await api.GET("/api/v1/papers/{paper_id}/related", {
    params: {
      path: { paper_id: paperId },
      query: { paper_version_id: paperVersionId },
    },
  });

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export type RelatedWork = components["schemas"]["RelatedWorkResponse"];
export type RelatedWorkItem = components["schemas"]["RelatedWorkItemResponse"];

export type ComparisonEvidence = components["schemas"]["ComparisonEvidenceResponse"];

export async function getComparison(comparisonId: string) {
  const { data, error, response } = await api.GET("/api/v1/comparisons/{comparison_id}", {
    params: { path: { comparison_id: comparisonId } },
  });

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export type Comparison = components["schemas"]["ComparisonResponse"];
export type ComparisonDimension = components["schemas"]["ComparisonDimensionResponse"];
export type PaperRelation = components["schemas"]["PaperRelationResponse"];

type LatestDailyQuery = NonNullable<
  paths["/api/v1/daily/latest"]["get"]["parameters"]["query"]
>;
type DailyQuery = NonNullable<
  paths["/api/v1/daily/{logical_date}"]["get"]["parameters"]["query"]
>;

export async function getLatestDaily(query: LatestDailyQuery = {}) {
  const { data, error, response } = await api.GET("/api/v1/daily/latest", {
    params: { query },
  });

  if (response.status === 404) {
    const detail = readErrorDetail(error);
    if (detail.code === "PRODUCT_RUN_NOT_FOUND") {
      return null;
    }
  }

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export async function getDaily(logicalDate: string, query: DailyQuery = {}) {
  const { data, error, response } = await api.GET("/api/v1/daily/{logical_date}", {
    params: { path: { logical_date: logicalDate }, query },
  });

  if (response.status === 404) {
    const detail = readErrorDetail(error);
    if (detail.code === "PRODUCT_RUN_NOT_FOUND") {
      return null;
    }
  }

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export type DailyRunEnvelope = Exclude<Awaited<ReturnType<typeof getLatestDaily>>, null>;
export type Report = components["schemas"]["ReportResponse"];
export type ReportEvidenceReference = components["schemas"]["ReportEvidenceReferenceResponse"];
export type RunSummary = components["schemas"]["RunSummary"];
export type RunItem = components["schemas"]["RunItemResponse"];

type DailyReportsQuery = NonNullable<
  paths["/api/v1/reports/daily"]["get"]["parameters"]["query"]
>;

export async function getDailyReports(query: DailyReportsQuery = {}) {
  const { data, error, response } = await api.GET("/api/v1/reports/daily", {
    params: { query },
  });

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

type GraphQuery = NonNullable<paths["/api/v1/graph"]["get"]["parameters"]["query"]>;

export async function getKnowledgeGraph(query: GraphQuery = {}) {
  const { data, error, response } = await api.GET("/api/v1/graph", {
    params: { query },
  });

  if (response.status === 404) {
    const detail = readErrorDetail(error);
    if (detail.code === "GRAPH_NOT_FOUND") {
      return null;
    }
  }

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export type KnowledgeGraph = Exclude<Awaited<ReturnType<typeof getKnowledgeGraph>>, null>;
export type GraphNode = components["schemas"]["GraphNodeResponse"];
export type GraphEdge = components["schemas"]["GraphEdgeResponse"];
export type GraphEntityType = components["schemas"]["GraphEntityType"];
export type GraphRelationType = components["schemas"]["GraphRelationType"];
export type RelationProvenance = components["schemas"]["RelationProvenance"];

type TrendsQuery = NonNullable<paths["/api/v1/trends"]["get"]["parameters"]["query"]>;

export async function getTrends(query: TrendsQuery = {}) {
  const { data, error, response } = await api.GET("/api/v1/trends", {
    params: { query },
  });

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export type Trends = Awaited<ReturnType<typeof getTrends>>;
export type TrendSnapshot = components["schemas"]["TrendSnapshotResponse"];
export type TrendWindow = components["schemas"]["TrendWindow"];

type LineageQuery = NonNullable<
  paths["/api/v1/lineages/{entity_or_paper_id}"]["get"]["parameters"]["query"]
>;

export async function getLineage(entityOrPaperId: string, query: LineageQuery = {}) {
  const { data, error, response } = await api.GET(
    "/api/v1/lineages/{entity_or_paper_id}",
    {
      params: { path: { entity_or_paper_id: entityOrPaperId }, query },
    },
  );

  if (response.status === 404) {
    const detail = readErrorDetail(error);
    if (detail.code === "LINEAGE_NOT_FOUND") {
      return null;
    }
  }

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export type Lineage = Exclude<Awaited<ReturnType<typeof getLineage>>, null>;

type RunsQuery = NonNullable<paths["/api/v1/runs"]["get"]["parameters"]["query"]>;
type LatestRunQuery = NonNullable<
  paths["/api/v1/runs/latest"]["get"]["parameters"]["query"]
>;

export async function getRuns(query: RunsQuery = {}) {
  const { data, error, response } = await api.GET("/api/v1/runs", {
    params: { query },
  });

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export async function getRun(runId: string) {
  const { data, error, response } = await api.GET("/api/v1/runs/{run_id}", {
    params: { path: { run_id: runId } },
  });

  if (response.status === 404) {
    const detail = readErrorDetail(error);
    if (detail.code === "RUN_NOT_FOUND") {
      return null;
    }
  }

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export type RunDetail = Exclude<Awaited<ReturnType<typeof getRun>>, null>;

export async function getLatestRun(query: LatestRunQuery = {}) {
  const { data, error, response } = await api.GET("/api/v1/runs/latest", {
    params: { query },
  });

  if (response.status === 404) {
    return null;
  }

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export type LatestRun = Exclude<Awaited<ReturnType<typeof getLatestRun>>, null>;
