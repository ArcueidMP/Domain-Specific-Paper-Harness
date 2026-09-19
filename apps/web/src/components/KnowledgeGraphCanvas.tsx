import cytoscape from "cytoscape";
import type { Core, EventObjectNode } from "cytoscape";
import { useEffect, useRef } from "react";

import type { KnowledgeGraph } from "../api/client";

type KnowledgeGraphCanvasProps = {
  graph: KnowledgeGraph;
  selectedNodeId: string | undefined;
  onNodeSelect: (nodeId: string) => void;
};

export function KnowledgeGraphCanvas({ graph, selectedNodeId, onNodeSelect }: KnowledgeGraphCanvasProps) {
  const container = useRef<HTMLDivElement>(null);
  const canvas = useRef<Core | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const columns = Math.ceil(Math.sqrt(graph.nodes.length));
    const instance = cytoscape({
      container: container.current,
      elements: [
        ...graph.nodes.map((node, index) => ({
          data: {
            id: node.id, label: node.display_label, entityType: node.entity_type,
            inferred: node.inferred ? "yes" : "no",
          },
          position: { x: (index % columns) * 180, y: Math.floor(index / columns) * 110 },
        })),
        ...graph.edges.map((edge) => ({
          data: {
            id: edge.id, source: edge.source_entity_id, target: edge.target_entity_id,
            label: edge.relation_type.replaceAll("_", " ").toLowerCase(),
            inferred: edge.inferred ? "yes" : "no",
          },
        })),
      ],
      layout: graph.edges.length ? {
        name: "cose", animate: false, randomize: false, fit: true, padding: 45,
        nodeRepulsion: () => 12000, idealEdgeLength: () => 140,
        componentSpacing: 90, nodeDimensionsIncludeLabels: true,
      } : { name: "grid", padding: 45, avoidOverlap: true, nodeDimensionsIncludeLabels: true },
      style: [
        { selector: "node", style: {
          "background-color": "#507965", color: "#183b2c", label: "data(label)",
          "font-family": "system-ui, sans-serif", "font-size": 12,
          "text-wrap": "ellipsis", "text-max-width": "150px",
          "text-valign": "bottom", "text-margin-y": 9, "min-zoomed-font-size": 9,
          "text-background-color": "#fbfaf5", "text-background-opacity": 0.88,
          "text-background-padding": "3px", width: 26, height: 26,
        } },
        { selector: 'node[entityType = "PAPER"]', style: {
          shape: "round-rectangle", "background-color": "#183b2c", width: 42, height: 30,
        } },
        { selector: 'node[entityType = "METHOD"]', style: { "background-color": "#397d85" } },
        { selector: 'node[entityType = "DATASET"]', style: { "background-color": "#5578a0" } },
        { selector: 'node[entityType = "BENCHMARK"]', style: { "background-color": "#b18136" } },
        { selector: 'node[entityType = "TASK"]', style: { "background-color": "#8a7297" } },
        { selector: 'node[inferred = "yes"]', style: {
          "border-color": "#e76d3e", "border-style": "dashed", "border-width": 3,
        } },
        { selector: "node:selected", style: {
          "overlay-color": "#e76d3e", "overlay-opacity": 0.22, "overlay-padding": 7,
          "font-weight": 750,
        } },
        { selector: "edge", style: {
          width: 1.5, "line-color": "#9aa99d", "target-arrow-color": "#9aa99d",
          "target-arrow-shape": "triangle", "curve-style": "bezier",
          "font-size": 10, color: "#526257", "text-background-color": "#fbfaf5",
          "text-background-opacity": 0.95, "text-background-padding": "3px",
        } },
        { selector: "edge.connected", style: { label: "data(label)", width: 2.5 } },
        { selector: 'edge[inferred = "yes"]', style: {
          "line-color": "#e76d3e", "line-style": "dashed", "target-arrow-color": "#e76d3e",
        } },
      ],
      minZoom: 0.08, maxZoom: 2.5, wheelSensitivity: 0.2,
    });
    canvas.current = instance;
    if (instance.zoom() > 1.2) instance.zoom(1.2);
    instance.on("tap", "node", (event: EventObjectNode) => onNodeSelect(event.target.id()));
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      canvas.current = null;
      instance.destroy();
    };
  }, [graph, onNodeSelect]);

  useEffect(() => {
    const instance = canvas.current;
    if (!instance) return;
    instance.nodes().unselect();
    instance.edges().removeClass("connected");
    if (selectedNodeId) {
      const node = instance.getElementById(selectedNodeId);
      node.select();
      node.connectedEdges().addClass("connected");
    }
  }, [graph, selectedNodeId]);

  function focusSelected() {
    const instance = canvas.current;
    if (!instance || !selectedNodeId) return;
    const node = instance.getElementById(selectedNodeId);
    instance.fit(node.closedNeighborhood(), 70);
    if (instance.zoom() > 1.4) instance.zoom(1.4);
    instance.center(node);
  }

  function zoom(factor: number) {
    const instance = canvas.current;
    if (!instance) return;
    instance.zoom({ level: Math.max(instance.minZoom(), Math.min(instance.maxZoom(), instance.zoom() * factor)),
      renderedPosition: { x: instance.width() / 2, y: instance.height() / 2 } });
  }

  return <>
    <div className="graph-toolbar" role="group" aria-label="Graph view controls">
      <button type="button" onClick={() => zoom(1.3)} aria-label="Zoom in">+</button>
      <button type="button" onClick={() => zoom(1 / 1.3)} aria-label="Zoom out">−</button>
      <button type="button" onClick={() => canvas.current?.fit(undefined, 45)}>Fit graph</button>
      <button type="button" disabled={!selectedNodeId} onClick={focusSelected}>Focus selected</button>
    </div>
    <div ref={container} className="knowledge-graph-canvas" role="img"
      aria-label={`Interactive knowledge graph with ${graph.nodes.length} visible nodes and ${graph.edges.length} visible relations`} />
    <p className="graph-canvas-help">Drag to pan. Scroll or use + / − to zoom. Select a node for details; explore connections to open its neighborhood.</p>
  </>;
}
