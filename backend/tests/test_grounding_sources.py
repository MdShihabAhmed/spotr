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
