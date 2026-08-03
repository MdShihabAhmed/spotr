"""Tutor tests: the no-leakage context seam, the disclosure judge, sequential reveal, and
the streamed message turn (endpoints run against sqlite with the LLM/grounding faked)."""

import json

import pytest
from sqlalchemy import select

from app.agents.tutor import DisclosureDecision, build_message_thread, judge_disclosure
from app.models import HintLevel, LevelUnlock, Problem, SessionMessage, TutorSession

# Distinct sentinels per rung so a leak is unmistakable in assertions.
LEVELS = [
    (1, "SENTINEL_ONE: notice the array is sorted."),
    (2, "SENTINEL_TWO: the key observation is a two-pointer sweep."),
    (3, "SENTINEL_THREE: the approach is two pointers, O(n), correct because ..."),
    (4, "SENTINEL_FOUR: full solution — l=0, r=n-1, move inward."),
]


@pytest.fixture
async def ready_slug(db_session_maker):
    async with db_session_maker() as db:
        problem = Problem(
            slug="cf-1846C",
            source="codeforces",
            external_id="1846C",
            title="Two Pointers",
            statement_text="Given a sorted array, find a pair summing to X.",
            constraints_text="1 <= n <= 2e5",
            sample_tests=[{"input": "3\n1 2 3", "expected_output": "YES"}],
            ingestion_status="ready",
        )
        db.add(problem)
        await db.commit()
        await db.refresh(problem)
        for level, content in LEVELS:
            db.add(HintLevel(problem_id=problem.id, level=level, content=content))
        await db.commit()
    return "cf-1846C"


def _parse_sse(text: str) -> list[dict]:
    return [
        json.loads(line[len("data:") :].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


# --- pure context builder: no-leakage by construction ------------------------
def test_thread_never_includes_unrevealed_levels():
    thread = build_message_thread(
        problem_title="Two Pointers",
        statement="Given a sorted array, find a pair summing to X.",
        constraints="1 <= n <= 2e5",
        revealed=[(1, LEVELS[0][1]), (2, LEVELS[1][1])],  # only rungs 1–2 unlocked
        grounding=[],
        history=[("user", "hi"), ("assistant", "what do you notice?")],
        user_message="is it two pointers?",
    )
    blob = json.dumps(thread)
    assert "SENTINEL_ONE" in blob and "SENTINEL_TWO" in blob  # earned rungs are present
    assert "SENTINEL_THREE" not in blob and "SENTINEL_FOUR" not in blob  # locked rungs absent
    assert thread[0]["role"] == "system"
    assert thread[-1] == {"role": "user", "content": "is it two pointers?"}


def test_thread_with_nothing_revealed_stays_high_level():
    thread = build_message_thread(
        problem_title="P",
        statement="stmt",
        constraints=None,
        revealed=[],
        grounding=[],
        history=[],
        user_message="help",
    )
    blob = json.dumps(thread)
    assert "SENTINEL" not in blob
    assert "draw it out with questions" in thread[0]["content"]


# --- disclosure judge --------------------------------------------------------
async def test_judge_unlocks_only_when_demonstrated():
    async def fake_chat(**kwargs):
        return {"demonstrated": True, "reason": "clearly stated the two-pointer idea"}

    decision = await judge_disclosure(
        user_message="I think we can use two pointers from both ends",
        target_hint=LEVELS[1][1],
        chat_fn=fake_chat,
    )
    assert decision.demonstrated is True


async def test_judge_fails_closed_on_error():
    async def boom(**kwargs):
        raise RuntimeError("model down")

    decision = await judge_disclosure(user_message="?", target_hint="x", chat_fn=boom)
    assert decision.demonstrated is False  # never unlock on an error


# --- endpoints ---------------------------------------------------------------
async def test_get_session_requires_auth(client, ready_slug):
    assert (await client.get("/tutor/cf-1846C")).status_code == 401


async def test_get_session_creates_and_reports_state(client, auth_headers, ready_slug):
    r = await client.get("/tutor/cf-1846C", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "active"
    assert body["revealed_levels"] == []
    assert body["revealed"] == []
    assert body["level_count"] == 4
    assert body["messages"] == []


async def test_reveal_is_sequential_then_exhausts(client, auth_headers, ready_slug):
    for expected in (1, 2, 3, 4):
        r = await client.post("/tutor/cf-1846C/reveal", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["level"] == expected
        assert body["content"] == LEVELS[expected - 1][1]
        assert body["via"] == "manual"  # manual reveal → "opened"
        assert body["revealed_levels"] == list(range(1, expected + 1))
    # All unlocked → the next reveal is rejected.
    r = await client.post("/tutor/cf-1846C/reveal", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["code"] == "all_levels_revealed"

    # Serialized state reports each rung as opened (violet), with no earned quote.
    state = (await client.get("/tutor/cf-1846C", headers=auth_headers)).json()
    assert [r["via"] for r in state["revealed"]] == ["manual"] * 4
    assert all(r["earned_text"] is None for r in state["revealed"])


async def test_tutor_rejects_unready_and_ladderless_problems(client, auth_headers, db_session_maker):
    async with db_session_maker() as db:
        db.add(
            Problem(
                slug="cf-1p",
                source="codeforces",
                external_id="1P",
                sample_tests=[{"input": "1", "expected_output": "1"}],
                ingestion_status="pending",
            )
        )
        # Ready but with NO ladder (no editorial was supplied).
        db.add(
            Problem(
                slug="cf-2q",
                source="codeforces",
                external_id="2Q",
                sample_tests=[{"input": "1", "expected_output": "1"}],
                ingestion_status="ready",
            )
        )
        await db.commit()

    r1 = await client.get("/tutor/cf-1p", headers=auth_headers)
    assert r1.status_code == 409 and r1.json()["code"] == "problem_not_ready"
    r2 = await client.get("/tutor/cf-2q", headers=auth_headers)
    assert r2.status_code == 409 and r2.json()["code"] == "no_levels"
    r3 = await client.get("/tutor/cf-9999Z", headers=auth_headers)
    assert r3.status_code == 404 and r3.json()["code"] == "problem_not_found"


def _fake_stream(captured):
    async def stream(*, task, messages):
        captured["messages"] = messages
        for token in ("Have ", "you ", "tried a pointer?"):
            yield token

    return stream


async def _no_grounding(query):
    return []


async def test_message_streams_tokens_persists_and_does_not_leak(
    client, auth_headers, ready_slug, db_session_maker, monkeypatch
):
    captured: dict = {}

    async def no_unlock(*, user_message, target_hint):
        return DisclosureDecision(False, "not yet")

    # The streaming turn opens its OWN session (not the request's) — point that maker at
    # the test sqlite engine, since the real one is Postgres.
    monkeypatch.setattr("app.routers.tutor.async_session_maker", db_session_maker)
    monkeypatch.setattr("app.routers.tutor.stream_completion", _fake_stream(captured))
    monkeypatch.setattr("app.routers.tutor.retrieve_grounding", _no_grounding)
    monkeypatch.setattr("app.routers.tutor.judge_disclosure", no_unlock)

    r = await client.post(
        "/tutor/cf-1846C/message", json={"content": "where do I start?"}, headers=auth_headers
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    assert "token" in types
    assert types[-1] == "done"
    reply = "".join(e["text"] for e in events if e["type"] == "token")
    assert reply == "Have you tried a pointer?"

    # No-leakage end-to-end: with nothing revealed, no rung content reached the model.
    blob = json.dumps(captured["messages"])
    assert "SENTINEL" not in blob

    # Both turns are persisted.
    async with db_session_maker() as db:
        msgs = (await db.scalars(select(SessionMessage))).all()
        assert {m.role for m in msgs} == {"user", "assistant"}
        assistant = next(m for m in msgs if m.role == "assistant")
        assert assistant.content == "Have you tried a pointer?"


async def test_message_auto_unlocks_when_judge_demonstrates(
    client, auth_headers, ready_slug, db_session_maker, monkeypatch
):
    captured: dict = {}

    async def demonstrates(*, user_message, target_hint):
        return DisclosureDecision(True, "student stated the insight")

    monkeypatch.setattr("app.routers.tutor.async_session_maker", db_session_maker)
    monkeypatch.setattr("app.routers.tutor.stream_completion", _fake_stream(captured))
    monkeypatch.setattr("app.routers.tutor.retrieve_grounding", _no_grounding)
    monkeypatch.setattr("app.routers.tutor.judge_disclosure", demonstrates)

    r = await client.post(
        "/tutor/cf-1846C/message",
        json={"content": "the array is sorted so we can sweep from both ends"},
        headers=auth_headers,
    )
    events = _parse_sse(r.text)
    unlock = next(e for e in events if e["type"] == "unlock")
    assert unlock["level"] == 1
    assert unlock["via"] == "chat"  # chat demonstration → "earned"
    assert unlock["matched_message"] == "the array is sorted so we can sweep from both ends"
    done = next(e for e in events if e["type"] == "done")
    assert done["revealed_levels"] == [1]
    assert "chunk_count" in done  # for the "In the prompt" panel

    async with db_session_maker() as db:
        session = await db.scalar(select(TutorSession))
        assert session.revealed_levels == [1]
        # Earned unlock is persisted with the message that earned it (survives reload).
        unlock_row = await db.scalar(select(LevelUnlock))
        assert unlock_row.via == "chat"
        assert unlock_row.level == 1
        earned_msg = await db.get(SessionMessage, unlock_row.earned_message_id)
        assert earned_msg.role == "user"
        assert earned_msg.content == "the array is sorted so we can sweep from both ends"

    # Serialized state quotes the earned message back and marks the rung green.
    state = (await client.get("/tutor/cf-1846C", headers=auth_headers)).json()
    earned = next(r for r in state["revealed"] if r["level"] == 1)
    assert earned["via"] == "chat"
    assert earned["earned_text"] == "the array is sorted so we can sweep from both ends"
