import { Link, useParams } from "react-router-dom";
import { ApiError, type Figure, type IngestionStatus, type Problem } from "../lib/api";
import { useProblem } from "../hooks/useProblems";
import { MathText } from "../components/MathText";

/** Diagrams the user attached to a given section, rendered as-is. */
function Figures({ figures, section }: { figures: Figure[]; section: Figure["section"] }) {
  const shown = figures.filter((f) => f.section === section);
  if (shown.length === 0) return null;
  return (
    <div className="figure-gallery">
      {shown.map((f, i) => (
        <img key={i} src={f.data_uri} alt={`${section} figure ${i + 1}`} />
      ))}
    </div>
  );
}

const STATUS_LABEL: Record<IngestionStatus, string> = {
  pending: "Queued",
  normalizing: "Cleaning up…",
  generating_levels: "Building the hint ladder…",
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
  const { slug } = useParams();
  const { data: problem, isPending, error } = useProblem(slug ?? null);

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
          <h1>{p.title || p.external_id}</h1>
          <StatusBadge status={p.ingestion_status} />
        </div>
        <p className="muted">
          {p.url ? (
            <a href={p.url} target="_blank" rel="noreferrer">
              {p.external_id} on {p.source} ↗
            </a>
          ) : (
            <>
              {p.external_id} on {p.source}
            </>
          )}
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

          {p.statement_text && (
            <details className="card" open>
              <summary>Statement</summary>
              <MathText text={p.statement_text} />
              <Figures figures={p.figures} section="statement" />
            </details>
          )}

          {p.constraints_text && (
            <details className="card">
              <summary>Constraints</summary>
              <MathText text={p.constraints_text} />
            </details>
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

          {(p.note_text || p.figures.some((f) => f.section === "note")) && (
            <section className="card">
              <h2>Note</h2>
              {p.note_text && <MathText text={p.note_text} />}
              <Figures figures={p.figures} section="note" />
            </section>
          )}

          {/* The editorial is deliberately NOT rendered — showing it would spoil the
              graduated ladder. We only confirm it was captured. */}
          <section className="card">
            <h2>Tutor</h2>
            {p.level_count > 0 ? (
              <div className="stack">
                <p className="muted">
                  ✓ {p.level_count}-level hint ladder built from the editorial (kept hidden so it
                  stays meaningful). Work through it with the Socratic tutor — it reveals one level
                  at a time and unlocks a hint when you figure it out yourself.
                </p>
                <Link to={`/problems/${p.slug}/tutor`} className="btn btn-primary">
                  Open the tutor →
                </Link>
              </div>
            ) : p.has_editorial ? (
              <p className="muted">
                Editorial captured and hidden; the hint ladder will appear here once generated.
              </p>
            ) : (
              <p className="muted">
                No editorial captured yet, so no ladder. Add the editorial blog URL from the ingest page.
              </p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
