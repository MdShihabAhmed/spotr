import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useCurrentUser } from "../hooks/useAuth";

type Health = { status: string };

/** The build order. Kept honest on purpose: shipped means shipped. */
const ROADMAP = [
  { step: 1, name: "Foundation — auth, database, cache", done: true },
  { step: 2, name: "Re-architecture — source-agnostic schema + source abstraction", done: true },
  { step: 3, name: "Problem input — paste/screenshot → AI normalize → ingest", done: true },
  { step: 4, name: "Grounding RAG (CP-Algorithms, USACO, Handbook, PyRival)", done: false },
  { step: 5, name: "Socratic tutor + revealed-set disclosure", done: false },
  { step: 6, name: "No-leakage eval", done: false },
  { step: 7, name: "Self-host packaging + README", done: false },
];

export function DashboardPage() {
  const { data: user } = useCurrentUser();
  const { data: health, isError } = useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<Health>("/health"),
    refetchInterval: 30_000,
  });

  const shipped = ROADMAP.filter((r) => r.done).length;

  return (
    <div className="stack">
      <section className="title-row">
        <div>
          <h1>Welcome back</h1>
          <p className="muted">
            Signed in as <code>{user?.email}</code>
          </p>
        </div>
        <Link to="/ingest" className="btn btn-primary">
          Ingest a problem
        </Link>
      </section>

      <section className="grid">
        <div className="card stat">
          <span className="stat-label">API</span>
          <span className={isError ? "stat-value bad" : "stat-value good"}>
            {isError ? "unreachable" : (health?.status ?? "…")}
          </span>
        </div>
        <div className="card stat">
          <span className="stat-label">CF handle</span>
          <span className="stat-value">{user?.cf_handle ?? "not linked"}</span>
        </div>
        <div className="card stat">
          <span className="stat-label">Build progress</span>
          <span className="stat-value">
            {shipped} / {ROADMAP.length}
          </span>
        </div>
      </section>

      <section className="card">
        <h2>What's built</h2>
        <p className="muted">
          Steps 1–3 are live: you can ingest a problem (paste or screenshot → AI-cleaned → editorial
          fetched, hint ladder built). The tutor chat and grounding ship next — nothing below is faked.
        </p>
        <ul className="roadmap">
          {ROADMAP.map((item) => (
            <li key={item.step} className={item.done ? "done" : ""}>
              <span className="tick">{item.done ? "✓" : item.step}</span>
              <span>{item.name}</span>
              {!item.done && <span className="badge">planned</span>}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
