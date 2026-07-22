"""Fetch a Codeforces editorial via the official API (`blogEntry.view`).

`blogEntry.view` lives on `api.codeforces.com`, which is **not** Cloudflare-blocked,
and returns the blog's full content as HTML with `$$$` LaTeX intact. An editorial
blog usually covers a whole contest; we store the full text here (LaTeX preserved)
and leave per-problem slicing to hint generation (step 3), where the LLM can pick out
the relevant problem's section more robustly than heading-regex would.

Best-effort: the editorial is optional for reaching `ready`, so any failure returns
None rather than raising.
"""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from app.cf_url import parse_blog_id

_BLOG_API = "https://codeforces.com/api/blogEntry.view"


async def fetch_editorial_text(
    blog_url: str, *, http: httpx.AsyncClient | None = None
) -> str | None:
    """Return the editorial blog's text (LaTeX preserved), or None on any failure."""
    blog_id = parse_blog_id(blog_url)
    if blog_id is None:
        return None

    client = http or httpx.AsyncClient(timeout=httpx.Timeout(25.0))
    try:
        resp = await client.get(_BLOG_API, params={"blogEntryId": blog_id})
        resp.raise_for_status()
        body = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if http is None:
            await client.aclose()

    if body.get("status") != "OK":
        return None
    content_html = body.get("result", {}).get("content") or ""
    if not content_html:
        return None
    # Strip HTML tags but keep the $$$...$$$ LaTeX, which lives in the text nodes.
    return BeautifulSoup(content_html, "html.parser").get_text("\n", strip=True) or None
