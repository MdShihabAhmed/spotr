"""Qdrant vector store - the semantic-retrieval half of grounding.

Qdrant holds *only* the grounding sources the user added, chunked at section level with
provenance in each point's payload. The tutor embeds a query and searches here to cite
relevant passages. (Ladder hints are NOT here - they live in Postgres, gated by the
revealed-set filter, not similarity.)

The collection name encodes the embedding model (`Settings.grounding_collection`), so a
model change is a different collection, never a silent mix of incompatible vectors.

Everything takes an injectable `client`, and `AsyncQdrantClient(location=":memory:")`
gives tests a real in-process Qdrant with no service - so retrieval logic is tested for
real, not mocked away.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from qdrant_client import AsyncQdrantClient, models

from app.config import get_settings


@lru_cache
def get_qdrant() -> AsyncQdrantClient:
    """Process-wide async Qdrant client (points at `QDRANT_URL`)."""
    settings = get_settings()
    return AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)


@dataclass(frozen=True)
class GroundingHit:
    """One retrieved chunk with its provenance - everything a citation needs."""

    source: str
    title: str
    url: str
    page: int | None
    content: str
    score: float

    def citation(self) -> dict:
        """The client-facing citation shape ({source, title, url, page})."""
        return {"source": self.source, "title": self.title, "url": self.url, "page": self.page}


async def ensure_collection(
    client: AsyncQdrantClient, *, name: str | None = None, dim: int | None = None
) -> str:
    """Create the grounding collection (cosine distance) if it doesn't exist. Idempotent
    so `seed` can be re-run and resumed. Returns the collection name."""
    settings = get_settings()
    name = name or settings.grounding_collection
    dim = dim or settings.embedding_dim
    if not await client.collection_exists(name):
        await client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        )
    return name


async def upsert_chunks(
    client: AsyncQdrantClient,
    points: list[tuple[str, list[float], dict]],
    *,
    name: str | None = None,
) -> None:
    """Upsert `(point_id, vector, payload)` triples. Re-embedding the same chunk id
    overwrites it, so a re-seed doesn't duplicate."""
    if not points:
        return
    settings = get_settings()
    await client.upsert(
        collection_name=name or settings.grounding_collection,
        points=[
            models.PointStruct(id=pid, vector=vector, payload=payload)
            for pid, vector, payload in points
        ],
    )


async def delete_source(client: AsyncQdrantClient, source: str, *, name: str | None = None) -> None:
    """Remove every point belonging to `source` (payload filter). Used when a user deletes one
    of their added grounding sources - the Postgres `GroundingChunk` rows are removed alongside.
    Idempotent: deleting an absent source is a no-op."""
    settings = get_settings()
    collection = name or settings.grounding_collection
    if not await client.collection_exists(collection):
        return
    await client.delete(
        collection_name=collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key="source", match=models.MatchValue(value=source))]
            )
        ),
    )


async def search(
    client: AsyncQdrantClient,
    query_vector: list[float],
    *,
    limit: int | None = None,
    name: str | None = None,
    sources: list[str] | None = None,
) -> list[GroundingHit]:
    """Return the most similar chunks as `GroundingHit`s (provenance + score). `sources`
    optionally restricts to specific corpora."""
    settings = get_settings()
    query_filter = None
    if sources:
        query_filter = models.Filter(
            must=[models.FieldCondition(key="source", match=models.MatchAny(any=sources))]
        )
    result = await client.query_points(
        collection_name=name or settings.grounding_collection,
        query=query_vector,
        limit=limit or settings.grounding_top_k,
        query_filter=query_filter,
        with_payload=True,
    )
    hits: list[GroundingHit] = []
    for point in result.points:
        payload = point.payload or {}
        hits.append(
            GroundingHit(
                source=payload.get("source", ""),
                title=payload.get("title", ""),
                url=payload.get("url", ""),
                page=payload.get("page"),
                content=payload.get("content", ""),
                score=point.score,
            )
        )
    return hits
