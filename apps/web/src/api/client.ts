import createClient from "openapi-fetch";

import type { paths } from "./schema";

const api = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export class ApiRequestError extends Error {
  readonly status: number;

  constructor(status: number, detail?: string) {
    super(detail ? `Request failed (${status}): ${detail}` : `Request failed with status ${status}.`);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

function readErrorDetail(error: unknown): string | undefined {
  if (typeof error !== "object" || error === null || !("detail" in error)) {
    return undefined;
  }

  const detail = error.detail;
  return typeof detail === "string" ? detail : undefined;
}

export async function getTopics() {
  const { data, error, response } = await api.GET("/api/v1/topics");

  if (!response.ok || data === undefined) {
    throw new ApiRequestError(response.status, readErrorDetail(error));
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
    throw new ApiRequestError(response.status, readErrorDetail(error));
  }

  return data;
}

export type PapersResponse = Awaited<ReturnType<typeof getPapers>>;
export type PaperSummary = PapersResponse["items"][number];

export async function getLatestRun() {
  const { data, error, response } = await api.GET("/api/v1/runs/latest");

  if (response.status === 404) {
    return null;
  }

  if (!response.ok || data === undefined) {
    throw new ApiRequestError(response.status, readErrorDetail(error));
  }

  return data;
}

export type LatestRun = Exclude<Awaited<ReturnType<typeof getLatestRun>>, null>;
