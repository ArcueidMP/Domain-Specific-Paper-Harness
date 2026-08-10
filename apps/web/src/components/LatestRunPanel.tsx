import { Link } from "react-router-dom";

import type { RunItem, RunSummary } from "../api/client";
import { formatDateTime } from "../lib/format";
import { RunStatusBadge } from "./RunStatusBadge";

type LatestRunPanelProps = {
  run: RunSummary;
  items?: RunItem[];
  heading?: string;
};

export function LatestRunPanel({ run, items = [], heading = "Daily run" }: LatestRunPanelProps) {
  const failedItems = items.filter(
    (item) => item.status === "FAILED" || item.failed_stage !== null,
  );

  return (
    <article className="run-panel card">
      <div className="run-panel-heading">
        <div>
          <p className="eyebrow">{heading}</p>
          <h2 className="panel-title">{run.logical_date}</h2>
          <span className="run-operation">{run.operation.replaceAll("_", " ")}</span>
          {run.analysis_scope ? (
            <span className={`scope-badge ${run.analysis_scope.toLocaleLowerCase()}`}>
              {run.analysis_scope === "FULL_TEXT" ? "Full text analysis" : "Abstract-only analysis"}
            </span>
          ) : null}
        </div>
        <RunStatusBadge status={run.status} />
      </div>
      <dl className="run-metrics">
        <div>
          <dt>Discovered</dt>
          <dd>{run.discovered_count}</dd>
        </div>
        <div>
          <dt>Selected</dt>
          <dd>{run.selected_count}</dd>
        </div>
        <div>
          <dt>Completed</dt>
          <dd>{run.completed_count}</dd>
        </div>
        <div>
          <dt>Failed</dt>
          <dd>{run.failed_count}</dd>
        </div>
      </dl>
      <div className="run-timeline">
        <div>
          <span>Started</span>
          <strong>{formatDateTime(run.started_at)}</strong>
        </div>
        <div>
          <span>Completed</span>
          <strong>{formatDateTime(run.completed_at)}</strong>
        </div>
      </div>
      {run.status === "PARTIAL" ? (
        <div className="partial-run-banner" role="alert">
          <strong>Partial daily run</strong>
          <span>
            {failedItems.length > 0
              ? `${failedItems.length} selected paper${failedItems.length === 1 ? "" : "s"} did not complete every required stage.`
              : "One or more selected papers did not complete every required stage."}
          </span>
        </div>
      ) : null}
      {failedItems.length > 0 ? (
        <section className="run-item-failures" aria-labelledby={`run-failures-${run.id}`}>
          <h3 id={`run-failures-${run.id}`}>Item failures</h3>
          <ul>
            {failedItems.map((item) => {
              const identifier = `arXiv:${item.canonical_arxiv_id}`;
              return (
                <li key={item.id}>
                  <Link to={`/papers/${item.paper_id}`}>
                    <strong>{item.paper_title}</strong>
                    <span>{identifier}</span>
                  </Link>
                  <dl>
                    <div>
                      <dt>Failed stage</dt>
                      <dd>{item.failed_stage?.replaceAll("_", " ") ?? "Not recorded"}</dd>
                    </div>
                    <div>
                      <dt>Error code</dt>
                      <dd>{item.error_code ?? "Not recorded"}</dd>
                    </div>
                    <div>
                      <dt>Retryable</dt>
                      <dd>
                        {item.retryable === null
                          ? "Not recorded"
                          : item.retryable
                            ? "Yes"
                            : "No"}
                      </dd>
                    </div>
                  </dl>
                  {item.error_detail ? <p>{item.error_detail}</p> : null}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
      {run.error_code ? (
        <div className="run-warning" role="alert">
          <strong>{run.error_code}</strong>
          <span>{run.error_detail ?? "The run recorded an item-level failure."}</span>
        </div>
      ) : null}
    </article>
  );
}
