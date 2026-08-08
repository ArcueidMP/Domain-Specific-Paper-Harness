import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { latestRunQuery, papersQuery, topicsQuery } from "../api/queries";
import { LatestRunPanel } from "../components/LatestRunPanel";
import { PaperCard } from "../components/PaperCard";
import { StateNotice } from "../components/StateNotice";

function errorMessage(error: Error): string {
  return `${error.message} Verify that the API is available, then try again.`;
}

export function DashboardPage() {
  const topics = useQuery(topicsQuery());
  const papers = useQuery(papersQuery(5, 0));
  const latestRun = useQuery(latestRunQuery());

  return (
    <section className="page-section">
      <div className="dashboard-hero">
        <div className="hero-copy">
          <p className="eyebrow">Research signal / Broad LLM agents</p>
          <h1>What changed in agent research?</h1>
          <p className="lede">
            A private, traceable view of newly discovered arXiv work—normalized by version and
            ready for structured analysis.
          </p>
        </div>
        <div className="hero-aside">
          <span className="hero-index">01</span>
          <p>Discovery runs daily at 05:00 Asia/Kuala_Lumpur.</p>
          <div className="hero-rule" />
          <small>Source boundary</small>
          <strong>arXiv discovery only</strong>
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
          <span>Latest normalized</span>
          <strong>{latestRun.data?.normalized_count ?? "—"}</strong>
        </div>
      </div>

      <div className="dashboard-grid">
        <section className="recent-papers" aria-labelledby="recent-papers-title">
          <div className="section-title-row">
            <h2 id="recent-papers-title">Recently updated papers</h2>
            <Link className="section-link" to="/papers">
              Browse all papers
            </Link>
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

        <aside aria-label="Daily ingestion status">
          <div className="section-title-row">
            <h2>Ingestion state</h2>
          </div>
          {latestRun.isPending ? <StateNotice kind="loading" title="Loading the latest run" /> : null}
          {latestRun.isError ? (
            <StateNotice
              kind="error"
              detail={errorMessage(latestRun.error)}
              onRetry={() => void latestRun.refetch()}
            />
          ) : null}
          {latestRun.isSuccess && latestRun.data === null ? (
            <StateNotice
              kind="empty"
              title="No ingestion run recorded"
              detail="The latest run will appear here after the daily job completes for the first time."
            />
          ) : null}
          {latestRun.data ? <LatestRunPanel run={latestRun.data} /> : null}
        </aside>
      </div>
    </section>
  );
}
