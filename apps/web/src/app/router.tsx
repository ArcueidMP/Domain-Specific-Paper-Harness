import { createBrowserRouter } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { ComparisonPage } from "../pages/ComparisonPage";
import { DashboardPage } from "../pages/DashboardPage";
import { DailyReportPage } from "../pages/DailyReportPage";
import { LineagePage } from "../pages/LineagePage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { PaperDetailPage } from "../pages/PaperDetailPage";
import { PapersPage } from "../pages/PapersPage";
import { RunsPage } from "../pages/RunsPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "papers", element: <PapersPage /> },
      { path: "papers/:paperId", element: <PaperDetailPage /> },
      { path: "comparisons/:comparisonId", element: <ComparisonPage /> },
      { path: "reports/daily", element: <DailyReportPage /> },
      { path: "reports/daily/:logicalDate", element: <DailyReportPage /> },
      {
        path: "graph",
        lazy: async () => {
          const { KnowledgeGraphPage } = await import("../pages/KnowledgeGraphPage");
          return { Component: KnowledgeGraphPage };
        },
      },
      {
        path: "trends",
        lazy: async () => {
          const { TrendsPage } = await import("../pages/TrendsPage");
          return { Component: TrendsPage };
        },
      },
      { path: "lineages/:entityOrPaperId", element: <LineagePage /> },
      { path: "runs", element: <RunsPage /> },
      { path: "runs/:runId", element: <RunsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
