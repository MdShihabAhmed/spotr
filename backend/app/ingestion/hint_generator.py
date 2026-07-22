"""Generate 4 graduated hints from a problem's statement + constraints + editorial.

Runs once at ingestion time (not per request). The design goal is **intuition, not
spoilers**: the model reasons over the *statement and constraints together with* the
editorial and teaches the problem→approach mapping — why an approach is correct and how
the constraints point to it — laddering from a gentle nudge to the full solution.

All 4 levels are produced in **one structured-output call** (cheaper, and the model keeps
the levels distinct when it writes them together). Output is validated (4 non-empty,
each at least as revealing/long as the previous); on failure we retry once, then raise.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.config import get_settings
from app.llm import LLMError, complete_json
from app.models import Problem

NUM_LEVELS = 4

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {f"level{i}": {"type": "string"} for i in range(1, NUM_LEVELS + 1)},
    "required": [f"level{i}" for i in range(1, NUM_LEVELS + 1)],
    "additionalProperties": False,
}

_SYSTEM = """\
You are an expert competitive-programming coach. Your job is NOT to reveal the solution \
as fast as possible — it is to build the student's intuition so they can solve similar \
problems on their own. The single most valuable skill you teach is reading the problem \
statement and the CONSTRAINTS and reasoning about what approach they point to.

You are given a problem statement, its constraints, and its official editorial. Produce \
exactly four graduated hints as JSON. Each level must be strictly more revealing than the \
one before it, and must NOT leak content that belongs to a later level:

- level1: What to NOTICE. Point at the specific signals in the statement/constraints \
  (e.g. the size of n, the time limit, the structure of the input/output) and what class \
  of technique they suggest — WITHOUT naming the final algorithm or giving the idea away. \
  This is the "how do I even start reading this problem" nudge.
- level2: The core INSIGHT/observation, and WHY the constraints steer toward this class of \
  approach (e.g. "n ≤ 20 means we can afford something exponential"; "n up to 2·10^5 with a \
  1s limit rules out O(n^2), so we need O(n log n)"). Still no full method, no code.
- level3: The full APPROACH and WHY IT IS CORRECT — the complete reasoning a solver uses, \
  connecting the constraints and observations to the method. No code.
- level4: The full solution explanation, paraphrased in your own words (you may describe the \
  code structure, but do not copy the editorial verbatim). Tie it back to the earlier levels.

Rules:
- Ground everything in THIS problem's statement/constraints/editorial. Do not invent facts.
- Explain the *why*, not just the *what*. The reasoning chain (constraints → complexity \
  budget → approach family → specific technique → correctness) is the point.
- Keep math in readable form; LaTeX like $$$n \\le 10^5$$$ is fine.
- Return only the JSON object with keys level1..level4."""


class HintGenerationError(Exception):
    """The model failed to produce 4 valid graduated hints."""


ChatFn = Callable[..., Awaitable[dict[str, Any]]]


def _build_user_prompt(problem: Problem) -> str:
    return (
        f"# Problem: {problem.title or f'{problem.cf_contest_id}{problem.cf_index}'}\n"
        f"Rating: {problem.rating if problem.rating is not None else 'unknown'}\n"
        f"Tags: {', '.join(problem.tags) if problem.tags else 'none'}\n\n"
        f"## Statement\n{problem.statement_text or '(not available)'}\n\n"
        f"## Constraints\n{problem.constraints_text or '(see statement)'}\n\n"
        f"## Official editorial (may cover the whole contest — use the part for this problem)\n"
        f"{problem.editorial_text or '(not available)'}\n"
    )


def _validate(levels: list[str]) -> bool:
    if len(levels) != NUM_LEVELS or not all(level.strip() for level in levels):
        return False
    # Roughly increasing detail: each level at least ~80% the length of the previous.
    # A cheap guard against the model collapsing everything into level 1.
    return all(len(levels[i]) >= 0.8 * len(levels[i - 1]) for i in range(1, NUM_LEVELS))


async def generate_hints(problem: Problem, *, chat_fn: ChatFn = complete_json) -> list[str]:
    """Return 4 graduated hint strings (index 0 = level 1). Raises on failure.

    `chat_fn` is injectable so tests exercise the validation/retry logic without a live
    LLM call.
    """
    user = _build_user_prompt(problem)
    model = get_settings().llm_model_strong
    last_error = "unknown"

    for _ in range(2):  # one retry
        try:
            data = await chat_fn(
                model=model,
                system=_SYSTEM,
                user=user,
                schema=_SCHEMA,
                schema_name="graduated_hints",
            )
        except LLMError as exc:
            last_error = str(exc)
            continue

        levels = [str(data.get(f"level{i}", "")).strip() for i in range(1, NUM_LEVELS + 1)]
        if _validate(levels):
            return levels
        last_error = "output failed validation (empty or non-increasing levels)"

    raise HintGenerationError(f"could not generate 4 valid graduated hints: {last_error}")
