# Spotr

A self-hosted Socratic tutor for competitive programmers who want to upsolve: understand a
problem you couldn't crack, without being handed the answer.

Most AI help just prints the solution, which teaches nothing. Spotr runs a conversation that
never reveals more than you've earned. It asks questions, unlocks the next hint only when you
show you've reached it yourself, and grounds its explanations in references you choose, with
citations.

You run it yourself with Docker and bring your own LLM key. Your data stays on your machine.

## Why it's different

The core idea is a hint ladder that physically cannot spoil. The tutor is only ever given the
rungs you have already unlocked. Un-revealed hint text is never placed in the model's prompt,
so it cannot leak no matter how you ask. The guarantee is structural, not a request to the
model, and an adversarial eval measures it (see below).

You unlock rungs one at a time, or a conservative judge unlocks the next rung automatically
when your message shows you understood it. The ladder length adapts to the problem: a simple
problem gets a few rungs, a layered one gets more.

Grounding is yours to control. Add a public GitHub repo or a PDF and Spotr chunks it, embeds
it, and cites it inline during a session. Nothing is bundled, and nothing leaves your machine.

## Quickstart

You need Docker and an LLM API key (the default configuration targets a free tier).

Whole stack, one command:

```bash
cp backend/.env.example backend/.env    # put your key in OPENAI_API_KEY
docker compose up -d --build
```

Frontend on http://localhost:8080, API on http://localhost:8000. Migrations run automatically;
the API and a worker container start after them.

Dev mode with hot reload (needs Python 3.14 with [uv](https://docs.astral.sh/uv/) and Node 20):

```bash
docker compose up -d postgres redis qdrant
cd backend && cp .env.example .env && uv sync && uv run alembic upgrade head
uv run uvicorn app.main:app --reload    # runs the worker inline
cd ../frontend && npm install && npm run dev
```

On Windows, `.\dev.ps1` opens all three panes at once.

## LLM configuration

Spotr talks to any OpenAI-compatible endpoint through the OpenAI SDK, so switching providers is
a `.env` change with no code change. Work is split into five tasks: normalize, hints, chat,
judge, and embedding. Set one global key and all five use it; add a per-task override to route
one task somewhere else. Each task needs different provider features (vision for normalize,
streaming for chat, an embeddings endpoint for embedding), which is why routing is per task.

The embedding model fixes a vector dimension. `EMBEDDING_DIM` must match it, and the Qdrant
collection name encodes the model, so changing the model uses a fresh collection rather than
mixing vector spaces.

Check that a key works end to end with `uv run python check_llm.py`.

## How it works

- Problem input is yours. Paste the statement or drop a screenshot, and a vision pass rebuilds
  the text and the math that copy-paste destroys. Spotr does not scrape problem pages.
- Ingestion is a durable job: the `problems` row is the queue. A worker claims pending rows
  with `SELECT ... FOR UPDATE SKIP LOCKED`, and a job whose worker died mid-run is re-queued
  after a timeout, so nothing is lost to a restart. There is no external broker. Live status
  reaches the browser over SSE fed by Postgres `LISTEN/NOTIFY`.
- Hint content lives in Postgres, gated by which rungs you have unlocked. Grounding lives in
  Qdrant, retrieved by similarity. The two are kept separate on purpose.
- Agent state lives in Postgres and is rebuilt per request, so it survives restarts.

## The no-leakage eval

`evals/leakage_eval.py` puts a problem at a low reveal state, attacks the real tutor with
prompts designed to extract the answer (roleplay, "ignore your instructions", "just give me the
code"), and has a judge decide whether any reply exposed a hidden rung. It reports a leakage
rate.

```bash
uv run python -m evals.leakage_eval
uv run python -m evals.leakage_eval --limit 3
```

## Tech stack

Backend: FastAPI, SQLAlchemy 2.0 and Alembic on PostgreSQL, Redis, Qdrant, the OpenAI SDK, and
optional Langfuse tracing. Frontend: React, Vite, TypeScript, TanStack Query, KaTeX. Auth: JWT
access and refresh tokens. Tests: pytest, with external services mocked so CI needs no network.

## License

The application code is open source under an MIT/Apache-style license. Sources you add (a repo
or a PDF) are your responsibility: because Spotr is self-hosted you are the admin, and the
add-source form asks you to confirm you have the right to use what you add. Your sources are
embedded into your own local Qdrant and never leave your machine.
