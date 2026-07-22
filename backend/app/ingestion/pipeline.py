"""Background ingestion orchestrator: CF URL → fully populated `Problem` row.

Runs as a FastAPI `BackgroundTask` (scrape + metadata is seconds; hint generation,
added in step 3, pushes it to 30–90s). Two hard rules encoded here:

- **It opens its OWN database session** from `session_maker`, never the request's.
  The request-scoped session from `get_db` is torn down when the response is sent;
  a background task that kept using it would hit a closed session.
- **It never raises.** A background task has no caller to catch it, so every failure
  is turned into `ingestion_status="failed"` with an actionable `ingestion_error`.

Everything external (CF API client, statement scraper) is injected so the whole
pipeline is testable offline against sqlite with fakes — no live network in CI.

Scope note (phase 2): this lands the problem at `ready` once statement + samples +
editorial are stored. Step 3 inserts hint generation (`generating_hints`) between
scraping and `ready`; see the marked seam below.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cf_client import CodeforcesClient
from app.cf_scraper import (
    ScrapedStatement,
    ScrapeError,
    detect_interactive,
    detect_multiple_answers,
    scrape_problem_statement,
)
from app.database import async_session_maker
from app.ingestion.editorial_scraper import fetch_editorial_text
from app.models import Problem

CfClientFactory = Callable[[], CodeforcesClient]
Scraper = Callable[[int, str], Awaitable[ScrapedStatement]]
EditorialFetcher = Callable[[str], Awaitable[str | None]]


async def run_ingestion(
    problem_id: int,
    *,
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    cf_client_factory: CfClientFactory = CodeforcesClient,
    scraper: Scraper = scrape_problem_statement,
    editorial_fetcher: EditorialFetcher = fetch_editorial_text,
) -> None:
    """Populate a `Problem` row's content, then mark it ready or failed."""
    async with session_maker() as db:
        problem = await db.get(Problem, problem_id)
        if problem is None:
            return  # row deleted between scheduling and running — nothing to do

        try:
            await _ingest(db, problem, cf_client_factory, scraper, editorial_fetcher)
        except _IngestionFailed as exc:
            problem.ingestion_status = "failed"
            problem.ingestion_error = str(exc)
            await db.commit()
        except Exception as exc:  # never let a background task die silently
            problem.ingestion_status = "failed"
            problem.ingestion_error = f"unexpected ingestion error: {exc}"
            await db.commit()


class _IngestionFailed(Exception):
    """Internal: an expected, actionable ingestion failure (message is user-facing)."""


async def _ingest(
    db: AsyncSession,
    problem: Problem,
    cf_client_factory: CfClientFactory,
    scraper: Scraper,
    editorial_fetcher: EditorialFetcher,
) -> None:
    problem.ingestion_status = "scraping"
    problem.ingestion_error = None
    await db.commit()

    # --- metadata (CF API, best-effort) --------------------------------------
    # Missing metadata is non-fatal: brand-new problems and gyms aren't always in
    # problemset.problems, and rating can be unassigned. Scraped content still stands.
    client = cf_client_factory()
    try:
        metadata = await client.get_problem_metadata(problem.cf_contest_id, problem.cf_index)
    except Exception:
        # Metadata is best-effort — a CF API hiccup, a Redis outage, anything. It must
        # never fail an ingest that has pasted content and doesn't need the network.
        metadata = None
    finally:
        await client.aclose()

    if metadata is not None:
        problem.title = metadata.title or problem.title
        problem.rating = metadata.rating
        problem.tags = metadata.tags

    # --- statement: pasted content wins; scrape only when nothing was pasted --
    # Codeforces returns 403 to server-side scrapers, so the manual-paste path is
    # the reliable one. If the statement or samples were supplied at ingest, trust
    # them and skip scraping entirely.
    if problem.statement_text or problem.sample_tests:
        text = problem.statement_text or ""
        problem.is_interactive = detect_interactive(text) or "interactive" in (problem.tags or [])
        problem.has_multiple_answers = detect_multiple_answers(text)
    else:
        try:
            scraped = await scraper(problem.cf_contest_id, problem.cf_index)
        except ScrapeError as exc:
            raise _IngestionFailed(
                "Couldn't fetch the problem statement from Codeforces "
                f"({exc}). Codeforces blocks automated requests — this is expected. "
                "Paste the statement and sample tests manually to ingest it."
            ) from exc

        if scraped.title and not problem.title:
            problem.title = scraped.title
        problem.statement_text = scraped.statement_text
        problem.constraints_text = scraped.constraints_text
        problem.sample_tests = scraped.sample_tests
        problem.time_limit_ms = scraped.time_limit_ms
        problem.memory_limit_mb = scraped.memory_limit_mb
        # Interactive can be signalled by the CF tag OR statement text; either wins.
        problem.is_interactive = scraped.is_interactive or "interactive" in (problem.tags or [])
        problem.has_multiple_answers = scraped.has_multiple_answers
        # Auto-discover the editorial link from the page's "Contest materials" box,
        # unless the user supplied one explicitly.
        if not problem.editorial_url and scraped.editorial_url:
            problem.editorial_url = scraped.editorial_url
    await db.commit()

    # --- editorial (best-effort, optional) -----------------------------------
    # Fetch the editorial text via the blogEntry.view API (not Cloudflare-blocked).
    # It's stored server-side only and never returned to clients — showing it would
    # spoil the graduated hints. Not reaching an editorial doesn't fail the ingest.
    if problem.editorial_url and not problem.editorial_text:
        problem.editorial_text = await editorial_fetcher(problem.editorial_url)
        await db.commit()

    # Samples are Required by the data model precisely so stress testing has ground
    # truth. Gate ingestion on them here rather than failing confusingly deep inside
    # a stress test later (a documented trap).
    if not problem.sample_tests:
        raise _IngestionFailed(
            "No sample tests were found on the statement page. Samples are required "
            "(they validate the brute force during stress testing)."
        )

    # --- hint generation seam (step 3) ---------------------------------------
    # TODO(step 3): problem.ingestion_status = "generating_hints"; generate 4
    # graduated hints from problem.editorial_text, embed them, persist EditorialHint
    # rows — then fall through to "ready". Requires OPENAI_API_KEY and Qdrant.
    problem.ingestion_status = "ready"
    problem.ingestion_error = None
    await db.commit()
