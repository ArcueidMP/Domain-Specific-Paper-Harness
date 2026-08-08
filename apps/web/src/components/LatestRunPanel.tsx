import type { LatestRun } from "../api/client";
import { formatDateTime } from "../lib/format";
import { RunStatusBadge } from "./RunStatusBadge";

type LatestRunPanelProps = {
  run: LatestRun;
};

export function LatestRunPanel({ run }: LatestRunPanelProps) {
  return (
    <article className="run-panel card">
      <div className="run-panel-heading">
        <div>
          <p className="eyebrow">Latest daily run</p>
          <h2 className="panel-title">{run.logical_date}</h2>
        </div>
        <RunStatusBadge status={run.status} />
      </div>
      <dl className="run-metrics">
        <div>
          <dt>Discovered</dt>
          <dd>{run.discovered_count}</dd>
        </div>
        <div>
          <dt>Normalized</dt>
          <dd>{run.normalized_count}</dd>
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
      {run.error_code ? (
        <div className="run-warning" role="alert">
          <strong>{run.error_code}</strong>
          <span>{run.error_detail ?? "The run recorded an item-level failure."}</span>
        </div>
      ) : null}
    </article>
  );
}
