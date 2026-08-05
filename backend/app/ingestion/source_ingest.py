"""Process one user-added grounding source: clone/parse → chunk → embed → Qdrant.

The worker calls `ingest_source(source_id)` for each claimed `GroundingSource` row. Like the
problem pipeline, it opens its OWN sessions and NEVER raises — every failure lands as
`status="failed"` + an actionable `error`, and each transition fires a NOTIFY so the SSE
status endpoint pushes progress. Reuses the corpus embed/upsert machinery in
`ingestion.grounding` (idempotent + resumable, so a rate-limited run continues on retry).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.database import async_session_maker
from app.ingestion.grounding import ingest_corpus, loader_for_source
from app.llm import LLMRateLimitError
from app.models import GroundingChunk, GroundingSource
from app.queue import notify_source_status

logger = logging.getLogger("spotr.grounding")


async def _schedule_retry(
    session_maker: async_sessionmaker[AsyncSession],
    source_id: int,
    *,
    delay_seconds: float,
    error: str,
    reset_attempts: bool = False,
) -> None:
    """Park a source in `retrying` with a future `retry_at`; the worker re-claims it once due,
    and resume skips already-embedded chunks. `reset_attempts` keeps a quota wait (external,
    not the job's fault) from counting toward the error-retry cap."""
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
    async with session_maker() as db:
        src = await db.get(GroundingSource, source_id)
        if src is None:
            return
        src.status = "retrying"
        src.error = error[:1000]
        src.retry_at = retry_at
        if reset_attempts:
            src.attempts = 0
        await db.commit()
    await notify_source_status(source_id)


async def _set_status(
    session_maker: async_sessionmaker[AsyncSession],
    source_id: int,
    status: str,
    *,
    error: str | None = None,
    chunk_count: int | None = None,
) -> None:
    async with session_maker() as db:
        src = await db.get(GroundingSource, source_id)
        if src is None:
            return
        src.status = status
        if error is not None:
            src.error = error[:1000]
        if chunk_count is not None:
            src.chunk_count = chunk_count
        await db.commit()
    await notify_source_status(source_id)


async def ingest_source(
    source_id: int, *, session_maker: async_sessionmaker[AsyncSession] = async_session_maker
) -> None:
    """Clone/parse, embed, and upsert a grounding source. Never raises."""
    # Load the row and build its loader while attached (needs kind/source_key/location/label).
    async with session_maker() as db:
        src = await db.get(GroundingSource, source_id)
        if src is None:
            return
        try:
            loader = loader_for_source(src)
        except ValueError as exc:
            src.status = "failed"
            src.error = str(exc)[:1000]
            await db.commit()
            await notify_source_status(source_id)
            return
        source_key = src.source_key

    try:
        # Cloning a repo (or locating an uploaded PDF) is blocking — keep it off the event loop.
        local = await asyncio.to_thread(loader.ensure_local)
        await _set_status(session_maker, source_id, "embedding")

        # loader.load(local) is a generator; ingest_corpus embeds + upserts, resumable.
        await ingest_corpus(loader, session_maker=session_maker, chunks=loader.load(local))

        async with session_maker() as db:
            total = await db.scalar(
                select(func.count())
                .select_from(GroundingChunk)
                .where(GroundingChunk.source == source_key)
            )
        await _set_status(session_maker, source_id, "ready", error="", chunk_count=total or 0)
    except LLMRateLimitError as exc:
        # Quota/rate-limit during embedding — not a bad job, just out of budget. Reschedule far
        # out (free-tier daily reset) and don't count it toward the error-retry cap. Already-
        # embedded chunks are saved, so the retry resumes rather than restarting.
        settings = get_settings()
        logger.warning("grounding source %s rate-limited; retrying in ~%ss", source_id, settings.source_ratelimit_retry_seconds)
        await _schedule_retry(
            session_maker,
            source_id,
            delay_seconds=settings.source_ratelimit_retry_seconds,
            error=f"Embedding hit the rate limit / daily quota — will retry automatically once it resets. ({exc})",
            reset_attempts=True,
        )
    except Exception as exc:  # noqa: BLE001 — any other failure: back off a few times, then fail
        logger.exception("grounding source %s failed", source_id)
        settings = get_settings()
        async with session_maker() as db:
            row = await db.get(GroundingSource, source_id)
            attempts = row.attempts if row else settings.source_max_error_attempts
        detail = str(exc) or exc.__class__.__name__
        if attempts >= settings.source_max_error_attempts:
            await _set_status(
                session_maker, source_id, "failed",
                error=f"{detail} (gave up after {attempts} attempts)",
            )
        else:
            await _schedule_retry(
                session_maker,
                source_id,
                delay_seconds=settings.source_error_retry_seconds,
                error=f"{detail} — will retry automatically (attempt {attempts}).",
            )
