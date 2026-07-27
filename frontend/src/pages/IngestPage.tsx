import { useState, type ChangeEvent, type ClipboardEvent, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, type Figure, type SampleTest } from "../lib/api";
import { useIngestProblem, useNormalize } from "../hooks/useProblems";
import { MathText } from "../components/MathText";

const EMPTY_SAMPLE: SampleTest = { input: "", expected_output: "" };
const MAX_IMAGES = 5; // a long problem may span several screenshots

async function fileToDataUri(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function IngestPage() {
  const [source] = useState("codeforces"); // only source for now; dropdown is future-proofing
  const [url, setUrl] = useState("");
  const [editorialUrl, setEditorialUrl] = useState("");

  // Screenshots are the extraction input — vision preserves math that rendered
  // copy-paste flattens. (Paste-text was dropped: its extraction quality was worse.)
  const [images, setImages] = useState<string[]>([]);

  // Editable, structured problem — populated by AI extraction, then reviewed/fixed.
  const [statement, setStatement] = useState("");
  const [constraints, setConstraints] = useState("");
  const [samples, setSamples] = useState<SampleTest[]>([{ ...EMPTY_SAMPLE }]);
  const [note, setNote] = useState("");
  const [limits, setLimits] = useState<{ time_ms: number | null; mem_mb: number | null }>({
    time_ms: null,
    mem_mb: null,
  });
  // Diagrams the AI can't reproduce as text — uploaded/pasted as-is, tagged by section.
  const [figures, setFigures] = useState<Figure[]>([]);
  const [extracted, setExtracted] = useState(false);

  const normalize = useNormalize();
  const ingest = useIngestProblem();
  const navigate = useNavigate();

  function updateSample(i: number, field: keyof SampleTest, value: string) {
    setSamples((prev) => prev.map((s, j) => (j === i ? { ...s, [field]: value } : s)));
  }
  const addSample = () => setSamples((prev) => [...prev, { ...EMPTY_SAMPLE }]);
  const removeSample = (i: number) =>
    setSamples((prev) => (prev.length === 1 ? prev : prev.filter((_, j) => j !== i)));

  async function onPickImages(e: ChangeEvent<HTMLInputElement>) {
    const picked = Array.from(e.target.files ?? []);
    e.target.value = ""; // let the same file be re-picked after a remove
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
    if (imgs.length === 0) return; // let text paste behave normally
    e.preventDefault();
    addFigures(await Promise.all(imgs.map(fileToDataUri)));
  }
  const setFigureSection = (i: number, section: Figure["section"]) =>
    setFigures((prev) => prev.map((f, j) => (j === i ? { ...f, section } : f)));
  const removeFigure = (i: number) => setFigures((prev) => prev.filter((_, j) => j !== i));

  function onExtract() {
    normalize.mutate(
      { images },
      {
        onSuccess: (n) => {
          setStatement(n.statement);
          setConstraints(n.constraints ?? "");
          setSamples(n.samples.length ? n.samples : [{ ...EMPTY_SAMPLE }]);
          setNote(n.note ?? "");
          setLimits({ time_ms: n.time_limit_ms, mem_mb: n.memory_limit_mb });
          setExtracted(true);
        },
      },
    );
  }

  function onIngest(e: FormEvent) {
    e.preventDefault();
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
      { onSuccess: (p) => navigate(`/problems/${p.slug}`) },
    );
  }

  const canExtract = images.length > 0;
  const err = normalize.error ?? ingest.error;
  const message =
    err instanceof ApiError
      ? err.code === "invalid_problem_url"
        ? "That doesn't look like a Codeforces problem URL. Use a /problemset/ or /contest/ link."
        : err.message
      : err
        ? "Couldn't reach the backend. Is it running on :8000?"
        : null;

  return (
    <div className="stack">
      <section>
        <h1>Ingest a problem</h1>
        <p className="muted">
          Give the problem URL, then drop screenshot(s) of the statement — the AI reads the
          rendered math and returns clean, structured text for you to review before ingesting.
          Add any diagrams separately as figures. The editorial is fetched server-side and never
          shown to you, so the tutor's guidance stays meaningful.
        </p>
      </section>

      <section className="card">
        <form onSubmit={onIngest}>
          {message && <div className="error">{message}</div>}

          <div className="field-row">
            <div>
              <label htmlFor="source">Source</label>
              <select id="source" value={source} disabled>
                <option value="codeforces">Codeforces</option>
              </select>
            </div>
            <div style={{ flex: 1 }}>
              <label htmlFor="url">Problem URL</label>
              <input
                id="url"
                type="url"
                required
                placeholder="https://codeforces.com/problemset/problem/1846/C"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </div>
          </div>

          <label htmlFor="editorial_url">Editorial blog URL (optional)</label>
          <input
            id="editorial_url"
            type="url"
            placeholder="https://codeforces.com/blog/entry/118884"
            value={editorialUrl}
            onChange={(e) => setEditorialUrl(e.target.value)}
          />
          <p className="hint">
            Fetched server-side to build the graduated levels — <strong>you never see it</strong>. Leave
            blank if you don't have it (you can add it on a later re-ingest).
          </p>

          <label>Problem screenshots</label>
          <div className="screenshot-pick">
            {images.length > 0 && (
              <div className="ss-grid">
                {images.map((src, i) => (
                  <div key={i} className="ss-thumb">
                    <img src={src} alt={`screenshot ${i + 1}`} />
                    <button
                      type="button"
                      className="ss-remove"
                      aria-label={`Remove screenshot ${i + 1}`}
                      onClick={() => removeImage(i)}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}
            {images.length < MAX_IMAGES && (
              <label className="ss-add">
                + Add screenshot{images.length > 0 ? "s" : ""}
                <input type="file" accept="image/*" multiple onChange={onPickImages} hidden />
              </label>
            )}
            <p className="hint">
              Add up to {MAX_IMAGES} — capture a long problem in parts (statement, then
              constraints, then samples) and they're stitched together.
            </p>
          </div>

          <button
            type="button"
            className="btn btn-ghost"
            onClick={onExtract}
            disabled={!canExtract || normalize.isPending}
          >
            {normalize.isPending ? "Extracting…" : "✨ Extract with AI"}
          </button>

          {extracted && (
            <div className="extracted">
              <p className="hint">Review and fix anything the AI got wrong, then ingest.</p>

              <label htmlFor="statement">Statement</label>
              <textarea
                id="statement"
                rows={6}
                value={statement}
                onChange={(e) => setStatement(e.target.value)}
              />
              {statement.trim() && (
                <div className="render-preview">
                  <span className="render-preview-tag">Rendered</span>
                  <MathText text={statement} />
                </div>
              )}

              <label htmlFor="constraints">Constraints (input/output format + bounds)</label>
              <textarea
                id="constraints"
                rows={3}
                value={constraints}
                onChange={(e) => setConstraints(e.target.value)}
              />
              {constraints.trim() && (
                <div className="render-preview">
                  <span className="render-preview-tag">Rendered</span>
                  <MathText text={constraints} />
                </div>
              )}

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

              <label htmlFor="note">Note (explains the samples — shown after them)</label>
              <textarea
                id="note"
                rows={3}
                placeholder="The CF 'Note' section, if any."
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
              {note.trim() && (
                <div className="render-preview">
                  <span className="render-preview-tag">Rendered</span>
                  <MathText text={note} />
                </div>
              )}

              <label>Figures (optional)</label>
              <p className="hint">
                Diagrams the AI can't read as text. Upload or paste (Ctrl+V) the image, then mark
                whether it belongs to the statement or the note.
              </p>
              <div className="figures-editor" onPaste={onPasteFigure}>
                {figures.map((f, i) => (
                  <div key={i} className="figure-row">
                    <img src={f.data_uri} alt={`figure ${i + 1}`} className="figure-thumb" />
                    <div className="figure-controls">
                      <div className="radio-row">
                        <label>
                          <input
                            type="radio"
                            name={`fig-${i}`}
                            checked={f.section === "statement"}
                            onChange={() => setFigureSection(i, "statement")}
                          />
                          Statement
                        </label>
                        <label>
                          <input
                            type="radio"
                            name={`fig-${i}`}
                            checked={f.section === "note"}
                            onChange={() => setFigureSection(i, "note")}
                          />
                          Note
                        </label>
                      </div>
                      <button type="button" className="link-btn" onClick={() => removeFigure(i)}>
                        Remove
                      </button>
                    </div>
                  </div>
                ))}
                <label className="ss-add">
                  + Add figure
                  <input type="file" accept="image/*" multiple onChange={onPickFigures} hidden />
                </label>
              </div>
            </div>
          )}

          <button type="submit" className="btn btn-primary" disabled={ingest.isPending || !extracted}>
            {ingest.isPending ? "Ingesting…" : "Ingest"}
          </button>
        </form>
      </section>
    </div>
  );
}
