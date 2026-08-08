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

export async function getLatestRun() {
  const { data, error, response } = await api.GET("/api/v1/runs/latest");

  if (response.status === 404) {
    return null;
  }

  if (!response.ok || data === undefined) {
    throw requestError(response.status, error);
  }

  return data;
}

export type LatestRun = Exclude<Awaited<ReturnType<typeof getLatestRun>>, null>;
