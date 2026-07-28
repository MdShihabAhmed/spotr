"""Grounding ingestion — turn the openly-licensed corpora into cited, retrievable chunks.

Pipeline (per corpus): **clone/pull the source repo → chunk at section level with
provenance → embed → upsert to Qdrant + mirror to Postgres (`GroundingChunk`)**. Run as a
CLI (`python -m app.ingestion.grounding`) or a compose `seed` service. The tutor later
`retrieve_grounding(query)` to cite relevant passages.

Design decisions:
- **Section-level chunks, not whole articles.** Each chunk carries exact provenance
  (`source, url, section heading, page`) — a license obligation *and* a credibility feature
  (every tutor claim can be traced to a citation). This fixes the old `ConceptNote`
  one-embedding-per-article bug.
- **Deterministic point ids** (`uuid5` of source+url+section) so a re-seed overwrites in
  place — idempotent, and resumable (already-present chunks are skipped unless `--reset`).
- **Batched embedding** to respect the free-tier daily cap; the job can be re-run to finish.
- **Everything injectable** (`embed_fn`, `client`, `session_maker`) so ingestion is tested
  offline against an in-memory Qdrant with a fake embedder — no network, no service.

Corpora: CP-Algorithms (CC BY-SA) is the
flagship prose corpus and is fully implemented; PyRival (Apache-2.0) adds reference
implementations. USACO Guide (MDX) and the CP Handbook (PDF) have registered loaders whose
parsing is left as a marked extension point — the framework ingests any `CorpusLoader`.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import subprocess
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.database import async_session_maker
from app.llm import embed_texts
from app.models import GroundingChunk
from app.vector_store import GroundingHit, ensure_collection, get_qdrant, search, upsert_chunks

# uuid5 namespace for grounding points — keeps ids stable across re-seeds.
_POINT_NAMESPACE = uuid.UUID("6f9b2c1e-1c3a-4a4e-9f0b-c0ffee000001")

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


class RawChunk:
    """A section-level chunk before embedding. `point_id` is deterministic, so the same
    chunk maps to the same Qdrant point / `GroundingChunk` row on every re-seed."""

    __slots__ = ("source", "title", "url", "content", "page")

    def __init__(self, *, source: str, title: str, url: str, content: str, page: int | None = None):
        self.source = source
        self.title = title
        self.url = url
        self.content = content
        self.page = page

    @property
    def point_id(self) -> str:
        return str(uuid.uuid5(_POINT_NAMESPACE, f"{self.source}|{self.url}|{self.title}"))

    def payload(self) -> dict:
        return {
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "page": self.page,
            "content": self.content,
        }


# --- chunking ----------------------------------------------------------------
_FRONT_MATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_MIN_CHUNK_CHARS = 80  # drop trivially short sections (nav stubs, one-liners)


def chunk_markdown(text: str, *, source: str, url: str) -> list[RawChunk]:
    """Split a markdown article into section-level chunks (one per `##` heading, plus the
    intro before the first one). Front matter is stripped; the H1 title prefixes each
    section title so a chunk reads standalone (e.g. "Dijkstra — Complexity")."""
    text = _FRONT_MATTER.sub("", text).strip()
    h1 = _H1.search(text)
    doc_title = h1.group(1).strip() if h1 else url.rsplit("/", 1)[-1]

    # Split on H2 headings, keeping the heading with its body.
    parts = re.split(r"^##\s+(.+)$", text, flags=re.MULTILINE)
    # re.split with one capture group yields: [intro, heading1, body1, heading2, body2, ...]
    chunks: list[RawChunk] = []
    intro = _H1.sub("", parts[0]).strip()  # drop the H1 line from the intro body
    if len(intro) >= _MIN_CHUNK_CHARS:
        chunks.append(RawChunk(source=source, title=doc_title, url=url, content=intro))
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        section = f"{heading}\n\n{body}".strip()
        if len(section) < _MIN_CHUNK_CHARS:
            continue
        chunks.append(
            RawChunk(source=source, title=f"{doc_title} — {heading}", url=url, content=section)
        )
    return chunks


# --- loaders -----------------------------------------------------------------
class CorpusLoader(ABC):
    """Fetches one corpus to `corpora_dir/<source>` and yields its section chunks."""

    source: str
    repo: str = ""  # git URL; empty for non-git corpora (override `ensure_local`)

    def local_dir(self) -> Path:
        return Path(get_settings().corpora_dir) / self.source

    def ensure_local(self) -> Path:
        """Shallow-clone (or pull) the source repo. Overridden for non-git corpora."""
        dest = self.local_dir()
        if dest.exists():
            subprocess.run(["git", "-C", str(dest), "pull", "--ff-only", "--depth", "1"], check=False)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--depth", "1", self.repo, str(dest)], check=True)
        return dest

    @abstractmethod
    def load(self, local: Path) -> Iterator[RawChunk]:
        """Yield section-level chunks parsed from the local corpus."""


class CpAlgorithmsLoader(CorpusLoader):
    """CP-Algorithms (CC BY-SA 4.0): markdown under `src/`. Maps each file to its public
    cp-algorithms.com URL for citations."""

    source = "cp-algorithms"
    repo = "https://github.com/cp-algorithms/cp-algorithms"
    site = "https://cp-algorithms.com"

    def load(self, local: Path) -> Iterator[RawChunk]:
        for md in sorted((local / "src").rglob("*.md")):
            rel = md.relative_to(local / "src").as_posix()  # e.g. "graph/dijkstra.md"
            url = f"{self.site}/{rel[:-3]}.html"  # → https://cp-algorithms.com/graph/dijkstra.html
            text = md.read_text(encoding="utf-8", errors="ignore")
            yield from chunk_markdown(text, source=self.source, url=url)


class PyRivalLoader(CorpusLoader):
    """PyRival (Apache-2.0): reference implementations. Each `.py` under `pyrival/` is one
    chunk (title = module path, content = source), cited by its GitHub blob URL."""

    source = "pyrival"
    repo = "https://github.com/cheran-senthil/PyRival"
    blob = "https://github.com/cheran-senthil/PyRival/blob/master"

    def load(self, local: Path) -> Iterator[RawChunk]:
        for py in sorted((local / "pyrival").rglob("*.py")):
            if py.name == "__init__.py":
                continue
            rel = py.relative_to(local).as_posix()
            code = py.read_text(encoding="utf-8", errors="ignore").strip()
            if len(code) < _MIN_CHUNK_CHARS:
                continue
            yield RawChunk(
                source=self.source, title=rel, url=f"{self.blob}/{rel}", content=code
            )


# Registered loaders. USACO Guide (MDX) and CP Handbook (PDF) are intentional extension
# points — add their loaders here and the pipeline ingests them unchanged.
_LOADERS: dict[str, CorpusLoader] = {
    loader.source: loader for loader in (CpAlgorithmsLoader(), PyRivalLoader())
}


def available_corpora() -> list[str]:
    return sorted(_LOADERS)


# --- orchestration -----------------------------------------------------------
async def _existing_point_ids(db: AsyncSession, source: str) -> set[str]:
    rows = await db.scalars(
        select(GroundingChunk.qdrant_point_id).where(GroundingChunk.source == source)
    )
    return {pid for pid in rows if pid}


async def ingest_corpus(
    loader: CorpusLoader,
    *,
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    client=None,
    embed_fn: EmbedFn = embed_texts,
    batch_size: int | None = None,
    limit: int | None = None,
    reset: bool = False,
    chunks: Iterator[RawChunk] | None = None,
) -> int:
    """Embed a corpus's chunks and upsert them to Qdrant + Postgres. Returns the number of
    chunks newly embedded. Resumable: already-present chunks are skipped unless `reset`.

    `chunks` lets callers/tests supply chunks directly (skipping the git clone + file
    parse); otherwise the loader fetches and parses the corpus.
    """
    settings = get_settings()
    client = client or get_qdrant()
    batch_size = batch_size or settings.embedding_batch_size
    await ensure_collection(client)

    async with session_maker() as db:
        if reset:
            await db.execute(delete(GroundingChunk).where(GroundingChunk.source == loader.source))
            await db.commit()
            seen: set[str] = set()
        else:
            seen = await _existing_point_ids(db, loader.source)

    source_chunks = chunks if chunks is not None else loader.load(loader.ensure_local())

    # Deduplicate within the run and skip already-embedded chunks (resume).
    pending: list[RawChunk] = []
    run_ids: set[str] = set()
    for chunk in source_chunks:
        pid = chunk.point_id
        if pid in seen or pid in run_ids:
            continue
        run_ids.add(pid)
        pending.append(chunk)
        if limit is not None and len(pending) >= limit:
            break

    embedded = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        vectors = await embed_fn([c.content for c in batch])
        await upsert_chunks(
            client, [(c.point_id, v, c.payload()) for c, v in zip(batch, vectors)]
        )
        async with session_maker() as db:
            for chunk in batch:
                db.add(
                    GroundingChunk(
                        source=chunk.source,
                        title=chunk.title[:255],
                        url=chunk.url[:512],
                        page=chunk.page,
                        content=chunk.content,
                        qdrant_point_id=chunk.point_id,
                    )
                )
            await db.commit()
        embedded += len(batch)
    return embedded


async def retrieve_grounding(
    query: str,
    *,
    client=None,
    embed_fn: EmbedFn = embed_texts,
    top_k: int | None = None,
    sources: list[str] | None = None,
) -> list[GroundingHit]:
    """Embed `query` and return the most relevant grounding chunks (with provenance) for
    the tutor to cite. Empty query or an empty collection yields no hits, never an error."""
    if not query.strip():
        return []
    client = client or get_qdrant()
    vectors = await embed_fn([query])
    return await search(client, vectors[0], limit=top_k, sources=sources)


# --- CLI ---------------------------------------------------------------------
async def _run(sources: list[str], *, limit: int | None, reset: bool) -> None:
    for name in sources:
        loader = _LOADERS[name]
        print(f"[grounding] ingesting {name} …")
        count = await ingest_corpus(loader, limit=limit, reset=reset)
        print(f"[grounding] {name}: embedded {count} new chunk(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the grounding knowledge base into Qdrant.")
    parser.add_argument(
        "--sources",
        nargs="*",
        default=available_corpora(),
        choices=available_corpora(),
        help="Corpora to ingest (default: all registered).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max chunks per corpus (testing).")
    parser.add_argument(
        "--reset", action="store_true", help="Re-embed from scratch (drop this source's rows first)."
    )
    args = parser.parse_args()
    asyncio.run(_run(args.sources, limit=args.limit, reset=args.reset))


if __name__ == "__main__":
    main()
