"""Grounding sources — "bring your own references".

Users add a public GitHub repo or upload a PDF; a worker clones/parses/chunks/embeds it into
the same Qdrant collection as the built-in corpora, so the tutor cites it like anything else.
Self-hosted → the user is the admin: they must ack (`license_ack`) that it runs on their
machine + key and that they have the right to use it.

Same durable-queue + LISTEN/NOTIFY-SSE shape as problem ingestion:
- `POST /grounding/sources/github` | `POST /grounding/sources/pdf` — create + enqueue.
- `GET /grounding/sources` — list (newest first).
- `DELETE /grounding/sources/{id}` — remove its Qdrant points + chunk rows (+ the stored PDF).
- `GET /grounding/sources/{id}/events` — live status SSE.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select

from app.config import get_settings
from app.database import async_session_maker
from app.dependencies import CurrentUser, DbSession
from app.errors import ApiError
from app.models import GroundingChunk, GroundingSource
from app.queue import (
    SOURCE_STATUS_CHANNEL,
    enqueue_source,
    notify_source_enqueued,
    pg_listen,
    wait_for_notification,
)
from app.schemas import GithubSourceCreate, GroundingSourceListOut, GroundingSourceOut
from app.vector_store import delete_source, get_qdrant

router = APIRouter(prefix="/grounding", tags=["grounding"])

_TERMINAL = {"ready", "failed"}
_MAX_PDF_BYTES = 25 * 1024 * 1024  # 25 MB — a generous single-book cap


def _slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:28]
    return s or "source"


async def _unique_source_key(db, label: str) -> str:
    """A stable, unique ≤32-char key from the label (GroundingChunk.source fits 32)."""
    base = _slug(label)
    existing = set(
        (await db.scalars(select(GroundingSource.source_key).where(
            GroundingSource.source_key.like(f"{base}%")
        ))).all()
    )
    if base not in existing:
        return base
    for n in range(2, 1000):
        candidate = f"{base[:28]}-{n}"
        if candidate not in existing:
            return candidate
    raise ApiError(409, "source_key_exhausted", "Too many sources with a similar name.")


def _uploads_dir() -> Path:
    d = Path(get_settings().corpora_dir) / "_uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/sources/github", response_model=GroundingSourceOut)
async def add_github_source(payload: GithubSourceCreate, user: CurrentUser, db: DbSession, response: Response):
    if not payload.license_ack:
        raise ApiError(400, "license_not_acknowledged", "You must confirm you may use this source.")
    if not re.match(r"^https?://github\.com/[\w.-]+/[\w.-]+/?$", payload.repo_url.strip()):
        raise ApiError(400, "invalid_repo_url", "Provide a public GitHub repo URL like https://github.com/owner/repo.")

    source_key = await _unique_source_key(db, payload.label)
    src = GroundingSource(
        kind="github",
        label=payload.label.strip(),
        source_key=source_key,
        location=payload.repo_url.strip(),
        status="pending",
        license_ack=True,
    )
    db.add(src)
    await enqueue_source(db, src)  # commits `pending`
    await db.refresh(src)
    await notify_source_enqueued(src.id)
    response.status_code = 202
    return GroundingSourceOut.model_validate(src)


@router.post("/sources/pdf", response_model=GroundingSourceOut)
async def add_pdf_source(
    user: CurrentUser,
    db: DbSession,
    response: Response,
    file: UploadFile = File(...),
    label: str = Form(...),
    license_ack: bool = Form(False),
):
    if not license_ack:
        raise ApiError(400, "license_not_acknowledged", "You must confirm you may use this source.")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise ApiError(400, "not_a_pdf", "Upload a .pdf file.")
    data = await file.read()
    if len(data) > _MAX_PDF_BYTES:
        raise ApiError(400, "pdf_too_large", "PDF exceeds the 25 MB limit.")

    source_key = await _unique_source_key(db, label)
    dest = _uploads_dir() / f"{source_key}.pdf"
    dest.write_bytes(data)

    src = GroundingSource(
        kind="pdf",
        label=label.strip(),
        source_key=source_key,
        location=str(dest),
        status="pending",
        license_ack=True,
    )
    db.add(src)
    await enqueue_source(db, src)
    await db.refresh(src)
    await notify_source_enqueued(src.id)
    response.status_code = 202
    return GroundingSourceOut.model_validate(src)


@router.post("/sources/{source_id}/retry", response_model=GroundingSourceOut)
async def retry_source(source_id: int, user: CurrentUser, db: DbSession, response: Response):
    """Retry now — skip a scheduled wait. For a source that `failed` or is `retrying` (e.g. a
    network blip, or you've freed up quota and don't want to wait for the daily reset). Re-queues
    immediately with a fresh error-retry budget; ingestion resumes from what's already embedded."""
    src = await db.get(GroundingSource, source_id)
    if src is None:
        raise ApiError(404, "source_not_found", "No grounding source with that id.")
    if src.status in ("pending", "cloning", "embedding"):
        raise ApiError(409, "already_active", "This source is already queued or processing.")

    src.attempts = 0  # a manual retry gets the full backoff budget again
    await enqueue_source(db, src)  # sets pending, clears error + retry_at, commits
    await db.refresh(src)
    await notify_source_enqueued(src.id)
    response.status_code = 202
    return GroundingSourceOut.model_validate(src)


@router.get("/sources", response_model=GroundingSourceListOut)
async def list_sources(user: CurrentUser, db: DbSession):
    rows = (
        await db.scalars(select(GroundingSource).order_by(GroundingSource.created_at.desc(), GroundingSource.id.desc()))
    ).all()
    return GroundingSourceListOut(items=[GroundingSourceOut.model_validate(r) for r in rows])


@router.delete("/sources/{source_id}", status_code=204)
async def delete_grounding_source(source_id: int, user: CurrentUser, db: DbSession):
    src = await db.get(GroundingSource, source_id)
    if src is None:
        raise ApiError(404, "source_not_found", "No grounding source with that id.")

    # Remove its vectors, then its chunk rows, then the stored PDF, then the row itself.
    await delete_source(get_qdrant(), src.source_key)
    await db.execute(delete(GroundingChunk).where(GroundingChunk.source == src.source_key))
    if src.kind == "pdf":
        with contextlib.suppress(OSError):
            Path(src.location).unlink()
    await db.delete(src)
    await db.commit()
    return Response(status_code=204)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.get("/sources/{source_id}/events")
async def source_events(source_id: int, user: CurrentUser, db: DbSession, request: Request):
    """Live grounding-source status as SSE — mirrors the problem-events endpoint."""
    src = await db.get(GroundingSource, source_id)
    if src is None:
        raise ApiError(404, "source_not_found", "No grounding source with that id.")
    return StreamingResponse(
        _status_stream(source_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _read_status(source_id: int) -> tuple[str, str | None, int]:
    async with async_session_maker() as db:
        row = await db.get(GroundingSource, source_id)
        if row is None:
            return "failed", "source removed", 0
        return row.status, row.error, row.chunk_count


async def _status_stream(source_id: int) -> AsyncIterator[str]:
    async with pg_listen(SOURCE_STATUS_CHANNEL) as conn:
        status, error, chunks = await _read_status(source_id)
        yield _sse({"type": "status", "status": status, "error": error, "chunk_count": chunks})
        if status in _TERMINAL:
            return
        while True:
            payloads = await wait_for_notification(conn, timeout=15.0)
            if payloads and str(source_id) not in payloads:
                continue
            new = await _read_status(source_id)
            if (new[0], new[1], new[2]) != (status, error, chunks):
                status, error, chunks = new
                yield _sse({"type": "status", "status": status, "error": error, "chunk_count": chunks})
            if status in _TERMINAL:
                return
            await asyncio.sleep(0)
