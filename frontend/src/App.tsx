import { Route, Routes } from "react-router-dom";
import { AppShell } from "~/components/AppShell";
import { StatusPage } from "~/routes/StatusPage";
import { ExceptionQueuePage } from "~/routes/ExceptionQueuePage";
import { ExceptionDetailPage } from "~/routes/ExceptionDetailPage";
import { DashboardPage } from "~/routes/DashboardPage";
import { IntakePage } from "~/routes/IntakePage";
import { AgentRunPage } from "~/routes/AgentRunPage";
import { AuditPage } from "~/routes/AuditPage";
import { EvalsPage } from "~/routes/EvalsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<DashboardPage />} />
        <Route path="queue" element={<ExceptionQueuePage />} />
        <Route path="queue/:invoiceId" element={<ExceptionDetailPage />} />
        <Route path="intake" element={<IntakePage />} />
        <Route path="runs" element={<AgentRunPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="evals" element={<EvalsPage />} />
        <Route path="status" element={<StatusPage />} />
      </Route>
    </Routes>
  );
}
