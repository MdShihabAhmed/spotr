import { Link, useParams } from "react-router-dom";
import { ApiError, type IngestionStatus, type Problem } from "../lib/api";
import { useProblem } from "../hooks/useProblems";
import { MathText } from "../components/MathText";

const STATUS_LABEL: Record<IngestionStatus, string> = {
  pending: "Queued",
  scraping: "Fetching from Codeforces…",
  generating_hints: "Generating hints…",
  ready: "Ready",
  failed: "Failed",
};

function StatusBadge({ status }: { status: IngestionStatus }) {
  const tone = status === "ready" ? "good" : status === "failed" ? "bad" : "warn";
  const busy = status !== "ready" && status !== "failed";
  return (
    <span className={`status status-${tone}`}>
      {busy && <span className="spinner" aria-hidden />} {STATUS_LABEL[status]}
    </span>
  );
}

export function ProblemPage() {
  const { id } = useParams();
  const problemId = id ? Number(id) : null;
  const { data: problem, isPending, error } = useProblem(problemId);

  if (isPending) {
    return <p className="muted">Loading…</p>;
  }

  if (error) {
    const notFound = error instanceof ApiError && error.code === "problem_not_found";
    return (
      <div className="stack">
        <div className="error">{notFound ? "No problem with that id." : "Couldn't load this problem."}</div>
        <Link to="/ingest" className="btn btn-ghost">
          ← Ingest a problem
        </Link>
      </div>
    );
  }

  return <ProblemView problem={problem} />;
}

function ProblemView({ problem }: { problem: Problem }) {
  const p = problem;

  return (
    <div className="stack">
      <section>
        <div className="title-row">
          <h1>{p.title || `Problem ${p.cf_contest_id}${p.cf_index}`}</h1>
          <StatusBadge status={p.ingestion_status} />
        </div>
        <p className="muted">
          <a href={p.cf_url} target="_blank" rel="noreferrer">
            {p.cf_contest_id}
            {p.cf_index} on Codeforces ↗
          </a>
        </p>
      </section>

      {p.ingestion_status === "failed" && (
        <div className="error">
          {p.ingestion_error ?? "Ingestion failed."}
          <div style={{ marginTop: "0.6rem" }}>
            <Link to="/ingest" className="btn btn-ghost">
              Try again with a pasted editorial
            </Link>
          </div>
        </div>
      )}

      {p.ingestion_status !== "failed" && (
        <>
          <section className="grid">
            <div className="card stat">
              <span className="stat-label">Rating</span>
              <span className="stat-value">{p.rating ?? "—"}</span>
            </div>
            <div className="card stat">
              <span className="stat-label">Time limit</span>
              <span className="stat-value">{p.time_limit_ms ? `${p.time_limit_ms} ms` : "—"}</span>
            </div>
            <div className="card stat">
              <span className="stat-label">Memory limit</span>
              <span className="stat-value">{p.memory_limit_mb ? `${p.memory_limit_mb} MB` : "—"}</span>
            </div>
          </section>

          {p.tags.length > 0 && (
            <section className="card">
              <h2>Tags</h2>
              <div className="tags">
                {p.tags.map((t) => (
                  <span key={t} className="tag">
                    {t}
                  </span>
                ))}
              </div>
              {(p.is_interactive || p.has_multiple_answers) && (
                <p className="hint hint-warn">
                  {p.is_interactive && "Interactive. "}
                  {p.has_multiple_answers && "Multiple answers accepted. "}
                  Excluded from stress testing.
                </p>
              )}
            </section>
          )}

          <section className="card">
            <h2>Sample tests</h2>
            {p.sample_tests.length === 0 ? (
              <p className="muted">None fetched yet.</p>
            ) : (
              <div className="samples">
                {p.sample_tests.map((s, i) => (
                  <div key={i} className="sample">
                    <div>
                      <div className="stat-label">Input</div>
                      <pre>{s.input}</pre>
                    </div>
                    <div>
                      <div className="stat-label">Expected output</div>
                      <pre>{s.expected_output}</pre>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {p.constraints_text && (
            <details className="card">
              <summary>Constraints</summary>
              <MathText text={p.constraints_text} />
            </details>
          )}

          {p.statement_text && (
            <details className="card" open>
              <summary>Statement</summary>
              <MathText text={p.statement_text} />
            </details>
          )}

          {/* The editorial is deliberately NOT rendered — showing it would spoil the
              graduated hints. We only confirm it was captured. */}
          <section className="card">
            <h2>Editorial</h2>
            {p.has_editorial ? (
              <p className="muted">
                ✓ Captured and hidden. It's used to generate graduated hints (step 3) — you won't see the
                editorial itself, so the hints stay meaningful.
              </p>
            ) : (
              <p className="muted">
                Not captured yet. Add the editorial blog URL from the ingest page when you have it.
              </p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
