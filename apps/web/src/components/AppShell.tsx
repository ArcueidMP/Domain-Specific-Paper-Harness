import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Outlet, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { topicsQuery } from "../api/queries";
import { defaultTopicSlug } from "../lib/topic";
import { TopicNavLink } from "./TopicLink";

const navigation = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/reports/daily", label: "Reports", end: false },
  { to: "/papers", label: "Papers", end: false },
  { to: "/graph", label: "Graph", end: false },
  { to: "/trends", label: "Trends", end: false },
  { to: "/runs", label: "Runs", end: false },
] as const;

export function AppShell() {
  const topics = useQuery(topicsQuery());
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const topicSlug = searchParams.get("topic") ?? defaultTopicSlug;

  useEffect(() => {
    if (!searchParams.has("topic")) {
      const next = new URLSearchParams(searchParams);
      next.set("topic", defaultTopicSlug);
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  function selectTopic(nextTopic: string) {
    let pathname = location.pathname;
    const next = new URLSearchParams(location.search);

    if (pathname.startsWith("/papers/") || pathname.startsWith("/comparisons/")) {
      pathname = "/papers";
      next.delete("paper_id");
      next.delete("entity_id");
    } else if (pathname.startsWith("/lineages/")) {
      pathname = "/graph";
      next.delete("paper_id");
      next.delete("entity_id");
    } else if (pathname.startsWith("/runs/")) {
      pathname = "/runs";
    } else if (pathname === "/graph") {
      next.delete("paper_id");
      next.delete("entity_id");
    }

    next.set("topic", nextTopic);
    void navigate({ pathname, search: `?${next.toString()}` });
  }

  return (
    <div className="app-shell">
      <header className="site-header">
        <div className="header-inner">
          <TopicNavLink className="brand" to="/" aria-label="Paper Harness dashboard">
            <span className="brand-mark" aria-hidden="true">
              PH
            </span>
            <span className="brand-copy">
              <strong>Paper Harness</strong>
              <small>Domain-specific research intelligence</small>
            </span>
          </TopicNavLink>
          <nav className="primary-nav" aria-label="Primary navigation">
            {navigation.map((item) => (
              <TopicNavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
              >
                {item.label}
              </TopicNavLink>
            ))}
          </nav>
          <div className="header-tools">
            <label className="topic-selector">
              <span>Active topic</span>
              <select
                aria-label="Active topic"
                value={topicSlug}
                onChange={(event) => selectTopic(event.target.value)}
              >
                {topics.isPending ? <option value={topicSlug}>Loading topics…</option> : null}
                {topics.data?.items.map((topic) => (
                  <option key={topic.id} value={topic.slug}>
                    {topic.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="private-label">
              <span className="private-dot" aria-hidden="true" />
              Private workspace
            </div>
          </div>
        </div>
      </header>
      <main className="page-container" id="main-content">
        <Outlet key={topicSlug} />
      </main>
      <footer className="site-footer">
        <span>Domain-Specific Paper Harness</span>
        <span>Daily discovery from arXiv only</span>
      </footer>
    </div>
  );
}
