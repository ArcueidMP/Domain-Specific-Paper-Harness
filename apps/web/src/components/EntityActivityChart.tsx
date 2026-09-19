import { useState } from "react";

import type { TrendSnapshot } from "../api/client";
import { TopicLink } from "./TopicLink";

export function EntityActivityChart({ entities }: { entities: TrendSnapshot["entity_counts"] }) {
  const [tooltipId, setTooltipId] = useState<string>();
  const maximum = Math.max(1, ...entities.flatMap((entity) => [
    entity.change.current_count, entity.change.preceding_count,
  ]));

  return <div className="entity-activity-chart" aria-label="Entity counts by trend window">
    <div className="entity-activity-legend">
      <span><i className="current" />Current</span>
      <span><i className="preceding" />Preceding</span>
      <small>Shared scale: 0–{maximum}</small>
    </div>
    <ol>
      {entities.map((entity) => (
        <li className="entity-activity-row" key={entity.entity_id}>
          <div className="entity-activity-name"
            onMouseEnter={() => setTooltipId(entity.entity_id)}
            onMouseLeave={() => setTooltipId(undefined)}
            onFocus={() => setTooltipId(entity.entity_id)}
            onBlur={() => setTooltipId(undefined)}
            onKeyDown={(event) => { if (event.key === "Escape") setTooltipId(undefined); }}>
            <small>{entity.entity_type.replaceAll("_", " ").toLowerCase()}</small>
            <TopicLink to={`/graph?entity_id=${entity.entity_id}`}
              aria-describedby={`entity-label-${entity.entity_id}`}>
              <span className="entity-short-label">{entity.label}</span>
            </TopicLink>
            <span className="entity-full-label" role="tooltip" hidden={tooltipId !== entity.entity_id}
              id={`entity-label-${entity.entity_id}`}>
              {entity.label}
            </span>
          </div>
          <div className="entity-activity-bars" aria-label={`Current ${entity.change.current_count}; preceding ${entity.change.preceding_count}`}>
            <div><span className="entity-bar-track"><i className="current"
              style={{ width: `${entity.change.current_count / maximum * 100}%` }} /></span>
              <strong>{entity.change.current_count}<span className="sr-only"> current</span></strong></div>
            <div><span className="entity-bar-track"><i className="preceding"
              style={{ width: `${entity.change.preceding_count / maximum * 100}%` }} /></span>
              <strong>{entity.change.preceding_count}<span className="sr-only"> preceding</span></strong></div>
          </div>
        </li>
      ))}
    </ol>
  </div>;
}
