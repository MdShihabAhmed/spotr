import { useRef, useState } from "react";
import { ApiError, type GroundingSource } from "../lib/api";
import { useAddGithubSource, useAddPdfSource, useDeleteSource, useSources } from "../hooks/useSources";

// The built-ins are CLI-seeded, not rows in the table — shown here for context, not managed.
const BUILTINS = [
  { label: "CP-Algorithms", note: "CC BY-SA 4.0" },
  { label: "PyRival", note: "Apache-2.0" },
  { label: "USACO Guide", note: "loader is an extension point" },
  { label: "CP Handbook", note: "loader is an extension point" },
];

const STATUS_TEXT: Record<GroundingSource["status"], string> = {
  pending: "queued",
  cloning: "fetching…",
  embedding: "embedding…",
  ready: "ready",
  failed: "failed",
  retrying: "waiting to retry",
};

const ACTIVE = new Set<GroundingSource["status"]>(["pending", "cloning", "embedding"]);

/** A rough "in ~24h / ~15m" for a scheduled retry time. */
function relTime(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "shortly";
  const mins = Math.round(ms / 60000);
  if (mins < 90) return `in ~${mins}m`;
  return `in ~${Math.round(mins / 60)}h`;
}

const CONSENT =
  "This runs on your machine and embeds on your own key. I confirm I have the right to use this source.";

export function SourcesPage() {
  const { data, isPending } = useSources();
  const [tab, setTab] = useState<"github" | "pdf">("github");

  return (
    <div className="plain-page">
      <h1>Grounding sources</h1>
      <p className="lede">
        Ground the tutor in references <em>you</em> trust — a public GitHub repo or a PDF. Each
        gets chunked, embedded, and cited inline like the built-in corpora. It all stays on your
        machine.
      </p>

      <div className="src-tabs">
        <button className={`src-tab ${tab === "github" ? "on" : ""}`} onClick={() => setTab("github")}>
          GitHub repo
        </button>
        <button className={`src-tab ${tab === "pdf" ? "on" : ""}`} onClick={() => setTab("pdf")}>
          PDF
        </button>
      </div>

      {tab === "github" ? <GithubForm /> : <PdfForm />}

      <div className="section-label" style={{ marginTop: 36 }}>
        Your sources
      </div>
      {isPending && <p className="muted">Loading…</p>}
      {data && data.items.length === 0 && <p className="muted">None yet — add one above.</p>}
      {data && data.items.length > 0 && (
        <ul className="src-list">
          {data.items.map((s) => (
            <SourceRow key={s.id} s={s} />
          ))}
        </ul>
      )}

      <div className="section-label" style={{ marginTop: 36 }}>
        Built-in corpora
      </div>
      <ul className="src-list src-builtins">
        {BUILTINS.map((b) => (
          <li key={b.label} className="src-row">
            <span className="src-label">{b.label}</span>
            <span className="src-meta">{b.note}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function GithubForm() {
  const [label, setLabel] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [ack, setAck] = useState(false);
  const add = useAddGithubSource();

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    add.mutate(
      { label, repo_url: repoUrl, license_ack: ack },
      { onSuccess: () => { setLabel(""); setRepoUrl(""); setAck(false); } },
    );
  };

  return (
    <form className="src-form" onSubmit={submit}>
      <div className="field">
        <label htmlFor="g-label">Name</label>
        <input id="g-label" className="input" value={label} required maxLength={80}
          placeholder="My team's notes" onChange={(e) => setLabel(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="g-url">Public GitHub repo</label>
        <input id="g-url" className="input" value={repoUrl} required
          placeholder="https://github.com/owner/repo" onChange={(e) => setRepoUrl(e.target.value)} />
      </div>
      <Consent ack={ack} setAck={setAck} />
      {add.isError && <div className="error-box">{msg(add.error)}</div>}
      <button className="btn" disabled={!ack || add.isPending}>
        {add.isPending ? "adding…" : "add source"}
      </button>
    </form>
  );
}

function PdfForm() {
  const [label, setLabel] = useState("");
  const [ack, setAck] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const add = useAddPdfSource();

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    add.mutate(
      { file, label, licenseAck: ack },
      { onSuccess: () => { setLabel(""); setAck(false); if (fileRef.current) fileRef.current.value = ""; } },
    );
  };

  return (
    <form className="src-form" onSubmit={submit}>
      <div className="field">
        <label htmlFor="p-label">Name</label>
        <input id="p-label" className="input" value={label} required maxLength={80}
          placeholder="Competitive Programmer's Handbook" onChange={(e) => setLabel(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="p-file">PDF file</label>
        <input id="p-file" className="input" type="file" accept="application/pdf" ref={fileRef} required />
      </div>
      <Consent ack={ack} setAck={setAck} />
      {add.isError && <div className="error-box">{msg(add.error)}</div>}
      <button className="btn" disabled={!ack || add.isPending}>
        {add.isPending ? "uploading…" : "add source"}
      </button>
    </form>
  );
}

function Consent({ ack, setAck }: { ack: boolean; setAck: (v: boolean) => void }) {
  return (
    <label className="src-consent">
      <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
      <span>{CONSENT}</span>
    </label>
  );
}

function SourceRow({ s }: { s: GroundingSource }) {
  const del = useDeleteSource();
  return (
    <li className="src-row">
      <span className="src-label">
        {s.label}
        <span className="src-kind">{s.kind}</span>
      </span>
      <span className="src-meta">
        {s.chunk_count > 0 && <span className="src-count">{s.chunk_count} chunks</span>}
        <span className={`src-status src-status-${s.status}`}>{STATUS_TEXT[s.status]}</span>
        <button
          className="src-remove"
          title="Remove this source"
          disabled={del.isPending}
          onClick={() => del.mutate(s.id)}
        >
          remove
        </button>
      </span>
      {s.status === "failed" && s.error && <span className="src-error">{s.error}</span>}
      {s.status === "retrying" && (
        <span className="src-error src-working">
          {s.retry_at ? `retries ${relTime(s.retry_at)}` : "will retry"}
          {s.chunk_count > 0 ? ` · resumes from ${s.chunk_count} embedded` : ""}
          {s.error ? ` — ${s.error}` : ""}
        </span>
      )}
      {ACTIVE.has(s.status) && (
        <span className="src-error src-working">working — this can take a while on your key.</span>
      )}
    </li>
  );
}

function msg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong.";
}
