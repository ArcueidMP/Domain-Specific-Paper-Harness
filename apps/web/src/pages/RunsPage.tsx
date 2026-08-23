import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { runQuery, runsQuery } from "../api/queries";
import { LatestRunPanel } from "../components/LatestRunPanel";
import { ReportDetail } from "../components/ReportDetail";
import { RunStatusBadge } from "../components/RunStatusBadge";
import { StateNotice } from "../components/StateNotice";
import { TopicLink } from "../components/TopicLink";
import { formatDateTime } from "../lib/format";
import { useTopicSlug } from "../lib/topic";

function readable(value: string): string {
  return value.replaceAll("_", " ").toLocaleLowerCase();
}

function executionModeLabel(value: string): string {
  if (value === "SMOKE") {
    return "Deployment smoke";
  }
  if (value === "NORMAL") {
    return "Normal execution";
  }
  if (value === "REPROCESS") {
    return "Reprocessed publication";
  }
  return "Standalone operation";
}

export function RunsPage() {
  const topicSlug = useTopicSlug();
  const { runId } = useParams();
  const runs = useQuery(runsQuery(topicSlug));
  const detail = useQuery(runQuery(topicSlug, runId));

  return (
    <section className="page-section">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Persisted pipeline operations</p>
          <h1>Run status</h1>
          <p className="lede">
            Inspect each stage, stable error code, retryability decision, and publication result.
            This read-only view never starts or retries a job.
          </p>
        </div>
      </header>

      <div className="runs-layout">
        <aside className="run-index" aria-labelledby="run-index-title">
          <div className="section-title-row">
            <h2 id="run-index-title">Recent runs</h2>
            {runs.data ? <span>{runs.data.total} recorded</span> : null}
          </div>
          {runs.isPending ? <StateNotice kind="loading" title="Loading run history" /> : null}
          {runs.isError ? (
            <StateNotice
              kind="error"
              detail={runs.error.message}
              onRetry={() => void runs.refetch()}
            />
          ) : null}
          {runs.data?.items.length === 0 ? (
            <StateNotice
              kind="empty"
              title="No runs recorded"
              detail="A persisted pipeline run will appear after the first command or scheduled job."
            />
          ) : null}
          {runs.data && runs.data.items.length > 0 ? (
            <ol className="run-index-list">
              {runs.data.items.map((run) => (
                <li key={run.id}>
                  <TopicLink
                    className={run.id === detail.data?.id ? "active" : ""}
                    to={`/runs/${run.id}`}
                  >
                    <div>
                      <strong>{run.logical_date}</strong>
                      <div className="run-index-statuses" aria-label="Operation and pipeline status">
                        <span>
                          <small>Operation</small>
                          <RunStatusBadge status={run.status} />
                        </span>
                        {run.pipeline_status ? (
                          <span>
                            <small>Pipeline</small>
                            <RunStatusBadge status={run.pipeline_status} />
                          </span>
                        ) : null}
                      </div>
                    </div>
                    <span>
                      {executionModeLabel(run.pipeline_execution_mode)} · {readable(run.operation)}
                    </span>
                    <small>{run.completed_count} completed / {run.failed_count} failed</small>
                  </TopicLink>
                </li>
              ))}
            </ol>
          ) : null}
        </aside>

        <div className="run-detail-column">
          {detail.isPending ? <StateNotice kind="loading" title="Loading run detail" /> : null}
          {detail.isError ? (
            <StateNotice
              kind="error"
              detail={`${detail.error.message} No item state has been inferred.`}
              onRetry={() => void detail.refetch()}
            />
          ) : null}
          {detail.isSuccess && detail.data === null ? (
            <StateNotice
              kind="empty"
              title="Run not found"
              detail="The requested run ID does not match a persisted operation."
            />
          ) : null}
          {detail.data ? (
            <>
              <LatestRunPanel run={detail.data} items={detail.data.items} heading="Selected run" />
              <section className="run-items" aria-labelledby="run-items-title">
                <div className="section-title-row">
                  <h2 id="run-items-title">Item stages</h2>
                  <span>{detail.data.items.length} selected papers</span>
                </div>
                {detail.data.items.length === 0 ? (
                  <StateNotice
                    kind="empty"
                    title="No run items"
                    detail="This operation did not persist any per-paper item state."
                  />
                ) : (
                  <div className="run-item-table-wrap card">
                    <table className="run-item-table">
                      <thead>
                        <tr>
                          <th>Paper</th>
                          <th>Current stage</th>
                          <th>Status</th>
                          <th>Failure</th>
                          <th>Updated</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.data.items.map((item) => (
                          <tr key={item.id}>
                            <td>
                              <TopicLink to={`/papers/${item.paper_id}`}>
                                {item.paper_title}
                              </TopicLink>
                              <small>arXiv:{item.canonical_arxiv_id}</small>
                            </td>
                            <td>{readable(item.stage)}</td>
                            <td><span className={`item-status ${item.status.toLocaleLowerCase()}`}>{item.status}</span></td>
                            <td>
                              {item.error_code ? (
                                <>
                                  <strong>{item.error_code}</strong>
                                  <span>{item.failed_stage ? readable(item.failed_stage) : "stage unavailable"}</span>
                                  <small>{item.retryable === null ? "retryability unavailable" : item.retryable ? "retryable" : "not retryable"}</small>
                                  {item.error_detail ? <p>{item.error_detail}</p> : null}
                                </>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td>{formatDateTime(item.updated_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
              {detail.data.report ? (
                <section className="run-report" aria-labelledby="run-report-title">
                  <div className="section-title-row">
                    <h2 id="run-report-title">Published report</h2>
                  </div>
                  <ReportDetail report={detail.data.report} items={detail.data.items} compact />
                </section>
              ) : null}
            </>
          ) : null}
        </div>
      </div>
    </section>
  );
}
