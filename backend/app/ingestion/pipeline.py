"""Background ingestion orchestrator: user-provided problem → populated `Problem` row.

Runs as a FastAPI `BackgroundTask` (editorial fetch + level generation is multi-second).
Two hard rules:

- **It opens its OWN database session** from `session_maker`, never the request's (the
  request-scoped session is torn down when the response is sent).
- **It never raises.** Every failure becomes `ingestion_status="failed"` with an
  actionable `ingestion_error`.

Data model (re-architected): the user supplies statement/constraints/samples (no
scraping). The pipeline fetches only permitted extras — metadata + editorial — via the
problem's `ProblemSource`, then generates the graduated ladder. Everything external is
injected so the whole pipeline is testable offline against sqlite with fakes.

Seam: `normalize.py` (step 3) will run before this to clean pasted/screenshot input; for
now statement/constraints/samples are taken as supplied.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import async_session_maker
from app.ingestion.hint_generator import HintGenerationError, generate_hints
from app.models import HintLevel, Problem
from app.problem_flags import detect_interactive, detect_multiple_answers
from app.queue import notify_problem_status
from app.sources import ProblemSource, SourceError, get_source

SourceResolver = Callable[[str], ProblemSource]
LevelGenerator = Callable[[Problem], Awaitable[list[str]]]


async def run_ingestion(
    problem_id: int,
    *,
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    source_resolver: SourceResolver = get_source,
    level_generator: LevelGenerator = generate_hints,
) -> None:
    """Populate a `Problem` row's content, then mark it ready or failed."""
    async with session_maker() as db:
        problem = await db.get(Problem, problem_id)
        if problem is None:
            return  # row deleted between scheduling and running — nothing to do

        try:
            await _ingest(db, problem, source_resolver, level_generator)
        except _IngestionFailed as exc:
            problem.ingestion_status = "failed"
            problem.ingestion_error = str(exc)
            await db.commit()
            await notify_problem_status(problem.id)
        except Exception as exc:  # never let a background task die silently
            problem.ingestion_status = "failed"
            problem.ingestion_error = f"unexpected ingestion error: {exc}"
            await db.commit()
            await notify_problem_status(problem.id)


class _IngestionFailed(Exception):
    """Internal: an expected, actionable ingestion failure (message is user-facing)."""


async def _ingest(
    db: AsyncSession,
    problem: Problem,
    source_resolver: SourceResolver,
    level_generator: LevelGenerator,
) -> None:
    # Claimed by a worker as `normalizing` already; set it here too so a direct call (tests)
    # and the SSE progress stream both see the in-flight state.
    problem.ingestion_status = "normalizing"
    problem.ingestion_error = None
    await db.commit()
    await notify_problem_status(problem.id)

    try:
        source = source_resolver(problem.source)
    except ValueError as exc:
        raise _IngestionFailed(f"Unknown problem source {problem.source!r}: {exc}") from exc

    # --- metadata (permitted API, best-effort) -------------------------------
    # Missing metadata is non-fatal: brand-new problems / gyms aren't always listed, and
    # rating can be unassigned. A CF API hiccup or Redis outage must never fail an ingest
    # whose statement/samples the user already provided.
    try:
        metadata = await source.fetch_metadata(problem.source_metadata or {})
    except Exception:
        metadata = None
    if metadata is not None:
        problem.title = metadata.title or problem.title
        problem.rating = metadata.rating
        problem.tags = metadata.tags

    # The user provides the statement/constraints/samples; derive the exclusion flags.
    text = problem.statement_text or ""
    problem.is_interactive = detect_interactive(text) or "interactive" in (problem.tags or [])
    problem.has_multiple_answers = detect_multiple_answers(text)
    await db.commit()

    # Samples are required (they anchor the tutor's I/O references and future stress-testing).
    if not problem.sample_tests:
        raise _IngestionFailed(
            "No sample tests were provided. Paste at least one sample (input + expected "
            "output) so the problem can be ingested."
        )

    # --- editorial -----------------------------------------------------------
    # Fetched via the source's permitted API (CF: blogEntry.view). Stored server-side
    # only, never returned to clients. A problem with NO editorial_url reaches `ready`
    # without a ladder (fine — the editorial is optional). But if the user *supplied* a
    # link, a fetch failure is surfaced: the whole point of the link was the ladder, so
    # failing visibly beats silently reaching `ready` with an unexplained "no editorial".
    if problem.editorial_url and not problem.editorial_text:
        try:
            editorial = await source.fetch_editorial(problem.editorial_url)
        except SourceError as exc:
            raise _IngestionFailed(
                f"Couldn't fetch the editorial from that link: {exc}. Double-check the "
                "blog URL (e.g. https://codeforces.com/blog/entry/155337), or paste the "
                "editorial text directly, then re-ingest."
            ) from exc
        if not editorial:
            raise _IngestionFailed(
                "The editorial link didn't yield any content. Make sure it's a "
                "Codeforces blog link (…/blog/entry/<id>), or paste the editorial text "
                "directly, then re-ingest."
            )
        problem.editorial_text = editorial
        await db.commit()

    # --- graduated ladder ----------------------------------------------------
    # Needs an editorial to ground the levels. Without one the problem still reaches
    # `ready` (statement + samples are usable) — levels get generated on a later
    # re-ingest once an editorial link is supplied.
    if problem.editorial_text:
        problem.ingestion_status = "generating_levels"
        await db.commit()
        await notify_problem_status(problem.id)
        try:
            levels = await level_generator(problem)
        except HintGenerationError as exc:
            raise _IngestionFailed(f"Level generation failed: {exc}") from exc

        # Replace any prior levels so a re-ingest is idempotent, not additive.
        await db.execute(delete(HintLevel).where(HintLevel.problem_id == problem.id))
        for level, content in enumerate(levels, start=1):
            db.add(HintLevel(problem_id=problem.id, level=level, content=content))
        await db.commit()

    problem.ingestion_status = "ready"
    problem.ingestion_error = None
    await db.commit()
    await notify_problem_status(problem.id)
