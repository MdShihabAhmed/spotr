"""Pure text heuristics for problem-exclusion flags.

Detect interactive / multiple-answer problems from the statement text. These flag
problems the (future) stress-tester can't validate — an interactive problem has no
fixed I/O, and a "print any" problem has no single ground-truth output. Kept as pure
functions so they're trivially testable and reusable across sources.
"""

from __future__ import annotations

import re

# Phrases signalling "multiple correct answers accepted".
_MULTI_ANSWER_PATTERNS = [
    r"print any",
    r"output any",
    r"any of them",
    r"any correct answer",
    r"if there are multiple",
    r"if there exist multiple",
    r"any valid",
    r"you may print",
]


def detect_interactive(text: str) -> bool:
    return "this is an interactive problem" in (text or "").lower()


def detect_multiple_answers(text: str) -> bool:
    lowered = (text or "").lower()
    return any(re.search(p, lowered) for p in _MULTI_ANSWER_PATTERNS)
