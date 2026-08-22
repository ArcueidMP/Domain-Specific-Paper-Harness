import { useQuery } from "@tanstack/react-query";
import type { FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { dailyQuery, dailyReportsQuery, latestDailyQuery } from "../api/queries";
import { LatestRunPanel } from "../components/LatestRunPanel";
import { ReportDetail } from "../components/ReportDetail";
import { StateNotice } from "../components/StateNotice";
import { TopicLink } from "../components/TopicLink";
import { useTopicSlug } from "../lib/topic";

export function DailyReportPage() {
  const { logicalDate } = useParams();
  const navigate = useNavigate();
  const topicSlug = useTopicSlug();
  const publication = useQuery(
    logicalDate ? dailyQuery(topicSlug, logicalDate) : latestDailyQuery(topicSlug),
  );
  const history = useQuery(dailyReportsQuery(topicSlug));

  function selectDate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const date = data.get("logical-date");
    if (typeof date === "string" && date) {
      void navigate(`/reports/daily/${date}?topic=${encodeURIComponent(topicSlug)}`);
    }
  }

  return (
    <section className="page-section">
      <header className="page-heading report-page-heading">
        <div>
          <p className="eyebrow">Daily and historical reports</p>
          <h1>Research report</h1>
          <p className="lede">
            Published evidence, comparison, graph, and trend results for one logical day. Failed
            product runs remain visible even when no report was published. Delayed publication
            freezes the inputs available when it first starts; it is not a reconstructed
            historical end-of-day corpus.
          </p>
        </div>
        <form className="date-navigation" onSubmit={selectDate}>
          <label htmlFor="logical-date">Open logical date</label>
          <div>
            <input
              id="logical-date"
              name="logical-date"
              type="date"
              defaultValue={logicalDate ?? publication.data?.run.logical_date ?? ""}
            />
            <button type="submit">Open</button>
          </div>
          {logicalDate ? (
            <TopicLink to="/reports/daily">Return to latest</TopicLink>
          ) : null}
        </form>
      </header>

      {publication.isPending ? (
        <StateNotice kind="loading" title="Loading daily publication" />
      ) : null}
      {publication.isError ? (
        <StateNotice
          kind="error"
          detail={`${publication.error.message} The requested publication was not replaced with an empty report.`}
          onRetry={() => void publication.refetch()}
        />
      ) : null}
      {publication.isSuccess && publication.data === null ? (
        <StateNotice
          kind="empty"
          title="No product run for this date"
          detail="No persisted PRODUCT_PUBLICATION run matches the requested date and topic."
        />
      ) : null}

      {publication.data ? (
        <div className="daily-report-layout">
          <LatestRunPanel
            run={publication.data.run}
            items={publication.data.items}
            heading="Product publication run"
          />
          {publication.data.report ? (
            <ReportDetail report={publication.data.report} />
          ) : (
            <StateNotice
              kind="empty"
              title="No report was published"
              detail="This run is still inspectable, but its state did not permit an atomic report publication. Review the run status and item errors above."
            />
          )}
        </div>
      ) : null}

      <aside className="report-history" aria-labelledby="report-history-title">
        <div className="section-title-row">
          <h2 id="report-history-title">Published history</h2>
          {history.data ? <span>{history.data.total} daily reports</span> : null}
        </div>
        {history.isPending ? <StateNotice kind="loading" title="Loading report history" /> : null}
        {history.isError ? (
          <StateNotice
            kind="error"
            detail={history.error.message}
            onRetry={() => void history.refetch()}
          />
        ) : null}
        {history.data?.items.length === 0 ? (
          <StateNotice
            kind="empty"
            title="No daily reports published"
            detail="Report history includes only atomically published daily reports."
          />
        ) : null}
        {history.data && history.data.items.length > 0 ? (
          <ol className="report-history-list">
            {history.data.items.map((report) => (
              <li key={report.id}>
                <TopicLink
                  className={report.logical_date === publication.data?.run.logical_date ? "active" : ""}
                  to={`/reports/daily/${report.logical_date}`}
                >
                  <span>{report.logical_date}</span>
                  <strong>{report.title}</strong>
                  <small>{report.status}</small>
                </TopicLink>
              </li>
            ))}
          </ol>
        ) : null}
      </aside>
    </section>
  );
}
