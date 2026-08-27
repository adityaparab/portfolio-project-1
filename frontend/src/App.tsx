import { Route, Routes } from "react-router-dom";
import { AppShell } from "~/components/AppShell";
import { ScreenPlaceholder } from "~/components/ScreenPlaceholder";
import { StatusPage } from "~/routes/StatusPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route
          index
          element={<ScreenPlaceholder title="Dashboard" issue="#33 (3.6)" />}
        />
        <Route
          path="queue"
          element={<ScreenPlaceholder title="Exception Review" issue="#32 (3.5)" />}
        />
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
