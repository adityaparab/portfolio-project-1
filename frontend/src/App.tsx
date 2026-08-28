import { Route, Routes } from "react-router-dom";
import { AppShell } from "~/components/AppShell";
import { ScreenPlaceholder } from "~/components/ScreenPlaceholder";
import { StatusPage } from "~/routes/StatusPage";
import { ExceptionQueuePage } from "~/routes/ExceptionQueuePage";
import { ExceptionDetailPage } from "~/routes/ExceptionDetailPage";
import { DashboardPage } from "~/routes/DashboardPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<DashboardPage />} />
        <Route path="queue" element={<ExceptionQueuePage />} />
        <Route path="queue/:invoiceId" element={<ExceptionDetailPage />} />
        <Route
          path="intake"
          element={<ScreenPlaceholder title="Intake" issue="#34 (3.7)" />}
        />
        <Route
          path="runs"
          element={<ScreenPlaceholder title="Agent Run" issue="#36 (3.8)" />}
        />
        <Route
          path="audit"
          element={<ScreenPlaceholder title="Audit & Trace" issue="#37 (3.9)" />}
        />
        <Route
          path="evals"
          element={<ScreenPlaceholder title="Evals" issue="#38 (3.10)" />}
        />
        <Route path="status" element={<StatusPage />} />
      </Route>
    </Routes>
  );
}
