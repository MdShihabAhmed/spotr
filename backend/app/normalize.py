"""Normalize a user-provided problem into clean structured fields via the LLM.

The user gives us a problem as **pasted text** (rendered-page copy-paste mangles LaTeX
and line breaks in browser-dependent ways) or a **screenshot** (Gemini flash is
multimodal — it reads *rendered* math and reconstructs it as `$$$…$$$` LaTeX, which is
strictly better than OCR). Either way we return clean `{statement, constraints, samples,
limits}` for the user to review/edit before ingesting — normalization is interactive, not
a background guess, because vision/paste can err and the user should confirm.

`chat_fn` is injectable so the parsing/validation is tested without a live LLM.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.llm import LLMError, complete_json

ChatFn = Callable[..., Awaitable[dict[str, Any]]]


class NormalizeError(Exception):
    """The LLM couldn't extract a usable problem from the input."""


@dataclass
class NormalizedProblem:
    statement: str
    constraints: str | None = None
    samples: list[dict] = field(default_factory=list)  # [{input, expected_output}]
    time_limit_ms: int | None = None
    memory_limit_mb: int | None = None


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "statement": {"type": "string"},
        "constraints": {"type": "string"},
        "samples": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "input": {"type": "string"},
                    "expected_output": {"type": "string"},
                },
                "required": ["input", "expected_output"],
                "additionalProperties": False,
            },
        },
        "time_limit_ms": {"type": ["integer", "null"]},
        "memory_limit_mb": {"type": ["integer", "null"]},
    },
    "required": ["statement", "constraints", "samples", "time_limit_ms", "memory_limit_mb"],
    "additionalProperties": False,
}

_SYSTEM = """\
You extract a competitive-programming problem into clean, structured fields from a user's \
pasted text or a screenshot. Rules:
- Reproduce the FULL problem statement faithfully. Do not summarize, solve, or add commentary.
- Preserve all mathematics as Codeforces-style LaTeX delimited by triple dollars, e.g. \
$$$n \\le 2 \\cdot 10^5$$$. Fix broken/duplicated math and line breaks from copy-paste.
- Put the input format / constraints section (bounds on n, ranges, etc.) in `constraints`; \
if there is no distinct section, leave it an empty string.
- Extract every sample test as {input, expected_output}, preserving exact whitespace/newlines.
- Fill `time_limit_ms` and `memory_limit_mb` if stated (e.g. "2 seconds" -> 2000), else null.
- Return only the JSON object."""


def _build_user_prompt(text: str | None) -> str:
    if text:
        return "Here is the pasted problem. Clean and structure it:\n\n" + text
    return "Read the attached screenshot of the problem and structure it."


async def normalize(
    *,
    text: str | None = None,
    image_data_uri: str | None = None,
    chat_fn: ChatFn = complete_json,
) -> NormalizedProblem:
    """Return a cleaned `NormalizedProblem` from pasted text or a screenshot data URI."""
    if not text and not image_data_uri:
        raise NormalizeError("provide either pasted text or a screenshot")

    model = get_settings().llm_model_strong  # math/vision fidelity matters here
    try:
        data = await chat_fn(
            model=model,
            system=_SYSTEM,
            user=_build_user_prompt(text),
            schema=_SCHEMA,
            schema_name="normalized_problem",
            images=[image_data_uri] if image_data_uri else None,
        )
    except LLMError as exc:
        raise NormalizeError(f"normalization failed: {exc}") from exc

    statement = str(data.get("statement", "")).strip()
    if not statement:
        raise NormalizeError("no statement could be extracted from the input")

    constraints = str(data.get("constraints", "")).strip() or None
    samples = [
        {"input": str(s.get("input", "")), "expected_output": str(s.get("expected_output", ""))}
        for s in data.get("samples", [])
        if isinstance(s, dict)
    ]
    return NormalizedProblem(
        statement=statement,
        constraints=constraints,
        samples=samples,
        time_limit_ms=data.get("time_limit_ms"),
        memory_limit_mb=data.get("memory_limit_mb"),
    )
