"""Codeforces official API wrapper (metadata only — no HTML scraping here).

Two responsibilities:
- Fetch problem metadata (title, rating, tags) from the public `problemset.problems`
  endpoint. That endpoint returns *every* CF problem (~10MB), so the response is
  cached in Redis with a 24h TTL and indexed in-process — never fetched per problem.
- Enforce Codeforces' request etiquette (≤1 request / 2s, identifiable User-Agent).
  The throttle is **Redis-backed**, not an in-process lock, because the app may run
  with more than one worker and an in-process throttle would let N workers each make
  a request per interval.

Statement/editorial *scraping* lives in `cf_scraper.py` — the API and the scraper
are deliberately separate (different failure modes: the API is reliable JSON, the
HTML sits behind Cloudflare).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx

from app.cache import get_redis

API_BASE = "https://codeforces.com/api"
# Identifiable per CF etiquette — a generic bot UA is more likely to be blocked.
USER_AGENT = "ForcesAI/0.1 (+https://github.com/; upsolving-mentor)"

_PROBLEMSET_CACHE_KEY = "cf:problemset:problems"
_PROBLEMSET_TTL_SECONDS = 24 * 60 * 60

_THROTTLE_KEY = "cf:throttle"
_THROTTLE_INTERVAL_MS = 2000  # ≤1 outbound CF request per 2s, globally


class CodeforcesAPIError(Exception):
    """Raised when the CF API is unreachable or returns a non-OK status."""


@dataclass(frozen=True)
class ProblemMetadata:
    title: str
    rating: int | None
    tags: list[str]


class CodeforcesClient:
    """Async wrapper around the CF API.

    `redis` and `http` are injectable so tests can supply fakes / respx transports
    and skip the real Redis throttle. In the app, construct with defaults.
    """

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        redis=None,
        throttle: bool = True,
    ):
        self._http = http or httpx.AsyncClient(
            base_url=API_BASE,
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(15.0),
        )
        self._owns_http = http is None
        self._redis = redis if redis is not None else get_redis()
        self._throttle_enabled = throttle

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _throttle(self) -> None:
        """Block until it's this caller's turn under the global ≤1/2s budget.

        Uses `SET key 1 NX PX interval` as a distributed lease: whoever sets it
        first "owns" the current window; everyone else sleeps and retries. Simple,
        correct across workers, and good enough for the low request volume here.
        """
        if not self._throttle_enabled:
            return
        while True:
            acquired = await self._redis.set(
                _THROTTLE_KEY, "1", nx=True, px=_THROTTLE_INTERVAL_MS
            )
            if acquired:
                return
            await asyncio.sleep(_THROTTLE_INTERVAL_MS / 1000)

    async def _fetch_problemset(self) -> list[dict]:
        """Return the raw `problems` array from `problemset.problems`, Redis-cached."""
        cached = await self._redis.get(_PROBLEMSET_CACHE_KEY)
        if cached is not None:
            return json.loads(cached)

        await self._throttle()
        try:
            resp = await self._http.get("/problemset.problems")
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise CodeforcesAPIError(f"problemset.problems request failed: {exc}") from exc

        if body.get("status") != "OK":
            raise CodeforcesAPIError(f"CF API returned status {body.get('status')!r}")

        problems = body["result"]["problems"]
        await self._redis.set(
            _PROBLEMSET_CACHE_KEY, json.dumps(problems), ex=_PROBLEMSET_TTL_SECONDS
        )
        return problems

    async def get_problem_metadata(
        self, contest_id: int, index: str
    ) -> ProblemMetadata | None:
        """Metadata for one problem, or None if the CF API doesn't list it.

        `None` is an expected outcome (very new problems, gyms, ratings not yet
        assigned) — the caller treats missing metadata as non-fatal and can still
        rely on scraped/manual content.
        """
        problems = await self._fetch_problemset()
        for p in problems:
            if p.get("contestId") == contest_id and p.get("index") == index:
                return ProblemMetadata(
                    title=p.get("name", ""),
                    rating=p.get("rating"),
                    tags=list(p.get("tags", [])),
                )
        return None
