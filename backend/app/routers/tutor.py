"""Tutor endpoints - the centerpiece: grounded Socratic chat with revealed-set disclosure.

- `GET  /tutor/{slug}`          - session state: unlocked rungs (with content), history, status.
- `POST /tutor/{slug}/reveal`   - manual SEQUENTIAL reveal (unlock max(revealed)+1; no skips).
- `POST /tutor/{slug}/message`  - one chat turn, streamed as Server-Sent Events.

Disclosure is a revealed **set** in Postgres, reconstructed per request (no in-memory agent
state). A turn: retrieve grounding -> run the conservative judge (maybe auto-unlock the next
rung the student demonstrably figured out) -> stream a grounded, cited reply built ONLY from
unlocked rungs. Un-revealed `HintLevel` content never enters the prompt - no-leakage by
construction (see `agents/tutor.py`).

The streaming turn opens its OWN DB session inside the generator (the request-scoped session
is torn down when the response headers are sent, before the stream body runs) - same rule as
the ingestion background task.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tutor import build_message_thread, judge_disclosure
from app.database import async_session_maker
from app.dependencies import CurrentUser, DbSession
from app.errors import ApiError
from app.ingestion.grounding import retrieve_grounding
from app.llm import stream_completion
from app.models import HintLevel, LevelUnlock, Problem, SessionMessage, TutorSession
from app.schemas import (
    MessageRequest,
    RevealedLevel,
    RevealOut,
    TutorMessageOut,
    TutorSessionOut,
)

router = APIRouter(prefix="/tutor", tags=["tutor"])


# --- shared loaders ----------------------------------------------------------
async def _load_ready_problem(db: AsyncSession, slug: str) -> Problem:
    problem = await db.scalar(select(Problem).where(Problem.slug == slug))
    if problem is None:
        raise ApiError(404, "problem_not_found", "No problem with that slug")
    if problem.ingestion_status != "ready":
        raise ApiError(409, "problem_not_ready", "This problem is still being ingested.")
    count = await db.scalar(
        select(func.count()).select_from(HintLevel).where(HintLevel.problem_id == problem.id)
    )
    if not count:
        raise ApiError(
            409,
            "no_levels",
            "This problem has no hint ladder yet - add the editorial link and re-ingest.",
        )
    return problem


async def _levels_map(db: AsyncSession, problem_id: int) -> dict[int, str]:
    rows = await db.scalars(
        select(HintLevel).where(HintLevel.problem_id == problem_id).order_by(HintLevel.level)
    )
    return {r.level: r.content for r in rows}


async def _get_or_create_session(
    db: AsyncSession, user_id: int, problem_id: int
) -> TutorSession:
    session = await db.scalar(
        select(TutorSession).where(
            TutorSession.user_id == user_id, TutorSession.problem_id == problem_id
        )
    )
    if session is None:
        session = TutorSession(
            user_id=user_id, problem_id=problem_id, revealed_levels=[], status="active"
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
    return session


async def _unlocks_map(db: AsyncSession, session_id: int) -> dict[int, LevelUnlock]:
    rows = await db.scalars(select(LevelUnlock).where(LevelUnlock.session_id == session_id))
    return {u.level: u for u in rows}


async def _serialize(db: AsyncSession, problem: Problem, session: TutorSession) -> TutorSessionOut:
    levels = await _levels_map(db, problem.id)
    revealed = sorted(session.revealed_levels or [])
    msgs = (
        await db.scalars(
            select(SessionMessage)
            .where(SessionMessage.session_id == session.id)
            .order_by(SessionMessage.created_at, SessionMessage.id)
        )
    ).all()
    by_id = {m.id: m.content for m in msgs}
    unlocks = await _unlocks_map(db, session.id)

    revealed_out: list[RevealedLevel] = []
    for lvl in revealed:
        if lvl not in levels:
            continue
        u = unlocks.get(lvl)
        # Rows unlocked before this table existed default to "opened" (no earned message).
        revealed_out.append(
            RevealedLevel(
                level=lvl,
                content=levels[lvl],
                via=u.via if u else "manual",
                earned_text=by_id.get(u.earned_message_id) if u and u.earned_message_id else None,
                earned_message_id=u.earned_message_id if u else None,
                unlocked_at=u.created_at if u else None,
            )
        )

    return TutorSessionOut(
        slug=problem.slug,
        status=session.status,
        revealed_levels=revealed,
        revealed=revealed_out,
        level_count=len(levels),
        messages=[TutorMessageOut.model_validate(m) for m in msgs],
    )


# --- endpoints ---------------------------------------------------------------
@router.get("/{slug}", response_model=TutorSessionOut)
async def get_session(slug: str, user: CurrentUser, db: DbSession):
    problem = await _load_ready_problem(db, slug)
    session = await _get_or_create_session(db, user.id, problem.id)
    return await _serialize(db, problem, session)


@router.post("/{slug}/reveal", response_model=RevealOut)
async def reveal(slug: str, user: CurrentUser, db: DbSession):
    """Unlock the next rung sequentially. Rejects skips by construction - you can only ever
    unlock `max(revealed)+1`."""
    problem = await _load_ready_problem(db, slug)
    levels = await _levels_map(db, problem.id)
    session = await _get_or_create_session(db, user.id, problem.id)
    revealed = list(session.revealed_levels or [])
    nxt = (max(revealed) if revealed else 0) + 1
    if nxt > len(levels):
        raise ApiError(409, "all_levels_revealed", "Every hint is already unlocked.")
    revealed.append(nxt)
    session.revealed_levels = revealed  # reassign so SQLAlchemy tracks the JSON change
    db.add(LevelUnlock(session_id=session.id, level=nxt, via="manual"))
    await db.commit()
    return RevealOut(level=nxt, content=levels[nxt], via="manual", revealed_levels=sorted(revealed))


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.post("/{slug}/message")
async def message(slug: str, body: MessageRequest, user: CurrentUser, db: DbSession):
    """Stream one tutor turn as SSE. Validates readiness up front (so errors are real HTTP
    statuses), then hands off to a generator with its own DB session."""
    problem = await _load_ready_problem(db, slug)
    await _get_or_create_session(db, user.id, problem.id)
    return StreamingResponse(
        _stream_turn(user_id=user.id, problem_id=problem.id, content=body.content),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_turn(*, user_id: int, problem_id: int, content: str) -> AsyncIterator[str]:
    """The turn: persist the user message, retrieve grounding, maybe auto-unlock the next
    rung, then stream a grounded reply and persist it. Yields SSE `data:` frames of shape
    {type: "unlock"|"token"|"done"|"error", …}."""
    async with async_session_maker() as db:
        problem = await db.get(Problem, problem_id)
        session = await _get_or_create_session(db, user_id, problem_id)
        levels = await _levels_map(db, problem_id)
        revealed = list(session.revealed_levels or [])

        # History BEFORE this turn (so the new user message isn't duplicated in the thread).
        prior = (
            await db.scalars(
                select(SessionMessage)
                .where(SessionMessage.session_id == session.id)
                .order_by(SessionMessage.created_at, SessionMessage.id)
            )
        ).all()
        history = [(m.role, m.content) for m in prior]

        user_msg = SessionMessage(session_id=session.id, role="user", content=content)
        db.add(user_msg)
        await db.commit()
        await db.refresh(user_msg)  # need its id to attribute a chat unlock to this message

        # 1) Grounding retrieval - enrichment, never required. Failure (e.g. unseeded
        #    corpus / no key) degrades to no citations rather than breaking the chat.
        try:
            hits = await retrieve_grounding(content)
        except Exception:
            hits = []
        citations = [h.citation() for h in hits]

        # 2) Conservative disclosure judge on the NEXT rung only (sequential). Unlock just
        #    that rung if the student demonstrably figured it out themselves - an "earned"
        #    unlock, attributed to this message so the card can quote their words.
        nxt = (max(revealed) if revealed else 0) + 1
        unlocked: int | None = None
        if session.status == "active" and nxt in levels and nxt not in revealed:
            decision = await judge_disclosure(user_message=content, target_hint=levels[nxt])
            if decision.demonstrated:
                revealed.append(nxt)
                session.revealed_levels = sorted(revealed)
                db.add(
                    LevelUnlock(
                        session_id=session.id,
                        level=nxt,
                        via="chat",
                        earned_message_id=user_msg.id,
                    )
                )
                await db.commit()
                unlocked = nxt
                yield _sse(
                    {"type": "unlock", "level": nxt, "via": "chat", "matched_message": content}
                )

        # 3) Build the thread from ONLY unlocked rungs (+ grounding + history), then stream.
        revealed_contents = [(lvl, levels[lvl]) for lvl in sorted(revealed) if lvl in levels]
        thread = build_message_thread(
            problem_title=problem.title or problem.external_id,
            statement=problem.statement_text or "",
            constraints=problem.constraints_text,
            revealed=revealed_contents,
            grounding=hits,
            history=history,
            user_message=content,
        )

        reply_parts: list[str] = []
        try:
            async for delta in stream_completion(task="chat", messages=thread):
                reply_parts.append(delta)
                yield _sse({"type": "token", "text": delta})
        except Exception as exc:
            yield _sse({"type": "error", "detail": f"The tutor couldn't respond: {exc}"})
            return

        reply = "".join(reply_parts).strip()
        db.add(
            SessionMessage(
                session_id=session.id,
                role="assistant",
                content=reply,
                citations=citations or None,
            )
        )
        await db.commit()

        yield _sse(
            {
                "type": "done",
                "citations": citations,
                # Retrieved-chunk count for the "In the prompt" panel (all retrieved hits are
                # cited, so this equals len(citations); surfaced explicitly for the UI).
                "chunk_count": len(hits),
                "revealed_levels": sorted(revealed),
                "unlocked": unlocked,
            }
        )
