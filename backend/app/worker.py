"""The ingestion worker: claims durable jobs from Postgres and runs the pipeline.

Runs two ways:
- **Inline** in the API process (dev convenience) when `RUN_INLINE_WORKER=true` — `main.py`
  starts `run_worker` as a background task on startup.
- **Standalone** as its own container (`python -m app.worker`) in the compose stack, with
  `RUN_INLINE_WORKER=false` on the API — one fewer thing sharing the web process, and you can
  scale workers independently. Multiple workers are safe: `FOR UPDATE SKIP LOCKED` guarantees
  no two claim the same job.

The loop drains every ready job, then waits on a `LISTEN ingest_jobs` NOTIFY (with a short
poll interval as a safety net that also drives periodic crash recovery).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.database import async_session_maker
from app.ingestion.pipeline import run_ingestion
from app.ingestion.source_ingest import ingest_source
from app.queue import (
    INGEST_CHANNEL,
    claim_next_job,
    claim_next_source,
    pg_listen,
    recover_stuck_jobs,
    recover_stuck_sources,
    wait_for_notification,
)
from app.models import GroundingSource, Problem  # noqa: F401 — configure mappers before a claim

logger = logging.getLogger("spotr.worker")


async def drain_queue(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    process=run_ingestion,
    stuck_timeout_seconds: int,
    max_attempts: int,
) -> int:
    """Recover crashed jobs, then claim-and-process until the queue is empty. Each claim and
    each processing run uses its OWN session (a job may take a minute; we don't hold a
    transaction open across it). Returns how many jobs were processed."""
    async with session_maker() as db:
        await recover_stuck_jobs(
            db, stuck_timeout_seconds=stuck_timeout_seconds, max_attempts=max_attempts
        )

    processed = 0
    while True:
        async with session_maker() as db:
            problem_id = await claim_next_job(db)
        if problem_id is None:
            return processed
        # run_ingestion opens its own session and never raises (failures → status=failed).
        await process(problem_id, session_maker=session_maker)
        processed += 1


async def drain_sources(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    process=ingest_source,
    stuck_timeout_seconds: int,
    max_attempts: int,
) -> int:
    """Recover crashed grounding-source jobs, then claim-and-process until none remain. Same
    durable-queue shape as `drain_queue`, over the `grounding_sources` table."""
    async with session_maker() as db:
        await recover_stuck_sources(
            db, stuck_timeout_seconds=stuck_timeout_seconds, max_attempts=max_attempts
        )

    processed = 0
    while True:
        async with session_maker() as db:
            source_id = await claim_next_source(db)
        if source_id is None:
            return processed
        await process(source_id, session_maker=session_maker)
        processed += 1


async def run_worker(
    *,
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    settings: Settings | None = None,
    stop_event: asyncio.Event | None = None,
    process=run_ingestion,
) -> None:
    """Long-running loop: drain the queue, then sleep on a `LISTEN ingest_jobs` NOTIFY until
    the next job arrives (or the poll interval elapses). Runs until `stop_event` is set."""
    s = settings or get_settings()
    stop = stop_event or asyncio.Event()
    logger.info("ingestion worker starting (poll=%ss)", s.worker_poll_interval_seconds)

    async with pg_listen(INGEST_CHANNEL) as conn:
        while not stop.is_set():
            try:
                n = await drain_queue(
                    session_maker,
                    process=process,
                    stuck_timeout_seconds=s.worker_stuck_timeout_seconds,
                    max_attempts=s.worker_max_attempts,
                )
                if n:
                    logger.info("processed %d job(s)", n)
                # Grounding sources share the wake channel, so drain them each pass too.
                m = await drain_sources(
                    session_maker,
                    stuck_timeout_seconds=s.worker_stuck_timeout_seconds,
                    max_attempts=s.worker_max_attempts,
                )
                if m:
                    logger.info("processed %d grounding source(s)", m)
            except Exception:  # a bad pass must not kill the worker
                logger.exception("worker pass failed; continuing")
            # Wait for a new-job notification; the poll timeout is the fallback that also
            # re-runs stuck recovery periodically even if no NOTIFY ever arrives.
            with contextlib.suppress(Exception):
                await wait_for_notification(conn, timeout=s.worker_poll_interval_seconds)

    logger.info("ingestion worker stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # psycopg's async driver needs a selector event loop; Windows defaults to Proactor, which
    # it cannot use. uvicorn --reload handles this for the inline worker; the standalone
    # process must set it itself.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    stop = asyncio.Event()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        with contextlib.suppress(NotImplementedError):  # add_signal_handler is POSIX-only
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
        await run_worker(stop_event=stop)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
