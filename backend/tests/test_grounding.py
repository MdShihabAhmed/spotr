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
