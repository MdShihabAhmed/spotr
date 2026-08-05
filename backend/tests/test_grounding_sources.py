import pytest
from sqlalchemy import select

from app.models import GroundingChunk, GroundingSource
from app.queue import enqueue_source
from app.worker import drain_sources

REPO = "https://github.com/owner/repo"


@pytest.fixture
def enqueued(monkeypatch):
    """Record which source ids got enqueued (patching the NOTIFY seam, like `scheduled`)."""
    ids: list[int] = []

    async def fake(source_id: int):
        ids.append(source_id)

    monkeypatch.setattr("app.routers.grounding.notify_source_enqueued", fake)
    return ids


async def test_add_github_requires_auth(client):
    r = await client.post(
        "/grounding/sources/github",
        json={"label": "x", "repo_url": REPO, "license_ack": True},
    )
    assert r.status_code == 401


async def test_add_github_requires_license_ack(client, auth_headers, enqueued):
    r = await client.post(
        "/grounding/sources/github",
        json={"label": "My Notes", "repo_url": REPO, "license_ack": False},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["code"] == "license_not_acknowledged"
    assert enqueued == []


async def test_add_github_rejects_non_github_url(client, auth_headers, enqueued):
    r = await client.post(
        "/grounding/sources/github",
        json={"label": "x", "repo_url": "https://example.com/x", "license_ack": True},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_repo_url"


async def test_add_github_creates_enqueues_and_lists(client, auth_headers, enqueued):
    r = await client.post(
        "/grounding/sources/github",
        json={"label": "My Team Notes", "repo_url": REPO, "license_ack": True},
        headers=auth_headers,
    )
    assert r.status_code == 202
    body = r.json()
    assert body["kind"] == "github"
    assert body["source_key"] == "my-team-notes"  # slug of the label
    assert body["status"] == "pending"
    assert enqueued == [body["id"]]

    listed = await client.get("/grounding/sources", headers=auth_headers)
    assert [i["source_key"] for i in listed.json()["items"]] == ["my-team-notes"]


async def test_duplicate_labels_get_distinct_keys(client, auth_headers, enqueued):
    for _ in range(2):
        await client.post(
            "/grounding/sources/github",
            json={"label": "Notes", "repo_url": REPO, "license_ack": True},
            headers=auth_headers,
        )
    items = (await client.get("/grounding/sources", headers=auth_headers)).json()["items"]
    assert sorted(i["source_key"] for i in items) == ["notes", "notes-2"]


async def test_delete_removes_chunks_and_row(client, auth_headers, enqueued, db_session_maker, monkeypatch):
    # Don't dial Qdrant in a unit test — assert the Postgres side of the cleanup.
    async def fake_delete(_client, _source, **_kw):
        return None

    monkeypatch.setattr("app.routers.grounding.delete_source", fake_delete)

    created = (
        await client.post(
            "/grounding/sources/github",
            json={"label": "Notes", "repo_url": REPO, "license_ack": True},
            headers=auth_headers,
        )
    ).json()
    sid, key = created["id"], created["source_key"]
    async with db_session_maker() as db:
        db.add(GroundingChunk(source=key, title="t", url="u", content="content here", qdrant_point_id="p1"))
        await db.commit()

    r = await client.delete(f"/grounding/sources/{sid}", headers=auth_headers)
    assert r.status_code == 204
    async with db_session_maker() as db:
        assert (await db.scalars(select(GroundingChunk).where(GroundingChunk.source == key))).all() == []
        assert await db.get(GroundingSource, sid) is None


async def test_delete_unknown_source_404(client, auth_headers):
    r = await client.delete("/grounding/sources/9999", headers=auth_headers)
    assert r.status_code == 404


async def test_drain_sources_claims_and_processes(db_session_maker):
    async with db_session_maker() as db:
        src = GroundingSource(
            kind="github", label="R", source_key="r", location=REPO, status="pending", license_ack=True
        )
        db.add(src)
        await db.commit()
        sid = src.id

    processed: list[int] = []

    async def fake_process(source_id: int, *, session_maker):
        processed.append(source_id)
        async with session_maker() as db:
            row = await db.get(GroundingSource, source_id)
            row.status = "ready"
            await db.commit()

    n = await drain_sources(
        db_session_maker, process=fake_process, stuck_timeout_seconds=600, max_attempts=3
    )
    assert n == 1
    assert processed == [sid]
    async with db_session_maker() as db:
        assert (await db.get(GroundingSource, sid)).status == "ready"


class _DummyLoader:
    source = "x"

    def ensure_local(self):
        from pathlib import Path

        return Path(".")

    def load(self, local):
        return iter([])


async def _seed_claimed(db_session_maker, *, key, attempts):
    """A source in an in-flight state (as if just claimed), ready for ingest_source to run."""
    async with db_session_maker() as db:
        s = GroundingSource(
            kind="github", label="R", source_key=key, location=REPO,
            status="cloning", license_ack=True, attempts=attempts,
        )
        db.add(s)
        await db.commit()
        return s.id


async def test_ratelimit_schedules_retry_and_resets_attempts(db_session_maker, monkeypatch):
    from app.ingestion import source_ingest as si
    from app.llm import LLMRateLimitError

    sid = await _seed_claimed(db_session_maker, key="rl", attempts=3)
    monkeypatch.setattr(si, "loader_for_source", lambda src: _DummyLoader())

    async def boom(*a, **k):
        raise LLMRateLimitError("quota exhausted", retry_after=None)

    monkeypatch.setattr(si, "ingest_corpus", boom)
    await si.ingest_source(sid, session_maker=db_session_maker)

    async with db_session_maker() as db:
        row = await db.get(GroundingSource, sid)
        assert row.status == "retrying"
        assert row.retry_at is not None  # scheduled for a future daily-reset retry
        assert row.attempts == 0  # a quota wait doesn't count toward the error cap


async def test_generic_error_under_cap_retries(db_session_maker, monkeypatch):
    from app.ingestion import source_ingest as si

    sid = await _seed_claimed(db_session_maker, key="err", attempts=1)
    monkeypatch.setattr(si, "loader_for_source", lambda src: _DummyLoader())

    async def boom(*a, **k):
        raise ValueError("clone failed")

    monkeypatch.setattr(si, "ingest_corpus", boom)
    await si.ingest_source(sid, session_maker=db_session_maker)

    async with db_session_maker() as db:
        row = await db.get(GroundingSource, sid)
        assert row.status == "retrying"
        assert row.retry_at is not None


async def test_generic_error_at_cap_fails_terminally(db_session_maker, monkeypatch):
    from app.ingestion import source_ingest as si

    sid = await _seed_claimed(db_session_maker, key="dead", attempts=5)  # == source_max_error_attempts
    monkeypatch.setattr(si, "loader_for_source", lambda src: _DummyLoader())

    async def boom(*a, **k):
        raise ValueError("still broken")

    monkeypatch.setattr(si, "ingest_corpus", boom)
    await si.ingest_source(sid, session_maker=db_session_maker)

    async with db_session_maker() as db:
        row = await db.get(GroundingSource, sid)
        assert row.status == "failed"


async def test_claim_picks_due_retry_not_future(db_session_maker):
    from datetime import datetime, timedelta, timezone

    from app.queue import claim_next_source

    now = datetime.now(timezone.utc)
    async with db_session_maker() as db:
        due = GroundingSource(
            kind="github", label="D", source_key="due", location=REPO,
            status="retrying", retry_at=now - timedelta(minutes=1), license_ack=True,
        )
        future = GroundingSource(
            kind="github", label="F", source_key="fut", location=REPO,
            status="retrying", retry_at=now + timedelta(hours=1), license_ack=True,
        )
        db.add_all([due, future])
        await db.commit()
        due_id = due.id

    async with db_session_maker() as db:
        assert await claim_next_source(db) == due_id  # the due one
    async with db_session_maker() as db:
        assert await claim_next_source(db) is None  # the future one isn't eligible yet


async def test_enqueue_source_sets_pending(db_session_maker):
    async with db_session_maker() as db:
        src = GroundingSource(
            kind="github", label="R", source_key="r2", location=REPO, status="failed",
            error="boom", license_ack=True,
        )
        db.add(src)
        await db.commit()
        await enqueue_source(db, src)
        assert src.status == "pending"
        assert src.error is None
