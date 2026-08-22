import { useDeferredValue, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { papersQuery } from "../api/queries";
import { PaperCard } from "../components/PaperCard";
import { StateNotice } from "../components/StateNotice";
import { useTopicSlug } from "../lib/topic";

const pageSize = 20;

export function PapersPage() {
  const topicSlug = useTopicSlug();
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search.trim().toLocaleLowerCase());
  const papers = useQuery({
    ...papersQuery(topicSlug, pageSize, offset),
    placeholderData: keepPreviousData,
  });

  const visiblePapers = useMemo(() => {
    if (!papers.data || deferredSearch.length === 0) {
      return papers.data?.items ?? [];
    }

    return papers.data.items.filter((paper) => {
      const searchable = [paper.title, paper.abstract, paper.canonical_arxiv_id, ...paper.authors]
        .join(" ")
        .toLocaleLowerCase();
      return searchable.includes(deferredSearch);
    });
  }, [deferredSearch, papers.data]);

  const total = papers.data?.total ?? 0;
  const hasPrevious = offset > 0;
  const hasNext = offset + pageSize < total;

  return (
    <section className="page-section">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Versioned corpus</p>
          <h1>Papers</h1>
          <p className="lede">
            Stable arXiv identities with explicit versions, authorship, categories, and source links.
          </p>
        </div>
        <label className="paper-search">
          <span>Filter this page</span>
          <input
            type="search"
            placeholder="Title, author, or arXiv ID"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </div>

      <div className="corpus-toolbar">
        <span>
          {papers.data ? `${total.toLocaleString()} persisted paper${total === 1 ? "" : "s"}` : "Reading corpus…"}
        </span>
        {papers.isFetching && !papers.isPending ? <span role="status">Refreshing…</span> : null}
      </div>

      {papers.isPending ? <StateNotice kind="loading" title="Loading papers" /> : null}
      {papers.isError ? (
        <StateNotice
          kind="error"
          detail={`${papers.error.message} Verify that the API is available, then try again.`}
          onRetry={() => void papers.refetch()}
        />
      ) : null}
      {papers.data?.items.length === 0 ? (
        <StateNotice
          kind="empty"
          title="The paper corpus is empty"
          detail="No arXiv papers have been persisted yet. Run the daily ingestion command first."
        />
      ) : null}
      {papers.data && papers.data.items.length > 0 && visiblePapers.length === 0 ? (
        <StateNotice
          kind="empty"
          title="No papers match this filter"
          detail="Try a title fragment, author surname, or canonical arXiv identifier from this page."
        />
      ) : null}
      {visiblePapers.length > 0 ? (
        <div className="paper-list">
          {visiblePapers.map((paper) => (
            <PaperCard key={paper.id} paper={paper} />
          ))}
        </div>
      ) : null}

      {papers.data && papers.data.items.length > 0 ? (
        <nav className="pagination" aria-label="Paper pages">
          <button
            type="button"
            disabled={!hasPrevious}
            onClick={() => setOffset((current) => Math.max(0, current - pageSize))}
          >
            Previous
          </button>
          <span>
            {offset + 1}–{Math.min(offset + pageSize, total)} of {total}
          </span>
          <button
            type="button"
            disabled={!hasNext}
            onClick={() => setOffset((current) => current + pageSize)}
          >
            Next
          </button>
        </nav>
      ) : null}
    </section>
  );
}
