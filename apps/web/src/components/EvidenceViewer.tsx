import type { AnalysisClaim, EvidenceItem } from "../api/client";
import { formatDateTime } from "../lib/format";

type EvidenceViewerProps = {
  evidence: EvidenceItem[];
  claims: AnalysisClaim[];
};

function label(value: string): string {
  return value.toLocaleLowerCase().replaceAll("_", " ");
}

function coordinateLabel(item: EvidenceItem["coordinates"][number]): string {
  return `Page ${item.page}, x ${item.x}, y ${item.y}, width ${item.width}, height ${item.height}`;
}

export function EvidenceViewer({ evidence, claims }: EvidenceViewerProps) {
  const claimsById = new Map(claims.map((claim) => [claim.id, claim]));

  return (
    <section className="evidence-viewer" aria-labelledby="evidence-title">
      <div className="section-title-row evidence-title-row">
        <div>
          <p className="eyebrow">Grounded excerpts</p>
          <h2 id="evidence-title">Evidence viewer</h2>
        </div>
        <span>{evidence.length} evidence records</span>
      </div>

      <div className="evidence-list">
        {evidence.map((item) => {
          const pages = [...new Set(item.coordinates.map((coordinate) => coordinate.page))];
          return (
            <article className="evidence-card card" id={`evidence-${item.id}`} key={item.id}>
              <div className="evidence-topline">
                <span>{item.section}</span>
                <span className={`evidence-type ${item.evidence_type.toLocaleLowerCase()}`}>
                  {label(item.evidence_type)}
                </span>
              </div>

              <blockquote>{item.excerpt}</blockquote>

              <dl className="evidence-location">
                <div>
                  <dt>Passage</dt>
                  <dd>{item.passage_id}</dd>
                </div>
                <div>
                  <dt>Page position</dt>
                  <dd>
                    {pages.length > 0 ? pages.map((page) => `p. ${page}`).join(", ") : "Unavailable"}
                  </dd>
                </div>
                <div>
                  <dt>Extraction source</dt>
                  <dd>{item.extraction_source}</dd>
                </div>
              </dl>

              {item.coordinates.length > 0 ? (
                <details className="coordinate-details">
                  <summary>Recorded source coordinates</summary>
                  <ul>
                    {item.coordinates.map((coordinate, index) => (
                      <li key={`${item.id}-${coordinate.page}-${index}`}>
                        {coordinateLabel(coordinate)}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}

              <div className="supported-claims">
                <strong>Supports claims</strong>
                <div>
                  {item.supported_claim_ids.map((claimId) => {
                    const claim = claimsById.get(claimId);
                    return claim ? (
                      <a href={`#claim-${claim.id}`} key={claimId}>
                        {claim.key}: {label(claim.claim_type)}
                      </a>
                    ) : (
                      <span key={claimId}>{claimId}</span>
                    );
                  })}
                </div>
              </div>

              <div className="evidence-provenance">
                <span>
                  {item.provider} / {item.model_version}
                </span>
                <span>Prompt {item.prompt_version}</span>
                <span>{label(item.verification_status)}</span>
                <span>{formatDateTime(item.generated_at)}</span>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
