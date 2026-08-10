import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { ApiRequestError, type ComparisonEvidence } from "../api/client";
import { comparisonQuery, paperQuery } from "../api/queries";
import { StateNotice } from "../components/StateNotice";
import { formatDateTime } from "../lib/format";

function label(value: string): string {
  return value.toLocaleLowerCase().replaceAll("_", " ");
}

function errorMessage(error: Error): string {
  return `${error.message} Verify that the API is available, then try again.`;
}

function EvidenceLinks({
  evidenceById,
  evidenceIds,
  includeIds = false,
}: {
  evidenceById: ReadonlyMap<string, ComparisonEvidence>;
  evidenceIds: string[];
  includeIds?: boolean;
}) {
  if (evidenceIds.length === 0) {
    return <span className="matrix-no-evidence">No linked evidence</span>;
  }

  return (
    <span className="matrix-evidence-links">
      {evidenceIds.map((evidenceId, index) => (
        evidenceById.has(evidenceId) ? (
          <a key={evidenceId} href={`#comparison-evidence-${evidenceId}`}>
            Evidence {index + 1}
            {includeIds ? `: ${evidenceId}` : ""}
          </a>
        ) : (
          <span className="matrix-missing-evidence" key={evidenceId}>
            Missing evidence {evidenceId}
          </span>
        )
      ))}
    </span>
  );
}

export function ComparisonPage() {
  const { comparisonId = "" } = useParams();
  const comparison = useQuery({
    ...comparisonQuery(comparisonId),
    enabled: comparisonId.length > 0,
  });
  const sourcePaperId = comparison.data?.source_paper_id ?? "";
  const targetPaperId = comparison.data?.target_paper_id ?? "";
  const sourcePaper = useQuery({
    ...paperQuery(sourcePaperId),
    enabled: comparison.isSuccess,
  });
  const targetPaper = useQuery({
    ...paperQuery(targetPaperId),
    enabled: comparison.isSuccess,
  });

  if (comparison.isPending) {
    return (
      <section className="page-section">
        <StateNotice kind="loading" title="Loading structured comparison" />
      </section>
    );
  }

  if (comparison.isError) {
    const missing =
      comparison.error instanceof ApiRequestError && comparison.error.status === 404;
    return (
      <section className="page-section">
        <Link className="back-link" to="/papers">
          Back to papers
        </Link>
        {missing ? (
          <StateNotice
            kind="empty"
            title="Comparison not found"
            detail="The requested structured comparison is not present in the persisted corpus."
          />
        ) : (
          <StateNotice
            kind="error"
            title="Unable to load this comparison"
            detail={errorMessage(comparison.error)}
            onRetry={() => void comparison.refetch()}
          />
        )}
      </section>
    );
  }

  if (sourcePaper.isPending || targetPaper.isPending) {
    return (
      <section className="page-section">
        <StateNotice kind="loading" title="Loading compared papers" />
      </section>
    );
  }

  if (sourcePaper.isError || targetPaper.isError) {
    const paperError =
      sourcePaper.error ?? targetPaper.error ?? new Error("Compared paper metadata is unavailable.");
    return (
      <section className="page-section">
        <Link className="back-link" to="/papers">
          Back to papers
        </Link>
        <StateNotice
          kind="error"
          title="Unable to load comparison papers"
          detail={errorMessage(paperError)}
          onRetry={() => {
            void sourcePaper.refetch();
            void targetPaper.refetch();
          }}
        />
      </section>
    );
  }

  const detail = comparison.data;
  const dimensions = [...detail.dimensions].sort((left, right) => left.position - right.position);
  const evidenceById = new Map(detail.evidence.map((item) => [item.id, item]));
  const sourceTitle =
    sourcePaper.data.versions.find((version) => version.id === detail.source_paper_version_id)
      ?.title ?? sourcePaper.data.title;
  const targetTitle =
    targetPaper.data.versions.find((version) => version.id === detail.target_paper_version_id)
      ?.title ?? targetPaper.data.title;

  return (
    <section className="page-section comparison-page">
      <Link className="back-link" to={`/papers/${detail.source_paper_id}`}>
        Back to source paper
      </Link>

      <header className="comparison-header">
        <div>
          <p className="eyebrow">New versus historical</p>
          <h1>Structured paper comparison</h1>
          <p>{detail.summary}</p>
        </div>
        <div className="comparison-badges">
          <span className={`comparability-badge ${detail.comparability_status.toLocaleLowerCase()}`}>
            {label(detail.comparability_status)}
          </span>
          <span className="ai-label">AI-generated · {label(detail.verification_status)}</span>
        </div>
      </header>

      <section className="comparability-reason card" aria-labelledby="comparability-title">
        <p className="eyebrow">Scope decision</p>
        <h2 id="comparability-title">Why this comparability status applies</h2>
        <p>{detail.comparability_reason}</p>
      </section>

      <section className="comparison-matrix-section" aria-labelledby="comparison-matrix-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">Evidence-linked dimensions</p>
            <h2 id="comparison-matrix-title">Comparison matrix</h2>
          </div>
          <span>{dimensions.length} fixed dimensions</span>
        </div>
        <div className="comparison-table-wrap card">
          <table className="comparison-table">
            <thead>
              <tr>
                <th scope="col">Dimension</th>
                <th scope="col">
                  <Link to={`/papers/${sourcePaper.data.id}`}>{sourceTitle}</Link>
                  <span>New paper</span>
                </th>
                <th scope="col">
                  <Link to={`/papers/${targetPaper.data.id}`}>{targetTitle}</Link>
                  <span>Historical paper</span>
                </th>
                <th scope="col">Assessment</th>
              </tr>
            </thead>
            <tbody>
              {dimensions.map((dimension) => (
                <tr key={dimension.id}>
                  <th scope="row">{label(dimension.name)}</th>
                  <td>
                    <p>{dimension.source_value}</p>
                    <EvidenceLinks
                      evidenceById={evidenceById}
                      evidenceIds={dimension.source_evidence_ids}
                    />
                  </td>
                  <td>
                    <p>{dimension.target_value}</p>
                    <EvidenceLinks
                      evidenceById={evidenceById}
                      evidenceIds={dimension.target_evidence_ids}
                    />
                  </td>
                  <td>{dimension.assessment}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="comparison-evidence" aria-labelledby="comparison-evidence-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">Version-pinned excerpts</p>
            <h2 id="comparison-evidence-title">Comparison evidence</h2>
          </div>
          <span>{detail.evidence.length} evidence records</span>
        </div>
        {detail.evidence.length === 0 ? (
          <StateNotice
            kind="empty"
            title="No comparison evidence projected"
            detail="The comparison response contains no evidence excerpts. Matrix claims are not treated as grounded without them."
          />
        ) : (
          <div className="comparison-evidence-list">
            {detail.evidence.map((item) => {
              const paperTitle =
                item.paper_id === detail.source_paper_id ? sourceTitle : targetTitle;
              return (
                <article
                  className="comparison-evidence-card card"
                  id={`comparison-evidence-${item.id}`}
                  key={item.id}
                >
                  <div className="comparison-evidence-heading">
                    <strong>{label(item.evidence_type)} evidence</strong>
                    <span>{label(item.verification_status)}</span>
                  </div>
                  <blockquote>{item.excerpt}</blockquote>
                  <dl>
                    <div>
                      <dt>Paper</dt>
                      <dd>
                        <Link to={`/papers/${item.paper_id}`}>{paperTitle}</Link>
                      </dd>
                    </div>
                    <div>
                      <dt>Paper version ID</dt>
                      <dd>{item.paper_version_id}</dd>
                    </div>
                    <div>
                      <dt>Analysis ID / scope</dt>
                      <dd>
                        {item.analysis_id} / {label(item.analysis_scope)}
                      </dd>
                    </div>
                    <div>
                      <dt>Section / evidence ID</dt>
                      <dd>
                        {item.section} / {item.id}
                      </dd>
                    </div>
                  </dl>
                </article>
              );
            })}
          </div>
        )}
      </section>

      <section className="comparison-relations" aria-labelledby="comparison-relations-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">Persisted relations</p>
            <h2 id="comparison-relations-title">How the papers relate</h2>
          </div>
          <span>{detail.relations.length} relations</span>
        </div>
        {detail.relations.length === 0 ? (
          <StateNotice
            kind="empty"
            title="No paper relations recorded"
            detail="The comparison does not assert a relation without persisted evidence."
          />
        ) : (
          <div className="comparison-relation-list">
            {detail.relations.map((relation) => (
              <article className="comparison-relation card" key={relation.id}>
                <div>
                  <strong>{label(relation.relation_type)}</strong>
                  <span className={relation.provenance === "LLM_INFERRED" ? "ai-label" : "source-label"}>
                    {relation.provenance === "LLM_INFERRED"
                      ? "AI-inferred"
                      : label(relation.provenance)}
                  </span>
                </div>
                <p>{relation.justification}</p>
                <small>
                  {relation.confidence === null || relation.confidence === undefined
                    ? "No numeric confidence recorded"
                    : `${Math.round(relation.confidence * 100)}% uncalibrated model-assessed evidential confidence (not a probability)`}
                  {` · ${label(relation.verification_status)} · ${relation.evidence_ids.length} evidence links`}
                  {relation.provider
                    ? ` · ${relation.provider} / ${relation.model_version ?? "version not recorded"} · Prompt ${relation.prompt_version ?? "not recorded"}`
                    : ""}
                </small>
                <EvidenceLinks
                  evidenceById={evidenceById}
                  evidenceIds={relation.evidence_ids}
                  includeIds
                />
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="comparison-provenance" aria-labelledby="comparison-provenance-title">
        <div>
          <p className="eyebrow">Generation record</p>
          <h2 id="comparison-provenance-title">Comparison provenance</h2>
          <p>
            This comparison is AI-generated and remains {label(detail.verification_status)}. Author
            claims and reported results are presented only within the persisted evidence scope.
          </p>
        </div>
        <dl>
          <div>
            <dt>Provider / model</dt>
            <dd>
              {detail.provider} / {detail.model_version}
            </dd>
          </div>
          <div>
            <dt>Configured model</dt>
            <dd>{detail.configured_model}</dd>
          </div>
          <div>
            <dt>Prompt / source</dt>
            <dd>
              {detail.prompt_version} / {detail.source}
            </dd>
          </div>
          <div>
            <dt>Generated</dt>
            <dd>{formatDateTime(detail.generated_at)}</dd>
          </div>
          <div>
            <dt>Usage</dt>
            <dd>
              {detail.usage.total_tokens} tokens · {detail.usage.call_count} call ·{" "}
              {detail.usage.duration_ms} ms
            </dd>
          </div>
          <div>
            <dt>Search session</dt>
            <dd>{detail.search_session_id}</dd>
          </div>
          <div>
            <dt>Source analysis</dt>
            <dd>
              {detail.source_analysis_id} / {label(detail.source_analysis_scope)}
            </dd>
          </div>
          <div>
            <dt>Target analysis</dt>
            <dd>
              {detail.target_analysis_id} / {label(detail.target_analysis_scope)}
            </dd>
          </div>
        </dl>
      </section>
    </section>
  );
}
