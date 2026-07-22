import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, type SampleTest } from "../lib/api";
import { useIngestProblem } from "../hooks/useProblems";

const EMPTY_SAMPLE: SampleTest = { input: "", expected_output: "" };

export function IngestPage() {
  const [cfUrl, setCfUrl] = useState("");
  const [editorialUrl, setEditorialUrl] = useState("");
  const [statement, setStatement] = useState("");
  const [constraints, setConstraints] = useState("");
  const [samples, setSamples] = useState<SampleTest[]>([{ ...EMPTY_SAMPLE }]);
  const ingest = useIngestProblem();
  const navigate = useNavigate();

  function updateSample(i: number, field: keyof SampleTest, value: string) {
    setSamples((prev) => prev.map((s, j) => (j === i ? { ...s, [field]: value } : s)));
  }
  function addSample() {
    setSamples((prev) => [...prev, { ...EMPTY_SAMPLE }]);
  }
  function removeSample(i: number) {
    setSamples((prev) => (prev.length === 1 ? prev : prev.filter((_, j) => j !== i)));
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const filledSamples = samples.filter((s) => s.input.trim() && s.expected_output.trim());
    ingest.mutate(
      {
        cf_url: cfUrl.trim(),
        editorial_url: editorialUrl.trim() || undefined,
        statement_text: statement.trim() || undefined,
        constraints_text: constraints.trim() || undefined,
        sample_tests: filledSamples.length ? filledSamples : undefined,
      },
      { onSuccess: (problem) => navigate(`/problems/${problem.id}`) },
    );
  }

  const error = ingest.error;
  const message =
    error instanceof ApiError
      ? error.code === "invalid_cf_url"
        ? "That doesn't look like a Codeforces problem URL. Use a /problemset/ or /contest/ link."
        : error.message
      : error
        ? "Couldn't reach the backend. Is it running on :8000?"
        : null;

  return (
    <div className="stack">
      <section>
        <h1>Ingest a problem</h1>
        <p className="muted">
          Paste the Codeforces problem URL — the statement, constraints, and sample tests are fetched
          automatically (with the math intact), and metadata comes from the CF API. You don't need to
          read or paste anything.
        </p>
      </section>

      <section className="card">
        <form onSubmit={onSubmit}>
          {message && <div className="error">{message}</div>}

          <label htmlFor="cf_url">Codeforces problem URL</label>
          <input
            id="cf_url"
            type="url"
            required
            placeholder="https://codeforces.com/problemset/problem/1846/C"
            value={cfUrl}
            onChange={(e) => setCfUrl(e.target.value)}
          />

          <label htmlFor="editorial_url">Editorial blog URL (optional)</label>
          <input
            id="editorial_url"
            type="url"
            placeholder="https://codeforces.com/blog/entry/118884"
            value={editorialUrl}
            onChange={(e) => setEditorialUrl(e.target.value)}
          />
          <p className="hint">
            Fetched server-side for hint generation — <strong>you never see it</strong>, so the graduated
            hints stay meaningful. Leave blank for now if you don't have it.
          </p>

          <button type="submit" className="btn btn-primary" disabled={ingest.isPending}>
            {ingest.isPending ? "Submitting…" : "Ingest"}
          </button>

          <details className="fallback">
            <summary>Fetching blocked? Paste the statement manually</summary>
            <p className="hint">
              Only needed if automatic fetching fails. The statement isn't a spoiler — you have to read
              the problem to solve it.
            </p>

            <label htmlFor="statement">Statement</label>
            <textarea
              id="statement"
              rows={5}
              value={statement}
              onChange={(e) => setStatement(e.target.value)}
            />

            <label htmlFor="constraints">Constraints</label>
            <textarea
              id="constraints"
              rows={3}
              value={constraints}
              onChange={(e) => setConstraints(e.target.value)}
            />

            <label>Sample tests</label>
            <div className="samples-editor">
              {samples.map((s, i) => (
                <div key={i} className="sample-edit">
                  <div className="sample-edit-head">
                    <span className="stat-label">Sample {i + 1}</span>
                    {samples.length > 1 && (
                      <button type="button" className="link-btn" onClick={() => removeSample(i)}>
                        Remove
                      </button>
                    )}
                  </div>
                  <div className="sample">
                    <div>
                      <label htmlFor={`in-${i}`}>Input</label>
                      <textarea
                        id={`in-${i}`}
                        rows={3}
                        value={s.input}
                        onChange={(e) => updateSample(i, "input", e.target.value)}
                      />
                    </div>
                    <div>
                      <label htmlFor={`out-${i}`}>Expected output</label>
                      <textarea
                        id={`out-${i}`}
                        rows={3}
                        value={s.expected_output}
                        onChange={(e) => updateSample(i, "expected_output", e.target.value)}
                      />
                    </div>
                  </div>
                </div>
              ))}
              <button type="button" className="btn btn-ghost" onClick={addSample}>
                + Add sample
              </button>
            </div>
          </details>
        </form>
      </section>
    </div>
  );
}
