import { useQuery } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { useSearchParams } from "react-router-dom";

import type {
  GraphEntityType,
  GraphNode,
  GraphRelationType,
  RelationProvenance,
} from "../api/client";
import { knowledgeGraphQuery } from "../api/queries";
import { KnowledgeGraphCanvas } from "../components/KnowledgeGraphCanvas";
import { StateNotice } from "../components/StateNotice";
import { TopicLink } from "../components/TopicLink";
import { useTopicSlug } from "../lib/topic";

const entityTypes = [
  "PAPER",
  "RESEARCH_PROBLEM",
  "METHOD",
  "TASK",
  "DATASET",
  "BENCHMARK",
] as const satisfies readonly GraphEntityType[];

const relationTypes = [
  "CITES",
  "SIMILAR_TO",
  "EXTENDS",
  "COMPARES_WITH",
  "CONTRADICTS",
  "IMPROVES_ON",
  "ADDRESSES",
  "USES_METHOD",
  "TARGETS_TASK",
  "USES_DATASET",
  "EVALUATES_ON",
] as const satisfies readonly GraphRelationType[];

const provenanceTypes = [
  "METADATA_EXPLICIT",
  "TEXT_EXPLICIT",
  "DETERMINISTICALLY_DERIVED",
  "LLM_INFERRED",
  "HUMAN_VERIFIED",
] as const satisfies readonly RelationProvenance[];

function readable(value: string): string {
  return value.replaceAll("_", " ").toLocaleLowerCase();
}

function evidencePaperId(node: GraphNode | undefined): string | undefined {
  return node?.paper_id ?? node?.mentions[0]?.paper_id;
}

export function KnowledgeGraphPage() {
  const topicSlug = useTopicSlug();
  const [searchParams, setSearchParams] = useSearchParams();
  const scopedPaperId = searchParams.get("paper_id") || undefined;
  const requestedNodeId = searchParams.get("entity_id") || undefined;
  const [entityType, setEntityType] = useState<GraphEntityType | undefined>();
  const [relationType, setRelationType] = useState<GraphRelationType | undefined>();
  const [provenance, setProvenance] = useState<RelationProvenance | undefined>();
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const graph = useQuery(
    knowledgeGraphQuery(topicSlug, {
      entityType,
      relationType,
      provenance,
      paperId: scopedPaperId,
      entityId: requestedNodeId,
    }),
  );
  const selectNode = useCallback(
    (nodeId: string) => {
      setSelectedNodeId(nodeId);
      const next = new URLSearchParams(searchParams);
      next.set("entity_id", nodeId);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams, setSelectedNodeId],
  );
  const graphData = graph.data;

  const selectedNode =
    graphData?.nodes.find((node) => node.id === selectedNodeId) ??
    graphData?.nodes.find((node) => node.id === requestedNodeId) ??
    graphData?.nodes[0];
  const selectedEdges =
    selectedNode && graphData
      ? graphData.edges.filter(
          (edge) =>
            edge.source_entity_id === selectedNode.id || edge.target_entity_id === selectedNode.id,
        )
      : [];
  const lineagePaperId = evidencePaperId(selectedNode);

  return (
    <section className="page-section">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Provenance-aware knowledge graph</p>
          <h1>Research connections</h1>
          <p className="lede">
            Bounded paper, problem, method, task, dataset, and benchmark relationships. Dashed
            orange marks AI-inferred records; it never implies human verification.
          </p>
        </div>
      </header>

      <form className="graph-filters" aria-label="Knowledge graph filters">
        <label>
          Node type
          <select
            value={entityType ?? ""}
            onChange={(event) =>
              setEntityType((event.target.value || undefined) as GraphEntityType | undefined)
            }
          >
            <option value="">All node types</option>
            {entityTypes.map((value) => (
              <option key={value} value={value}>
                {readable(value)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Relation
          <select
            value={relationType ?? ""}
            onChange={(event) =>
              setRelationType((event.target.value || undefined) as GraphRelationType | undefined)
            }
          >
            <option value="">All relations</option>
            {relationTypes.map((value) => (
              <option key={value} value={value}>
                {readable(value)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Provenance
          <select
            value={provenance ?? ""}
            onChange={(event) =>
              setProvenance((event.target.value || undefined) as RelationProvenance | undefined)
            }
          >
            <option value="">All provenance</option>
            {provenanceTypes.map((value) => (
              <option key={value} value={value}>
                {readable(value)}
              </option>
            ))}
          </select>
        </label>
      </form>

      {scopedPaperId ? (
        <div className="bounded-data-banner" role="status">
          <strong>Paper-scoped graph</strong>
          <span>Showing the bounded published neighborhood for paper {scopedPaperId}.</span>
          <TopicLink to="/graph">Clear paper scope</TopicLink>
        </div>
      ) : null}

      {graph.isPending ? <StateNotice kind="loading" title="Loading the knowledge graph" /> : null}
      {graph.isError ? (
        <StateNotice
          kind="error"
          detail={`${graph.error.message} The graph cannot be reconstructed from partial API data.`}
          onRetry={() => void graph.refetch()}
        />
      ) : null}
      {graph.isSuccess && (graphData == null || graphData.nodes.length === 0) ? (
        <StateNotice
          kind="empty"
          title="No matching graph records"
          detail="No persisted nodes match these filters. Clear a filter or publish graph updates from a completed paper."
        />
      ) : null}

      {graphData && graphData.nodes.length > 0 ? (
        <>
          {graphData.truncated ? (
            <div className="bounded-data-banner" role="status">
              <strong>Bounded graph payload</strong>
              <span>
                Showing {graphData.nodes.length} of {graphData.total_nodes} nodes and{" "}
                {graphData.edges.length} of {graphData.total_edges} relations, with{" "}
                {graphData.nodes.reduce((total, node) => total + node.mentions.length, 0)} of{" "}
                {graphData.total_mentions} mentions. Apply filters for a narrower, complete view.
              </span>
            </div>
          ) : null}
          <div className="graph-layout">
            <div className="graph-visual card">
              <div className="graph-legend" aria-label="Graph legend">
                <span><i className="legend-explicit" /> Explicit or derived</span>
                <span><i className="legend-inferred" /> AI-inferred</span>
              </div>
              <KnowledgeGraphCanvas
                graph={graphData}
                selectedNodeId={selectedNode?.id}
                onNodeSelect={selectNode}
              />
            </div>
            {selectedNode ? (
              <aside className="graph-detail card" aria-labelledby="selected-graph-node">
                <p className="eyebrow">Selected node</p>
                <h2 id="selected-graph-node">{selectedNode.display_label}</h2>
                <div className="graph-labels">
                  <span>{readable(selectedNode.entity_type)}</span>
                  <span>{readable(selectedNode.provenance)}</span>
                  {selectedNode.inferred ? <strong>AI-inferred</strong> : <strong>Not inferred</strong>}
                </div>
                <p className="graph-source">Source: {selectedNode.source}</p>
                <dl>
                  <div>
                    <dt>Mentions</dt>
                    <dd>{selectedNode.mention_count}</dd>
                  </div>
                  <div>
                    <dt>Verification</dt>
                    <dd>{readable(selectedNode.mentions[0]?.verification_status ?? "UNVERIFIED")}</dd>
                  </div>
                </dl>
                <div className="graph-detail-actions">
                  {selectedNode.paper_id ? (
                    <TopicLink className="primary-button" to={`/papers/${selectedNode.paper_id}`}>
                      Open paper
                    </TopicLink>
                  ) : null}
                  {lineagePaperId ? (
                    <TopicLink className="section-link" to={`/lineages/${lineagePaperId}`}>
                      View related paper lineage
                    </TopicLink>
                  ) : null}
                </div>

                <section className="graph-relations" aria-labelledby="selected-relations">
                  <h3 id="selected-relations">Visible relations</h3>
                  {selectedEdges.length === 0 ? (
                    <p>No relation in this bounded result references the selected node.</p>
                  ) : (
                    <ul>
                      {selectedEdges.map((edge) => {
                        const peerId =
                          edge.source_entity_id === selectedNode.id
                            ? edge.target_entity_id
                            : edge.source_entity_id;
                        const peer = graphData.nodes.find((node) => node.id === peerId);
                        return (
                          <li key={edge.id}>
                            <div>
                              <strong>{readable(edge.relation_type)}</strong>
                              {edge.inferred ? <span>AI-inferred</span> : null}
                            </div>
                            <p>{peer?.display_label ?? "Node outside display details"}</p>
                            <small>
                              {readable(edge.provenance)} / {readable(edge.verification_status)}
                            </small>
                            <p>{edge.justification}</p>
                            {edge.confidence !== null && edge.confidence !== undefined ? (
                              <p className="confidence-meaning">
                                {edge.confidence.toFixed(2)} model-reported support strength. {" "}
                                {edge.confidence_meaning}
                              </p>
                            ) : null}
                            {edge.evidence.length > 0 ? (
                              <div className="graph-evidence-links">
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
              </aside>
            ) : null}
          </div>

          <section className="graph-node-index" aria-labelledby="graph-node-index">
            <div className="section-title-row">
              <h2 id="graph-node-index">Visible node index</h2>
              <span>{graphData.nodes.length} bounded nodes</span>
            </div>
            <ul>
              {graphData.nodes.map((node) => (
                <li key={node.id}>
                  <button
                    className={node.id === selectedNode?.id ? "active" : ""}
                    type="button"
                    onClick={() => selectNode(node.id)}
                  >
                    <span>{readable(node.entity_type)}</span>
                    <strong>{node.display_label}</strong>
                    {node.inferred ? <small>AI-inferred</small> : null}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </>
      ) : null}
    </section>
  );
}
