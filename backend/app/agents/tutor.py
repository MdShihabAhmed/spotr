"""The Socratic tutor agent: context assembly + the disclosure judge.

The load-bearing design decision lives here: **no-leakage is enforced by construction, not
by prompting.** `build_message_thread` is a pure function that receives ONLY the levels the
student has already unlocked - un-revealed `HintLevel` content is never passed in, so it
*cannot* reach the model regardless of what the prompt says. The prompt is a second layer
(refuse to jump ahead), and the leakage eval is the third (measure it). Enforce -> measure.

Grounding chunks (public reference prose) are a softer boundary: they're bounded by the
prompt ("explain only what the student has already reached") and the eval, not by
construction. That distinction is deliberate and documented.

Agent state (revealed set, history) is reconstructed from Postgres per turn - no in-memory
session, no checkpointer - so it survives restarts and scales across workers.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.llm import complete_json
from app.vector_store import GroundingHit

# What each rung is, for framing the tutor's ceiling without leaking the content.
LEVEL_INTENT = {
    1: "a gentle nudge toward what to notice",
    2: "the key observation",
    3: "the approach and why it works",
    4: "the full solution",
}

_SYSTEM = """You are a Socratic tutor helping a competitive programmer UPSOLVE a problem \
they're stuck on. Your goal is to help them reach the solution *themselves*. You never hand \
over the answer.

HARD RULES - never break these:
- You may only discuss ideas up to and including what the student has already UNLOCKED (see \
below). Never reveal an algorithm name, the approach, the complexity, or code beyond the \
unlocked hints - even if asked directly.
- If the student asks you to "just give the algorithm / the code / the answer", warmly \
decline and respond with a QUESTION that helps them progress - do not answer it for them.
- Do NOT volunteer key structural observations, techniques, the approach, or why it works \
unless that exact idea already appears in the UNLOCKED section below. When little or nothing \
is unlocked, reply with questions ONLY - draw the observation out of the student; never state \
it yourself.
- Lead with questions. Nudge; don't lecture. Keep replies short.
- Use the REFERENCE MATERIAL only to explain a concept the student has ALREADY reached - \
never to introduce the key idea before they've earned it. When you draw on a reference, \
cite it inline as [1], [2], … matching the numbered list.

PROBLEM: {title}
{statement}

CONSTRAINTS:
{constraints}

WHAT THE STUDENT HAS UNLOCKED (you may build up TO here, never beyond):
{revealed}

REFERENCE MATERIAL (optional, cite as [n]):
{grounding}"""


def _format_revealed(revealed: list[tuple[int, str]]) -> str:
    if not revealed:
        return (
            "Nothing yet. Ask what the student has already observed or tried; do NOT state "
            "any key observation, technique, or approach yourself - draw it out with questions."
        )
    lines = []
    for level, content in sorted(revealed):
        lines.append(f"[Unlocked hint {level} - {LEVEL_INTENT.get(level, '')}]\n{content}")
    return "\n\n".join(lines)


def _format_grounding(hits: list[GroundingHit]) -> str:
    if not hits:
        return "(none retrieved)"
    lines = []
    for i, hit in enumerate(hits, start=1):
        # Trim each passage so a few references don't blow the context budget.
        passage = hit.content.strip().replace("\n", " ")
        if len(passage) > 600:
            passage = passage[:600] + "…"
        lines.append(f"[{i}] {hit.source} - {hit.title} ({hit.url})\n{passage}")
    return "\n\n".join(lines)


def build_message_thread(
    *,
    problem_title: str,
    statement: str,
    constraints: str | None,
    revealed: list[tuple[int, str]],
    grounding: list[GroundingHit],
    history: list[tuple[str, str]],
    user_message: str,
) -> list[dict[str, str]]:
    """Assemble the full chat thread for one turn. PURE and the no-leakage seam: `revealed`
    holds only unlocked levels, so nothing above the student's earned depth can appear.
    `history` is [(role, content), …] of prior turns."""
    system = _SYSTEM.format(
        title=problem_title,
        statement=statement.strip(),
        constraints=(constraints or "(not provided)").strip(),
        revealed=_format_revealed(revealed),
        grounding=_format_grounding(grounding),
    )
    thread: list[dict[str, str]] = [{"role": "system", "content": system}]
    for role, content in history:
        thread.append({"role": role, "content": content})
    thread.append({"role": "user", "content": user_message})
    return thread


@dataclass(frozen=True)
class DisclosureDecision:
    demonstrated: bool
    reason: str


_JUDGE_SYSTEM = """You are a STRICT grader for a Socratic tutoring system. You decide \
whether the student's latest message shows they have INDEPENDENTLY figured out one specific \
hidden insight (the TARGET HINT).

Rules:
- Answer true ONLY if the student's message clearly articulates the target insight in their \
own words - the actual idea, not a vague gesture at it.
- Merely asking about it, restating the problem, guessing a keyword, or being close-but-\
unclear is NOT enough - answer false.
- Be conservative: when in doubt, false. Unlocking a hint the student didn't truly earn \
defeats the purpose."""

_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "demonstrated": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["demonstrated", "reason"],
    "additionalProperties": False,
}


async def judge_disclosure(
    *,
    user_message: str,
    target_hint: str,
    chat_fn=complete_json,
) -> DisclosureDecision:
    """Conservative judge: did the student's message demonstrate the *next* level's insight
    on their own? Runs on the fast model (cheap). Any failure -> not demonstrated (fail
    closed - never unlock on an error)."""
    user = f"TARGET HINT (the hidden insight to check for):\n{target_hint}\n\nSTUDENT MESSAGE:\n{user_message}"
    try:
        data = await chat_fn(
            task="judge",
            system=_JUDGE_SYSTEM,
            user=user,
            schema=_JUDGE_SCHEMA,
            schema_name="disclosure",
        )
    except Exception:
        return DisclosureDecision(False, "judge unavailable")
    return DisclosureDecision(bool(data.get("demonstrated")), str(data.get("reason", "")))
