import { Fragment, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ApiError,
  getTutorSession,
  revealNextLevel,
  streamTutorMessage,
  type Citation,
  type Problem,
  type RevealedLevel,
  type UnlockVia,
} from "../lib/api";
import { useProblem } from "../hooks/useProblems";
import { useTutorSession } from "../hooks/useTutor";
import { MathText } from "../components/MathText";

// We generate ladder *content*, not titles, and the ladder length is dynamic — so the
// rung title is a role label derived from the rung's *position* in this problem's ladder,
// not a fixed per-index map. First rung is always the nudge, last is always the solution.
function levelTitle(lvl: number, count: number): string {
  if (lvl <= 1) return "The first thing to notice";
  if (lvl >= count) return "The full solution";
  if (lvl === count - 1) return "The approach, and why it works";
  return "The key observation";
}

const CHIPS = ["where do I start?", "what do the constraints rule out?", "i think i have it →"];

// --- resizable columns -------------------------------------------------------
// The three panels are user-draggable rather than fixed: everyone reads a different
// balance of problem vs chat. Widths live in localStorage so the choice sticks. The
// middle (chat) stays flexible; only the two side rails carry an explicit px width.
const COLS_KEY = "fai.session.cols.v1";
const LEFT = { min: 320, max: 820, def: 460 };
const RIGHT = { min: 240, max: 480, def: 300 };
const clampN = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

function useSessionColumns() {
  const [cols, setCols] = useState<{ left: number; right: number }>(() => {
    try {
      const raw = localStorage.getItem(COLS_KEY);
      if (raw) {
        const p = JSON.parse(raw);
        if (typeof p.left === "number" && typeof p.right === "number")
          return { left: clampN(p.left, LEFT.min, LEFT.max), right: clampN(p.right, RIGHT.min, RIGHT.max) };
      }
    } catch {
      /* corrupt/absent — fall back to defaults */
    }
    return { left: LEFT.def, right: RIGHT.def };
  });
  useEffect(() => {
    try {
      localStorage.setItem(COLS_KEY, JSON.stringify(cols));
    } catch {
      /* private-mode / quota — layout just won't persist */
    }
  }, [cols]);
  return {
    cols,
    resizeLeft: (dx: number) => setCols((c) => ({ ...c, left: clampN(c.left + dx, LEFT.min, LEFT.max) })),
    resizeRight: (dx: number) => setCols((c) => ({ ...c, right: clampN(c.right - dx, RIGHT.min, RIGHT.max) })),
    resetLeft: () => setCols((c) => ({ ...c, left: LEFT.def })),
    resetRight: () => setCols((c) => ({ ...c, right: RIGHT.def })),
  };
}

/** A draggable vertical divider. Reports per-frame deltas; the parent clamps. Double-click
 *  resets. Pointer capture keeps the drag alive even when the cursor outruns the 7px track. */
function Gutter({ label, onResize, onReset }: { label: string; onResize: (dx: number) => void; onReset: () => void }) {
  const lastX = useRef(0);
  const dragging = useRef(false);
  const down = (e: PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    dragging.current = true;
    lastX.current = e.clientX;
    e.currentTarget.setPointerCapture(e.pointerId);
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    const dx = e.clientX - lastX.current;
    lastX.current = e.clientX;
    if (dx) onResize(dx);
  };
  const up = (e: PointerEvent<HTMLDivElement>) => {
    dragging.current = false;
    e.currentTarget.releasePointerCapture(e.pointerId);
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
  };
  return (
    <div
      className="col-gutter"
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      title="Drag to resize · double-click to reset"
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={up}
      onDoubleClick={onReset}
    >
      <span className="col-gutter-grip" aria-hidden />
    </div>
  );
}

type Item =
  | { kind: "msg"; at: string; role: "user" | "assistant"; content: string; citations: Citation[] | null }
  | { kind: "unlock"; at: string; level: number; via: UnlockVia; quote: string | null; content: string };

const pad = (n: number) => String(n).padStart(2, "0");

// --- left: the problem -------------------------------------------------------
function StatementColumn({ p }: { p: Problem }) {
  const meta = [
    p.slug,
    p.rating ?? "unrated",
    p.time_limit_ms || p.memory_limit_mb
      ? `${p.time_limit_ms ? p.time_limit_ms / 1000 + "s" : ""}${p.memory_limit_mb ? " / " + p.memory_limit_mb + "MB" : ""}`.trim()
      : null,
  ].filter(Boolean);
  const stmtFigures = p.figures.filter((f) => f.section === "statement");
  const noteFigures = p.figures.filter((f) => f.section === "note");
  const constraintLines = (p.constraints_text ?? "").split("\n").map((l) => l.trim()).filter(Boolean);

  return (
    <div className="stmt">
      <div className="stmt-head">
        <div className="stmt-meta">{meta.join(" · ")}</div>
        <h1 className="stmt-title">{p.title || p.external_id}</h1>
      </div>
      <div className="stmt-body">
        {p.statement_text ? <MathText text={p.statement_text} /> : <span className="muted">No statement.</span>}

        {stmtFigures.map((f, i) => (
          <figure key={i} style={{ margin: 0 }}>
            <img className="figure-real" src={f.data_uri} alt={`figure ${i + 1}`} />
            <figcaption className="figure-cap">figure from the statement</figcaption>
          </figure>
        ))}

        {constraintLines.length > 0 && (
          <>
            <span className="section-label">Constraints</span>
            <div className="constraint-list">
              {constraintLines.map((line, i) => (
                <button
                  key={i}
                  className="constraint"
                  onClick={() =>
                    window.dispatchEvent(new CustomEvent("fai-prefill", { detail: `What does "${line}" rule out?` }))
                  }
                >
                  <MathText text={line} />
                </button>
              ))}
            </div>
            <p className="constraint-help">Click any constraint to ask what it rules out.</p>
          </>
        )}

        {p.sample_tests.length > 0 && (
          <>
            <span className="section-label">Sample</span>
            <div className="sample-grid">
              {p.sample_tests.map((s, i) => (
                <Fragment key={i}>
                  <div className="sample-cell">
                    <span className="section-label">input</span>
                    <pre>{s.input}</pre>
                  </div>
                  <div className="sample-cell">
                    <span className="section-label">output</span>
                    <pre>{s.expected_output}</pre>
                  </div>
                </Fragment>
              ))}
            </div>
          </>
        )}

        {p.note_text && (
          <>
            <span className="section-label">Note</span>
            <MathText text={p.note_text} />
          </>
        )}
        {noteFigures.map((f, i) => (
          <figure key={i} style={{ margin: 0 }}>
            <img className="figure-real" src={f.data_uri} alt={`note figure ${i + 1}`} />
            <figcaption className="figure-cap">figure from the note</figcaption>
          </figure>
        ))}
      </div>
    </div>
  );
}

// --- right: the ladder vault -------------------------------------------------
function Vault({
  levelCount,
  revealed,
  chunkCount,
  onReveal,
  revealing,
}: {
  levelCount: number;
  revealed: RevealedLevel[];
  chunkCount: number;
  onReveal: () => void;
  revealing: boolean;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const byLevel = new Map(revealed.map((r) => [r.level, r]));
  const revealedInts = revealed.map((r) => r.level).sort((a, b) => a - b);
  const nextLevel = revealedInts.length < levelCount ? revealedInts.length + 1 : null;
  const earned = revealed.filter((r) => r.via === "chat").length;
  const opened = revealed.filter((r) => r.via === "manual").length;

  const dots = Array.from({ length: levelCount }, (_, i) => (i < revealedInts.length ? "●" : "○")).join("");
  const stateLine =
    revealedInts.length === 0
      ? "Nothing earned yet. Say the idea and it unlocks itself."
      : `${earned} earned by your own reasoning, ${opened} opened. Tap a title to reread it.`;

  const withheld = Array.from({ length: levelCount }, (_, i) => i + 1).filter((l) => !byLevel.has(l));
  const allOpen = withheld.length === 0;

  return (
    <div className="vault">
      <div className="vault-head">
        <div className="vault-head-row">
          <span className="vault-title">Ladder</span>
          <span className="vault-dots">{dots}</span>
        </div>
        <p className="vault-state">{stateLine}</p>
      </div>

      {Array.from({ length: levelCount }, (_, i) => i + 1).map((lvl) => {
        const r = byLevel.get(lvl);
        if (r) {
          const isEarned = r.via === "chat";
          return (
            <div key={lvl} className="rung rung-open">
              <div className="rung-top">
                <span className="rung-num">{pad(lvl)}</span>
                <span className={`rung-state ${isEarned ? "rung-state-earned" : "rung-state-opened"}`}>
                  {isEarned ? "✓ earned · your own words" : "opened by you"}
                </span>
              </div>
              <button className="rung-titlebtn" onClick={() => setExpanded(expanded === lvl ? null : lvl)}>
                {levelTitle(lvl, levelCount)}
              </button>
              {expanded === lvl && (
                <div className="rung-expand">
                  <MathText text={r.content} />
                </div>
              )}
            </div>
          );
        }
        return (
          <div key={lvl} className="rung rung-sealed">
            <div className="rung-top">
              <span className="rung-num">{pad(lvl)}</span>
              <span className="rung-state rung-state-sealed">sealed</span>
            </div>
            <div className="seal" aria-hidden />
            {lvl === nextLevel ? (
              <button className="reveal-btn" onClick={onReveal} disabled={revealing}>
                {revealing ? "opening…" : `open level ${pad(lvl)}`}
              </button>
            ) : (
              <div className="rung-afterhint">opens only after {pad(lvl - 1)}</div>
            )}
          </div>
        );
      })}

      <div className="prompt-panel">
        <span className="section-label">In the prompt</span>
        <div className="prompt-row">
          <span className="k">grounding chunks</span>
          <span className="v">{chunkCount} retrieved</span>
        </div>
        <div className="prompt-row">
          <span className="k">levels in prompt</span>
          <span className="v v-earned">{revealedInts.length ? `{${revealedInts.join(",")}}` : "none"}</span>
        </div>
        <div className="prompt-row">
          <span className="k">levels withheld</span>
          <span className="v v-struck">{withheld.length ? withheld.join(", ") : "none"}</span>
        </div>
        <div className="prompt-row">
          <span className="k">editorial</span>
          <span className="v v-struck">never</span>
        </div>
        <p className="prompt-note">
          {allOpen
            ? "Every level is open. The ladder is complete."
            : `Level${withheld.length > 1 ? "s" : ""} ${withheld.join(", ")} ${
                withheld.length > 1 ? "are" : "is"
              } never placed in the prompt — not the text, not the title.`}
        </p>
      </div>
    </div>
  );
}

// --- page --------------------------------------------------------------------
export function SessionPage() {
  const { slug } = useParams();
  const problem = useProblem(slug ?? null);
  const session = useTutorSession(slug ?? null);

  const { cols, resizeLeft, resizeRight, resetLeft, resetRight } = useSessionColumns();
  const [timeline, setTimeline] = useState<Item[]>([]);
  const [revealed, setRevealed] = useState<RevealedLevel[]>([]);
  const [levelCount, setLevelCount] = useState(0);
  const [chunkCount, setChunkCount] = useState(0);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revealing, setRevealing] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const seeded = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Seed the transcript/rail once from the loaded session, interleaving unlock cards with
  // messages by timestamp (earned cards land between the user msg and the reply).
  useEffect(() => {
    if (!session.data || seeded.current) return;
    const s = session.data;
    const items: Item[] = s.messages.map((m) => ({
      kind: "msg" as const,
      at: m.created_at,
      role: m.role as "user" | "assistant",
      content: m.content,
      citations: m.citations,
    }));
    for (const r of s.revealed) {
      items.push({ kind: "unlock", at: r.unlocked_at ?? "", level: r.level, via: r.via, quote: r.earned_text, content: r.content });
    }
    items.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
    setTimeline(items);
    setRevealed(s.revealed);
    setLevelCount(s.level_count);
    const lastAssistant = [...s.messages].reverse().find((m) => m.role === "assistant");
    setChunkCount(lastAssistant?.citations?.length ?? 0);
    seeded.current = true;
  }, [session.data]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [timeline, streaming]);

  // Left-column constraint clicks prefill the composer.
  useEffect(() => {
    const handler = (e: Event) => setInput((e as CustomEvent).detail);
    window.addEventListener("fai-prefill", handler);
    return () => window.removeEventListener("fai-prefill", handler);
  }, []);

  const readyProblem = problem.data?.ingestion_status === "ready";

  async function onReveal() {
    if (!slug) return;
    setRevealing(true);
    setErrMsg(null);
    try {
      const r = await revealNextLevel(slug);
      const rung: RevealedLevel = {
        level: r.level,
        content: r.content,
        via: "manual",
        earned_text: null,
        earned_message_id: null,
        unlocked_at: new Date().toISOString(),
      };
      setRevealed((prev) => [...prev, rung]);
      setTimeline((prev) => [...prev, { kind: "unlock", at: rung.unlocked_at!, level: r.level, via: "manual", quote: null, content: r.content }]);
    } catch (e) {
      setErrMsg(e instanceof ApiError ? e.message : "Couldn't open the next level.");
    } finally {
      setRevealing(false);
    }
  }

  async function onSend() {
    const content = input.trim();
    if (!content || busy || !slug) return;
    setInput("");
    setTimeline((prev) => [...prev, { kind: "msg", at: new Date().toISOString(), role: "user", content, citations: null }]);
    setStreaming("");
    setBusy(true);
    setErrMsg(null);

    let acc = "";
    let citations: Citation[] | null = null;
    let unlockedLevel: number | null = null;
    let matched = "";
    await streamTutorMessage(slug, content, {
      onToken: (t) => {
        acc += t;
        setStreaming(acc);
      },
      onUnlock: (level, _via, matchedMessage) => {
        unlockedLevel = level;
        matched = matchedMessage;
      },
      onDone: (info) => {
        citations = info.citations?.length ? info.citations : null;
        setChunkCount(info.chunk_count);
      },
      onError: (detail) => setErrMsg(detail),
    });

    // A rung earned via chat: fetch its (now-unlocked) content and drop an earned card in
    // before the reply, then update the rail.
    if (unlockedLevel != null) {
      try {
        const fresh = await getTutorSession(slug);
        setRevealed(fresh.revealed);
        const rung = fresh.revealed.find((r) => r.level === unlockedLevel);
        if (rung) {
          setTimeline((prev) => [
            ...prev,
            { kind: "unlock", at: new Date().toISOString(), level: rung.level, via: "chat", quote: matched, content: rung.content },
          ]);
        }
      } catch {
        /* rail syncs on next load */
      }
    }
    if (acc.trim()) {
      setTimeline((prev) => [...prev, { kind: "msg", at: new Date().toISOString(), role: "assistant", content: acc, citations }]);
    }
    setStreaming(null);
    setBusy(false);
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  }

  // Not-ready / error states — a plain notice, not the three-column layout.
  if (session.isPending || problem.isPending) {
    return <div className="plain-page"><p className="muted">Loading the session…</p></div>;
  }
  if (session.error) {
    const code = session.error instanceof ApiError ? session.error.code : "";
    const msg =
      code === "no_levels"
        ? "This problem has no hint ladder yet — add the editorial link on the add-problem page and re-ingest."
        : code === "problem_not_ready"
          ? "This problem is still being ingested. It'll open as a session once it's ready."
          : code === "problem_not_found"
            ? "No problem with that id."
            : "Couldn't load the session.";
    return (
      <div className="plain-page">
        <div className="error-box">{msg}</div>
        <p style={{ marginTop: 16 }}>
          <Link to="/problems/new" className="btn btn-outline">add a problem</Link>
        </p>
      </div>
    );
  }

  return (
    <div className="session-scroll">
      <div
        className="session"
        style={{ gridTemplateColumns: `${cols.left}px 7px minmax(360px, 1fr) 7px ${cols.right}px` }}
      >
        {problem.data && <StatementColumn p={problem.data} />}

        <Gutter label="Resize the problem panel" onResize={resizeLeft} onReset={resetLeft} />

        <section className="chat">
          <div className="chat-scroll">
            <div className="chat-inner">
              {timeline.length === 0 && !streaming && (
                <div className="msg msg-tutor">
                  What have you tried, or what's confusing? Tell me where you're stuck and we'll work
                  from there — I won't hand you the answer.
                </div>
              )}
              {timeline.map((it, i) =>
                it.kind === "msg" ? (
                  it.role === "assistant" ? (
                    <div key={i} className="msg msg-tutor">
                      <MathText text={it.content} />
                      {it.citations && it.citations.length > 0 && (
                        <div className="cites">
                          {it.citations.map((c, j) => (
                            <a key={j} className="cite" href={c.url} target="_blank" rel="noreferrer">
                              <b>[{j + 1}]</b> {c.source}
                              {c.title ? ` · ${c.title}` : ""}
                              {c.page ? `, p.${c.page}` : ""}
                            </a>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div key={i} className="msg msg-user">
                      <MathText text={it.content} />
                    </div>
                  )
                ) : (
                  <div key={i} className={`unlock ${it.via === "chat" ? "unlock-earned" : "unlock-opened"}`}>
                    <div className="unlock-badge">
                      {it.via === "chat" ? `✓ you figured out level ${it.level} yourself` : `level ${it.level} opened`}
                    </div>
                    {it.via === "chat" && it.quote && <div className="unlock-quote">“{it.quote}”</div>}
                    <div className="unlock-title">{levelTitle(it.level, levelCount)}</div>
                    <div className="unlock-body">
                      <MathText text={it.content} />
                    </div>
                  </div>
                ),
              )}
              {streaming !== null && (
                <div className="msg msg-tutor">
                  {streaming ? <MathText text={streaming} /> : <span className="thinking">reading your message…</span>}
                </div>
              )}
              {errMsg && <div className="error-box">{errMsg}</div>}
              <div ref={bottomRef} />
            </div>
          </div>

          <div className="composer">
            <div className="composer-inner">
              <div className="chips">
                {CHIPS.map((c) => (
                  <button key={c} className="chip" onClick={() => setInput(c)}>
                    {c}
                  </button>
                ))}
              </div>
              <div className="composer-input">
                <textarea
                  rows={2}
                  placeholder="Say what you are thinking."
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={onKey}
                  disabled={busy || !readyProblem}
                />
                <button className="composer-send" onClick={onSend} disabled={busy || !input.trim() || !readyProblem}>
                  send
                </button>
              </div>
              <p className="composer-hint">
                enter to send · a conservative judge unlocks only the level you actually demonstrate
              </p>
            </div>
          </div>
        </section>

        <Gutter label="Resize the ladder panel" onResize={resizeRight} onReset={resetRight} />

        <Vault levelCount={levelCount} revealed={revealed} chunkCount={chunkCount} onReveal={onReveal} revealing={revealing} />
      </div>
    </div>
  );
}
