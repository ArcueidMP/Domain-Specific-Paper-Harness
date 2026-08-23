import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { TrendSnapshot, TrendWindow } from "../api/client";
import { trendsQuery } from "../api/queries";
import { RunStatusBadge } from "../components/RunStatusBadge";
import { StateNotice } from "../components/StateNotice";
import { TopicLink } from "../components/TopicLink";
import { useTopicSlug } from "../lib/topic";

const windows = ["7D", "30D", "90D"] as const satisfies readonly TrendWindow[];

function isWindow(value: string | null): value is TrendWindow {
  return windows.some((window) => window === value);
}

function readable(value: string): string {
  return value.replaceAll("_", " ").toLocaleLowerCase();
}

function relativeChange(snapshot: TrendSnapshot): string {
  const change = snapshot.paper_count_change;
  if (change.relative_change === null) {
    return change.growth_status === "ZERO_DENOMINATOR"
      ? "Not calculated: the preceding window has zero papers."
      : "Not calculated for this sample size.";
  }
  const percentage = Number(change.relative_change) * 100;
  return `${percentage > 0 ? "+" : ""}${percentage.toFixed(1)}% versus the preceding window`;
}

export function TrendsPage() {
  const topicSlug = useTopicSlug();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialWindow = searchParams.get("window");
  const [window, setWindow] = useState<TrendWindow>(isWindow(initialWindow) ? initialWindow : "7D");
  const trends = useQuery(trendsQuery(topicSlug, [window]));
  const snapshot = trends.data?.items[0];

  function chooseWindow(value: TrendWindow) {
    setWindow(value);
    const next = new URLSearchParams(searchParams);
    next.set("window", value);
    setSearchParams(next);
  }

  const paperCounts = snapshot
    ? [
        { period: "Preceding", papers: snapshot.preceding_paper_count },
        { period: "Current", papers: snapshot.included_paper_count },
      ]
    : [];
  const entityCounts = snapshot
    ? [...snapshot.entity_counts]
        .sort((left, right) => right.change.current_count - left.change.current_count)
        .slice(0, 10)
        .map((entity) => ({
          name: entity.label,
          current: entity.change.current_count,
          preceding: entity.change.preceding_count,
        }))
    : [];

  return (
    <section className="page-section">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Deterministic 7 / 30 / 90-day trends</p>
          <h1>Corpus trends</h1>
          <p className="lede">
            Counts are computed from persisted structured records and equal preceding windows.
            Small samples and zero denominators remain explicit instead of becoming trend claims.
            A product activity date reflects inputs frozen when publication first started, not a
            reconstructed historical end-of-day corpus.
          </p>
        </div>
      </header>

      <div className="window-selector" role="group" aria-label="Trend window">
        {windows.map((value) => (
          <button
            aria-pressed={window === value}
            className={window === value ? "active" : ""}
            key={value}
            type="button"
            onClick={() => chooseWindow(value)}
          >
            {value === "7D" ? "7 days" : value === "30D" ? "30 days" : "90 days"}
          </button>
        ))}
      </div>

      {trends.isPending ? <StateNotice kind="loading" title={`Loading the ${window} trend`} /> : null}
      {trends.isError ? (
        <StateNotice
          kind="error"
          detail={`${trends.error.message} Trend counts are not estimated when storage is unavailable.`}
          onRetry={() => void trends.refetch()}
        />
      ) : null}
      {trends.isSuccess && !snapshot ? (
        <StateNotice
          kind="empty"
          title={`No ${window} trend snapshot`}
          detail="No deterministic snapshot is persisted for this topic and window."
        />
      ) : null}

      {snapshot ? (
        <>
          <section className="trend-overview card" aria-labelledby="trend-overview-title">
            <div>
              <p className="eyebrow">{snapshot.window} snapshot</p>
              <h2 id="trend-overview-title">
                {snapshot.window_start} to {snapshot.window_end}
              </h2>
              <p>{relativeChange(snapshot)}</p>
              <span className={`sufficiency ${snapshot.data_sufficiency.toLocaleLowerCase()}`}>
                {readable(snapshot.data_sufficiency)} data
              </span>
            </div>
            <dl>
              <div>
                <dt>Current papers</dt>
                <dd>{snapshot.included_paper_count}</dd>
              </div>
              <div>
                <dt>Preceding papers</dt>
                <dd>{snapshot.preceding_paper_count}</dd>
              </div>
              <div>
                <dt>Absolute change</dt>
                <dd>{snapshot.paper_count_change.absolute_change}</dd>
              </div>
              <div>
                <dt>Growth status</dt>
                <dd>{readable(snapshot.paper_count_change.growth_status)}</dd>
              </div>
            </dl>
          </section>

          {snapshot.data_sufficiency !== "SUFFICIENT" ? (
            <div className="insufficient-data-banner" role="status">
              <RunStatusBadge
                status={
                  snapshot.data_sufficiency === "INSUFFICIENT"
                    ? "INSUFFICIENT_DATA"
                    : "LIMITED_DATA"
                }
              />
              <span>
                This window contains {snapshot.included_paper_count} papers. The configured
                thresholds are {snapshot.thresholds.limited_paper_count} for limited and{" "}
                {snapshot.thresholds.sufficient_paper_count} for sufficient data; interpret counts
                as corpus observations, not field-wide movement.
              </span>
            </div>
          ) : null}

          <div className="trend-chart-grid">
            <section className="trend-chart card" aria-labelledby="paper-count-chart">
              <div className="section-title-row">
                <h2 id="paper-count-chart">Paper activity</h2>
                <span>Equal {snapshot.window} windows</span>
              </div>
              <div className="chart-frame" aria-label="Current and preceding paper count chart">
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={paperCounts} margin={{ top: 10, right: 10, bottom: 10, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="period" />
                    <YAxis allowDecimals={false} />
                    <Tooltip />
                    <Bar dataKey="papers" fill="#25533e" radius={[5, 5, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>

            <section className="trend-chart card" aria-labelledby="entity-count-chart">
              <div className="section-title-row">
                <h2 id="entity-count-chart">Top entity activity</h2>
                <span>{entityCounts.length} visible entities</span>
              </div>
              {entityCounts.length === 0 ? (
                <StateNotice
                  kind="empty"
                  title="No entity counts"
                  detail="No graph entity mentions fall inside this persisted window."
                />
              ) : (
                <div className="chart-frame" aria-label="Entity counts by trend window">
                  <ResponsiveContainer width="100%" height={320}>
                    <BarChart
                      data={entityCounts}
                      layout="vertical"
                      margin={{ top: 10, right: 10, bottom: 10, left: 24 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" allowDecimals={false} />
                      <YAxis type="category" dataKey="name" width={112} tick={{ fontSize: 10 }} />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="preceding" fill="#aeb5ad" />
                      <Bar dataKey="current" fill="#e76d3e" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </section>
          </div>

          <section className="trend-entities" aria-labelledby="trend-entities-title">
            <div className="section-title-row">
              <h2 id="trend-entities-title">Entity changes</h2>
              <span>
                {snapshot.entity_counts.length} of {snapshot.total_entities} persisted entities
              </span>
            </div>
            {snapshot.truncated ? (
              <p className="data-note">
                Showing the highest-activity entities within the bounded API response.
              </p>
            ) : null}
            {snapshot.entity_counts.length === 0 ? (
              <StateNotice
                kind="empty"
                title="No entity activity"
                detail="The selected window contains no entity mentions."
              />
            ) : (
              <ul>
                {snapshot.entity_counts.map((entity) => (
                  <li className="card" key={entity.entity_id}>
                    <div>
                      <span>{readable(entity.entity_type)}</span>
                      <TopicLink to={`/graph?entity_id=${entity.entity_id}`}>
                        {entity.label}
                      </TopicLink>
                    </div>
                    <dl>
                      <div>
                        <dt>Current</dt>
                        <dd>{entity.change.current_count}</dd>
                      </div>
                      <div>
                        <dt>Preceding</dt>
                        <dd>{entity.change.preceding_count}</dd>
                      </div>
                      <div>
                        <dt>Status</dt>
                        <dd>{readable(entity.change.growth_status)}</dd>
                      </div>
                    </dl>
                    <small>
                      {entity.newly_appearing
                        ? "Newly appearing in this bounded corpus"
                        : entity.recurring
                          ? "Recurring in both windows"
                          : "Observed in this snapshot"}
                    </small>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="representative-papers" aria-labelledby="representative-title">
            <div className="section-title-row">
              <h2 id="representative-title">Representative papers</h2>
              <span>{snapshot.representative_papers.length} deterministic representatives</span>
            </div>
            {snapshot.representative_papers.length === 0 ? (
              <StateNotice
                kind="empty"
                title="No representative papers"
                detail="The selected window did not produce a representative persisted paper set."
              />
            ) : (
              <ol>
                {snapshot.representative_papers.map((paper) => (
                  <li key={paper.paper_version_id}>
                    <TopicLink to={`/papers/${paper.paper_id}`}>{paper.title}</TopicLink>
                    <span>{paper.activity_date}</span>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section className="trend-relations" aria-labelledby="trend-relations-title">
            <div className="section-title-row">
              <h2 id="trend-relations-title">Relation counts</h2>
              <span>{snapshot.relation_counts.length} relation types</span>
            </div>
            {snapshot.relation_counts.length === 0 ? (
              <StateNotice
                kind="empty"
                title="No relation activity"
                detail="No supported graph relations fall inside the selected window."
              />
            ) : (
              <div className="trend-relation-table-wrap card">
                <table className="trend-relation-table">
                  <thead>
                    <tr>
                      <th>Relation</th>
                      <th>Current</th>
                      <th>Preceding</th>
                      <th>Absolute change</th>
                      <th>Growth status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snapshot.relation_counts.map((relation) => (
                      <tr key={relation.relation_type}>
                        <th>{readable(relation.relation_type)}</th>
                        <td>{relation.change.current_count}</td>
                        <td>{relation.change.preceding_count}</td>
                        <td>{relation.change.absolute_change}</td>
                        <td>{readable(relation.change.growth_status)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <footer className="trend-provenance">
            <span>Aggregation: {snapshot.aggregation_version}</span>
            <span>Product activity through {snapshot.as_of_date}</span>
            <span>Preceding window: {snapshot.preceding_window_start} to {snapshot.preceding_window_end}</span>
          </footer>
        </>
      ) : null}
    </section>
  );
}
