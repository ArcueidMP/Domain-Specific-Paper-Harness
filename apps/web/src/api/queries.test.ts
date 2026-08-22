import { describe, expect, it, vi } from "vitest";

import {
  dailyQuery,
  dailyReportsQuery,
  knowledgeGraphQuery,
  latestDailyQuery,
  lineageQuery,
  papersQuery,
  runQuery,
  runsQuery,
  trendsQuery,
} from "./queries";
import { jsonResponse } from "../test/render";

const topic = "world-models";

function requestUrl(input: RequestInfo | URL): URL {
  return new URL(input instanceof Request ? input.url : input.toString(), "http://localhost");
}

async function execute(options: { queryFn?: unknown }) {
  if (typeof options.queryFn !== "function") {
    throw new TypeError("Expected an executable query function.");
  }
  await (options.queryFn as () => Promise<unknown>)();
}

describe("topic-scoped query contracts", () => {
  it("includes the topic in every topic-dependent query key", () => {
    expect(papersQuery(topic, 20, 0).queryKey).toEqual([
      "papers",
      { topic, limit: 20, offset: 0 },
    ]);
    expect(latestDailyQuery(topic).queryKey).toEqual(["daily", "latest", { topic }]);
    expect(dailyQuery(topic, "2026-08-22").queryKey).toEqual([
      "daily",
      "2026-08-22",
      { topic },
    ]);
    expect(dailyReportsQuery(topic).queryKey).toEqual([
      "reports",
      "daily",
      { topic, limit: 20, offset: 0 },
    ]);
    expect(knowledgeGraphQuery(topic).queryKey).toEqual(["graph", { topic }]);
    expect(trendsQuery(topic, ["7D"]).queryKey).toEqual([
      "trends",
      { topic, windows: ["7D"] },
    ]);
    expect(lineageQuery(topic, "paper-id").queryKey).toEqual([
      "lineages",
      "paper-id",
      { topic, maxDepth: 5 },
    ]);
    expect(runsQuery(topic).queryKey).toEqual([
      "runs",
      { topic, limit: 50, offset: 0 },
    ]);
    expect(runQuery(topic).queryKey).toEqual([
      "runs",
      "latest",
      "detail",
      { topic },
    ]);
  });

  it("sends the active topic to every topic-dependent API request", async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL) => Promise<Response>>((input) => {
      void input;
      return Promise.resolve(jsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    await execute(papersQuery(topic, 20, 0));
    await execute(latestDailyQuery(topic));
    await execute(dailyQuery(topic, "2026-08-22"));
    await execute(dailyReportsQuery(topic));
    await execute(knowledgeGraphQuery(topic));
    await execute(trendsQuery(topic, ["7D"]));
    await execute(lineageQuery(topic, "paper-id"));
    await execute(runsQuery(topic));
    await execute(runQuery(topic));

    expect(fetchMock).toHaveBeenCalledTimes(9);
    for (const [input] of fetchMock.mock.calls) {
      expect(requestUrl(input).searchParams.get("topic")).toBe(topic);
    }
  });
});
