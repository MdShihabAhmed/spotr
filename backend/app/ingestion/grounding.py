"""Grounding ingestion: turn a user-added source into cited, retrievable chunks.

Pipeline (per source): fetch it locally (clone a repo, or read an uploaded file), chunk at
section level with provenance, embed, then upsert to Qdrant and mirror to Postgres
(`GroundingChunk`). The tutor later calls `retrieve_grounding(query)` to cite relevant
passages.

Design decisions:
- Section-level chunks, not whole documents. Each chunk carries exact provenance
  (`source, url, section heading, page`) so every tutor claim can be traced to a citation.
- Deterministic point ids (`uuid5` of source+url+section) so a re-run overwrites in place:
  idempotent, and resumable (already-present chunks are skipped unless `reset`).
- Batched embedding, so a run that hits a provider's daily cap can be re-run to finish.
- Everything injectable (`embed_fn`, `client`, `session_maker`) so ingestion is tested
  offline against an in-memory Qdrant with a fake embedder, no network and no service.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import stat
import subprocess
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.database import async_session_maker
from app.llm import LLMRateLimitError, embed_texts
from app.models import GroundingChunk
from app.vector_store import GroundingHit, ensure_collection, get_qdrant, search, upsert_chunks

logger = logging.getLogger("spotr.grounding")

# uuid5 namespace for grounding points - keeps ids stable across re-seeds.
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
_FM_TITLE = re.compile(r"^title:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)
_H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_MIN_CHUNK_CHARS = 80  # drop trivially short sections (nav stubs, one-liners)

# MDX component tags (`<Resources>`, `<FocusProblem problem="x" />`). Only capitalized names:
# lowercase `<` is ordinary prose/math (`$a < b$`) or plain HTML, which we leave alone.
_JSX_TAG = re.compile(r"</?[A-Z][\w.]*(?:\s[^<>]*)?/?>")
_JSX_OPEN_UNCLOSED = re.compile(r"^\s*<[A-Z][\w.]*(?:\s[^<>]*)?$")


def strip_mdx(text: str) -> str:
    """Drop MDX component tags so an `.mdx` lesson chunks as plain markdown. The text between
    tags is kept (it's often the prose, e.g. a `<Resource>` description), and fenced code is
    never touched (C++ `#include <vector>` / Java generics look like tags)."""
    out: list[str] = []
    in_fence = False
    in_tag = False  # inside a tag whose props span several lines
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
        elif in_fence:
            out.append(line)
        elif in_tag:
            in_tag = ">" not in line
        elif _JSX_OPEN_UNCLOSED.match(line):
            in_tag = True
        else:
            out.append(_JSX_TAG.sub("", line))
    return "\n".join(out)


def chunk_markdown(text: str, *, source: str, url: str) -> list[RawChunk]:
    """Split a markdown article into section-level chunks (one per `##` heading, plus the
    intro before the first one). Front matter is stripped; the document title (H1, else the
    front matter `title:`) prefixes each section title so a chunk reads standalone (e.g.
    "Dijkstra - Complexity")."""
    front = _FRONT_MATTER.match(text)
    fm_title = _FM_TITLE.search(front.group(0)) if front else None
    text = _FRONT_MATTER.sub("", text).strip()
    h1 = _H1.search(text)
    if h1:
        doc_title = h1.group(1).strip()
    elif fm_title:
        doc_title = fm_title.group(1).strip()
    else:
        doc_title = url.rsplit("/", 1)[-1]

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
            RawChunk(source=source, title=f"{doc_title} - {heading}", url=url, content=section)
        )
    return chunks


# --- loaders -----------------------------------------------------------------
def _force_remove(func, path, _exc) -> None:
    """rmtree error hook: git marks pack files read-only, which Windows refuses to delete."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


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
            pulled = subprocess.run(
                ["git", "-C", str(dest), "pull", "--ff-only", "--depth", "1"], check=False
            )
            if pulled.returncode == 0:
                return dest
            # An interrupted clone leaves a `.git` with no commits. Pull fails on it forever and
            # the walk would see an empty tree, so the source would reach `ready` with 0 chunks.
            # Start over instead of silently reusing it.
            shutil.rmtree(dest, onexc=_force_remove)
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", self.repo, str(dest)], check=True)
        return dest

    @abstractmethod
    def load(self, local: Path) -> Iterator[RawChunk]:
        """Yield section-level chunks parsed from the local corpus."""


# --- orchestration -----------------------------------------------------------
async def _embed_pacing_rate_limits(
    embed_fn: EmbedFn, texts: list[str], sleep: Callable[[float], Awaitable[None]]
) -> list[list[float]]:
    """Embed one batch, sitting out per-minute rate limits. The in-call 429 retries give up
    within ~30s, shorter than a provider's 60s window, so a per-minute token limit used to
    surface as LLMRateLimitError and park the whole source for a day. Waiting a full window
    and retrying the same batch rides that out; a real daily cap still fails every pause and
    propagates, so the caller's long wait only applies when it's needed."""
    settings = get_settings()
    for _ in range(settings.embedding_ratelimit_pauses):
        try:
            return await embed_fn(texts)
        except LLMRateLimitError:
            logger.info("embedding rate-limited; pausing %ss", settings.embedding_ratelimit_pause_seconds)
            await sleep(settings.embedding_ratelimit_pause_seconds)
    return await embed_fn(texts)


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
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    """Embed a corpus's chunks and upsert them to Qdrant + Postgres. Returns the number of
    chunks newly embedded. Resumable: already-present chunks are skipped unless `reset`.

    `chunks` lets callers/tests supply chunks directly (skipping the git clone + file
    parse); otherwise the loader fetches and parses the corpus. `on_progress(done, total)` is
    called once the total is known and after each embedded batch - the source pipeline uses it
    to surface live progress (`done` counts already-embedded chunks from prior runs too)."""
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

    already = len(seen)  # chunks embedded on prior runs (resume)
    total = already + len(pending)
    if on_progress is not None:
        await on_progress(already, total)  # so the total shows before the first batch lands

    embedded = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        vectors = await _embed_pacing_rate_limits(embed_fn, [c.content for c in batch], sleep)
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
        if on_progress is not None:
            await on_progress(already + embedded, total)
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


# --- user-provided sources (bring your own references) -----------------------
# Files we chunk from a user's GitHub repo. Prose is section/paragraph-chunked; code files
# become one chunk each. Everything else (images, binaries) is skipped.
_MARKDOWN_EXTS = {".md", ".markdown", ".mdx"}
_PROSE_EXTS = _MARKDOWN_EXTS | {".txt", ".rst"}
# No .js/.ts: in a reference repo they're almost always the website/tooling around the content
# (next.config.ts, storybook, UI), and embedding them buried the actual lessons in noise.
_CODE_EXTS = {".py", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".java", ".kt", ".rs", ".go", ".sql"}
_SKIP_DIRS = {"node_modules", "vendor", "dist", "build", "__pycache__"}  # plus any dot-dir
_MAX_FILES = 4000  # a guard so a giant repo can't embed forever / exhaust the day's quota
_MAX_FILE_BYTES = 512_000  # skip huge generated files
_CHUNK_TARGET_CHARS = 1500  # greedy paragraph packing target for non-markdown prose


def chunk_text(
    text: str, *, source: str, title: str, url: str, page: int | None = None
) -> list[RawChunk]:
    """Chunk plain prose (non-markdown) by packing blank-line-separated paragraphs up to
    ~`_CHUNK_TARGET_CHARS`. Used for `.txt`/`.rst` files and extracted PDF pages."""
    text = text.strip()
    if len(text) < _MIN_CHUNK_CHARS:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[RawChunk] = []
    buf = ""
    part = 1
    for para in paragraphs:
        if buf and len(buf) + len(para) + 2 > _CHUNK_TARGET_CHARS:
            chunks.append(RawChunk(source=source, title=title, url=f"{url}#{part}", content=buf, page=page))
            part += 1
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if len(buf) >= _MIN_CHUNK_CHARS:
        chunks.append(RawChunk(source=source, title=title, url=f"{url}#{part}", content=buf, page=page))
    return chunks


def _github_blob_base(repo_url: str) -> str:
    """`https://github.com/o/r(.git)` -> `https://github.com/o/r/blob/HEAD` for file citations."""
    return f"{repo_url.rstrip('/').removesuffix('.git')}/blob/HEAD"


class UserGithubLoader(CorpusLoader):
    """A user-added public GitHub repo. Prose (md/mdx/txt/rst) is section/paragraph-chunked; code
    files become one chunk each. Cited by the file's GitHub blob URL."""

    def __init__(self, *, source_key: str, repo_url: str):
        self.source = source_key
        self.repo = repo_url

    def load(self, local: Path) -> Iterator[RawChunk]:
        blob = _github_blob_base(self.repo)
        seen = 0
        for path in sorted(local.rglob("*")):
            if seen >= _MAX_FILES:
                break
            if not path.is_file():
                continue
            rel_parts = path.relative_to(local).parts
            if any(part in _SKIP_DIRS or part.startswith(".") for part in rel_parts):
                continue
            ext = path.suffix.lower()
            if ext not in _PROSE_EXTS and ext not in _CODE_EXTS:
                continue
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                body = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            seen += 1
            rel = path.relative_to(local).as_posix()
            url = f"{blob}/{rel}"
            if ext == ".mdx":
                yield from chunk_markdown(strip_mdx(body), source=self.source, url=url)
            elif ext in _MARKDOWN_EXTS:
                yield from chunk_markdown(body, source=self.source, url=url)
            elif ext in _PROSE_EXTS:
                yield from chunk_text(body, source=self.source, title=rel, url=url)
            elif len(body.strip()) >= _MIN_CHUNK_CHARS:  # code: one chunk per file
                yield RawChunk(source=self.source, title=rel, url=url, content=body.strip())


class UserPdfLoader(CorpusLoader):
    """A user-uploaded PDF, already stored at `location`. One chunk-set per page (cited with a
    page number). `pypdf` is imported lazily so the rest of grounding works without it."""

    def __init__(self, *, source_key: str, pdf_path: str, label: str):
        self.source = source_key
        self._path = Path(pdf_path)
        self._label = label

    def ensure_local(self) -> Path:
        return self._path  # already on disk (uploaded); nothing to clone

    def load(self, local: Path) -> Iterator[RawChunk]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # optional dep - surfaced as an actionable ingest error
            raise RuntimeError(
                "PDF grounding needs the 'pypdf' package. Install it with: uv add pypdf"
            ) from exc
        reader = PdfReader(str(self._path))
        for i, pdf_page in enumerate(reader.pages, start=1):
            text = (pdf_page.extract_text() or "").strip()
            yield from chunk_text(
                text, source=self.source, title=f"{self._label} - p.{i}", url=self._label, page=i
            )


def loader_for_source(src) -> CorpusLoader:
    """Build the right loader for a `GroundingSource` row (kind picks the loader)."""
    if src.kind == "github":
        return UserGithubLoader(source_key=src.source_key, repo_url=src.location)
    if src.kind == "pdf":
        return UserPdfLoader(source_key=src.source_key, pdf_path=src.location, label=src.label)
    raise ValueError(f"unknown grounding source kind: {src.kind}")
