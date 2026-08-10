import { createBrowserRouter } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { ComparisonPage } from "../pages/ComparisonPage";
import { DashboardPage } from "../pages/DashboardPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { PaperDetailPage } from "../pages/PaperDetailPage";
import { PapersPage } from "../pages/PapersPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "papers", element: <PapersPage /> },
      { path: "papers/:paperId", element: <PaperDetailPage /> },
      { path: "comparisons/:comparisonId", element: <ComparisonPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
