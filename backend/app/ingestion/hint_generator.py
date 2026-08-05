"""Generate a graduated hint ladder from a problem's statement + constraints + editorial.

Runs once at ingestion time (not per request). The design goal is **intuition, not
spoilers**: the model reasons over the *statement and constraints together with* the
editorial and teaches the problem->approach mapping - why an approach is correct and how
the constraints point to it - laddering from a gentle nudge to the full solution.

**The ladder length is dynamic**, not a fixed four. A one-idea problem earns a short
ladder (a nudge -> the observation -> the full solution); a problem that stacks several
independent insights earns a longer one. The model chooses the rung count to match the
problem's conceptual depth, bounded to [MIN_LEVELS, MAX_LEVELS] so it can neither collapse
to a spoiler nor sprawl. The first rung is always "what to notice" and the last is always
the full solution; the middle rungs are the reasoning steps in between.

All levels are produced in **one structured-output call** (cheaper, and the model keeps
the levels distinct when it writes them together). Output is validated (rung count in
range, each level non-empty, substantive, and distinct - NOT by length, since a crisp
final answer is often shorter than the reasoning that motivates it); on failure we retry
once, then raise.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.llm import LLMError, complete_json
from app.models import Problem

MIN_LEVELS = 2
MAX_LEVELS = 6

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "levels": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": MIN_LEVELS,
            "maxItems": MAX_LEVELS,
        }
    },
    "required": ["levels"],
    "additionalProperties": False,
}

_SYSTEM = f"""\
You are an expert competitive-programming coach. Your job is NOT to reveal the solution \
as fast as possible - it is to build the student's intuition so they can solve similar \
problems on their own. The single most valuable skill you teach is reading the problem \
statement and the CONSTRAINTS and reasoning about what approach they point to.

You are given a problem statement, its constraints, and its official editorial. Produce a \
graduated hint ladder as JSON: a "levels" array of strings, ordered from the gentlest \
nudge to the full solution. Each level must be strictly more revealing than the one before \
it, and must NOT leak content that belongs to a later level.

DECIDE HOW MANY RUNGS THIS PROBLEM NEEDS - between {MIN_LEVELS} and {MAX_LEVELS}. Do not \
pad to a fixed number. A problem that turns on a single observation deserves a short ladder \
(≈3 rungs); a problem that stacks several independent insights (a reduction, then a data \
structure, then an implementation trick) deserves more. Let the reasoning depth set the \
count, not a template.

The ladder always has these anchors, with the middle rungs flexing to fit:
- FIRST rung - what to NOTICE. Point at the specific signals in the statement/constraints \
  (e.g. the size of n, the time limit, the structure of the input/output) and what class \
  of technique they suggest - WITHOUT naming the final algorithm or giving the idea away. \
  This is the "how do I even start reading this problem" nudge.
- MIDDLE rung(s) - the core INSIGHT(S) and approach, one reasoning step at a time, and WHY \
  the constraints steer toward this class of approach (e.g. "n ≤ 20 means we can afford \
  something exponential"; "n up to 2·10^5 with a 1s limit rules out O(n^2), so we need \
  O(n log n)"). Build up the method; save code for the last rung. If the problem has one \
  insight, this is a single rung; if it has several, give each its own rung.
- LAST rung - the full solution explanation, paraphrased in your own words (you may describe \
  the code structure, but do not copy the editorial verbatim). Tie it back to the earlier \
  levels.

Rules:
- Ground everything in THIS problem's statement/constraints/editorial. Do not invent facts.
- Explain the *why*, not just the *what*. The reasoning chain (constraints -> complexity \
  budget -> approach family -> specific technique -> correctness) is the point.
- Every rung must be distinct and substantive - never a filler rung added to hit a count.
- Keep math in readable form; LaTeX like $$$n \\le 10^5$$$ is fine.
- Return only the JSON object with a single "levels" array."""


class HintGenerationError(Exception):
    """The model failed to produce a valid graduated hint ladder."""


ChatFn = Callable[..., Awaitable[dict[str, Any]]]


def _build_user_prompt(problem: Problem) -> str:
    return (
        f"# Problem: {problem.title or problem.external_id}\n"
        f"Rating: {problem.rating if problem.rating is not None else 'unknown'}\n"
        f"Tags: {', '.join(problem.tags) if problem.tags else 'none'}\n\n"
        f"## Statement\n{problem.statement_text or '(not available)'}\n\n"
        f"## Constraints\n{problem.constraints_text or '(see statement)'}\n\n"
        f"## Official editorial (may cover the whole contest - use the part for this problem)\n"
        f"{problem.editorial_text or '(not available)'}\n"
    )


_MIN_LEVEL_CHARS = 20  # a substantive nudge, not "N/A" or a single word


def _validate(levels: list[str]) -> bool:
    if not (MIN_LEVELS <= len(levels) <= MAX_LEVELS):
        return False
    stripped = [level.strip() for level in levels]
    # Every level must be non-empty and substantive - guards against the model
    # collapsing the ladder or emitting a near-empty level. We deliberately do NOT
    # check that later levels are *longer*: graduated hints increase in what they
    # reveal, not in character count (a crisp final answer is often shorter than the
    # reasoning that motivates it).
    if not all(len(level) >= _MIN_LEVEL_CHARS for level in stripped):
        return False
    # Distinct: hints that repeat verbatim aren't graduated.
    return len(set(stripped)) == len(stripped)


async def generate_hints(problem: Problem, *, chat_fn: ChatFn = complete_json) -> list[str]:
    """Return the graduated hint ladder (index 0 = level 1) - a variable number of rungs
    in [MIN_LEVELS, MAX_LEVELS] chosen to fit the problem. Raises on failure.

    `chat_fn` is injectable so tests exercise the validation/retry logic without a live
    LLM call.
    """
    user = _build_user_prompt(problem)
    last_error = "unknown"

    for _ in range(2):  # one retry
        try:
            data = await chat_fn(
                task="hints",
                system=_SYSTEM,
                user=user,
                schema=_SCHEMA,
                schema_name="graduated_hints",
            )
        except LLMError as exc:
            last_error = str(exc)
            continue

        raw = data.get("levels")
        if not isinstance(raw, list):
            last_error = "output missing a 'levels' array"
            continue
        levels = [str(level).strip() for level in raw]
        if _validate(levels):
            return levels
        last_error = "output failed validation (count out of range, empty, or duplicate levels)"

    raise HintGenerationError(f"could not generate a valid graduated hint ladder: {last_error}")
