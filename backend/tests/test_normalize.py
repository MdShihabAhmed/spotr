import pytest

from app.normalize import NormalizeError, normalize


def _payload():
    return {
        "statement": "You are given $$$n$$$ integers. Find two whose sum is $$$n$$$.",
        "constraints": "$$$1 \\le n \\le 100$$$",
        "samples": [{"input": "4", "expected_output": "2 2"}],
        "note": "In the first sample, $$$2 + 2 = 4$$$.",
        "time_limit_ms": 2000,
        "memory_limit_mb": 256,
    }


async def test_normalize_text_parses_into_structured_problem():
    async def chat_fn(**kwargs):
        assert kwargs["images"] is None  # text path: no image attached
        return _payload()

    result = await normalize(text="messy pasted problem", chat_fn=chat_fn)
    assert result.statement.startswith("You are given")
    assert result.constraints == "$$$1 \\le n \\le 100$$$"
    assert result.samples == [{"input": "4", "expected_output": "2 2"}]
    assert result.note == "In the first sample, $$$2 + 2 = 4$$$."
    assert result.time_limit_ms == 2000
    assert result.memory_limit_mb == 256


async def test_blank_note_becomes_none():
    async def chat_fn(**kwargs):
        p = _payload()
        p["note"] = ""
        return p

    result = await normalize(text="x", chat_fn=chat_fn)
    assert result.note is None


async def test_screenshots_attach_all_images():
    seen = {}

    async def chat_fn(**kwargs):
        seen["images"] = kwargs["images"]
        return _payload()

    uris = ["data:image/png;base64,AAAA", "data:image/png;base64,BBBB"]
    await normalize(image_data_uris=uris, chat_fn=chat_fn)
    assert seen["images"] == uris  # all parts sent together, in order


async def test_requires_some_input():
    with pytest.raises(NormalizeError):
        await normalize()


async def test_empty_statement_raises():
    async def chat_fn(**kwargs):
        bad = _payload()
        bad["statement"] = "   "
        return bad

    with pytest.raises(NormalizeError):
        await normalize(text="x", chat_fn=chat_fn)


async def test_blank_constraints_become_none():
    async def chat_fn(**kwargs):
        p = _payload()
        p["constraints"] = ""
        return p

    result = await normalize(text="x", chat_fn=chat_fn)
    assert result.constraints is None
