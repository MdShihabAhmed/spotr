"""Codeforces problem source — permitted API only (no HTML scraping).

Folds together the former `cf_url` (URL identity parsing), `cf_client` (metadata via
`problemset.problems`, Redis-cached + throttled), and `editorial_scraper` (editorial via
`blogEntry.view`). All three CF API endpoints live on `api.codeforces.com`, which is not
Cloudflare-blocked. Statement/constraints/samples are supplied by the user, not fetched.
"""

from __future__ import annotations

import asyncio
import json
import re

import httpx
from bs4 import BeautifulSoup

from app.cache import get_redis
from app.sources.base import (
    ProblemIdentity,
    ProblemSource,
    SourceError,
    SourceMetadata,
    register_source,
)

API_BASE = "https://codeforces.com/api"
USER_AGENT = "Spotr/0.1 (+https://github.com/; upsolving-mentor)"

_PROBLEMSET_CACHE_KEY = "cf:problemset:problems"
_PROBLEMSET_TTL_SECONDS = 24 * 60 * 60
_THROTTLE_KEY = "cf:throttle"
_THROTTLE_INTERVAL_MS = 2000  # ≤1 outbound CF request per 2s, globally (Redis lease)

# Both problem-URL forms map to the same (contest, index) identity; the blog form
# identifies an editorial. Tolerant of scheme/www/trailing-slash/query/fragment.
_PROBLEMSET = re.compile(r"^/problemset/problem/(?P<contest>\d+)/(?P<index>[A-Za-z]\d*)/?$")
_CONTEST = re.compile(r"^/contest/(?P<contest>\d+)/problem/(?P<index>[A-Za-z]\d*)/?$")
_BLOG = re.compile(r"/blog/entry/(\d+)")


def parse_blog_id(url: str) -> int | None:
    match = _BLOG.search(url or "")
    return int(match.group(1)) if match else None


class CodeforcesSource(ProblemSource):
    name = "codeforces"

    def __init__(self, *, http: httpx.AsyncClient | None = None, redis=None, throttle: bool = True):
        # Injectable for tests (respx transport / fake redis / throttle off).
        self._http = http
        self._redis = redis
        self._throttle_enabled = throttle

    # --- identity ------------------------------------------------------------
    def parse_identity(self, url: str) -> ProblemIdentity:
        raw = (url or "").strip()
        if not raw:
            raise ValueError("empty url")
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

        contest_id = int(match.group("contest"))
        index = match.group("index").upper()
        return ProblemIdentity(
            source=self.name,
            external_id=f"{contest_id}{index}",  # "1846C"
            url=f"https://codeforces.com/problemset/problem/{contest_id}/{index}",
            source_metadata={"contest_id": contest_id, "index": index},
        )

    # --- permitted fetches ---------------------------------------------------
    async def fetch_metadata(self, source_metadata: dict) -> SourceMetadata | None:
        contest_id = source_metadata.get("contest_id")
        index = source_metadata.get("index")
        if contest_id is None or index is None:
            return None
        problems = await self._fetch_problemset()
        for p in problems:
            if p.get("contestId") == contest_id and p.get("index") == index:
                return SourceMetadata(
                    title=p.get("name", ""),
                    rating=p.get("rating"),
                    tags=list(p.get("tags", [])),
                )
        return None

    async def fetch_editorial(self, editorial_url: str) -> str:
        """Editorial text via `blogEntry.view`. Raises `SourceError` (with a
        user-actionable reason) on every failure path — a swallowed `None` here would
        surface downstream as an unexplained "no editorial captured"."""
        blog_id = parse_blog_id(editorial_url)
        if blog_id is None:
            raise SourceError(
                f"{editorial_url!r} isn't a Codeforces blog link "
                "(expected a .../blog/entry/<id> URL)"
            )
        http, owns = self._client()
        try:
            await self._throttle()
            resp = await http.get(f"{API_BASE}/blogEntry.view", params={"blogEntryId": blog_id})
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"blogEntry.view request failed: {exc}") from exc
        finally:
            if owns:
                await http.aclose()
        if body.get("status") != "OK":
            # CF puts the human reason in `comment` (e.g. "Blog entry ... not found").
            raise SourceError(f"Codeforces API: {body.get('comment') or body.get('status')}")
        content_html = body.get("result", {}).get("content") or ""
        if not content_html:
            raise SourceError("the blog entry has no content")
        # Strip HTML but keep the $$$...$$$ LaTeX (it lives in the text nodes).
        text = BeautifulSoup(content_html, "html.parser").get_text("\n", strip=True)
        if not text:
            raise SourceError("the blog entry's content was empty after cleaning")
        return text

    # --- internals -----------------------------------------------------------
    def _client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._http is not None:
            return self._http, False
        return httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=httpx.Timeout(25.0)), True

    def _redis_client(self):
        return self._redis if self._redis is not None else get_redis()

    async def _throttle(self) -> None:
        """Block until it's this caller's turn under the global ≤1/2s budget, via a
        Redis `SET NX PX` lease (correct across >1 uvicorn worker)."""
        if not self._throttle_enabled:
            return
        redis = self._redis_client()
        while True:
            if await redis.set(_THROTTLE_KEY, "1", nx=True, px=_THROTTLE_INTERVAL_MS):
                return
            await asyncio.sleep(_THROTTLE_INTERVAL_MS / 1000)

    async def _fetch_problemset(self) -> list[dict]:
        redis = self._redis_client()
        cached = await redis.get(_PROBLEMSET_CACHE_KEY)
        if cached is not None:
            return json.loads(cached)

        http, owns = self._client()
        try:
            await self._throttle()
            resp = await http.get(f"{API_BASE}/problemset.problems")
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise SourceError(f"problemset.problems request failed: {exc}") from exc
        finally:
            if owns:
                await http.aclose()

        if body.get("status") != "OK":
            raise SourceError(f"CF API returned status {body.get('status')!r}")
        problems = body["result"]["problems"]
        await redis.set(_PROBLEMSET_CACHE_KEY, json.dumps(problems), ex=_PROBLEMSET_TTL_SECONDS)
        return problems


register_source(CodeforcesSource())
