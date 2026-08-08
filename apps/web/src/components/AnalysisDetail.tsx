import type { PaperAnalysis } from "../api/client";
import { formatDateTime } from "../lib/format";

type AnalysisDetailProps = {
  analysis: PaperAnalysis;
};

function label(value: string): string {
  return value.toLocaleLowerCase().replaceAll("_", " ");
}

function formatCost(value: unknown): string {
  if (value === null || value === undefined) {
    return "Unavailable";
  }

  const amount = typeof value === "number" ? value : Number(value);
  return Number.isFinite(amount) ? `$${amount.toFixed(6)}` : "Unavailable";
}

export function AnalysisDetail({ analysis }: AnalysisDetailProps) {
  return (
    <article className="analysis-detail card">
      <div className="analysis-heading">
        <div>
          <p className="eyebrow">Structured analysis</p>
          <h2>Analysis of arXiv v{analysis.arxiv_version}</h2>
        </div>
        <span className={`scope-badge ${analysis.analysis_scope.toLocaleLowerCase()}`}>
          {label(analysis.analysis_scope)}
        </span>
      </div>

      <div className="scope-notice">
        <strong>Analysis scope</strong>
        <span>
          {analysis.analysis_scope === "FULL_TEXT"
            ? "Grounded in the selected full text parsed by GROBID."
            : "Grounded only in the paper abstract selected before execution."}
        </span>
      </div>

      <section className="analysis-section" aria-labelledby="analysis-summary-title">
        <h3 id="analysis-summary-title">Summary</h3>
        <p>{analysis.summary}</p>
      </section>

      <div className="analysis-columns">
        <section className="analysis-section" aria-labelledby="research-problem-title">
          <h3 id="research-problem-title">Research problem</h3>
          <p>{analysis.research_problem}</p>
        </section>
        <section className="analysis-section" aria-labelledby="method-summary-title">
          <h3 id="method-summary-title">Method</h3>
          <p>{analysis.method_summary}</p>
        </section>
      </div>

      <div className="analysis-columns">
        <section className="analysis-section" aria-labelledby="contributions-title">
          <h3 id="contributions-title">Key contributions</h3>
          <ul>
            {analysis.key_contributions.map((contribution) => (
              <li key={contribution}>{contribution}</li>
            ))}
          </ul>
        </section>
        <section className="analysis-section" aria-labelledby="limitations-title">
          <h3 id="limitations-title">Reported limitations</h3>
          {analysis.limitations.length > 0 ? (
            <ul>
              {analysis.limitations.map((limitation) => (
                <li key={limitation}>{limitation}</li>
              ))}
            </ul>
          ) : (
            <p>No limitations were extracted.</p>
          )}
        </section>
      </div>

      <section className="analysis-section" aria-labelledby="claims-title">
        <div className="section-title-row compact-title-row">
          <h3 id="claims-title">Traceable claims</h3>
          <span>{analysis.claims.length} extracted</span>
        </div>
        <div className="claim-list">
          {analysis.claims.map((claim) => (
            <article className="claim-card" id={`claim-${claim.id}`} key={claim.id}>
              <div className="claim-topline">
                <span>{label(claim.claim_type)}</span>
                <span>{label(claim.verification_status)}</span>
              </div>
              <p>{claim.text}</p>
              <small>Claim key: {claim.key}</small>
            </article>
          ))}
        </div>
      </section>

      <section className="analysis-provenance" aria-labelledby="analysis-provenance-title">
        <div>
          <p className="eyebrow" id="analysis-provenance-title">
            Model and provenance
          </p>
          <dl>
            <div>
              <dt>Provider / configured model</dt>
              <dd>
                {analysis.provider} / {analysis.configured_model}
              </dd>
            </div>
            <div>
              <dt>Model version</dt>
              <dd>{analysis.model_version}</dd>
            </div>
            <div>
              <dt>Prompt version</dt>
              <dd>{analysis.prompt_version}</dd>
            </div>
            <div>
              <dt>Source</dt>
              <dd>{analysis.source}</dd>
            </div>
            <div>
              <dt>Parsed paper</dt>
              <dd>{analysis.parsed_paper_id ?? "Not applicable"}</dd>
            </div>
            <div>
              <dt>Parser</dt>
              <dd>
                {analysis.parser_name && analysis.parser_version
                  ? `${analysis.parser_name} / ${analysis.parser_version}`
                  : "Not applicable"}
              </dd>
            </div>
            <div>
              <dt>Verification</dt>
              <dd>{label(analysis.verification_status)}</dd>
            </div>
            <div>
              <dt>Generated</dt>
              <dd>{formatDateTime(analysis.generated_at)}</dd>
            </div>
          </dl>
        </div>
        <div>
          <p className="eyebrow">Recorded model usage</p>
          <dl>
            <div>
              <dt>Tokens</dt>
              <dd>{analysis.usage.total_tokens.toLocaleString()}</dd>
            </div>
            <div>
              <dt>Prompt / completion</dt>
              <dd>
                {analysis.usage.prompt_tokens.toLocaleString()} /{" "}
                {analysis.usage.completion_tokens.toLocaleString()}
              </dd>
            </div>
            <div>
              <dt>Calls</dt>
              <dd>{analysis.usage.call_count}</dd>
            </div>
            <div>
              <dt>Duration</dt>
              <dd>{analysis.usage.duration_ms.toLocaleString()} ms</dd>
            </div>
            <div>
              <dt>Estimated cost</dt>
              <dd>{formatCost(analysis.usage.estimated_cost_usd)}</dd>
            </div>
          </dl>
        </div>
      </section>
    </article>
  );
}
