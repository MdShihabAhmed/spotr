import pytest

from app.ingestion.hint_generator import (
    HintGenerationError,
    _build_user_prompt,
    generate_hints,
)
from app.llm import LLMError
from app.models import Problem


def _problem() -> Problem:
    return Problem(
        slug="cf-1846C",
        source="codeforces",
        external_id="1846C",
        title="C. Example",
        rating=1200,
        tags=["math"],
        statement_text="Find the answer.",
        constraints_text="1 <= n <= 2*10^5",
        editorial_text="Sort, then two pointers.",
    )


def _valid_payload():
    return {
        "level1": "Notice the size of n and the time limit.",
        "level2": "Because n is large, an O(n^2) scan is too slow — think O(n log n).",
        "level3": "Sort the array, then sweep with two pointers; this is correct because …",
        "level4": "Full walkthrough: sort ascending, maintain two indices, and for each … "
        "which yields the answer in O(n log n).",
    }


async def test_returns_four_levels_from_valid_output():
    async def chat_fn(**kwargs):
        return _valid_payload()

    levels = await generate_hints(_problem(), chat_fn=chat_fn)
    assert len(levels) == 4
    assert levels[0].startswith("Notice")


async def test_prompt_includes_statement_constraints_and_editorial():
    prompt = _build_user_prompt(_problem())
    assert "1 <= n <= 2*10^5" in prompt
    assert "Find the answer." in prompt
    assert "two pointers" in prompt  # editorial text is included


async def test_shorter_later_level_is_still_valid():
    # Regression: graduated hints increase in what they reveal, not in length. A crisp
    # final answer (level4) is often shorter than the level-3 reasoning — must NOT fail.
    async def chat_fn(**kwargs):
        return {
            "level1": "Look at how small the constraint on w is; efficiency is irrelevant here.",
            "level2": "Two even numbers always sum to an even number, so odd w is impossible.",
            "level3": "If w is even and greater than two, pick 2 and w-2, both even and positive; "
            "only w=2 fails because 1+1 are odd.",
            "level4": "Print YES iff w is even and w > 2.",
        }

    levels = await generate_hints(_problem(), chat_fn=chat_fn)
    assert len(levels) == 4
    assert len(levels[3]) < len(levels[2])  # later level is shorter, yet accepted


async def test_duplicate_levels_fail_validation():
    async def chat_fn(**kwargs):
        dup = _valid_payload()
        dup["level2"] = dup["level1"]  # two identical levels aren't graduated
        return dup

    with pytest.raises(HintGenerationError):
        await generate_hints(_problem(), chat_fn=chat_fn)


async def test_empty_level_fails_validation_then_raises():
    calls = 0

    async def chat_fn(**kwargs):
        nonlocal calls
        calls += 1
        bad = _valid_payload()
        bad["level3"] = "   "  # empty after strip
        return bad

    with pytest.raises(HintGenerationError):
        await generate_hints(_problem(), chat_fn=chat_fn)
    assert calls == 2  # retried once


async def test_llm_error_is_retried_then_raises():
    calls = 0

    async def chat_fn(**kwargs):
        nonlocal calls
        calls += 1
        raise LLMError("boom")

    with pytest.raises(HintGenerationError):
        await generate_hints(_problem(), chat_fn=chat_fn)
    assert calls == 2


async def test_retry_recovers_on_second_attempt():
    calls = 0

    async def chat_fn(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LLMError("transient")
        return _valid_payload()

    levels = await generate_hints(_problem(), chat_fn=chat_fn)
    assert len(levels) == 4
    assert calls == 2
