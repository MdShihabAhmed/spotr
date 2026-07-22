"""Problem ingestion endpoints.

`POST /problems/ingest` follows the 202-accept-then-poll pattern: it creates the
`Problem` row, schedules background ingestion, and returns immediately. The client
polls `GET /problems/{id}` for `ingestion_status`. Ingestion (scrape + soon hint
generation) is far too slow to block a request.
"""

from fastapi import APIRouter, BackgroundTasks, Response
from sqlalchemy import select

from app.cf_url import parse_cf_url
from app.dependencies import CurrentUser, DbSession
from app.errors import ApiError
from app.ingestion.pipeline import run_ingestion
from app.models import Problem
from app.schemas import ProblemIngest, ProblemOut

router = APIRouter(prefix="/problems", tags=["problems"])

# Statuses that mean "already ingested or in flight" — a re-ingest is a no-op that
# returns the existing row. Only a previously `failed` problem is re-attempted.
_IN_FLIGHT_OR_DONE = {"pending", "scraping", "generating_hints", "ready"}


def _apply_manual_content(problem: Problem, payload: ProblemIngest) -> None:
    """Copy any pasted content from the request onto the problem row. Only fields
    that were actually supplied are touched, so a retry doesn't wipe prior values."""
    if payload.statement_text is not None:
        problem.statement_text = payload.statement_text
    if payload.constraints_text is not None:
        problem.constraints_text = payload.constraints_text
    if payload.sample_tests is not None:
        problem.sample_tests = [s.model_dump() for s in payload.sample_tests]
    if payload.editorial_url is not None:
        problem.editorial_url = payload.editorial_url
    if payload.editorial_text is not None:
        problem.editorial_text = payload.editorial_text


@router.post("/ingest", response_model=ProblemOut)
async def ingest_problem(
    payload: ProblemIngest,
    user: CurrentUser,
    db: DbSession,
    background_tasks: BackgroundTasks,
    response: Response,
):
    """Submit a CF problem URL (+ optional editorial). Returns 202 for a new
    ingestion, 200 when the problem already exists (idempotent)."""
    try:
        parsed = parse_cf_url(payload.cf_url)
    except ValueError as exc:
        raise ApiError(400, "invalid_cf_url", f"Not a valid Codeforces problem URL: {exc}")

    # Idempotency keyed on (contest_id, index) parsed from the URL — NOT the raw URL
    # string, which has two forms for the same problem (see cf_url.py).
    existing = await db.scalar(
        select(Problem).where(
            Problem.cf_contest_id == parsed.contest_id,
            Problem.cf_index == parsed.index,
        )
    )
    if existing is not None:
        if existing.ingestion_status in _IN_FLIGHT_OR_DONE:
            response.status_code = 200
            return existing
        # Previously failed: let the caller retry, this time supplying the content
        # (statement / samples / editorial) that scraping couldn't get.
        _apply_manual_content(existing, payload)
        existing.ingestion_status = "pending"
        existing.ingestion_error = None
        await db.commit()
        background_tasks.add_task(run_ingestion, existing.id)
        response.status_code = 202
        return existing

    problem = Problem(
        cf_url=parsed.canonical_url,
        cf_contest_id=parsed.contest_id,
        cf_index=parsed.index,
        ingestion_status="pending",
    )
    _apply_manual_content(problem, payload)
    db.add(problem)
    await db.commit()
    await db.refresh(problem)

    # Scheduled after the response (and this commit) — the background task opens its
    # own session and must be able to read the row we just committed.
    background_tasks.add_task(run_ingestion, problem.id)
    response.status_code = 202
    return problem


@router.get("/{problem_id}", response_model=ProblemOut)
async def get_problem(problem_id: int, user: CurrentUser, db: DbSession):
    problem = await db.get(Problem, problem_id)
    if problem is None:
        raise ApiError(404, "problem_not_found", "No problem with that id")
    return problem
