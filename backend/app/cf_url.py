"""Parse Codeforces problem URLs into their `(contest_id, index)` identity.

This is the *identity* layer for ingestion. A single problem is reachable at two
URL forms:

    https://codeforces.com/problemset/problem/1846/C
    https://codeforces.com/contest/1846/problem/C

Keying idempotency on the raw URL string would treat these as two different
problems — the second form would miss the lookup, attempt an INSERT, and die on
the `(cf_contest_id, cf_index)` unique constraint instead of returning the
existing row. So we parse both forms to the same `(contest_id, index)` at the
door and look up on that. `cf_url` is display data, not an identity key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Both accepted URL shapes, tolerant of http/https, optional www, a trailing
# slash, and a query string / fragment (which we ignore).
_PROBLEMSET = re.compile(
    r"^/problemset/problem/(?P<contest>\d+)/(?P<index>[A-Za-z]\d*)/?$"
)
_CONTEST = re.compile(
    r"^/contest/(?P<contest>\d+)/problem/(?P<index>[A-Za-z]\d*)/?$"
)

_BLOG = re.compile(r"/blog/entry/(\d+)")


def parse_blog_id(url: str) -> int | None:
    """Extract the numeric blog-entry id from a CF blog URL, or None.

    Editorials are blog entries; `blogEntry.view` fetches their content by this id via
    the (Cloudflare-free) API.
    """
    match = _BLOG.search(url or "")
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class ParsedProblem:
    contest_id: int
    index: str  # normalized upper-case, e.g. "C", "A", "F1"

    @property
    def canonical_url(self) -> str:
        """The `/problemset/` form — one stable string to store as `cf_url`."""
        return f"https://codeforces.com/problemset/problem/{self.contest_id}/{self.index}"


def parse_cf_url(url: str) -> ParsedProblem:
    """Parse a Codeforces problem URL into its `(contest_id, index)` identity.

    Raises `ValueError` if the URL is not a recognizable CF problem URL — the
    caller turns that into a 400 with a machine code, never a 500.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("empty url")

    # Normalize to just the path so host casing / www / scheme don't matter.
    without_scheme = re.sub(r"^https?://", "", raw, flags=re.IGNORECASE)
    without_host = re.sub(r"^(www\.)?codeforces\.com", "", without_scheme, flags=re.IGNORECASE)
    if without_host == without_scheme:
        raise ValueError(f"not a codeforces.com url: {url!r}")
    path = without_host.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path

    match = _PROBLEMSET.match(path) or _CONTEST.match(path)
    if match is None:
        raise ValueError(f"unrecognized codeforces problem url: {url!r}")

    return ParsedProblem(
        contest_id=int(match.group("contest")),
        index=match.group("index").upper(),
    )
