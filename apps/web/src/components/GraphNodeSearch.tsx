import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { graphNodeSearchQuery } from "../api/queries";
import { StateNotice } from "./StateNotice";

export function GraphNodeSearch({ topic, onSelect }: {
  topic: string;
  onSelect: (id: string) => void;
}) {
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const results = useQuery(graphNodeSearchQuery(topic, query, offset));

  return (
    <section className="graph-search" aria-label="Find a graph node">
      <form onSubmit={(event) => {
        event.preventDefault();
        setQuery(text.trim());
        setOffset(0);
      }}>
        <label htmlFor="graph-search-input">Search nodes</label>
        <div className="graph-search-field">
          <input id="graph-search-input" type="search" value={text} maxLength={200}
            placeholder="Name, paper title, or arXiv ID"
            aria-describedby="graph-search-help"
            onChange={(event) => setText(event.target.value)} />
          <button className="primary-button" type="submit" disabled={!text.trim()}>Search</button>
          {query ? <button className="secondary-button" type="button" onClick={() => {
            setText(""); setQuery(""); setOffset(0);
          }}>Clear search</button> : null}
        </div>
        <p id="graph-search-help">Search all published nodes in this topic, including nodes outside the current view.</p>
      </form>
      {query ? (
        <div className="graph-search-results" aria-live="polite" aria-busy={results.isFetching}>
          {results.isPending ? <p>Searching nodes…</p> : null}
          {results.isError ? <StateNotice kind="error" detail={results.error.message}
            onRetry={() => void results.refetch()} /> : null}
          {results.data ? <>
            <p>{results.data.total} results for “{query}”</p>
            {results.data.total === 0 ? <p>No matching nodes. Try a shorter name or another keyword.</p> : <>
              <ul aria-label="Node search results">
                {results.data.items.map((node) => <li key={node.id}>
                  <button type="button" onClick={() => onSelect(node.id)} title={node.display_label}>
                    <span>{node.entity_type.replaceAll("_", " ").toLowerCase()}</span>
                    <strong>{node.display_label}</strong>
                    <small>Explore connections →</small>
                  </button>
                </li>)}
              </ul>
              <div className="graph-search-pagination">
                <button type="button" disabled={offset === 0} onClick={() => setOffset(offset - 20)}>Previous results</button>
                <span>{offset + 1}–{offset + results.data.items.length} of {results.data.total}</span>
                <button type="button" disabled={offset + 20 >= results.data.total}
                  onClick={() => setOffset(offset + 20)}>Next results</button>
              </div>
            </>}
          </> : null}
        </div>
      ) : null}
    </section>
  );
}
