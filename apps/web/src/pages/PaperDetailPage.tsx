import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { ApiRequestError } from "../api/client";
import { paperAnalysisQuery, paperEvidenceQuery, paperQuery } from "../api/queries";
import { AnalysisDetail } from "../components/AnalysisDetail";
import { EvidenceViewer } from "../components/EvidenceViewer";
import { RelatedWorkPanel } from "../components/RelatedWorkPanel";
import { StateNotice } from "../components/StateNotice";
import { TopicLink } from "../components/TopicLink";
import { formatDate, formatDateTime } from "../lib/format";

function requestMessage(error: Error): string {
  return `${error.message} Verify that the API is available, then try again.`;
}

export function PaperDetailPage() {
  const { paperId = "" } = useParams();
  const paper = useQuery({
    ...paperQuery(paperId),
    enabled: paperId.length > 0,
  });
  const currentVersionNumber = paper.data?.current_version;
  const currentPaperVersionId = paper.data?.versions.find(
    (version) => version.version === currentVersionNumber,
  )?.id;
  const analysis = useQuery({
    ...paperAnalysisQuery(paperId, currentPaperVersionId ?? ""),
    enabled: paper.isSuccess && currentPaperVersionId !== undefined,
  });
  const evidence = useQuery({
    ...paperEvidenceQuery(
      paperId,
      analysis.data?.id ?? "",
      analysis.data?.paper_version_id ?? "",
      analysis.data?.analysis_scope ?? "ABSTRACT_ONLY",
    ),
    enabled: analysis.isSuccess && analysis.data !== null,
  });

  if (paper.isPending) {
    return (
      <section className="page-section">
        <StateNotice kind="loading" title="Loading paper details" />
      </section>
    );
  }

  if (paper.isError) {
    const missing = paper.error instanceof ApiRequestError && paper.error.status === 404;
    return (
      <section className="page-section">
        <TopicLink className="back-link" to="/papers">
          Back to papers
        </TopicLink>
        {missing ? (
          <StateNotice
            kind="empty"
            title="Paper not found"
            detail="The requested paper is not present in the persisted corpus."
          />
        ) : (
          <StateNotice
            kind="error"
            title="Unable to load this paper"
            detail={requestMessage(paper.error)}
            onRetry={() => void paper.refetch()}
          />
        )}
      </section>
    );
  }

  const detail = paper.data;

  return (
    <section className="page-section paper-detail-page">
      <TopicLink className="back-link" to="/papers">
        <span aria-hidden="true">←</span> Back to papers
      </TopicLink>

      <header className="paper-detail-header">
        <div>
          <p className="eyebrow">
            arXiv:{detail.canonical_arxiv_id}v{detail.current_version}
          </p>
          <h1>{detail.title}</h1>
          <p className="paper-detail-authors">{detail.authors.join(", ")}</p>
        </div>
        <div className="paper-detail-actions">
          <a className="primary-button" href={detail.pdf_url} target="_blank" rel="noreferrer">
            Open source PDF <span aria-hidden="true">↗</span>
          </a>
          <TopicLink className="section-link" to={`/graph?paper_id=${detail.id}`}>
            View paper graph
          </TopicLink>
          <TopicLink className="section-link" to={`/lineages/${detail.id}`}>
            View research lineage
          </TopicLink>
        </div>
      </header>

      <div className="paper-facts card" aria-label="Paper metadata">
        <dl>
          <div>
            <dt>Current version</dt>
            <dd>v{detail.current_version}</dd>
          </div>
          <div>
            <dt>First submitted</dt>
            <dd>{formatDate(detail.first_submitted_at)}</dd>
          </div>
          <div>
            <dt>Latest update</dt>
            <dd>{formatDateTime(detail.latest_updated_at)}</dd>
          </div>
          <div>
            <dt>Primary category</dt>
            <dd>{detail.primary_category}</dd>
          </div>
        </dl>
        <div className="paper-fact-detail">
          <strong>Tracked topics</strong>
          <span>{detail.topic_slugs.join(", ") || "None recorded"}</span>
        </div>
        <div className="paper-fact-detail">
          <strong>Categories</strong>
          <span>{detail.categories.join(", ")}</span>
        </div>
      </div>

      <section className="paper-abstract-panel" aria-labelledby="paper-abstract-title">
        <p className="eyebrow">Author abstract</p>
        <h2 id="paper-abstract-title">Abstract</h2>
        <p>{detail.abstract}</p>
      </section>

      <section className="analysis-panel" aria-label="Paper analysis">
        {analysis.isPending ? <StateNotice kind="loading" title="Loading structured analysis" /> : null}
        {analysis.isError ? (
          <StateNotice
            kind="error"
            title="Unable to load the analysis"
            detail={requestMessage(analysis.error)}
            onRetry={() => void analysis.refetch()}
          />
        ) : null}
        {analysis.isSuccess && analysis.data === null ? (
          <StateNotice
            kind="empty"
            title="Analysis not available"
            detail={`No structured analysis has been persisted for arXiv v${detail.current_version}. No alternate scope or model output has been substituted.`}
          />
        ) : null}
        {analysis.data ? <AnalysisDetail analysis={analysis.data} /> : null}
      </section>

      {analysis.data ? (
        <section className="evidence-panel" aria-label="Analysis evidence">
          {evidence.isPending ? <StateNotice kind="loading" title="Loading grounded evidence" /> : null}
          {evidence.isError ? (
            <StateNotice
              kind="error"
              title="Unable to load the evidence"
              detail={requestMessage(evidence.error)}
              onRetry={() => void evidence.refetch()}
            />
          ) : null}
          {evidence.data?.items.length === 0 ? (
            <StateNotice
              kind="empty"
              title="No evidence records available"
              detail="The analysis is persisted, but no evidence excerpts are available. Claims are not presented as grounded without supporting records."
            />
          ) : null}
          {evidence.data && evidence.data.items.length > 0 ? (
            <EvidenceViewer evidence={evidence.data.items} claims={analysis.data.claims} />
          ) : null}
        </section>
      ) : null}

      <section className="related-panel" aria-label="Historical related work">
        {currentPaperVersionId ? (
          <RelatedWorkPanel paperId={detail.id} paperVersionId={currentPaperVersionId} />
        ) : (
          <StateNotice
            kind="error"
            title="Unable to identify the current paper version"
            detail="Related work was not requested because the paper response did not contain its declared current version."
            onRetry={() => void paper.refetch()}
          />
        )}
      </section>
    </section>
  );
}
