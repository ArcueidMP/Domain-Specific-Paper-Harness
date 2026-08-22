import { useQuery } from "@tanstack/react-query";

import { latestDailyQuery, papersQuery, topicsQuery, trendsQuery } from "../api/queries";
import { LatestRunPanel } from "../components/LatestRunPanel";
import { PaperCard } from "../components/PaperCard";
import { ReportDetail } from "../components/ReportDetail";
import { StateNotice } from "../components/StateNotice";
import { TopicLink } from "../components/TopicLink";
import { useTopicSlug } from "../lib/topic";

function errorMessage(error: Error): string {
  return `${error.message} Verify that the API is available, then try again.`;
}

export function DashboardPage() {
  const topicSlug = useTopicSlug();
  const topics = useQuery(topicsQuery());
  const papers = useQuery(papersQuery(topicSlug, 5, 0));
  const latestDaily = useQuery(latestDailyQuery(topicSlug));
  const trends = useQuery(trendsQuery(topicSlug));
  const activeTopic = topics.data?.items.find((topic) => topic.slug === topicSlug);

  return (
    <section className="page-section">
      <div className="dashboard-hero">
        <div className="hero-copy">
          <p className="eyebrow">Research signal / {activeTopic?.name ?? "Selected topic"}</p>
          <h1>What changed in this research domain?</h1>
          <p className="lede">
            A private, evidence-linked view of daily papers, historical comparisons, research
            lineages, and deterministic corpus trends.
          </p>
        </div>
        <div className="hero-aside">
          <span className="hero-index">04</span>
          <p>Daily publication connects papers to the research graph.</p>
          <div className="hero-rule" />
          <small>Trust boundary</small>
          <strong>Persisted evidence and explicit uncertainty</strong>
        </div>
      </div>

      <div className="metric-strip" aria-label="Corpus summary">
        <div>
          <span>Tracked papers</span>
          <strong>{papers.data?.total ?? "—"}</strong>
        </div>
        <div>
          <span>Active topics</span>
          <strong>{topics.data?.total ?? "—"}</strong>
        </div>
        <div>
          <span>Latest completed</span>
          <strong>{latestDaily.data?.run.completed_count ?? "—"}</strong>
        </div>
      </div>

      <section className="dashboard-report" aria-labelledby="latest-report-title">
        <div className="section-title-row">
          <h2 id="latest-report-title">Latest daily report</h2>
          <TopicLink className="section-link" to="/reports/daily">
            Report history
          </TopicLink>
        </div>
        {latestDaily.isPending ? (
          <StateNotice kind="loading" title="Loading the latest product publication" />
        ) : null}
        {latestDaily.isError ? (
          <StateNotice
            kind="error"
            detail={errorMessage(latestDaily.error)}
            onRetry={() => void latestDaily.refetch()}
          />
        ) : null}
        {latestDaily.isSuccess && latestDaily.data === null ? (
          <StateNotice
            kind="empty"
            title="No product publication run recorded"
            detail="A daily report will appear only after a product publication run has persisted its graph, trends, and publication state."
          />
        ) : null}
        {latestDaily.data ? (
          <div className="dashboard-publication-grid">
            <LatestRunPanel
              run={latestDaily.data.run}
              items={latestDaily.data.items}
              heading="Latest product publication"
            />
            {latestDaily.data.report ? (
              <ReportDetail report={latestDaily.data.report} compact />
            ) : (
              <StateNotice
                kind="empty"
                title="Run has no published report"
                detail="FAILED runs remain visible, but publication is intentionally absent when no selected paper completes or the publication transaction fails."
              />
            )}
          </div>
        ) : null}
      </section>

      <section className="dashboard-trends" aria-labelledby="trend-cards-title">
        <div className="section-title-row">
          <h2 id="trend-cards-title">Deterministic trend windows</h2>
          <TopicLink className="section-link" to="/trends">
            Explore trends
          </TopicLink>
        </div>
        {trends.isPending ? <StateNotice kind="loading" title="Loading trend snapshots" /> : null}
        {trends.isError ? (
          <StateNotice
            kind="error"
            detail={errorMessage(trends.error)}
            onRetry={() => void trends.refetch()}
          />
        ) : null}
        {trends.data?.items.length === 0 ? (
          <StateNotice
            kind="empty"
            title="No trend snapshots published"
            detail="Trend windows are derived only from persisted structured data; empty windows are not filled with estimates."
          />
        ) : null}
        {trends.data && trends.data.items.length > 0 ? (
          <div className="trend-card-grid">
            {trends.data.items.map((trend) => (
              <TopicLink
                className="trend-card card"
                to={`/trends?window=${trend.window}`}
                key={trend.id}
              >
                <span>{trend.window}</span>
                <strong>{trend.included_paper_count}</strong>
                <small>included papers</small>
                <em className={`sufficiency ${trend.data_sufficiency.toLocaleLowerCase()}`}>
                  {trend.data_sufficiency.toLocaleLowerCase()} data
                </em>
              </TopicLink>
            ))}
          </div>
        ) : null}
      </section>

      <section className="recent-papers dashboard-recent" aria-labelledby="recent-papers-title">
        <div className="section-title-row">
          <h2 id="recent-papers-title">Recently updated papers</h2>
          <TopicLink className="section-link" to="/papers">
            Browse all papers
          </TopicLink>
        </div>
        {papers.isPending ? <StateNotice kind="loading" title="Loading the corpus" /> : null}
        {papers.isError ? (
          <StateNotice
            kind="error"
            detail={errorMessage(papers.error)}
            onRetry={() => void papers.refetch()}
          />
        ) : null}
        {papers.data?.items.length === 0 ? (
          <StateNotice
            kind="empty"
            title="No papers have been ingested"
            detail="Run the daily ingestion command to populate the first versioned arXiv records."
          />
        ) : null}
        {papers.data && papers.data.items.length > 0 ? (
          <div className="paper-list compact-list">
            {papers.data.items.map((paper) => (
              <PaperCard key={paper.id} paper={paper} compact />
            ))}
          </div>
        ) : null}
      </section>
    </section>
  );
}
