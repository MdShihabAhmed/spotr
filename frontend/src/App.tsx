import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import { Layout } from "./components/Layout";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { AccountPage } from "./pages/AccountPage";
import { IngestPage } from "./pages/IngestPage";
import { LandingPage } from "./pages/LandingPage";
import { LoginPage } from "./pages/LoginPage";
import { ProblemsListPage } from "./pages/ProblemsListPage";
import { RegisterPage } from "./pages/RegisterPage";
import { SessionPage } from "./pages/SessionPage";
import "./App.css";

function Protected({ children }: { children: React.ReactNode }) {
  return (
    <ProtectedRoute>
      <Layout>{children}</Layout>
    </ProtectedRoute>
  );
}

/** Public shell — the marketing landing is reachable signed-out; the nav adapts to auth. */
function Public({ children }: { children: React.ReactNode }) {
  return <Layout>{children}</Layout>;
}

/** Old `/problems/:slug/tutor` route folded into the session view at `/problems/:slug`. */
function TutorRedirect() {
  const { slug } = useParams();
  return <Navigate to={`/problems/${slug}`} replace />;
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />

        <Route path="/" element={<Public><LandingPage /></Public>} />
        <Route path="/problems" element={<Protected><ProblemsListPage /></Protected>} />
        <Route path="/problems/new" element={<Protected><IngestPage /></Protected>} />
        <Route path="/problems/:slug" element={<Protected><SessionPage /></Protected>} />
        <Route path="/account" element={<Protected><AccountPage /></Protected>} />

        {/* Legacy routes → new locations */}
        <Route path="/ingest" element={<Navigate to="/problems/new" replace />} />
        <Route path="/problems/:slug/tutor" element={<TutorRedirect />} />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
