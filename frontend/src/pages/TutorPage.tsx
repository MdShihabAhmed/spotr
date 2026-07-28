import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ApiError,
  getTutorSession,
  revealNextLevel,
  streamTutorMessage,
  type Citation,
  type RevealedLevel,
  type TutorMessage,
} from "../lib/api";
import { useTutorSession } from "../hooks/useTutor";
import { MathText } from "../components/MathText";

const LEVEL_LABEL: Record<number, string> = {
  1: "Nudge",
  2: "Key observation",
  3: "Approach & why",
  4: "Full solution",
};

function Citations({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;
  return (
    <div className="citations">
      <span className="citations-label">Grounded by</span>
      {citations.map((c, i) => (
        <a key={i} href={c.url} target="_blank" rel="noreferrer" className="citation">
          [{i + 1}] {c.source} — {c.title}
        </a>
      ))}
    </div>
  );
}

export function TutorPage() {
  const { slug } = useParams();
  const { data: session, isPending, error } = useTutorSession(slug ?? null);

  const [messages, setMessages] = useState<TutorMessage[]>([]);
  const [revealed, setRevealed] = useState<RevealedLevel[]>([]);
  const [levelCount, setLevelCount] = useState(0);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revealing, setRevealing] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [justUnlocked, setJustUnlocked] = useState<number | null>(null);
  const seeded = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Seed local chat/ladder state from the session once it loads. Afterward the stream
  // drives local state directly (React Query stays the initial-load source only).
  useEffect(() => {
    if (session && !seeded.current) {
      setMessages(session.messages);
      setRevealed(session.revealed);
      setLevelCount(session.level_count);
      seeded.current = true;
    }
  }, [session]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streaming]);

  if (isPending) return <p className="muted">Loading the tutor…</p>;
  if (error) {
    const code = error instanceof ApiError ? error.code : "";
    const msg =
      code === "no_levels"
        ? "This problem has no hint ladder yet — add the editorial link on the ingest page and re-ingest."
        : code === "problem_not_ready"
          ? "This problem is still being ingested. Try again once it's ready."
          : code === "problem_not_found"
            ? "No problem with that id."
            : "Couldn't load the tutor.";
    return (
      <div className="stack">
        <div className="error">{msg}</div>
        <Link to={`/problems/${slug}`} className="btn btn-ghost">
          ← Back to the problem
        </Link>
      </div>
    );
  }

  const revealedCount = revealed.length;
  const allRevealed = revealedCount >= levelCount;

  async function onReveal() {
    if (!slug) return;
    setRevealing(true);
    setErrMsg(null);
    try {
      const r = await revealNextLevel(slug);
      setRevealed((prev) => [...prev, { level: r.level, content: r.content }]);
    } catch (e) {
      setErrMsg(e instanceof ApiError ? e.message : "Couldn't reveal the next hint.");
    } finally {
      setRevealing(false);
    }
  }

  async function onSend(e: FormEvent) {
    e.preventDefault();
    const content = input.trim();
    if (!content || busy || !slug) return;
    setInput("");
    setMessages((prev) => [
      ...prev,
      { role: "user", content, citations: null, created_at: new Date().toISOString() },
    ]);
    setStreaming("");
    setBusy(true);
    setErrMsg(null);

    let acc = "";
    let citations: Citation[] | null = null;
    let unlockedRung: number | null = null;
    await streamTutorMessage(slug, content, {
      onToken: (t) => {
        acc += t;
        setStreaming(acc);
      },
      onUnlock: (level) => {
        unlockedRung = level;
        setJustUnlocked(level);
      },
      onDone: (info) => {
        citations = info.citations?.length ? info.citations : null;
      },
      onError: (detail) => setErrMsg(detail),
    });

    // A rung unlocked via chat — fetch the session to get its (now-earned) content.
    if (unlockedRung != null) {
      try {
        const fresh = await getTutorSession(slug);
        setRevealed(fresh.revealed);
      } catch {
        /* the unlock banner still shows; ladder syncs on next load */
      }
    }
    if (acc.trim()) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: acc, citations, created_at: new Date().toISOString() },
      ]);
    }
    setStreaming(null);
    setBusy(false);
  }

  return (
    <div className="tutor-layout">
      <aside className="ladder">
        <div className="ladder-head">
          <h2>Hint ladder</h2>
          <span className="muted">
            {revealedCount}/{levelCount} unlocked
          </span>
        </div>
        <p className="hint">
          Unlock hints one at a time — or figure one out in the chat and the tutor unlocks it
          for you.
        </p>

        {Array.from({ length: levelCount }, (_, i) => i + 1).map((lvl) => {
          const rung = revealed.find((r) => r.level === lvl);
          const isNext = !rung && lvl === revealedCount + 1;
          return (
            <div key={lvl} className={`rung ${rung ? "rung-open" : isNext ? "rung-next" : "rung-locked"}`}>
              <div className="rung-head">
                <span className="rung-label">
                  {rung ? "" : isNext ? "" : "🔒 "}
                  {lvl}. {LEVEL_LABEL[lvl] ?? `Hint ${lvl}`}
                </span>
                {isNext && (
                  <button className="btn btn-ghost btn-sm" onClick={onReveal} disabled={revealing}>
                    {revealing ? "…" : "Reveal"}
                  </button>
                )}
              </div>
              {rung && (
                <div className="rung-body">
                  <MathText text={rung.content} />
                </div>
              )}
            </div>
          );
        })}
        {allRevealed && levelCount > 0 && (
          <p className="muted" style={{ marginTop: "0.75rem" }}>
            All hints unlocked. Keep chatting to work through the details.
          </p>
        )}
      </aside>

      <section className="chat">
        <div className="chat-head">
          <h1>Tutor</h1>
          <Link to={`/problems/${slug}`} className="muted">
            ← problem
          </Link>
        </div>

        <div className="chat-thread">
          {messages.length === 0 && !streaming && (
            <div className="chat-empty muted">
              Stuck? Tell the tutor what you've tried or what's confusing. It won't hand you the
              answer — it nudges you toward the next idea, and unlocks a hint when you reach it
              yourself.
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`bubble bubble-${m.role}`}>
              <MathText text={m.content} />
              {m.citations && m.citations.length > 0 && <Citations citations={m.citations} />}
            </div>
          ))}
          {streaming !== null && (
            <div className="bubble bubble-assistant">
              {streaming ? <MathText text={streaming} /> : <span className="typing">…</span>}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {justUnlocked != null && (
          <div className="unlock-banner">
            ✓ You figured out hint {justUnlocked} ({LEVEL_LABEL[justUnlocked]}) yourself — unlocked.
          </div>
        )}
        {errMsg && <div className="error">{errMsg}</div>}

        <form className="chat-input" onSubmit={onSend}>
          <textarea
            rows={2}
            placeholder="Ask a question or share your idea…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSend(e);
              }
            }}
            disabled={busy}
          />
          <button type="submit" className="btn btn-primary" disabled={busy || !input.trim()}>
            {busy ? "…" : "Send"}
          </button>
        </form>
      </section>
    </div>
  );
}
