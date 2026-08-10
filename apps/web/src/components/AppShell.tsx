import { NavLink, Outlet } from "react-router-dom";

const navigation = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/reports/daily", label: "Reports", end: false },
  { to: "/papers", label: "Papers", end: false },
  { to: "/graph", label: "Graph", end: false },
  { to: "/trends", label: "Trends", end: false },
  { to: "/runs", label: "Runs", end: false },
] as const;

export function AppShell() {
  return (
    <div className="app-shell">
      <header className="site-header">
        <div className="header-inner">
          <NavLink className="brand" to="/" aria-label="Paper Harness dashboard">
            <span className="brand-mark" aria-hidden="true">
              PH
            </span>
            <span className="brand-copy">
              <strong>Paper Harness</strong>
              <small>LLM-agent research intelligence</small>
            </span>
          </NavLink>
          <nav className="primary-nav" aria-label="Primary navigation">
            {navigation.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="private-label">
            <span className="private-dot" aria-hidden="true" />
            Private workspace
          </div>
        </div>
      </header>
      <main className="page-container" id="main-content">
        <Outlet />
      </main>
      <footer className="site-footer">
        <span>Domain-Specific Paper Harness</span>
        <span>Daily discovery from arXiv only</span>
      </footer>
    </div>
  );
}
