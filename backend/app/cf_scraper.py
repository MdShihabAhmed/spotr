"""Scrape and parse a Codeforces problem *statement* page.

Kept separate from `cf_client.py` (the JSON API) on purpose: this endpoint is HTML
behind Cloudflare, so it has a completely different failure mode. Scrape failure is
an *expected* state, not a bug — Codeforces intermittently blocks datacenter/bot
traffic. Callers turn a `ScrapeError` into a clear, actionable `ingestion_error`
("couldn't fetch the statement — paste it manually"), never a 500.

`parse_statement_html` is a pure function so the parser is tested against recorded
HTML fixtures offline, with no live scraping in CI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag
from curl_cffi.requests import AsyncSession

# Heuristic phrases signalling "multiple correct answers accepted" — such problems
# are excluded from stress testing (a brute force can't be the single ground truth).
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


class ScrapeError(Exception):
    """Statement page couldn't be fetched or parsed (often Cloudflare)."""


# Link text signalling an editorial/tutorial, across CF's language labels.
_EDITORIAL_LINK_RE = re.compile(r"editorial|tutorial|разбор", re.IGNORECASE)


@dataclass
class ScrapedStatement:
    title: str = ""
    statement_text: str = ""
    constraints_text: str | None = None
    sample_tests: list[dict] = field(default_factory=list)  # {input, expected_output}
    time_limit_ms: int | None = None
    memory_limit_mb: int | None = None
    is_interactive: bool = False
    has_multiple_answers: bool = False
    editorial_url: str | None = None  # auto-discovered from the "Contest materials" box


def _pre_text(pre: Tag) -> str:
    """Text of a CF sample <pre>.

    Newer CF pages wrap each line in its own <div> (for multi-test grouping); older
    ones put raw text with <br>. Handle both: prefer the per-line divs, else fall
    back to the pre's own text with newlines preserved.
    """
    line_divs = pre.find_all("div", recursive=False)
    if line_divs:
        lines = [d.get_text() for d in line_divs]
    else:
        lines = pre.get_text("\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def _limit_value(block: Tag | None) -> str | None:
    """Text of a `.time-limit` / `.memory-limit` block minus its property title."""
    if block is None:
        return None
    title = block.find(class_="property-title")
    if title is not None:
        title.extract()
    return block.get_text(" ", strip=True) or None


def parse_statement_html(html: str) -> ScrapedStatement:
    """Parse a CF problem page's HTML into a `ScrapedStatement`.

    Raises `ScrapeError` if the page has no `.problem-statement` container — that's
    what a Cloudflare challenge / error page looks like, and it must not be mistaken
    for a problem with an empty statement.
    """
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find(class_="problem-statement")
    if root is None or not isinstance(root, Tag):
        raise ScrapeError("no .problem-statement found (page may be a Cloudflare block)")

    result = ScrapedStatement()

    header = root.find(class_="header")
    if isinstance(header, Tag):
        title_el = header.find(class_="title")
        if title_el is not None:
            result.title = title_el.get_text(strip=True)
        result.time_limit_ms = _parse_time_limit(_limit_value(header.find(class_="time-limit")))
        result.memory_limit_mb = _parse_memory_limit(_limit_value(header.find(class_="memory-limit")))

    # Statement body: the first direct-child <div> of the container with no class is
    # the problem prose (CF gives named classes to every other section).
    for child in root.find_all("div", recursive=False):
        if not child.get("class"):
            result.statement_text = child.get_text("\n", strip=True)
            break

    input_spec = root.find(class_="input-specification")
    if isinstance(input_spec, Tag):
        section_title = input_spec.find(class_="section-title")
        if section_title is not None:
            section_title.extract()
        result.constraints_text = input_spec.get_text("\n", strip=True) or None

    result.sample_tests = _parse_samples(root)

    full_text = root.get_text(" ", strip=True)
    result.is_interactive = (
        detect_interactive(full_text) or root.find(class_="interaction") is not None
    )
    result.has_multiple_answers = detect_multiple_answers(full_text)

    # The editorial link lives OUTSIDE .problem-statement (page sidebar), so search
    # the whole document, not just the statement root.
    result.editorial_url = _find_editorial_url(soup)

    return result


def _find_editorial_url(soup: BeautifulSoup) -> str | None:
    """Find the editorial/tutorial blog link in the page's 'Contest materials' box.

    A contest page links both an "Announcement" and an "Editorial" (sometimes in
    several languages). We want the editorial, preferring the English one.
    """
    candidates: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=re.compile(r"/blog/entry/\d+")):
        text = a.get_text(" ", strip=True)
        if _EDITORIAL_LINK_RE.search(text):
            candidates.append((text.lower(), a["href"]))
    if not candidates:
        return None
    for text, href in candidates:
        if "(en)" in text or "english" in text:
            return _absolute_cf_url(href)
    return _absolute_cf_url(candidates[0][1])


def _absolute_cf_url(href: str) -> str:
    return href if href.startswith("http") else f"https://codeforces.com{href}"


def detect_interactive(text: str) -> bool:
    """Heuristic interactive-problem detection from statement text (also used for
    manually pasted statements, where there's no `.interaction` element to check)."""
    return "this is an interactive problem" in text.lower()


def detect_multiple_answers(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(p, lowered) for p in _MULTI_ANSWER_PATTERNS)


def _parse_samples(root: Tag) -> list[dict]:
    samples: list[dict] = []
    sample_root = root.find(class_="sample-tests")
    if not isinstance(sample_root, Tag):
        return samples
    for test in sample_root.find_all(class_="sample-test"):
        inputs = test.find_all(class_="input")
        outputs = test.find_all(class_="output")
        for in_block, out_block in zip(inputs, outputs):
            in_pre = in_block.find("pre")
            out_pre = out_block.find("pre")
            if in_pre is None or out_pre is None:
                continue
            samples.append(
                {"input": _pre_text(in_pre), "expected_output": _pre_text(out_pre)}
            )
    return samples


def _parse_time_limit(text: str | None) -> int | None:
    """'2 seconds' / '500 ms' → milliseconds."""
    if not text:
        return None
    m = re.search(r"([\d.]+)\s*(seconds?|s|ms|milliseconds?)", text, re.IGNORECASE)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).lower()
    return int(value) if unit.startswith("ms") or unit == "milliseconds" else int(value * 1000)


def _parse_memory_limit(text: str | None) -> int | None:
    """'256 megabytes' → 256 (MB)."""
    if not text:
        return None
    m = re.search(r"(\d+)\s*(megabytes?|mb)", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


async def scrape_problem_statement(contest_id: int, index: str) -> ScrapedStatement:
    """Fetch and parse a problem's statement page. Raises `ScrapeError` on failure.

    Uses `curl_cffi` with `impersonate="chrome"`: Codeforces' Cloudflare blocks plain
    HTTP clients (403) by TLS/JA3 fingerprint, but a client that mirrors Chrome's
    fingerprint passes. The raw HTML preserves the `$$$...$$$` LaTeX, so constraints
    and formulas survive intact — unlike a copy-paste of the rendered page.
    """
    url = f"https://codeforces.com/problemset/problem/{contest_id}/{index}"
    try:
        async with AsyncSession() as session:
            resp = await session.get(url, impersonate="chrome", timeout=25)
            status, text = resp.status_code, resp.text
    except Exception as exc:  # curl_cffi network/transport errors
        raise ScrapeError(f"failed to fetch statement page: {exc}") from exc

    if status != 200:
        raise ScrapeError(f"statement page returned HTTP {status} (Cloudflare may be blocking)")
    return parse_statement_html(text)
