"""Pipeline tests: run the real orchestrator against sqlite with fake CF services."""

import pytest

from sqlalchemy import select

from app.cf_client import CodeforcesAPIError, ProblemMetadata
from app.cf_scraper import ScrapedStatement, ScrapeError
from app.ingestion.pipeline import run_ingestion
from app.models import EditorialHint, Problem

SAMPLE = [{"input": "4", "expected_output": "2 2"}]


async def _fake_hints(problem):
    """Stand-in for the LLM hint generator — returns 4 valid graduated levels."""
    return [f"Level {i}: increasingly detailed hint text about the approach." for i in range(1, 5)]


async def _hint_levels(session_maker, problem_id):
    async with session_maker() as db:
        rows = (
            await db.scalars(
                select(EditorialHint)
                .where(EditorialHint.problem_id == problem_id)
                .order_by(EditorialHint.level)
            )
        ).all()
        return [(r.level, r.content) for r in rows]


async def _seed_pending(session_maker, **kwargs) -> int:
    async with session_maker() as db:
        problem = Problem(
            cf_url="https://codeforces.com/problemset/problem/1846/C",
            cf_contest_id=1846,
            cf_index="C",
            ingestion_status="pending",
            **kwargs,
        )
        db.add(problem)
        await db.commit()
        await db.refresh(problem)
        return problem.id


def _client_returning(metadata):
    class FakeClient:
        async def get_problem_metadata(self, contest_id, index):
            return metadata

        async def aclose(self):
            pass

    return FakeClient


def _client_raising():
    class FakeClient:
        async def get_problem_metadata(self, contest_id, index):
            raise CodeforcesAPIError("API down")

        async def aclose(self):
            pass

    return FakeClient


async def test_happy_path_reaches_ready_with_metadata_and_samples(db_session_maker):
    pid = await _seed_pending(db_session_maker, editorial_text="ed")
    meta = ProblemMetadata(title="Sum of Two", rating=800, tags=["math", "greedy"])

    async def scraper(contest_id, index):
        return ScrapedStatement(
            title="C. Sum of Two",
            statement_text="find two numbers",
            constraints_text="1 <= n",
            sample_tests=SAMPLE,
            time_limit_ms=2000,
            memory_limit_mb=256,
        )

    await run_ingestion(
        pid,
        session_maker=db_session_maker,
        cf_client_factory=_client_returning(meta),
        scraper=scraper,
        hint_generator=_fake_hints,
    )

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "ready"
        assert p.ingestion_error is None
        assert p.title == "Sum of Two"  # API metadata title wins over scraped
        assert p.rating == 800
        assert p.tags == ["math", "greedy"]
        assert p.sample_tests == SAMPLE
        assert p.time_limit_ms == 2000

    # editorial ("ed") present → 4 graduated hints generated and stored
    levels = await _hint_levels(db_session_maker, pid)
    assert [level for level, _ in levels] == [1, 2, 3, 4]


async def test_metadata_api_failure_is_non_fatal(db_session_maker):
    pid = await _seed_pending(db_session_maker)

    async def scraper(contest_id, index):
        return ScrapedStatement(title="C. X", statement_text="s", sample_tests=SAMPLE)

    await run_ingestion(
        pid,
        session_maker=db_session_maker,
        cf_client_factory=_client_raising(),
        scraper=scraper,
    )

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "ready"
        assert p.title == "C. X"  # falls back to scraped title
        assert p.rating is None


async def test_scrape_failure_marks_failed_with_actionable_error(db_session_maker):
    pid = await _seed_pending(db_session_maker)

    async def scraper(contest_id, index):
        raise ScrapeError("cloudflare block")

    await run_ingestion(
        pid,
        session_maker=db_session_maker,
        cf_client_factory=_client_returning(None),
        scraper=scraper,
    )

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "failed"
        assert "manually" in p.ingestion_error


async def test_no_samples_is_rejected_before_ready(db_session_maker):
    pid = await _seed_pending(db_session_maker)

    async def scraper(contest_id, index):
        return ScrapedStatement(title="C. X", statement_text="s", sample_tests=[])

    await run_ingestion(
        pid,
        session_maker=db_session_maker,
        cf_client_factory=_client_returning(None),
        scraper=scraper,
    )

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "failed"
        assert "sample tests" in p.ingestion_error.lower()


async def test_pasted_content_skips_scraping(db_session_maker):
    # Statement + samples were pasted at ingest, so the scraper must NOT be called
    # (it would 403 against real Codeforces anyway).
    pid = await _seed_pending(
        db_session_maker,
        statement_text="find two numbers. if there are multiple answers, print any.",
        sample_tests=SAMPLE,
        editorial_text="the editorial",
    )
    meta = ProblemMetadata(title="Sum of Two", rating=800, tags=["math"])

    async def scraper(contest_id, index):
        raise AssertionError("scraper should not be called when content was pasted")

    await run_ingestion(
        pid,
        session_maker=db_session_maker,
        cf_client_factory=_client_returning(meta),
        scraper=scraper,
        hint_generator=_fake_hints,
    )

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "ready"
        assert p.sample_tests == SAMPLE
        assert p.title == "Sum of Two"
        # flags derived from the pasted statement text
        assert p.has_multiple_answers is True


async def test_editorial_discovered_from_scrape_and_fetched(db_session_maker):
    pid = await _seed_pending(db_session_maker)
    blog = "https://codeforces.com/blog/entry/118044"

    async def scraper(contest_id, index):
        return ScrapedStatement(
            title="C. X", statement_text="s", sample_tests=SAMPLE, editorial_url=blog
        )

    fetched: list[str] = []

    async def editorial_fetcher(url):
        fetched.append(url)
        return "the full contest editorial with $$$n \\le 20$$$"

    await run_ingestion(
        pid,
        session_maker=db_session_maker,
        cf_client_factory=_client_returning(None),
        scraper=scraper,
        editorial_fetcher=editorial_fetcher,
        hint_generator=_fake_hints,
    )

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "ready"
        assert p.editorial_url == blog  # discovered from the page
        assert fetched == [blog]  # fetched from the discovered link
        assert p.has_editorial is True
        assert "editorial" in p.editorial_text

    # editorial present → hints generated
    assert len(await _hint_levels(db_session_maker, pid)) == 4


async def test_hint_generation_failure_marks_ingestion_failed(db_session_maker):
    pid = await _seed_pending(db_session_maker, editorial_text="ed")
    from app.ingestion.hint_generator import HintGenerationError

    async def scraper(contest_id, index):
        return ScrapedStatement(title="C. X", statement_text="s", sample_tests=SAMPLE)

    async def bad_hints(problem):
        raise HintGenerationError("model gave 2 levels")

    await run_ingestion(
        pid,
        session_maker=db_session_maker,
        cf_client_factory=_client_returning(None),
        scraper=scraper,
        hint_generator=bad_hints,
    )

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "failed"
        assert "hint generation failed" in p.ingestion_error.lower()


async def test_no_editorial_reaches_ready_without_hints(db_session_maker):
    pid = await _seed_pending(db_session_maker)  # no editorial

    async def scraper(contest_id, index):
        return ScrapedStatement(title="C. X", statement_text="s", sample_tests=SAMPLE)

    await run_ingestion(
        pid,
        session_maker=db_session_maker,
        cf_client_factory=_client_returning(None),
        scraper=scraper,
        hint_generator=_fake_hints,
    )

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "ready"
    assert await _hint_levels(db_session_maker, pid) == []  # no editorial → no hints


async def test_interactive_tag_from_api_sets_flag(db_session_maker):
    pid = await _seed_pending(db_session_maker)
    meta = ProblemMetadata(title="A. Guess", rating=1200, tags=["interactive"])

    async def scraper(contest_id, index):
        # scraper itself didn't detect interactivity; the API tag must still win
        return ScrapedStatement(
            title="A. Guess", statement_text="s", sample_tests=SAMPLE, is_interactive=False
        )

    await run_ingestion(
        pid,
        session_maker=db_session_maker,
        cf_client_factory=_client_returning(meta),
        scraper=scraper,
    )

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.is_interactive is True
