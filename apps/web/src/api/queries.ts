import { queryOptions } from "@tanstack/react-query";

import { getLatestRun, getPapers, getTopics } from "./client";

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

export const latestRunQuery = () =>
  queryOptions({
    queryKey: ["runs", "latest"],
    queryFn: getLatestRun,
  });
