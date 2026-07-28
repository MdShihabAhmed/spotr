"""Problem ingestion endpoints.

`POST /problems/ingest` enqueues a durable job: it creates the `Problem` row (identity parsed
from the URL by the chosen source), sets it `pending`, and returns immediately with the
problem's **slug**. A separate worker (see `app/worker.py`) claims and processes it — the row
in Postgres *is* the queue, so the work survives an API restart or crash.

Progress reaches the client two ways: `GET /problems/{slug}` for a one-shot status read, and
`GET /problems/{slug}/events` for a live SSE stream fed by Postgres `LISTEN/NOTIFY` (no
polling).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.dependencies import CurrentUser, DbSession
from app.errors import ApiError
from app.models import HintLevel, Problem
from app.normalize import NormalizeError, normalize
from app.queue import (
    STATUS_CHANNEL,
    enqueue_ingestion,
    notify_job_enqueued,
    pg_listen,
    wait_for_notification,
)
from app.database import async_session_maker
from app.schemas import NormalizedOut, NormalizeRequest, ProblemIngest, ProblemOut
from app.sources import get_source

router = APIRouter(prefix="/problems", tags=["problems"])

# Ingestion is finished (for good or ill) at these; anything else is still in flight.
_TERMINAL_STATUSES = {"ready", "failed"}

# Statuses that mean "still processing" — a re-ingest is a no-op returning the existing
# row, so we never double-schedule the background task. `ready`/`failed` are terminal and
# may be re-processed (see `ingest_problem`): `failed` always retries; `ready` retries only
# when the payload brings an editorial the row doesn't have yet.
_IN_FLIGHT = {"pending", "normalizing", "generating_levels"}


async def _problem_out(db, problem: Problem) -> ProblemOut:
    """Serialize a problem, attaching its level count (content stays server-side)."""
    count = await db.scalar(
        select(func.count()).select_from(HintLevel).where(HintLevel.problem_id == problem.id)
    )
    out = ProblemOut.model_validate(problem)
    out.level_count = count or 0
    return out


def _apply_content(problem: Problem, payload: ProblemIngest) -> None:
    """Copy any supplied content onto the row. Only fields actually provided are
    touched, so a retry doesn't wipe prior values."""
    if payload.statement_text is not None:
        problem.statement_text = payload.statement_text
    if payload.constraints_text is not None:
        problem.constraints_text = payload.constraints_text
    if payload.sample_tests is not None:
        problem.sample_tests = [s.model_dump() for s in payload.sample_tests]
    if payload.note_text is not None:
        problem.note_text = payload.note_text
    if payload.figures is not None:
        problem.figures = [f.model_dump() for f in payload.figures]
    if payload.time_limit_ms is not None:
        problem.time_limit_ms = payload.time_limit_ms
    if payload.memory_limit_mb is not None:
        problem.memory_limit_mb = payload.memory_limit_mb
    if payload.editorial_url is not None:
        problem.editorial_url = payload.editorial_url
    if payload.editorial_text is not None:
        problem.editorial_text = payload.editorial_text


@router.post("/normalize", response_model=NormalizedOut)
async def normalize_input(payload: NormalizeRequest, user: CurrentUser):
    """Clean pasted text / a screenshot into structured `{statement, constraints,
    samples, limits}` for the user to review before ingesting. Synchronous (one LLM
    call) so the user confirms the extraction — vision/paste can err."""
    if not payload.text and not payload.images:
        raise ApiError(400, "empty_input", "Provide pasted text or a screenshot to normalize.")
    try:
        result = await normalize(text=payload.text, image_data_uris=payload.images)
    except NormalizeError as exc:
        raise ApiError(422, "normalization_failed", str(exc))
    return NormalizedOut(
        statement=result.statement,
        constraints=result.constraints,
        samples=result.samples,
        note=result.note,
        time_limit_ms=result.time_limit_ms,
        memory_limit_mb=result.memory_limit_mb,
    )


@router.post("/ingest", response_model=ProblemOut)
async def ingest_problem(
    payload: ProblemIngest,
    user: CurrentUser,
    db: DbSession,
    response: Response,
):
    """Submit a problem URL (+ provided statement/samples + optional editorial link).
    Returns 202 for a new/queued ingestion, 200 when the problem already exists (idempotent)."""
    try:
        source = get_source(payload.source)
    except ValueError as exc:
        raise ApiError(400, "unknown_source", f"Unknown problem source: {exc}")
    try:
        identity = source.parse_identity(payload.url)
    except ValueError as exc:
        raise ApiError(400, "invalid_problem_url", f"Not a valid problem URL: {exc}")

    # Idempotency keyed on (source, external_id) — NOT the raw URL, which has two forms
    # for the same Codeforces problem.
    existing = await db.scalar(
        select(Problem).where(
            Problem.source == identity.source,
            Problem.external_id == identity.external_id,
        )
    )
    if existing is not None:
        if existing.ingestion_status in _IN_FLIGHT:
            # Still queued/processing — don't re-enqueue; just report the current state.
            response.status_code = 200
            return await _problem_out(db, existing)

        # A re-ingest that brings an editorial the row doesn't have yet is worth
        # re-running (this is how "add the editorial on a later re-ingest" actually
        # works). A plain duplicate of a `ready` problem stays a no-op.
        brings_new_editorial = bool(
            (payload.editorial_url or payload.editorial_text) and not existing.editorial_text
        )
        if existing.ingestion_status == "ready" and not brings_new_editorial:
            response.status_code = 200
            return await _problem_out(db, existing)

        # Reprocess: a previously `failed` problem, or a `ready` one with a new editorial.
        # Apply whatever content the caller supplies (only provided fields are touched).
        _apply_content(existing, payload)
        await enqueue_ingestion(db, existing)
        await notify_job_enqueued(existing.id)
        response.status_code = 202
        return await _problem_out(db, existing)

    problem = Problem(
        slug=identity.slug,
        source=identity.source,
        external_id=identity.external_id,
        url=identity.url,
        source_metadata=identity.source_metadata,
        ingestion_status="pending",
    )
    _apply_content(problem, payload)
    db.add(problem)
    await db.commit()
    await db.refresh(problem)

    # The row is now `pending` in the queue; nudge an idle worker to claim it immediately.
    await notify_job_enqueued(problem.id)
    response.status_code = 202
    return await _problem_out(db, problem)


@router.get("/{slug}", response_model=ProblemOut)
async def get_problem(slug: str, user: CurrentUser, db: DbSession):
    problem = await db.scalar(select(Problem).where(Problem.slug == slug))
    if problem is None:
        raise ApiError(404, "problem_not_found", "No problem with that slug")
    return await _problem_out(db, problem)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.get("/{slug}/events")
async def problem_events(slug: str, user: CurrentUser, db: DbSession, request: Request):
    """Live ingestion status as Server-Sent Events — the push replacement for polling.

    Emits the current `{status, error}` immediately, then one frame per status change, and
    closes once the status is terminal (`ready`/`failed`). Changes are delivered by Postgres
    `LISTEN problem_status`; a short poll timeout is a safety net for a missed NOTIFY."""
    problem = await db.scalar(select(Problem).where(Problem.slug == slug))
    if problem is None:
        raise ApiError(404, "problem_not_found", "No problem with that slug")

    return StreamingResponse(
        _status_stream(problem.id, slug),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _read_status(problem_id: int) -> tuple[str, str | None]:
    async with async_session_maker() as db:
        row = await db.get(Problem, problem_id)
        if row is None:
            return "failed", "problem disappeared"
        return row.ingestion_status, row.ingestion_error


async def _status_stream(problem_id: int, slug: str) -> AsyncIterator[str]:
    """LISTEN for this problem's status changes and emit them. We LISTEN *before* the first
    read so no change can slip through the gap between reading and subscribing."""
    async with pg_listen(STATUS_CHANNEL) as conn:
        status, error = await _read_status(problem_id)
        yield _sse({"type": "status", "status": status, "error": error})
        if status in _TERMINAL_STATUSES:
            return

        while True:
            payloads = await wait_for_notification(conn, timeout=15.0)
            # Re-read on any notification for this problem (or on the timeout, as a fallback).
            if payloads and str(problem_id) not in payloads:
                continue
            new_status, new_error = await _read_status(problem_id)
            if new_status != status or new_error != error:
                status, error = new_status, new_error
                yield _sse({"type": "status", "status": status, "error": error})
            if status in _TERMINAL_STATUSES:
                return
            await asyncio.sleep(0)  # cooperative yield
