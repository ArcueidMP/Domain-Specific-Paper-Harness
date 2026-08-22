import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { lineageQuery } from "../api/queries";
import { StateNotice } from "../components/StateNotice";
import { TopicLink } from "../components/TopicLink";
import { useTopicSlug } from "../lib/topic";

function readable(value: string): string {
  return value.replaceAll("_", " ").toLocaleLowerCase();
}

export function LineagePage() {
  const topicSlug = useTopicSlug();
  const { entityOrPaperId } = useParams();
  const [maxDepth, setMaxDepth] = useState(5);
  const lineage = useQuery({
    ...lineageQuery(topicSlug, entityOrPaperId ?? "", maxDepth),
    enabled: entityOrPaperId !== undefined,
  });
  const lineageData = lineage.data;
  const chronologicalNodes = lineageData
    ? [...lineageData.nodes].sort((left, right) => {
        if (left.publication_date === null) return 1;
        if (right.publication_date === null) return -1;
        return left.publication_date.localeCompare(right.publication_date);
      })
    : [];

  if (!entityOrPaperId) {
    return (
      <StateNotice
        kind="empty"
        title="No lineage root selected"
        detail="Open a lineage from a paper, graph node, trend entity, or report highlight."
      />
    );
  }

  return (
    <section className="page-section">
      <header className="page-heading lineage-heading">
        <div>
          <p className="eyebrow">Bounded research lineage</p>
          <h1>How this work connects</h1>
          <p className="lede">
            Chronological predecessor relationships from the currently retrieved corpus only.
            Missing edges are not evidence that no predecessor exists. The product activity date
            reflects inputs frozen when publication first started, not a reconstructed historical
            end-of-day corpus.
          </p>
        </div>
        <label className="depth-control">
          Maximum depth
          <select value={maxDepth} onChange={(event) => setMaxDepth(Number(event.target.value))}>
            {[1, 2, 3, 4, 5].map((depth) => (
              <option key={depth} value={depth}>
                {depth}
              </option>
            ))}
          </select>
        </label>
      </header>

      {lineage.isPending ? <StateNotice kind="loading" title="Loading research lineage" /> : null}
      {lineage.isError ? (
        <StateNotice
          kind="error"
          detail={`${lineage.error.message} Lineage gaps are not filled with inferred web results.`}
          onRetry={() => void lineage.refetch()}
        />
      ) : null}
      {lineage.isSuccess && lineageData === null ? (
        <StateNotice
          kind="empty"
          title="No persisted lineage"
          detail="No lineage snapshot matches this paper or graph entity in the selected topic."
        />
      ) : null}

      {lineageData ? (
        <>
          {lineageData.truncated || !lineageData.explicit_predecessor_available ? (
            <div className="lineage-uncertainty" role="status">
              <strong>Lineage uncertainty</strong>
              <span>
                {lineageData.truncated
                  ? `This response reached its depth-${lineageData.max_depth}, ${lineageData.max_nodes}-node, or ${lineageData.max_edges}-edge bound. `
                  : ""}
                {lineageData.explicit_predecessor_available
                  ? "Explicit predecessor evidence is present."
                  : "No explicit predecessor is available in the currently retrieved corpus."}
              </span>
            </div>
          ) : null}

          <section className="lineage-summary card" aria-labelledby="lineage-summary-title">
            <div>
              <p className="eyebrow">Product activity through {lineageData.as_of_date}</p>
              <h2 id="lineage-summary-title">{lineageData.nodes.length} papers in view</h2>
              <p>{readable(lineageData.corpus_scope)}</p>
            </div>
            <dl>
              <div>
                <dt>Relations</dt>
                <dd>{lineageData.edges.length}</dd>
              </div>
              <div>
                <dt>Explicit predecessor</dt>
                <dd>{lineageData.explicit_predecessor_available ? "Yes" : "No"}</dd>
              </div>
              <div>
                <dt>Human-verified predecessor</dt>
                <dd>{lineageData.verified_predecessor_available ? "Yes" : "No"}</dd>
              </div>
            </dl>
          </section>

          <div className="lineage-layout">
            <section className="lineage-timeline" aria-labelledby="lineage-timeline-title">
              <div className="section-title-row">
                <h2 id="lineage-timeline-title">Chronological papers</h2>
                <span>Oldest to newest</span>
              </div>
              {chronologicalNodes.length === 0 ? (
                <StateNotice
                  kind="empty"
                  title="No lineage nodes"
                  detail="The persisted snapshot contains no visible paper nodes."
                />
              ) : (
                <ol>
                  {chronologicalNodes.map((node) => (
                    <li key={node.graph_entity_id}>
                      <span className="timeline-date">{node.publication_date ?? "Date unavailable"}</span>
                      <article className="card">
                        <div>
                          <span>Depth {node.depth}</span>
                          {node.paper_id === lineageData.root_paper_id ? <strong>Root paper</strong> : null}
                        </div>
                        <h3>
                          <TopicLink to={`/papers/${node.paper_id}`}>{node.title}</TopicLink>
                        </h3>
                        <TopicLink className="section-link" to={`/lineages/${node.paper_id}`}>
                          Re-root lineage here
                        </TopicLink>
                      </article>
                    </li>
                  ))}
                </ol>
              )}
            </section>

            <section className="lineage-relations" aria-labelledby="lineage-relations-title">
              <div className="section-title-row">
                <h2 id="lineage-relations-title">Supporting relations</h2>
                <span>{lineageData.edges.length} of at most {lineageData.max_edges} relations</span>
              </div>
              {lineageData.edges.length === 0 ? (
                <StateNotice
                  kind="empty"
                  title="No predecessor relation"
                  detail="The snapshot contains a root paper but no supported predecessor edge."
                />
              ) : (
                <ul>
                  {lineageData.edges.map((edge) => {
                    const sourceNode = lineageData.nodes.find(
                      (node) => node.graph_entity_id === edge.source_entity_id,
                    );
                    const targetNode = lineageData.nodes.find(
                      (node) => node.graph_entity_id === edge.target_entity_id,
                    );
                    return (
                      <li className="card" key={edge.id}>
                        <div className="lineage-relation-heading">
                          <strong>{readable(edge.relation_type)}</strong>
                          {edge.inferred ? <span>AI-inferred</span> : <span>Explicit or derived</span>}
                        </div>
                        <p>
                          {sourceNode?.title ?? "Unknown source"} → {targetNode?.title ?? "Unknown target"}
                        </p>
                        <blockquote>{edge.justification}</blockquote>
                        <dl>
                          <div>
                            <dt>Provenance</dt>
                            <dd>{readable(edge.provenance)}</dd>
                          </div>
                          <div>
                            <dt>Verification</dt>
                            <dd>{readable(edge.verification_status)}</dd>
                          </div>
                          <div>
                            <dt>Confidence</dt>
                            <dd>
                              {edge.confidence === null || edge.confidence === undefined
                                ? "Not supplied"
                                : `${edge.confidence.toFixed(2)} model-reported support strength`}
                            </dd>
                          </div>
                        </dl>
                        {edge.confidence_meaning ? (
                          <p className="confidence-meaning">{edge.confidence_meaning}</p>
                        ) : null}
                        {edge.evidence.length > 0 ? (
                          <div className="lineage-evidence-links">
                            {edge.evidence.map((reference, index) => (
                              <TopicLink
                                key={`${reference.evidence_id}:${reference.role}`}
                                to={`/papers/${reference.paper_id}#evidence-${reference.evidence_id}`}
                              >
                                Evidence {index + 1} ({readable(reference.role)})
                              </TopicLink>
                            ))}
                          </div>
                        ) : null}
                        {edge.model_provenance ? (
                          <small>
                            {edge.model_provenance.provider} / {edge.model_provenance.model_version} /{" "}
                            {edge.model_provenance.prompt_version}
                          </small>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          </div>

          <section className="lineage-limitations card" aria-labelledby="lineage-limitations-title">
            <h2 id="lineage-limitations-title">Lineage limits</h2>
            <ul>
              {lineageData.limitations.map((limitation) => (
                <li key={limitation}>{limitation}</li>
              ))}
              <li>Permitted relations: {lineageData.permitted_relation_types.map(readable).join(", ")}.</li>
            </ul>
            <small>Version {lineageData.lineage_version}</small>
          </section>
        </>
      ) : null}
    </section>
  );
}
