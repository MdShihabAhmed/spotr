"""Pipeline tests: run the real orchestrator against sqlite with a fake source.

The user provides statement + samples (no scraping); the pipeline fetches metadata +
editorial via the injected `ProblemSource` and generates the graduated ladder.
"""

from sqlalchemy import select

from app.ingestion.hint_generator import HintGenerationError
from app.ingestion.pipeline import run_ingestion
from app.models import HintLevel, Problem
from app.sources import SourceError, SourceMetadata

SAMPLE = [{"input": "4", "expected_output": "2 2"}]


async def _fake_levels(problem):
    """Stand-in for the LLM level generator — 4 valid graduated levels."""
    return [f"Level {i}: increasingly detailed guidance about the approach." for i in range(1, 5)]


class _FakeSource:
    """Injectable ProblemSource stand-in. `parse_identity` is unused in pipeline tests."""

    def __init__(self, *, metadata=None, editorial=None, metadata_raises=False):
        self._metadata = metadata
        self._editorial = editorial
        self._metadata_raises = metadata_raises
        self.editorial_calls: list[str] = []

    def parse_identity(self, url):  # pragma: no cover - not exercised here
        raise NotImplementedError

    async def fetch_metadata(self, source_metadata):
        if self._metadata_raises:
            raise SourceError("API down")
        return self._metadata

    async def fetch_editorial(self, editorial_url):
        self.editorial_calls.append(editorial_url)
        return self._editorial


def _resolver(source):
    return lambda name: source


async def _levels(session_maker, problem_id):
    async with session_maker() as db:
        rows = (
            await db.scalars(
                select(HintLevel)
                .where(HintLevel.problem_id == problem_id)
                .order_by(HintLevel.level)
            )
        ).all()
        return [(r.level, r.content) for r in rows]


async def _seed_pending(session_maker, **kwargs) -> int:
    async with session_maker() as db:
        problem = Problem(
            slug="cf-1846C",
            source="codeforces",
            external_id="1846C",
            source_metadata={"contest_id": 1846, "index": "C"},
            ingestion_status="pending",
            **kwargs,
        )
        db.add(problem)
        await db.commit()
        await db.refresh(problem)
        return problem.id


async def test_happy_path_reaches_ready_with_metadata_and_levels(db_session_maker):
    pid = await _seed_pending(
        db_session_maker, statement_text="find two numbers", sample_tests=SAMPLE, editorial_text="ed"
    )
    meta = SourceMetadata(title="Sum of Two", rating=800, tags=["math", "greedy"])
    source = _FakeSource(metadata=meta)

    await run_ingestion(
        pid,
        session_maker=db_session_maker,
        source_resolver=_resolver(source),
        level_generator=_fake_levels,
    )

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "ready"
        assert p.ingestion_error is None
        assert p.title == "Sum of Two"  # API metadata title wins
        assert p.rating == 800
        assert p.tags == ["math", "greedy"]
        assert p.sample_tests == SAMPLE

    assert [level for level, _ in await _levels(db_session_maker, pid)] == [1, 2, 3, 4]


async def test_metadata_api_failure_is_non_fatal(db_session_maker):
    pid = await _seed_pending(db_session_maker, statement_text="s", sample_tests=SAMPLE, title="C. X")
    source = _FakeSource(metadata_raises=True)

    await run_ingestion(pid, session_maker=db_session_maker, source_resolver=_resolver(source))

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "ready"
        assert p.title == "C. X"  # seeded title stays; metadata was best-effort
        assert p.rating is None


async def test_no_samples_is_rejected_before_ready(db_session_maker):
    pid = await _seed_pending(db_session_maker, statement_text="s")  # no samples
    source = _FakeSource(metadata=None)

    await run_ingestion(pid, session_maker=db_session_maker, source_resolver=_resolver(source))

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "failed"
        assert "sample" in p.ingestion_error.lower()


async def test_provided_content_derives_flags(db_session_maker):
    pid = await _seed_pending(
        db_session_maker,
        statement_text="find two numbers. if there are multiple answers, print any.",
        sample_tests=SAMPLE,
        editorial_text="the editorial",
    )
    source = _FakeSource(metadata=SourceMetadata(title="Sum of Two", rating=800, tags=["math"]))

    await run_ingestion(
        pid,
        session_maker=db_session_maker,
        source_resolver=_resolver(source),
        level_generator=_fake_levels,
    )

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "ready"
        assert p.title == "Sum of Two"
        assert p.has_multiple_answers is True


async def test_editorial_fetched_from_url(db_session_maker):
    blog = "https://codeforces.com/blog/entry/118044"
    pid = await _seed_pending(
        db_session_maker, statement_text="s", sample_tests=SAMPLE, editorial_url=blog
    )
    source = _FakeSource(
        metadata=None, editorial="the full contest editorial with $$$n \\le 20$$$"
    )

    await run_ingestion(
        pid,
        session_maker=db_session_maker,
        source_resolver=_resolver(source),
        level_generator=_fake_levels,
    )

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "ready"
        assert source.editorial_calls == [blog]
        assert p.has_editorial is True
        assert "editorial" in p.editorial_text

    assert len(await _levels(db_session_maker, pid)) == 4


async def test_level_generation_failure_marks_ingestion_failed(db_session_maker):
    pid = await _seed_pending(
        db_session_maker, statement_text="s", sample_tests=SAMPLE, editorial_text="ed"
    )
    source = _FakeSource(metadata=None)

    async def bad_levels(problem):
        raise HintGenerationError("model gave 2 levels")

    await run_ingestion(
        pid,
        session_maker=db_session_maker,
        source_resolver=_resolver(source),
        level_generator=bad_levels,
    )

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "failed"
        assert "level generation failed" in p.ingestion_error.lower()


async def test_no_editorial_reaches_ready_without_levels(db_session_maker):
    pid = await _seed_pending(db_session_maker, statement_text="s", sample_tests=SAMPLE)
    source = _FakeSource(metadata=None)

    await run_ingestion(
        pid,
        session_maker=db_session_maker,
        source_resolver=_resolver(source),
        level_generator=_fake_levels,
    )

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.ingestion_status == "ready"
    assert await _levels(db_session_maker, pid) == []  # no editorial → no levels


async def test_interactive_tag_from_api_sets_flag(db_session_maker):
    pid = await _seed_pending(db_session_maker, statement_text="s", sample_tests=SAMPLE)
    source = _FakeSource(metadata=SourceMetadata(title="A. Guess", rating=1200, tags=["interactive"]))

    await run_ingestion(pid, session_maker=db_session_maker, source_resolver=_resolver(source))

    async with db_session_maker() as db:
        p = await db.get(Problem, pid)
        assert p.is_interactive is True
