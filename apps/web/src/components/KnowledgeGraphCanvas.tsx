import cytoscape from "cytoscape";
import type { EventObjectNode } from "cytoscape";
import { useEffect, useRef } from "react";

import type { KnowledgeGraph } from "../api/client";

type KnowledgeGraphCanvasProps = {
  graph: KnowledgeGraph;
  selectedNodeId: string | undefined;
  onNodeSelect: (nodeId: string) => void;
};

export function KnowledgeGraphCanvas({
  graph,
  selectedNodeId,
  onNodeSelect,
}: KnowledgeGraphCanvasProps) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current) {
      return;
    }

    const instance = cytoscape({
      container: container.current,
      elements: [
        ...graph.nodes.map((node) => ({
          data: {
            id: node.id,
            label: node.display_label,
            entityType: node.entity_type,
            inferred: node.inferred ? "yes" : "no",
          },
          selected: node.id === selectedNodeId,
        })),
        ...graph.edges.map((edge) => ({
          data: {
            id: edge.id,
            source: edge.source_entity_id,
            target: edge.target_entity_id,
            label: edge.relation_type.replaceAll("_", " ").toLocaleLowerCase(),
            inferred: edge.inferred ? "yes" : "no",
          },
        })),
      ],
      layout: {
        name: "circle",
        fit: true,
        padding: 42,
        avoidOverlap: true,
      },
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#25533e",
            color: "#183b2c",
            label: "data(label)",
            "font-family": "system-ui, sans-serif",
            "font-size": 10,
            "font-weight": 650,
            "text-background-color": "#fbfaf5",
            "text-background-opacity": 0.92,
            "text-background-padding": "4px",
            "text-margin-y": 20,
            width: 28,
            height: 28,
          },
        },
        {
          selector: 'node[entityType = "PAPER"]',
          style: {
            shape: "round-rectangle",
            "background-color": "#183b2c",
            width: 42,
            height: 28,
          },
        },
        {
          selector: 'node[inferred = "yes"]',
          style: {
            "border-color": "#e76d3e",
            "border-style": "dashed",
            "border-width": 4,
          },
        },
        {
          selector: "node:selected",
          style: {
            "border-color": "#e76d3e",
            "border-style": "solid",
            "border-width": 5,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": "#aeb5ad",
            "target-arrow-color": "#aeb5ad",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: "data(label)",
            "font-size": 7,
            color: "#667068",
            "text-background-color": "#fbfaf5",
            "text-background-opacity": 0.84,
            "text-background-padding": "2px",
          },
        },
        {
          selector: 'edge[inferred = "yes"]',
          style: {
            "line-color": "#e76d3e",
            "line-style": "dashed",
            "target-arrow-color": "#e76d3e",
          },
        },
      ],
      wheelSensitivity: 0.25,
    });

    instance.on("tap", "node", (event: EventObjectNode) => {
      onNodeSelect(event.target.id());
    });

    return () => {
      instance.destroy();
    };
  }, [graph, onNodeSelect, selectedNodeId]);

  return (
    <div
      ref={container}
      className="knowledge-graph-canvas"
      role="img"
      aria-label={`Interactive knowledge graph with ${graph.nodes.length} visible nodes and ${graph.edges.length} visible relations`}
    />
  );
}
