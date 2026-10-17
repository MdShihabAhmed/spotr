"""Grounding ingestion + retrieval tests.

Run against a real in-process Qdrant (`AsyncQdrantClient(location=":memory:")`) and sqlite,
with a deterministic fake embedder - so chunking, upsert, resume, and similarity retrieval
are exercised for real, no network and no service.
"""

import math

import pytest
from qdrant_client import AsyncQdrantClient

from app.config import get_settings
from app.ingestion.grounding import (
    RawChunk,
    chunk_markdown,
    ingest_corpus,
    retrieve_grounding,
)
from app.models import GroundingChunk
from sqlalchemy import func, select

DIM = get_settings().embedding_dim


def fake_embed():
    """Deterministic char-position embedding: overlapping text -> higher cosine, so a query
    that shares words with a chunk ranks it first. Vectors match the collection dimension."""

    async def embed(texts):
        out = []
        for t in texts:
            v = [0.0] * DIM
            for i, ch in enumerate(t.lower()):
                v[(i * 7 + ord(ch)) % DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out

    return embed


@pytest.fixture
async def qdrant():
    client = AsyncQdrantClient(location=":memory:")
    yield client
    await client.close()


# --- chunking ----------------------------------------------------------------
MD = """---
title: Dijkstra
---
# Dijkstra Algorithm

Dijkstra finds shortest paths from a source in a weighted graph with non-negative edges.
This intro is long enough to survive the minimum-length filter for a chunk of prose.

## Complexity

The complexity is O(n^2 + m) with an array, or O(m log n) with a priority queue for sparse graphs.

## Tiny

x
"""


def test_chunk_markdown_splits_sections_with_provenance():
    chunks = chunk_markdown(MD, source="docs", url="https://example.test/docs/dijkstra")
    titles = [c.title for c in chunks]
    # Intro (H1 title) + the Complexity section; the "Tiny" section is dropped (too short).
    assert "Dijkstra Algorithm" in titles
    assert "Dijkstra Algorithm - Complexity" in titles
    assert all("Tiny" not in t for t in titles)
    # Front matter stripped; provenance carried on every chunk.
    assert all(c.source == "docs" for c in chunks)
    assert all(c.url.endswith("/docs/dijkstra") for c in chunks)
    assert "title: Dijkstra" not in "\n".join(c.content for c in chunks)


def test_point_id_is_stable():
    a = RawChunk(source="s", title="t", url="u", content="c")
    b = RawChunk(source="s", title="t", url="u", content="DIFFERENT")
    # Same source|url|title -> same point id (idempotent re-seed overwrites in place).
    assert a.point_id == b.point_id


# --- ingest + retrieve -------------------------------------------------------
CHUNKS = [
    RawChunk(source="docs", title="Dijkstra", url="https://x/dijkstra",
             content="dijkstra shortest path priority queue non negative weights"),
    RawChunk(source="docs", title="DSU", url="https://x/dsu",
             content="disjoint set union find path compression union by rank"),
    RawChunk(source="snippets", title="graphs/dijkstra.py", url="https://x/blob/dijkstra.py",
             content="def dijkstra(graph, start): heap based single source shortest path"),
]


async def test_ingest_writes_rows_and_makes_chunks_retrievable(db_session_maker, qdrant):
    embedded = await ingest_corpus(
        _loader(), session_maker=db_session_maker, client=qdrant,
        embed_fn=fake_embed(), chunks=iter(CHUNKS),
    )
    assert embedded == 3

    async with db_session_maker() as db:
        count = await db.scalar(select(func.count()).select_from(GroundingChunk))
        assert count == 3
        row = await db.scalar(select(GroundingChunk).where(GroundingChunk.title == "Dijkstra"))
        assert row.qdrant_point_id  # mirrored into Postgres with its Qdrant id

    hits = await retrieve_grounding(
        "shortest path priority queue", client=qdrant, embed_fn=fake_embed(), top_k=2
    )
    assert hits
    assert hits[0].title == "Dijkstra"  # most similar ranks first
    assert hits[0].url == "https://x/dijkstra"  # provenance for the citation
    assert hits[0].citation() == {
        "source": "docs", "title": "Dijkstra", "url": "https://x/dijkstra", "page": None
    }


async def test_retrieval_can_filter_by_source(db_session_maker, qdrant):
    await ingest_corpus(
        _loader(), session_maker=db_session_maker, client=qdrant,
        embed_fn=fake_embed(), chunks=iter(CHUNKS),
    )
    hits = await retrieve_grounding(
        "dijkstra shortest path", client=qdrant, embed_fn=fake_embed(), sources=["snippets"]
    )
    assert hits
    assert all(h.source == "snippets" for h in hits)


async def test_resume_skips_already_embedded_unless_reset(db_session_maker, qdrant):
    # Resume/reset key on the loader's source, so use only that source's chunks (what a
    # real loader yields).
    cp_chunks = [c for c in CHUNKS if c.source == "docs"]
    embed = fake_embed()
    first = await ingest_corpus(
        _loader(), session_maker=db_session_maker, client=qdrant, embed_fn=embed,
        chunks=iter(cp_chunks),
    )
    assert first == len(cp_chunks)
    # Re-run without reset: everything already embedded -> nothing new, no duplicate rows.
    again = await ingest_corpus(
        _loader(), session_maker=db_session_maker, client=qdrant, embed_fn=embed,
        chunks=iter(cp_chunks),
    )
    assert again == 0
    async with db_session_maker() as db:
        assert await db.scalar(select(func.count()).select_from(GroundingChunk)) == len(cp_chunks)

    # reset=True re-embeds from scratch (rows dropped first, so no duplication).
    reset = await ingest_corpus(
        _loader(), session_maker=db_session_maker, client=qdrant, embed_fn=embed,
        chunks=iter(cp_chunks), reset=True,
    )
    assert reset == len(cp_chunks)
    async with db_session_maker() as db:
        assert await db.scalar(select(func.count()).select_from(GroundingChunk)) == len(cp_chunks)


async def test_empty_query_returns_no_hits(qdrant):
    assert await retrieve_grounding("   ", client=qdrant, embed_fn=fake_embed()) == []


def _loader():
    """A loader whose source matches the CHUNKS' sources; parsing is bypassed via `chunks=`."""
    from app.ingestion.grounding import UserGithubLoader

    return UserGithubLoader(source_key="docs", repo_url="https://github.com/x/y")


MDX = """---
id: two-pointers
title: 'Two Pointers'
---

## Resources

<Resources>
\t<Resource
\t\tsource="CF EDU"
\t\ttitle="Two Pointers Method"
\t>
\t\tvideo explanation of two pointers
\t</Resource>
</Resources>

## Sum of Two Values

<FocusProblem problem="sample1" />

Move l right when the sum is too small and r left when it is too big.

```cpp
#include <vector>
vector<pair<int, int>> nums(n);
```
"""


def test_mdx_lesson_chunks_as_markdown_without_component_tags():
    from app.ingestion.grounding import strip_mdx

    stripped = strip_mdx(MDX)
    assert "<Resource" not in stripped and "<FocusProblem" not in stripped
    assert 'source="CF EDU"' not in stripped  # props of a multi-line tag go with it
    assert "video explanation of two pointers" in stripped  # text between tags stays

    chunks = chunk_markdown(stripped, source="usaco", url="https://x/Two_Pointers.mdx")
    by_title = {c.title: c.content for c in chunks}
    # No H1 in MDX lessons, so the front matter title names the document. (The Resources
    # section is too short once its tags are gone, so the usual minimum drops it.)
    assert set(by_title) == {"Two Pointers - Sum of Two Values"}
    body = by_title["Two Pointers - Sum of Two Values"]
    # Fenced code keeps its angle brackets untouched.
    assert "#include <vector>" in body and "vector<pair<int, int>>" in body


def test_github_loader_reads_mdx_and_skips_tooling(tmp_path):
    from app.ingestion.grounding import UserGithubLoader

    (tmp_path / "content").mkdir()
    (tmp_path / "content" / "Two_Pointers.mdx").write_text(MDX)
    (tmp_path / ".storybook").mkdir()
    (tmp_path / ".storybook" / "main.py").write_text("x = 1\n" * 40)
    (tmp_path / "next.config.ts").write_text("export default {}\n" * 20)
    (tmp_path / "algo.cpp").write_text("int main() { return 0; }\n" * 10)

    loader = UserGithubLoader(source_key="usaco", repo_url="https://github.com/o/r")
    urls = {c.url for c in loader.load(tmp_path)}
    assert urls == {
        "https://github.com/o/r/blob/HEAD/content/Two_Pointers.mdx",
        "https://github.com/o/r/blob/HEAD/algo.cpp",
    }


def test_broken_clone_is_recloned(tmp_path, monkeypatch):
    """A dir left by an interrupted clone fails `git pull`; it must be re-cloned, not reused."""
    import subprocess

    from app.ingestion import grounding

    loader = grounding.UserGithubLoader(source_key="usaco", repo_url="https://github.com/o/r")
    dest = tmp_path / "usaco"
    (dest / ".git").mkdir(parents=True)
    monkeypatch.setattr(loader, "local_dir", lambda: dest)
    calls = []

    def fake_run(cmd, check):
        calls.append(cmd[1])
        if cmd[1] == "clone":
            assert not dest.exists()  # the broken dir was removed first
            dest.mkdir()
        return subprocess.CompletedProcess(cmd, 0 if cmd[1] == "clone" else 128)

    monkeypatch.setattr(grounding.subprocess, "run", fake_run)
    assert loader.ensure_local() == dest
    assert calls == ["-C", "clone"]


async def test_per_minute_rate_limit_is_waited_out_not_raised(db_session_maker, qdrant):
    """A couple of 429s (a per-minute window) pause and retry the same batch; the run finishes."""
    from app.llm import LLMRateLimitError

    inner, failures, pauses = fake_embed(), [2], []

    async def flaky(texts):
        if failures[0]:
            failures[0] -= 1
            raise LLMRateLimitError("429")
        return await inner(texts)

    async def no_sleep(seconds):
        pauses.append(seconds)

    embedded = await ingest_corpus(
        _loader(), session_maker=db_session_maker, client=qdrant,
        embed_fn=flaky, chunks=iter(CHUNKS), sleep=no_sleep,
    )
    assert embedded == len(CHUNKS)
    assert pauses == [get_settings().embedding_ratelimit_pause_seconds] * 2


async def test_persistent_rate_limit_propagates_for_the_long_wait(db_session_maker, qdrant):
    """A daily cap fails every pause, so it still reaches the caller's 24h reschedule."""
    from app.llm import LLMRateLimitError

    async def capped(texts):
        raise LLMRateLimitError("429")

    async def no_sleep(seconds):
        pass

    with pytest.raises(LLMRateLimitError):
        await ingest_corpus(
            _loader(), session_maker=db_session_maker, client=qdrant,
            embed_fn=capped, chunks=iter(CHUNKS), sleep=no_sleep,
        )
