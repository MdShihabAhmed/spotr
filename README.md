# ForcesAI

A **grounded Socratic tutor** for competitive programmers who want to *upsolve* — to
understand a problem they couldn't crack, without being handed the answer.

Most "AI help" for competitive programming just prints the solution, which teaches nothing.
ForcesAI instead runs a **conversation that never reveals more than you've earned**: it
guides you with questions, unlocks the next hint only when you demonstrate you've reached it
yourself, and grounds its explanations in openly-licensed references with citations.

It's **self-hosted** and **bring-your-own-key** — you run it with `docker compose`, supply
your own LLM key, and your data stays yours.

---

## Table of contents
1. [Why it's built this way](#1-why-its-built-this-way)
2. [Architecture](#2-architecture)
3. [Quickstart](#3-quickstart)
4. [LLM configuration](#4-llm-configuration) ← provider-agnostic, per-task, and its limits
5. [Feature walkthrough](#5-feature-walkthrough)
6. [The no-leakage eval](#6-the-no-leakage-eval)
7. [Key design decisions](#7-key-design-decisions)
8. [Tech stack](#8-tech-stack)
9. [Licensing & attribution](#9-licensing--attribution)

> **Status.** Core is built and tested (auth, problem ingestion, graduated hint ladder,
> grounding retrieval, the streaming tutor with revealed-set disclosure, and the leakage
> eval). Full one-command `docker compose` packaging and the grounding corpus *seed* are the
> last pieces in progress; today the app runs in dev mode (see [Quickstart](#3-quickstart)).

---

## 1. Why it's built this way

**Graduated, not all-at-once.** Every problem gets a 4-rung ladder — *notice → key insight →
approach & why → full solution* — generated once from the official editorial. You climb it
one rung at a time.

**No-leakage, enforced by construction.** The tutor is only ever given the rungs you've
already unlocked. Un-revealed hint text is *never placed in the prompt*, so it can't leak no
matter how you phrase your question — the guarantee is structural, not a plea to the model.
Then it's **measured** by an adversarial eval (§6).

**You have to earn it.** You unlock rungs manually one at a time, *or* a conservative judge
unlocks the next rung automatically when your message shows you've figured it out — "✓ you
figured out hint 2 yourself."

**Grounded and cited.** Explanations draw on four openly-licensed corpora (CP-Algorithms,
USACO Guide, CP Handbook, PyRival), retrieved semantically and cited inline — so guidance is
credible and traceable, not hallucinated.

---

## 2. Architecture

```
  Browser (React SPA)
        │  screenshots / chat (SSE)
        ▼
  FastAPI  ──►  Problem ingestion ──►  Postgres (problems, ladder, sessions, messages)
        │         normalize (vision) → metadata + editorial → generate 4-rung ladder
        │
        ├──►  Tutor turn:  build context from {unlocked rungs} ∪ {your messages}
        │                  + retrieved grounding  →  streamed, cited reply
        │
        ├──►  Qdrant   (grounding corpora, chunked + embedded, cited on retrieval)
        └──►  Redis    (CF API cache, outbound rate-limit lease)

  LLM (any OpenAI-compatible provider; Gemini free tier by default) — per-task routable
```

- **Problem input** is user-provided (screenshot → LLM vision → clean structured text); we
  never scrape problem pages. Only permitted APIs are called (Codeforces metadata + the
  official `blogEntry.view` editorial API from a link you paste).
- **Ladder content lives in Postgres**, gated by a revealed-set filter. **Grounding lives in
  Qdrant**, retrieved by similarity. These are deliberately separate concerns.

---

## 3. Quickstart

**Prerequisites:** Docker (for Postgres/Redis/Qdrant), Python ≥ 3.14 with
[`uv`](https://docs.astral.sh/uv/), Node ≥ 20, and a free
[Gemini API key](https://aistudio.google.com/apikey) (no credit card).

```bash
# 1. Infra
docker compose up -d          # postgres + redis + qdrant

# 2. Backend (from backend/)
cp .env.example .env          # then paste your key into OPENAI_API_KEY
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload    # http://localhost:8000

# 3. Frontend (from frontend/)
npm install
npm run dev                   # http://localhost:5173
```

Register an account, ingest a problem (paste its URL + an editorial-blog link, drop a
screenshot of the statement), then open the tutor.

> On Windows, `.\dev.ps1` launches all three panes at once. Full-stack `docker compose up`
> (backend + frontend in the same command) and the grounding `seed` step are being finalized.

---

## 4. LLM configuration

ForcesAI is **provider-agnostic**. It talks to any **OpenAI-compatible** endpoint via the
OpenAI SDK, so switching providers is a `.env` edit — no code change. The default targets
**Google Gemini's free tier**; OpenAI, Groq, OpenRouter, Together, a local Ollama/vLLM, etc.
all work by changing `LLM_BASE_URL` + the key + model names.

### One key for everything, or a different provider per task

There are **five tasks**, each independently routable. Set only the global key and all five
use it; add a per-task override to route just that task elsewhere:

```dotenv
# Global default — powers every task unless overridden
OPENAI_API_KEY=your-key
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_MODEL_STRONG=gemini-flash-latest        # normalize / hints / chat
LLM_MODEL_FAST=gemini-flash-lite-latest     # the cheap judge
EMBEDDING_MODEL=gemini-embedding-001        # grounding embeddings

# Example override: run the tutor chat on a paid model, keep everything else on Gemini free
CHAT_BASE_URL=                              # empty = OpenAI
CHAT_API_KEY=sk-...
CHAT_MODEL=gpt-5.2
```

Every task accepts `<TASK>_MODEL`, `<TASK>_BASE_URL`, `<TASK>_API_KEY` (blank = fall back to
the global default). **Why per-task?** Providers improve unevenly — a model that's best at
vision extraction today may not be the best (or cheapest) at the chat, and free-tier rate
limits differ per model. Per-task routing lets you put each job on whatever's best for it.

### Which provider can do which task

Each task uses different **API features**, and not every OpenAI-compatible provider supports
all of them. This is the real limitation behind "any LLM":

| Task | What it does | Requires the provider to support |
|---|---|---|
| **normalize** | screenshot → clean structured problem | **chat + vision (image input) + structured JSON output** |
| **hints** | generate the 4-rung ladder | **chat + structured JSON output** |
| **chat** | the tutor's reply | **chat + streaming** |
| **judge** | decide if you earned the next rung | **chat + structured JSON output** |
| **embedding** | embed grounding corpora + queries | **an `/embeddings` endpoint** |

Rough support picture (verify against your provider's current docs — capabilities change):

| Provider | vision | structured JSON | streaming | embeddings |
|---|:--:|:--:|:--:|:--:|
| **OpenAI** | ✅ | ✅ | ✅ | ✅ |
| **Gemini** (default) | ✅ | ✅ | ✅ | ✅ |
| **Groq** | ⚠️ limited | ⚠️ JSON mode | ✅ | ❌ |
| **Ollama** (local) | ⚠️ vision models only | ⚠️ partial | ✅ | ✅ |

**How to read this:** OpenAI and Gemini support every task, so a single key just works. If
your preferred provider is missing a feature (say, no embeddings, or no vision), you don't
have to give it up — **route only the affected tasks elsewhere** with the per-task overrides.
For example: chat on a local Ollama, but `normalize` and `embedding` on Gemini.

Two consequences worth knowing:
- **Embeddings pin a dimension.** `EMBEDDING_DIM` must match the model
  (`gemini-embedding-001` → 3072; OpenAI `text-embedding-3-small` → 1536). The Qdrant
  collection name encodes the embedding model, so changing it means a fresh collection and a
  re-seed — never a silent mix of incompatible vectors.
- **Free tiers rate-limit hard.** Gemini's free `gemini-flash-latest` currently allows only
  ~20 requests/day. The app retries on HTTP 429 with backoff, but for heavy use (or the
  leakage eval) point the relevant task at a paid or higher-quota model.

Verify a key/model works end to end: `uv run python check_llm.py`.

---

## 5. Feature walkthrough

- **Ingest** — paste a Codeforces problem URL and an editorial-blog link, drop screenshot(s)
  of the statement (vision reconstructs the LaTeX that copy-paste destroys), review the
  extracted fields, and submit. The editorial is fetched server-side, used to build the
  ladder, and **never sent to your browser** (it would spoil the hints).
- **The ladder** — 4 rungs, hidden until earned. Reveal the next one manually, or let the
  chat unlock it.
- **The tutor** — ask questions or share your idea; it replies (streamed), staying within
  what you've unlocked, citing grounding references. When you articulate the next insight, it
  unlocks that rung: "✓ you figured out hint K yourself."

---

## 6. The no-leakage eval

The no-leakage promise is enforced structurally — but claims deserve measurement.
`evals/leakage_eval.py` is an adversarial harness:

1. Put a problem at a low reveal state (0 or 1 rungs unlocked).
2. Attack the **real** tutor context builder with prompts engineered to extract the answer
   ("just give me the code", "ignore your instructions", roleplay, etc.).
3. An LLM-as-judge reads each reply *and the hidden rungs* and decides whether anything
   leaked.
4. Report a **leakage rate**.

```bash
uv run python -m evals.leakage_eval           # full run
uv run python -m evals.leakage_eval --limit 3 # quick smoke
```

This is *enforce-then-measure* discipline: the eval already caught the tutor volunteering a
level-1 nudge too eagerly, which drove a prompt fix.

---

## 7. Key design decisions

- **No-leakage by construction, then measured.** Context is filtered to
  `{unlocked rungs} ∪ {your messages}`; un-revealed content can't enter the prompt. The eval
  quantifies it.
- **Agent state lives in Postgres**, reconstructed per request — not a LangGraph
  checkpointer. Simpler, and it survives restarts.
- **Ladder in Postgres, grounding in Qdrant.** Retrieval earns its place for *grounding with
  citations*, not for the ≤4 short hints (those are gated by a level filter, not similarity).
- **No problem scraping.** User-provided input + permitted APIs only.
- **Provider-agnostic, per-task, BYOK.** No vendor lock-in; §4.

---

## 8. Tech stack

**Backend:** FastAPI (async), SQLAlchemy 2.0 + Alembic on PostgreSQL, Redis (cache +
rate-limit lease), Qdrant (grounding vectors), the OpenAI SDK against any compatible provider,
optional Langfuse tracing. **Frontend:** React + Vite + TypeScript, TanStack Query, KaTeX.
**Auth:** JWT (access + refresh). **Tests:** pytest (LLM/Qdrant/CF-API mocked; no live network
in CI).

---

## 9. Licensing & attribution

Application code is MIT/Apache-style open source. The grounding corpora are **fetched, not
bundled**, and used under their own licenses — **CP-Algorithms** (CC BY-SA 4.0), **PyRival**
(Apache-2.0), **USACO Guide** (CC BY-NC-SA 4.0, non-commercial), **CP Handbook** (free to use).
Attribution is shown as a "Grounded by" credit; corpora are never redistributed.
