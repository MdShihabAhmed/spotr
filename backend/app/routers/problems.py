"""Problem ingestion endpoints.

`POST /problems/ingest` follows the 202-accept-then-poll pattern: it creates the
`Problem` row (identity parsed from the URL by the chosen source), schedules background
ingestion, and returns immediately with the problem's **slug**. The client polls
`GET /problems/{slug}` for `ingestion_status`.
"""

from fastapi import APIRouter, BackgroundTasks, Response
from sqlalchemy import func, select

from app.dependencies import CurrentUser, DbSession
from app.errors import ApiError
from app.ingestion.pipeline import run_ingestion
from app.models import HintLevel, Problem
from app.schemas import ProblemIngest, ProblemOut
from app.sources import get_source

router = APIRouter(prefix="/problems", tags=["problems"])

# Statuses that mean "already ingested or in flight" — a re-ingest is a no-op returning
# the existing row. Only a previously `failed` problem is re-attempted.
_IN_FLIGHT_OR_DONE = {"pending", "normalizing", "generating_levels", "ready"}


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
    """Submit a problem URL (+ provided statement/samples + optional editorial link).
    Returns 202 for a new ingestion, 200 when the problem already exists (idempotent)."""
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
        if existing.ingestion_status in _IN_FLIGHT_OR_DONE:
            response.status_code = 200
            return await _problem_out(db, existing)
        # Previously failed: retry, this time with whatever content the caller supplies.
        _apply_content(existing, payload)
        existing.ingestion_status = "pending"
        existing.ingestion_error = None
        await db.commit()
        background_tasks.add_task(run_ingestion, existing.id)
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

    # Scheduled after the response commit — the background task opens its own session and
    # must be able to read the row we just committed.
    background_tasks.add_task(run_ingestion, problem.id)
    response.status_code = 202
    return await _problem_out(db, problem)


@router.get("/{slug}", response_model=ProblemOut)
async def get_problem(slug: str, user: CurrentUser, db: DbSession):
    problem = await db.scalar(select(Problem).where(Problem.slug == slug))
    if problem is None:
        raise ApiError(404, "problem_not_found", "No problem with that slug")
    return await _problem_out(db, problem)
