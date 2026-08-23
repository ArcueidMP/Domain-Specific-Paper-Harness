import type { Report, RunItem } from "../api/client";
import { formatDateTime } from "../lib/format";
import { RunStatusBadge } from "./RunStatusBadge";
import { TopicLink } from "./TopicLink";

type ReportDetailProps = {
  report: Report;
  items?: RunItem[];
  compact?: boolean;
};

export function ReportDetail({ report, items = [], compact = false }: ReportDetailProps) {
  const noUpdate = report.publication_outcome === "NO_UPDATE";
  const itemsByVersion = new Map(items.map((item) => [item.paper_version_id, item]));

  return (
    <article className={`report-detail${compact ? " compact" : ""}`}>
      <header className="report-header card">
        <div>
          <p className="eyebrow">{report.report_type.toLocaleLowerCase()} report</p>
          <h2>{report.title}</h2>
          <p>{report.summary}</p>
        </div>
        <div className="report-header-status">
          <RunStatusBadge status={report.status} />
          {noUpdate ? <RunStatusBadge status="NO_UPDATE" /> : null}
          <span>{report.period_start === report.period_end ? report.period_start : `${report.period_start} to ${report.period_end}`}</span>
        </div>
      </header>

      {report.status === "PARTIAL" ? (
        <div className="partial-report-banner" role="alert">
          <strong>Partial report</strong>
          <span>
            {report.failures.length} selected paper{report.failures.length === 1 ? "" : "s"} did
            not complete core metadata or analysis processing. Their source metadata remains
            published, and unavailable work is not treated as evidence.
          </span>
        </div>
      ) : null}

      {noUpdate ? (
        <div className="no-update-banner" role="status">
          <strong>No research updates today</strong>
          <span>No relevant papers were found today.</span>
        </div>
      ) : null}

      <dl className="report-counts" aria-label="Report counts">
        <div>
          <dt>Retrieved</dt>
          <dd>{report.counts.retrieved}</dd>
        </div>
        <div>
          <dt>Selected</dt>
          <dd>{report.counts.selected}</dd>
        </div>
        <div>
          <dt>Processed</dt>
          <dd>{report.counts.processed}</dd>
        </div>
        <div>
          <dt>Completed</dt>
          <dd>{report.counts.completed}</dd>
        </div>
        <div>
          <dt>Failed</dt>
          <dd>{report.counts.failed}</dd>
        </div>
      </dl>

      {report.sections.length > 0 ? (
        <section className="report-sections" aria-label="Report narrative">
          {report.sections.map((section) => (
            <article className="report-section card" key={section.id}>
              <div className="report-section-heading">
                <h3>{section.kind.replaceAll("_", " ")}</h3>
                <span>{section.evidence_ids.length} evidence references</span>
              </div>
              <p>{section.narrative}</p>
              {section.evidence_ids.length > 0 ? (
                <div className="report-section-evidence" aria-label={`${section.kind} evidence`}>
                  {section.evidence_ids.map((evidenceId, index) => {
                    const evidence = report.evidence.find((item) => item.id === evidenceId);
                    return evidence ? (
                      <TopicLink
                        key={evidenceId}
                        to={`/papers/${evidence.paper_id}#evidence-${evidenceId}`}
                      >
                        Evidence {index + 1}
                      </TopicLink>
                    ) : (
                      <span key={evidenceId}>Evidence {index + 1} unavailable</span>
                    );
                  })}
                </div>
              ) : null}
            </article>
          ))}
        </section>
      ) : null}

      {report.highlighted_papers.length > 0 ? (
        <section className="report-block" aria-labelledby={`papers-${report.id}`}>
          <div className="section-title-row">
            <h2 id={`papers-${report.id}`}>Daily papers</h2>
            <span>{report.highlighted_papers.length} persisted selections</span>
          </div>
          <div className="report-highlight-grid">
            {report.highlighted_papers.map((paper) => {
              const item = itemsByVersion.get(paper.paper_version_id);
              const statuses = Array.from(
                new Set(
                  [
                    item?.analysis_status,
                    item?.related_work_status,
                    item?.comparison_status,
                    item?.trend_status,
                  ].flatMap((status) => (status ? [status] : [])),
                ),
              );
              return (
                <article className="report-highlight card" key={paper.paper_version_id}>
                  <h3>
                    <TopicLink to={`/papers/${paper.paper_id}`}>{paper.title}</TopicLink>
                  </h3>
                  {statuses.length > 0 ? (
                    <div className="publication-availability" aria-label="Paper availability">
                      {statuses.map((status) => (
                        <RunStatusBadge key={status} status={status} />
                      ))}
                    </div>
                  ) : null}
                  <p>{paper.reason}</p>
                  {item?.paper_abstract && item.paper_abstract !== paper.reason ? (
                    <p className="publication-abstract">{item.paper_abstract}</p>
                  ) : null}
                  <div className="publication-card-meta">
                    <small>{paper.evidence_ids.length} evidence references</small>
                    {item?.source_url ? (
                      <a href={item.source_url} rel="noreferrer" target="_blank">
                        Open arXiv source
                      </a>
                    ) : null}
                  </div>
                  {item?.comparison_reason ? <small>{item.comparison_reason}</small> : null}
                </article>
              );
            })}
          </div>
        </section>
      ) : null}

      {!compact && report.major_entities.length > 0 ? (
        <section className="report-block" aria-labelledby={`entities-${report.id}`}>
          <div className="section-title-row">
            <h2 id={`entities-${report.id}`}>Major entities</h2>
            <TopicLink className="section-link" to="/graph">
              Explore the graph
            </TopicLink>
          </div>
          <ul className="entity-chip-list">
            {report.major_entities.map((entity) => (
              <li key={entity.graph_entity_id}>
                <TopicLink to={`/graph?entity_id=${entity.graph_entity_id}`}>
                  <span>{entity.entity_type.replaceAll("_", " ")}</span>
                  <strong>{entity.label}</strong>
                  <small>
                    {entity.distinct_paper_count} distinct papers in the latest 7-day window
                  </small>
                </TopicLink>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {!compact && report.notable_comparisons.length > 0 ? (
        <section className="report-block" aria-labelledby={`comparisons-${report.id}`}>
          <div className="section-title-row">
            <h2 id={`comparisons-${report.id}`}>Notable comparisons</h2>
          </div>
          <ul className="report-comparison-list">
            {report.notable_comparisons.map((comparison) => (
              <li className="card" key={comparison.comparison_id}>
                <TopicLink to={`/comparisons/${comparison.comparison_id}`}>
                  <strong>{comparison.comparability_status.replaceAll("_", " ")}</strong>
                  <span>{comparison.summary}</span>
                  <small>{comparison.evidence_ids.length} evidence references</small>
                </TopicLink>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {!compact && report.evidence.length > 0 ? (
        <section className="report-block" aria-labelledby={`evidence-${report.id}`}>
          <div className="section-title-row">
            <h2 id={`evidence-${report.id}`}>Traceable evidence</h2>
            <span>{report.evidence.length} concise excerpts</span>
          </div>
          <div className="report-evidence-list">
            {report.evidence.map((evidence) => (
              <article
                className="report-evidence card"
                id={`report-evidence-${evidence.id}`}
                key={evidence.id}
              >
                <div>
                  <strong>{evidence.section}</strong>
                  <span>{evidence.verification_status.replaceAll("_", " ")}</span>
                </div>
                <blockquote>{evidence.excerpt}</blockquote>
                <TopicLink to={`/papers/${evidence.paper_id}#evidence-${evidence.id}`}>
                  Open evidence in paper
                </TopicLink>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {!compact && report.lineage_highlights.length > 0 ? (
        <section className="report-block" aria-labelledby={`lineages-${report.id}`}>
          <div className="section-title-row">
            <h2 id={`lineages-${report.id}`}>Research lineages</h2>
          </div>
          <ul className="lineage-highlight-list">
            {report.lineage_highlights.map((lineage) => (
              <li className="card" key={lineage.lineage_snapshot_id}>
                <TopicLink to={`/lineages/${lineage.root_paper_id}`}>
                  {lineage.summary}
                </TopicLink>
                <span>{lineage.uncertain ? "Uncertain lineage" : "Explicit lineage evidence"}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {!compact && report.failures.length > 0 ? (
        <section
          className="report-block report-failures availability"
          aria-labelledby={`failures-${report.id}`}
        >
          <div className="section-title-row">
            <h2 id={`failures-${report.id}`}>Unavailable analysis</h2>
          </div>
          <ul>
            {report.failures.map((failure) => (
              <li className="card" key={failure.id}>
                <TopicLink to={`/papers/${failure.paper_id}`}>
                  Paper {failure.paper_id}
                </TopicLink>
                <dl>
                  <div>
                    <dt>Failed stage</dt>
                    <dd>{failure.failed_stage.replaceAll("_", " ")}</dd>
                  </div>
                  <div>
                    <dt>Error code</dt>
                    <dd>{failure.error_code}</dd>
                  </div>
                  <div>
                    <dt>Retryable</dt>
                    <dd>{failure.retryable ? "Yes" : "No"}</dd>
                  </div>
                </dl>
                <p>{failure.error_detail}</p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {!compact && (report.limitations.length > 0 || report.missing_sections.length > 0) ? (
        <section className="report-limitations card" aria-labelledby={`limitations-${report.id}`}>
          <h2 id={`limitations-${report.id}`}>Scope and limitations</h2>
          <ul>
            {report.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
            {report.missing_sections.map((section) => (
              <li key={section}>{section}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <footer className="report-provenance">
        <span>{report.narrative_mode.replaceAll("_", " ")}</span>
        <span>{report.provider ? `${report.provider} / ${report.model_version}` : "Deterministic narrative"}</span>
        <span>{report.verification_status.replaceAll("_", " ")}</span>
        <span>Generated {formatDateTime(report.generated_at)}</span>
      </footer>
    </article>
  );
}
