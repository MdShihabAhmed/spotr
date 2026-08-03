import { useState, type ChangeEvent, type ClipboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, type Figure, type IngestionStatus, type SampleTest } from "../lib/api";
import { useIngestProblem, useNormalize, useProblem } from "../hooks/useProblems";
import { MathText } from "../components/MathText";

const EMPTY_SAMPLE: SampleTest = { input: "", expected_output: "" };
const MAX_IMAGES = 5;

async function fileToDataUri(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

// The pipeline mirrors ingestion_status. Backend folds metadata-fetch into `normalizing`, so
// "fetching metadata" reads done once we reach generating_levels.
const STAGES = [
  { name: "normalizing", note: "vision pass rebuilds statement, constraints, samples and LaTeX" },
  { name: "fetching metadata", note: "Codeforces API — rating, tags, limits — under the rate lease" },
  { name: "generating levels", note: "one grounded call produces four distinct levels" },
  { name: "ready", note: "all four sealed · session opened" },
];
const ACTIVE_INDEX: Record<IngestionStatus, number> = {
  pending: -1,
  normalizing: 0,
  generating_levels: 2,
  ready: 4,
  failed: -1,
};

function Pipeline({ status, error, slug }: { status: IngestionStatus | null; error: string | null; slug: string | null }) {
  const active = status ? ACTIVE_INDEX[status] : -1;
  const statusLine =
    status == null
      ? "Returns immediately with a slug; the panel streams status until the levels exist."
      : status === "failed"
        ? null
        : status === "ready"
          ? `${slug} is ready — four levels sealed.`
          : "Runs on the durable job queue on its own database session. A failure lands as a readable error, never a crash.";

  return (
    <aside className="pipeline">
      <span className="section-label">Pipeline</span>
      {STAGES.map((s, i) => {
        const state =
          status === "ready" || i < active ? "stage-done" : i === active ? "stage-active" : "stage-pending";
        return (
          <div key={s.name} className={`stage ${state}`}>
            <span className="stage-dot" aria-hidden />
            <div>
              <div className="stage-name">{s.name}</div>
              <p className="stage-note">{s.note}</p>
            </div>
          </div>
        );
      })}
      {status === "failed" && error ? (
        <div className="pipeline-error">{error}</div>
      ) : statusLine ? (
        <div className="pipeline-status">{statusLine}</div>
      ) : null}
    </aside>
  );
}

export function IngestPage() {
  const [tab, setTab] = useState<"screenshot" | "paste">("screenshot");
  const [source] = useState("codeforces");
  const [url, setUrl] = useState("");
  const [editorialUrl, setEditorialUrl] = useState("");
  const [images, setImages] = useState<string[]>([]);
  const [text, setText] = useState("");

  const [statement, setStatement] = useState("");
  const [constraints, setConstraints] = useState("");
  const [samples, setSamples] = useState<SampleTest[]>([{ ...EMPTY_SAMPLE }]);
  const [note, setNote] = useState("");
  const [limits, setLimits] = useState<{ time_ms: number | null; mem_mb: number | null }>({ time_ms: null, mem_mb: null });
  const [figures, setFigures] = useState<Figure[]>([]);
  const [extracted, setExtracted] = useState(false);
  const [submittedSlug, setSubmittedSlug] = useState<string | null>(null);

  const normalize = useNormalize();
  const ingest = useIngestProblem();
  const navigate = useNavigate();
  const submitted = useProblem(submittedSlug);
  const status = submitted.data?.ingestion_status ?? null;

  function updateSample(i: number, field: keyof SampleTest, value: string) {
    setSamples((prev) => prev.map((s, j) => (j === i ? { ...s, [field]: value } : s)));
  }
  const addSample = () => setSamples((prev) => [...prev, { ...EMPTY_SAMPLE }]);
  const removeSample = (i: number) => setSamples((prev) => (prev.length === 1 ? prev : prev.filter((_, j) => j !== i)));

  async function onPickImages(e: ChangeEvent<HTMLInputElement>) {
    const picked = Array.from(e.target.files ?? []);
    e.target.value = "";
    const uris = await Promise.all(picked.map(fileToDataUri));
    setImages((prev) => [...prev, ...uris].slice(0, MAX_IMAGES));
  }
  const removeImage = (i: number) => setImages((prev) => prev.filter((_, j) => j !== i));

  function addFigures(uris: string[]) {
    setFigures((prev) => [...prev, ...uris.map((u) => ({ data_uri: u, section: "statement" as const }))]);
  }
  async function onPickFigures(e: ChangeEvent<HTMLInputElement>) {
    const picked = Array.from(e.target.files ?? []);
    e.target.value = "";
    addFigures(await Promise.all(picked.map(fileToDataUri)));
  }
  async function onPasteFigure(e: ClipboardEvent) {
    const imgs = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith("image/"));
    if (imgs.length === 0) return;
    e.preventDefault();
    addFigures(await Promise.all(imgs.map(fileToDataUri)));
  }
  const setFigureSection = (i: number, section: Figure["section"]) =>
    setFigures((prev) => prev.map((f, j) => (j === i ? { ...f, section } : f)));
  const removeFigure = (i: number) => setFigures((prev) => prev.filter((_, j) => j !== i));

  function onExtract() {
    normalize.mutate(tab === "screenshot" ? { images } : { text }, {
      onSuccess: (n) => {
        setStatement(n.statement);
        setConstraints(n.constraints ?? "");
        setSamples(n.samples.length ? n.samples : [{ ...EMPTY_SAMPLE }]);
        setNote(n.note ?? "");
        setLimits({ time_ms: n.time_limit_ms, mem_mb: n.memory_limit_mb });
        setExtracted(true);
      },
    });
  }

  function onIngest() {
    const filled = samples.filter((s) => s.input.trim() && s.expected_output.trim());
    ingest.mutate(
      {
        source,
        url: url.trim(),
        statement_text: statement.trim() || undefined,
        constraints_text: constraints.trim() || undefined,
        sample_tests: filled.length ? filled : undefined,
        note_text: note.trim() || undefined,
        figures: figures.length ? figures : undefined,
        time_limit_ms: limits.time_ms ?? undefined,
        memory_limit_mb: limits.mem_mb ?? undefined,
        editorial_url: editorialUrl.trim() || undefined,
      },
      { onSuccess: (p) => setSubmittedSlug(p.slug) },
    );
  }

  const canExtract = tab === "screenshot" ? images.length > 0 : text.trim().length > 0;
  const canIngest = extracted || editorialUrl.trim().length > 0;
  const err = normalize.error ?? ingest.error;
  const message =
    err instanceof ApiError
      ? err.code === "invalid_problem_url"
        ? "That doesn't look like a Codeforces problem URL. Use a /problemset/ or /contest/ link."
        : err.message
      : err
        ? "Couldn't reach the backend. Is it running on :8000?"
        : null;

  const buttonLabel = ingest.isPending
    ? "working…"
    : submittedSlug && status !== "ready" && status !== "failed"
      ? "working…"
      : status === "ready"
        ? "open session"
        : "ingest";

  function onPrimary() {
    if (status === "ready" && submittedSlug) navigate(`/problems/${submittedSlug}`);
    else if (!submittedSlug) onIngest();
  }

  return (
    <div className="page">
      <h1 className="h1-ingest">Bring the problem to it.</h1>
      <p className="lede">
        Spotr never scrapes a problem page. You paste the statement or drop a screenshot —
        figures included. It fetches only what the official API permits: metadata, and the
        editorial from a link you supply. The editorial is read once, at ingestion, and never sent
        back to your browser.
      </p>

      <div className="ingest-grid">
        <div className="ingest-card">
          <div className="tabs">
            <button className={`tab ${tab === "paste" ? "active" : ""}`} onClick={() => setTab("paste")}>
              paste text
            </button>
            <button className={`tab ${tab === "screenshot" ? "active" : ""}`} onClick={() => setTab("screenshot")}>
              screenshot
            </button>
          </div>

          <div className="ingest-body">
            {message && <div className="error-box" style={{ marginBottom: 16 }}>{message}</div>}

            {tab === "screenshot" ? (
              <div className="field">
                {images.length > 0 && (
                  <div className="thumbs">
                    {images.map((src, i) => (
                      <div key={i} className="thumb">
                        <img src={src} alt={`screenshot ${i + 1}`} />
                        <button className="thumb-x" onClick={() => removeImage(i)} aria-label="remove">
                          ×
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                {images.length < MAX_IMAGES && (
                  <label className="dropzone">
                    <span className="dropzone-glyph" aria-hidden />
                    <span className="dropzone-title">drop a screenshot of the problem</span>
                    <span className="dropzone-sub">
                      Vision reads rendered math and figures directly — better than OCR, better than
                      copy-paste.
                    </span>
                    <input type="file" accept="image/*" multiple onChange={onPickImages} hidden />
                  </label>
                )}
              </div>
            ) : (
              <div className="field">
                <textarea
                  className="textarea"
                  rows={9}
                  placeholder="Paste the statement, constraints and samples. Messy is fine — mangled line breaks, half-broken math. A multimodal pass rebuilds the LaTeX."
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                />
              </div>
            )}

            <div className="pair">
              <div className="field">
                <label>Source</label>
                <select className="select" value={source} disabled>
                  <option value="codeforces">Codeforces</option>
                </select>
              </div>
              <div className="field" style={{ flex: 2 }}>
                <label>Editorial link</label>
                <input
                  className="input"
                  type="url"
                  placeholder="codeforces.com/blog/entry/…"
                  value={editorialUrl}
                  onChange={(e) => setEditorialUrl(e.target.value)}
                />
              </div>
            </div>

            <div className="field">
              <label>Problem URL</label>
              <input
                className="input"
                type="url"
                placeholder="https://codeforces.com/problemset/problem/1846/C"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </div>

            {!submittedSlug && (
              <button className="btn btn-outline" onClick={onExtract} disabled={!canExtract || normalize.isPending}>
                {normalize.isPending ? "extracting…" : "extract with AI"}
              </button>
            )}

            {extracted && !submittedSlug && (
              <div style={{ marginTop: 20 }}>
                <div className="field">
                  <label>Statement</label>
                  <textarea className="textarea" rows={6} value={statement} onChange={(e) => setStatement(e.target.value)} />
                  {statement.trim() && (
                    <div className="preview">
                      <span className="section-label">Rendered</span>
                      <MathText text={statement} />
                    </div>
                  )}
                </div>

                <div className="field">
                  <label>Constraints</label>
                  <textarea className="textarea" rows={3} value={constraints} onChange={(e) => setConstraints(e.target.value)} />
                  {constraints.trim() && (
                    <div className="preview">
                      <span className="section-label">Rendered</span>
                      <MathText text={constraints} />
                    </div>
                  )}
                </div>

                <div className="field">
                  <label>Sample tests</label>
                  {samples.map((s, i) => (
                    <div key={i} style={{ marginBottom: 10 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                        <span className="section-label">Sample {i + 1}</span>
                        {samples.length > 1 && (
                          <button className="tab" style={{ flex: "none", padding: "2px 8px" }} onClick={() => removeSample(i)}>
                            remove
                          </button>
                        )}
                      </div>
                      <div className="pair">
                        <textarea className="textarea" rows={3} placeholder="input" value={s.input} onChange={(e) => updateSample(i, "input", e.target.value)} />
                        <textarea className="textarea" rows={3} placeholder="expected output" value={s.expected_output} onChange={(e) => updateSample(i, "expected_output", e.target.value)} />
                      </div>
                    </div>
                  ))}
                  <span className="add-tile" onClick={addSample}>+ add sample</span>
                </div>

                <div className="field">
                  <label>Note (explains the samples — shown after them)</label>
                  <textarea className="textarea" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
                  {note.trim() && (
                    <div className="preview">
                      <span className="section-label">Rendered</span>
                      <MathText text={note} />
                    </div>
                  )}
                </div>

                <div className="field" onPaste={onPasteFigure}>
                  <label>Figures (upload or Ctrl+V)</label>
                  {figures.map((f, i) => (
                    <div key={i} className="figure-row" style={{ marginBottom: 8 }}>
                      <img src={f.data_uri} alt={`figure ${i + 1}`} style={{ width: 120, height: 90, objectFit: "contain", border: "1px solid var(--b-input)" }} />
                      <div>
                        <div className="radio-row">
                          <label style={{ textTransform: "none", letterSpacing: 0 }}>
                            <input type="radio" name={`fig-${i}`} checked={f.section === "statement"} onChange={() => setFigureSection(i, "statement")} /> statement
                          </label>
                          <label style={{ textTransform: "none", letterSpacing: 0 }}>
                            <input type="radio" name={`fig-${i}`} checked={f.section === "note"} onChange={() => setFigureSection(i, "note")} /> note
                          </label>
                        </div>
                        <span className="add-tile" onClick={() => removeFigure(i)} style={{ marginTop: 6 }}>remove</span>
                      </div>
                    </div>
                  ))}
                  <label className="add-tile">
                    + add figure
                    <input type="file" accept="image/*" multiple onChange={onPickFigures} hidden />
                  </label>
                </div>
              </div>
            )}
          </div>

          <div className="ingest-footer">
            <span className="throttle">outbound calls throttled to 1 req / 2s</span>
            <button className="btn" onClick={onPrimary} disabled={(!submittedSlug && (!canIngest || ingest.isPending)) || (!!submittedSlug && status !== "ready")}>
              {buttonLabel}
            </button>
          </div>
        </div>

        <Pipeline status={status} error={submitted.data?.ingestion_error ?? null} slug={submittedSlug} />
      </div>
    </div>
  );
}
