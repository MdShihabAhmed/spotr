"""Durable job-queue tests: enqueue → claim (FOR UPDATE SKIP LOCKED) → crash recovery, plus
the worker drain loop. Runs against sqlite; NOTIFY is no-op'd by the autouse conftest fixture,
and `FOR UPDATE SKIP LOCKED` is silently ignored by the sqlite dialect."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Problem
from app.queue import (
    claim_next_job,
    enqueue_ingestion,
    recover_stuck_jobs,
)
from app.worker import drain_queue


async def _add(db_session_maker, **kwargs) -> int:
    async with db_session_maker() as db:
        p = Problem(source="codeforces", **kwargs)
        db.add(p)
        await db.commit()
        await db.refresh(p)
        return p.id


async def test_enqueue_sets_pending_and_clears_prior_state(db_session_maker):
    pid = await _add(
        db_session_maker,
        slug="cf-1A",
        external_id="1A",
        ingestion_status="failed",
        ingestion_error="boom",
        claimed_at=datetime.now(timezone.utc),
        attempts=2,
    )
    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        await enqueue_ingestion(db, p)

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "pending"
        assert p.ingestion_error is None
        assert p.claimed_at is None


async def test_claim_marks_in_flight_and_increments_attempts(db_session_maker):
    pid = await _add(db_session_maker, slug="cf-1A", external_id="1A", ingestion_status="pending")

    async with db_session_maker() as db:
        claimed = await claim_next_job(db)
    assert claimed == pid

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "normalizing"
        assert p.attempts == 1
        assert p.claimed_at is not None


async def test_claim_returns_none_when_queue_empty(db_session_maker):
    await _add(db_session_maker, slug="cf-1A", external_id="1A", ingestion_status="ready")
    async with db_session_maker() as db:
        assert await claim_next_job(db) is None


async def test_claim_is_fifo(db_session_maker):
    first = await _add(db_session_maker, slug="cf-1A", external_id="1A", ingestion_status="pending")
    await _add(db_session_maker, slug="cf-1B", external_id="1B", ingestion_status="pending")
    async with db_session_maker() as db:
        assert await claim_next_job(db) == first  # lowest id first


async def test_recover_requeues_stuck_job(db_session_maker):
    # A job claimed long ago, still in-flight → its worker died; re-queue it.
    stale = datetime.now(timezone.utc) - timedelta(seconds=3600)
    pid = await _add(
        db_session_maker,
        slug="cf-1A",
        external_id="1A",
        ingestion_status="normalizing",
        claimed_at=stale,
        attempts=1,
    )
    async with db_session_maker() as db:
        recovered = await recover_stuck_jobs(db, stuck_timeout_seconds=600, max_attempts=3)
    assert recovered == [pid]

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "pending"
        assert p.claimed_at is None


async def test_recover_ignores_recently_claimed_job(db_session_maker):
    # Claimed 5s ago — still legitimately running; must NOT be touched.
    fresh = datetime.now(timezone.utc) - timedelta(seconds=5)
    pid = await _add(
        db_session_maker,
        slug="cf-1A",
        external_id="1A",
        ingestion_status="generating_levels",
        claimed_at=fresh,
        attempts=1,
    )
    async with db_session_maker() as db:
        assert await recover_stuck_jobs(db, stuck_timeout_seconds=600, max_attempts=3) == []
    async with db_session_maker() as db:
        assert (await db.get(Problem, pid)).ingestion_status == "generating_levels"


async def test_recover_fails_job_past_max_attempts(db_session_maker):
    stale = datetime.now(timezone.utc) - timedelta(seconds=3600)
    pid = await _add(
        db_session_maker,
        slug="cf-1A",
        external_id="1A",
        ingestion_status="normalizing",
        claimed_at=stale,
        attempts=3,  # already at the cap
    )
    async with db_session_maker() as db:
        await recover_stuck_jobs(db, stuck_timeout_seconds=600, max_attempts=3)
    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "failed"
        assert "crashed or restarted" in p.ingestion_error


async def test_drain_queue_processes_all_pending(db_session_maker):
    ids = [
        await _add(db_session_maker, slug=f"cf-{i}", external_id=str(i), ingestion_status="pending")
        for i in range(3)
    ]
    processed: list[int] = []

    async def fake_process(problem_id, *, session_maker):
        processed.append(problem_id)
        async with session_maker() as db:
            p = await db.get(Problem, problem_id)
            p.ingestion_status = "ready"
            await db.commit()

    n = await drain_queue(
        db_session_maker, process=fake_process, stuck_timeout_seconds=600, max_attempts=3
    )
    assert n == 3
    assert sorted(processed) == sorted(ids)

    async with db_session_maker() as db:
        rows = (await db.scalars(select(Problem))).all()
        assert all(r.ingestion_status == "ready" for r in rows)


async def test_drain_queue_recovers_then_processes(db_session_maker):
    # A stuck job + a fresh pending one: recovery re-queues the stuck job, then both run.
    stale = datetime.now(timezone.utc) - timedelta(seconds=3600)
    await _add(
        db_session_maker,
        slug="cf-stuck",
        external_id="STUCK",
        ingestion_status="normalizing",
        claimed_at=stale,
        attempts=1,
    )
    await _add(db_session_maker, slug="cf-new", external_id="NEW", ingestion_status="pending")

    async def fake_process(problem_id, *, session_maker):
        async with session_maker() as db:
            p = await db.get(Problem, problem_id)
            p.ingestion_status = "ready"
            await db.commit()

    n = await drain_queue(
        db_session_maker, process=fake_process, stuck_timeout_seconds=600, max_attempts=3
    )
    assert n == 2  # the recovered job + the new one
