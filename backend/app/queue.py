"""Durable ingestion job queue — the `problems` row *is* the queue.

Ingestion is 30–90s of API calls + LLM work, so it can't run inside the request. Instead of
Celery + a broker, the job state lives in the `Problem.ingestion_status` column and a worker
process claims work directly from Postgres:

- **Claim** a `pending` row with `SELECT … FOR UPDATE SKIP LOCKED` — multiple workers can run
  without ever grabbing the same job, and an unclaimed job just waits in the table.
- **Crash recovery**: a row left in an in-flight state past a timeout (its worker died) is
  re-queued (or failed after `max_attempts`). Nothing is lost to a restart or a crash — the
  durability a broker would give us, with one fewer moving part.
- **Wake-ups** ride Postgres `LISTEN/NOTIFY`: enqueue fires `ingest_jobs` so an idle worker
  picks up instantly, and each status change fires `problem_status` so the SSE endpoint can
  push live progress to the browser (replacing frontend polling).

All NOTIFY emits are best-effort (a NOTIFY failure, or sqlite in tests, must never break a
request or an ingest). Timestamps are set in Python so the claim/recovery logic is portable
to the sqlite test engine, where `FOR UPDATE` is a no-op.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import psycopg
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import engine
from app.models import GroundingSource, Problem

# Postgres NOTIFY channels (hardcoded constants — never interpolate user input into LISTEN).
INGEST_CHANNEL = "ingest_jobs"       # fired on enqueue → wakes an idle worker (problems + sources)
STATUS_CHANNEL = "problem_status"    # fired on every status change → feeds the SSE endpoint
SOURCE_STATUS_CHANNEL = "source_status"  # grounding-source status changes → its SSE endpoint

# The two claimed/in-flight states. `pending` waits to be claimed; `ready`/`failed` are terminal.
IN_FLIGHT_STATES = ("normalizing", "generating_levels")
# Grounding-source in-flight states (same durable-queue pattern; see GroundingSource).
SOURCE_IN_FLIGHT_STATES = ("cloning", "embedding")


# --- NOTIFY (best-effort) ----------------------------------------------------
async def _emit(channel: str, payload: str) -> None:
    """Fire a NOTIFY on its own short-lived connection. Swallows every error: on sqlite
    (tests) `pg_notify` doesn't exist, and a transient DB blip must not fail the caller."""
    try:
        async with engine.begin() as conn:  # begin() commits on exit → the NOTIFY is delivered
            await conn.execute(text("SELECT pg_notify(:c, :p)"), {"c": channel, "p": payload})
    except Exception:
        pass


async def notify_job_enqueued(problem_id: int) -> None:
    await _emit(INGEST_CHANNEL, str(problem_id))


async def notify_problem_status(problem_id: int) -> None:
    await _emit(STATUS_CHANNEL, str(problem_id))


async def notify_source_enqueued(source_id: int) -> None:
    # Sources share the worker's wake channel, so an enqueued source wakes the same idle worker.
    await _emit(INGEST_CHANNEL, f"source:{source_id}")


async def notify_source_status(source_id: int) -> None:
    await _emit(SOURCE_STATUS_CHANNEL, str(source_id))


# --- LISTEN ------------------------------------------------------------------
@contextlib.asynccontextmanager
async def pg_listen(channel: str) -> AsyncIterator[psycopg.AsyncConnection]:
    """Open a dedicated autocommit connection LISTENing on `channel`. A LISTEN holds a
    connection open, so this is deliberately separate from the SQLAlchemy pool. Consume
    notifications via `conn.notifies(timeout=…, stop_after=…)`."""
    # Raw psycopg wants the plain postgresql:// DSN, not the SQLAlchemy +psycopg form.
    conn = await psycopg.AsyncConnection.connect(get_settings().database_url, autocommit=True)
    try:
        await conn.execute(f"LISTEN {channel}")
        yield conn
    finally:
        await conn.close()


async def wait_for_notification(conn: psycopg.AsyncConnection, *, timeout: float) -> list[str]:
    """Block up to `timeout` seconds for notifications, returning their payloads (possibly
    empty on timeout). `stop_after=1` returns as soon as the first arrives."""
    payloads: list[str] = []
    async for note in conn.notifies(timeout=timeout, stop_after=1):
        payloads.append(note.payload)
    return payloads


# --- enqueue / claim / recover ----------------------------------------------
async def enqueue_ingestion(db: AsyncSession, problem: Problem) -> None:
    """Mark a problem `pending` so a worker will claim it, and clear any prior claim/error.
    Committing the `pending` status IS the enqueue. Callers fire `notify_job_enqueued` after
    (kept separate so it's an easy test seam and so the enqueue itself never depends on NOTIFY)."""
    problem.ingestion_status = "pending"
    problem.ingestion_error = None
    problem.claimed_at = None
    await db.commit()


async def claim_next_job(db: AsyncSession) -> int | None:
    """Atomically claim one pending job. `FOR UPDATE SKIP LOCKED` lets concurrent workers
    each grab a *different* row without blocking. Returns the claimed problem id, or None
    when the queue is empty."""
    problem = await db.scalar(
        select(Problem)
        .where(Problem.ingestion_status == "pending")
        .order_by(Problem.id)  # FIFO-ish
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if problem is None:
        return None
    problem.ingestion_status = "normalizing"  # first in-flight state
    problem.claimed_at = datetime.now(timezone.utc)
    problem.attempts += 1
    await db.commit()
    await notify_problem_status(problem.id)
    return problem.id


async def recover_stuck_jobs(
    db: AsyncSession, *, stuck_timeout_seconds: int, max_attempts: int
) -> list[int]:
    """Re-queue jobs whose worker died mid-run — rows stuck in an in-flight state with a
    claim older than the timeout. Past `max_attempts` a job is failed instead of retried
    forever (it may be the thing crashing the worker). Returns the affected problem ids."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stuck_timeout_seconds)
    stuck = (
        await db.scalars(
            select(Problem)
            .where(
                Problem.ingestion_status.in_(IN_FLIGHT_STATES),
                Problem.claimed_at.is_not(None),
                Problem.claimed_at < cutoff,
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    for problem in stuck:
        if problem.attempts >= max_attempts:
            problem.ingestion_status = "failed"
            problem.ingestion_error = (
                "Ingestion didn't finish after several attempts — the worker may have "
                "crashed or restarted mid-job. Re-ingest to try again."
            )
        else:
            problem.ingestion_status = "pending"  # back in the queue
            problem.claimed_at = None
    if stuck:
        await db.commit()
        for problem in stuck:
            await notify_problem_status(problem.id)
    return [problem.id for problem in stuck]


# --- grounding-source queue (same pattern, its own table) --------------------
async def enqueue_source(db: AsyncSession, source: GroundingSource) -> None:
    """Mark a grounding source `pending` for the worker; caller fires `notify_source_enqueued`."""
    source.status = "pending"
    source.error = None
    source.claimed_at = None
    await db.commit()


async def claim_next_source(db: AsyncSession) -> int | None:
    """Atomically claim one pending grounding source. Returns its id, or None if none pending."""
    source = await db.scalar(
        select(GroundingSource)
        .where(GroundingSource.status == "pending")
        .order_by(GroundingSource.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if source is None:
        return None
    source.status = "cloning"  # first in-flight state
    source.claimed_at = datetime.now(timezone.utc)
    source.attempts += 1
    await db.commit()
    await notify_source_status(source.id)
    return source.id


async def recover_stuck_sources(
    db: AsyncSession, *, stuck_timeout_seconds: int, max_attempts: int
) -> list[int]:
    """Re-queue (or fail) grounding sources whose worker died mid-ingest."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stuck_timeout_seconds)
    stuck = (
        await db.scalars(
            select(GroundingSource)
            .where(
                GroundingSource.status.in_(SOURCE_IN_FLIGHT_STATES),
                GroundingSource.claimed_at.is_not(None),
                GroundingSource.claimed_at < cutoff,
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    for source in stuck:
        if source.attempts >= max_attempts:
            source.status = "failed"
            source.error = "Ingestion didn't finish after several attempts. Remove and re-add to retry."
        else:
            source.status = "pending"
            source.claimed_at = None
    if stuck:
        await db.commit()
        for source in stuck:
            await notify_source_status(source.id)
    return [source.id for source in stuck]
