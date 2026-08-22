import { useQuery } from "@tanstack/react-query";

import type { RelatedWork, RelatedWorkItem } from "../api/client";
import { paperRelatedWorkQuery } from "../api/queries";
import { formatDateTime } from "../lib/format";
import { StateNotice } from "./StateNotice";
import { TopicLink } from "./TopicLink";

type RelatedWorkPanelProps = {
  paperId: string;
  paperVersionId: string;
};

type SearchAction = RelatedWork["actions"][number];

function label(value: string): string {
  return value.toLocaleLowerCase().replaceAll("_", " ");
}

function scoreLabel(value: string): string {
  return value === "semantic_scholar" ? "Semantic Scholar" : label(value);
}

function actionSubject(action: SearchAction): string {
  return action.query ?? action.target_semantic_scholar_id ?? action.target_arxiv_id ?? "bounded call";
}

function CandidateCard({
  actions,
  item,
}: {
  actions: SearchAction[];
  item: RelatedWorkItem;
}) {
  const selectorProvenance = item.candidate.provider
    ? `${item.candidate.provider} / ${item.candidate.configured_model ?? "model not recorded"} (${item.candidate.model_version ?? "version not recorded"}) · Prompt ${item.candidate.prompt_version ?? "not recorded"} · ${formatDateTime(item.candidate.generated_at)}`
    : "Deterministic pending decision";

  return (
    <article className="related-card card">
      <div className="related-card-heading">
        <div>
          <p className="eyebrow">
            Candidate {item.candidate.rank} · {item.paper.year ?? "Year unavailable"}
          </p>
          <h3>
            {item.candidate.local_paper_id ? (
              <TopicLink to={`/papers/${item.candidate.local_paper_id}`}>
                {item.paper.title}
              </TopicLink>
            ) : (
              item.paper.title
            )}
          </h3>
          <p>{item.paper.authors.join(", ") || "Authors unavailable"}</p>
        </div>
        <span className={`decision-badge ${item.candidate.decision.toLocaleLowerCase()}`}>
          {label(item.candidate.decision)}
        </span>
      </div>

      <p className="related-abstract">
        {item.paper.abstract ?? "Semantic Scholar did not return an abstract for this paper."}
      </p>

      <dl className="candidate-scores" aria-label={`Selection scores for ${item.paper.title}`}>
        {Object.entries(item.candidate.scores).map(([name, score]) => (
          <div key={name}>
            <dt>{scoreLabel(name)}</dt>
            <dd>{Math.round(score * 100)}%</dd>
          </div>
        ))}
      </dl>

      <div className="selection-decision">
        <strong>Selection decision</strong>
        <p>{item.candidate.decision_reason}</p>
        <span>
          {item.candidate.decision === "PENDING" ? "Pending" : "AI-guided selector"} ·{" "}
          {selectorProvenance} · {label(item.candidate.verification_status)}
        </span>
      </div>

      <div className="related-meta">
        <span>Origins: {item.candidate.origins.map(label).join(", ")}</span>
        <span>Depth {item.candidate.relation_depth}</span>
        <span>{item.paper.citation_count} citations</span>
        <span>Semantic Scholar: {item.paper.semantic_scholar_id}</span>
      </div>

      <div className="candidate-discoveries">
        <strong>Discovery provenance</strong>
        {item.discoveries.length === 0 ? (
          <p>No individual discovery record was persisted for this candidate.</p>
        ) : (
          <ul>
            {item.discoveries.map((discovery) => {
              const action = actions.find((candidateAction) => candidateAction.id === discovery.action_id);
              return (
                <li key={discovery.id}>
                  <span>{label(discovery.origin)}</span>
                  {action ? (
                    <a href={`#search-action-${action.id}`}>
                      Step {action.step}: {label(action.tool)} · {actionSubject(action)}
                    </a>
                  ) : (
                    <span>Deterministic local retrieval (no external action)</span>
                  )}
                  <small>
                    Depth {discovery.relation_depth} · discovered {formatDateTime(discovery.discovered_at)}
                    {action ? ` · ${action.duration_ms} ms action` : ""}
                  </small>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {Object.keys(item.paper.external_ids).length > 0 ? (
        <div className="external-identifiers">
          <strong>Source identifiers</strong>
          {Object.entries(item.paper.external_ids).map(([name, value]) => (
            <span key={name}>
              {name}: {value}
            </span>
          ))}
        </div>
      ) : null}

      {item.relations.length > 0 ? (
        <ul className="related-relations" aria-label="Recorded paper relations">
          {item.relations.map((relation) => (
            <li key={relation.id}>
              <span>{label(relation.relation_type)}</span>
              <p>{relation.justification}</p>
              <small>
                {relation.provenance === "LLM_INFERRED" ? "AI-inferred" : label(relation.provenance)}
                {relation.confidence === null || relation.confidence === undefined
                  ? ""
                  : ` · ${Math.round(relation.confidence * 100)}% uncalibrated model-assessed evidential confidence (not a probability)`}
                {` · ${label(relation.verification_status)}`}
              </small>
              {relation.evidence_ids.length > 0 ? (
                <small className="relation-evidence-ids">
                  Evidence IDs: {relation.evidence_ids.join(", ")}
                </small>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {item.comparison_id ? (
        <TopicLink
          className="primary-button comparison-link"
          to={`/comparisons/${item.comparison_id}`}
        >
          Open structured comparison
        </TopicLink>
      ) : (
        <p className="comparison-unavailable">No persisted comparison is available.</p>
      )}
    </article>
  );
}

export function RelatedWorkPanel({ paperId, paperVersionId }: RelatedWorkPanelProps) {
  const related = useQuery(paperRelatedWorkQuery(paperId, paperVersionId));

  if (related.isPending) {
    return <StateNotice kind="loading" title="Loading related work" />;
  }

  if (related.isError) {
    return (
      <StateNotice
        kind="error"
        title="Unable to load related work"
        detail={`${related.error.message} The persisted paper and analysis remain available.`}
        onRetry={() => void related.refetch()}
      />
    );
  }

  if (related.data.session === null) {
    return (
      <StateNotice
        kind="empty"
        title="Related work not available"
        detail="No historical-search session has been persisted for this paper. No alternate provider or synthetic recommendations were substituted."
      />
    );
  }

  const { session } = related.data;

  return (
    <section className="related-work" aria-labelledby="related-work-title">
      <div className="section-title-row related-title-row">
        <div>
          <p className="eyebrow">Historical retrieval</p>
          <h2 id="related-work-title">Related work</h2>
        </div>
        <span className={`status-badge ${session.status.toLocaleLowerCase()}`}>
          {label(session.status)}
        </span>
      </div>

      <div className="search-session-summary card">
        <div>
          <strong>Search objective</strong>
          <p>{session.objective}</p>
        </div>
        {session.crawler_queries ? (
          <div className="crawler-plan">
            <strong>Crawler decision</strong>
            <p>{session.crawler_decision_reason}</p>
            <ol>
              {session.crawler_queries.map((query) => (
                <li key={query}>{query}</li>
              ))}
            </ol>
            <span>
              Recommendations {session.crawler_use_recommendations ? "enabled" : "disabled"};
              references {session.crawler_expand_references ? "enabled" : "disabled"}; citations{" "}
              {session.crawler_expand_citations ? "enabled" : "disabled"}; generated{" "}
              {formatDateTime(session.crawler_generated_at)}
            </span>
          </div>
        ) : (
          <p className="crawler-plan-unavailable">
            No DeepSeek crawler plan was persisted before this session stopped.
          </p>
        )}
        <dl>
          <div>
            <dt>Stop reason</dt>
            <dd>{session.stop_reason ? label(session.stop_reason) : "Session still running"}</dd>
          </div>
          <div>
            <dt>Candidates</dt>
            <dd>{related.data.total}</dd>
          </div>
          <div>
            <dt>Bounded actions</dt>
            <dd>
              {related.data.actions.length} / {session.limits.max_steps}
            </dd>
          </div>
          <div>
            <dt>Completed</dt>
            <dd>{formatDateTime(session.completed_at)}</dd>
          </div>
          <div>
            <dt>Prior-work years</dt>
            <dd>
              {session.requested_year_from}–{session.effective_year_to}
            </dd>
          </div>
          <div>
            <dt>Source analysis</dt>
            <dd>
              {session.source_analysis_id} / {label(session.source_analysis_scope)}
            </dd>
          </div>
        </dl>
        <p className="search-provenance">
          {session.provider ? "AI-guided search" : "Deterministic search"} ·{" "}
          {session.provider ?? "provider not recorded"} /{" "}
          {session.configured_model ?? "model not recorded"} ({session.model_version ?? "version not recorded"})
          {session.prompt_version ? ` · Prompt ${session.prompt_version}` : ""}
        </p>
        {session.error_code ? (
          <div className="search-session-failure" role="status">
            <strong>{session.error_code}</strong>
            <span>{session.error_detail ?? "The search session failed without further detail."}</span>
          </div>
        ) : null}
      </div>

      <details className="search-actions card">
        <summary>Inspect search actions and stop controls ({related.data.actions.length})</summary>
        <p>
          Limits: {session.limits.max_queries} queries, {session.limits.max_candidates} candidates,
          depth {session.limits.max_citation_depth}, {session.limits.overall_timeout_seconds}s overall.
        </p>
        {related.data.actions.length === 0 ? (
          <p>No external search actions were recorded.</p>
        ) : (
          <ol>
            {related.data.actions.map((action) => (
              <li id={`search-action-${action.id}`} key={action.id}>
                <strong>
                  {action.tool} · {label(action.status)}
                </strong>
                <span>{action.query ?? action.target_semantic_scholar_id ?? "Bounded tool call"}</span>
                <small>
                  {action.decision_reason} · {action.result_count} results · {action.duration_ms} ms
                  {action.error_code ? ` · ${action.error_code}` : ""}
                </small>
              </li>
            ))}
          </ol>
        )}
      </details>

      {related.data.items.length === 0 ? (
        <StateNotice
          kind="empty"
          title="No related candidates selected"
          detail={`The persisted session stopped because ${label(session.stop_reason ?? session.status)} without retaining any candidates.`}
        />
      ) : (
        <div className="related-list">
          {related.data.items.map((item) => (
            <CandidateCard actions={related.data.actions} item={item} key={item.candidate.id} />
          ))}
        </div>
      )}
    </section>
  );
}
