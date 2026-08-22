import type { PaperSummary } from "../api/client";
import { formatDate } from "../lib/format";
import { TopicLink } from "./TopicLink";

type PaperCardProps = {
  paper: PaperSummary;
  compact?: boolean;
};

function formatAuthors(authors: string[]): string {
  if (authors.length === 0) {
    return "Authors unavailable";
  }

  if (authors.length <= 3) {
    return authors.join(", ");
  }

  return `${authors.slice(0, 3).join(", ")} +${authors.length - 3} more`;
}

export function PaperCard({ paper, compact = false }: PaperCardProps) {
  return (
    <article className={compact ? "paper-card compact" : "paper-card"}>
      <div className="paper-card-topline">
        <span className="arxiv-id">arXiv:{paper.canonical_arxiv_id}v{paper.current_version}</span>
        <span className="paper-category">{paper.primary_category}</span>
      </div>
      <h3>
        <TopicLink to={`/papers/${paper.id}`}>{paper.title}</TopicLink>
      </h3>
      <p className="paper-authors">{formatAuthors(paper.authors)}</p>
      {!compact ? <p className="paper-abstract">{paper.abstract}</p> : null}
      <div className="paper-card-footer">
        <span>Updated {formatDate(paper.latest_updated_at)}</span>
        <span className="paper-card-actions">
          <TopicLink to={`/papers/${paper.id}`}>View analysis</TopicLink>
          <a href={paper.pdf_url} target="_blank" rel="noreferrer">
            Open PDF <span aria-hidden="true">↗</span>
          </a>
        </span>
      </div>
    </article>
  );
}
