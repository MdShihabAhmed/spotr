import contextlib
import json

import pytest

from sqlalchemy import select

from app.models import Problem

PROBLEMSET_URL = "https://codeforces.com/problemset/problem/1846/C"
CONTEST_URL = "https://codeforces.com/contest/1846/problem/C"


@pytest.fixture
def scheduled(monkeypatch):
    """Record the ids the router enqueues for the worker. Patching the NOTIFY seam keeps
    router tests off Postgres (sqlite has no `pg_notify`) while still asserting *what* got
    queued - a job is enqueued by committing `pending` + firing this wake-up."""
    calls: list[int] = []

    async def fake_notify(problem_id: int):
        calls.append(problem_id)

    monkeypatch.setattr("app.routers.problems.notify_job_enqueued", fake_notify)
    return calls


async def test_ingest_requires_auth(client):
    r = await client.post("/problems/ingest", json={"url": PROBLEMSET_URL})
    assert r.status_code == 401


async def test_normalize_returns_structured_problem(client, auth_headers, monkeypatch):
    from app.normalize import NormalizedProblem

    async def fake_normalize(*, text=None, image_data_uris=None):
        assert text == "messy paste"
        return NormalizedProblem(
            statement="clean $$$n$$$",
            constraints="$$$1 \\le n$$$",
            samples=[{"input": "4", "expected_output": "2 2"}],
            note="$$$2+2=4$$$",
            time_limit_ms=2000,
            memory_limit_mb=256,
        )

    monkeypatch.setattr("app.routers.problems.normalize", fake_normalize)
    r = await client.post("/problems/normalize", json={"text": "messy paste"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["statement"] == "clean $$$n$$$"
    assert body["samples"] == [{"input": "4", "expected_output": "2 2"}]
    assert body["note"] == "$$$2+2=4$$$"
    assert body["time_limit_ms"] == 2000


async def test_normalize_empty_input_returns_400(client, auth_headers):
    r = await client.post("/problems/normalize", json={}, headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["code"] == "empty_input"


async def test_get_requires_auth(client):
    assert (await client.get("/problems/cf-1846C")).status_code == 401


async def test_ingest_new_returns_202_and_schedules(client, auth_headers, scheduled):
    r = await client.post(
        "/problems/ingest",
        json={"url": PROBLEMSET_URL, "editorial_text": "pasted editorial"},
        headers=auth_headers,
    )
    assert r.status_code == 202
    body = r.json()
    assert body["ingestion_status"] == "pending"
    assert body["slug"] == "cf-1846C"
    assert body["source"] == "codeforces"
    assert body["external_id"] == "1846C"
    # Raw editorial is never exposed; only whether one was captured.
    assert body["has_editorial"] is True
    assert "editorial_text" not in body
    assert scheduled == [body["id"]]


async def test_ingest_stores_note_and_figures(client, auth_headers, scheduled):
    r = await client.post(
        "/problems/ingest",
        json={
            "url": PROBLEMSET_URL,
            "note_text": "In sample 1, $$$2+2=4$$$.",
            "figures": [
                {"data_uri": "data:image/png;base64,AAAA", "section": "statement"},
                {"data_uri": "data:image/png;base64,BBBB", "section": "note"},
            ],
        },
        headers=auth_headers,
    )
    assert r.status_code == 202
    body = r.json()
    assert body["note_text"] == "In sample 1, $$$2+2=4$$$."
    assert [f["section"] for f in body["figures"]] == ["statement", "note"]


async def test_reingest_other_url_form_is_idempotent(client, auth_headers, scheduled):
    first = await client.post("/problems/ingest", json={"url": PROBLEMSET_URL}, headers=auth_headers)
    pid = first.json()["id"]
    # Same problem, the OTHER URL form - must return the existing row, not a dupe.
    second = await client.post("/problems/ingest", json={"url": CONTEST_URL}, headers=auth_headers)
    assert second.status_code == 200
    assert second.json()["id"] == pid
    assert scheduled == [pid]  # not scheduled again


async def test_reingest_ready_with_new_editorial_reprocesses(
    client, auth_headers, scheduled, db_session_maker
):
    # A problem that reached `ready` without an editorial must re-run when the editorial
    # link is supplied on a later re-ingest - not be a silent no-op.
    created = (
        await client.post("/problems/ingest", json={"url": PROBLEMSET_URL}, headers=auth_headers)
    ).json()
    pid = created["id"]
    async with db_session_maker() as db:
        problem = await db.get(Problem, pid)
        problem.ingestion_status = "ready"  # simulate a completed ingest with no editorial
        await db.commit()
    scheduled.clear()

    blog = "https://codeforces.com/blog/entry/155337"
    r = await client.post(
        "/problems/ingest",
        json={"url": PROBLEMSET_URL, "editorial_url": blog},
        headers=auth_headers,
    )
    assert r.status_code == 202
    assert r.json()["ingestion_status"] == "pending"
    assert scheduled == [pid]

    async with db_session_maker() as db:
        problem = await db.scalar(select(Problem).where(Problem.slug == "cf-1846C"))
        assert problem.editorial_url == blog


async def test_reingest_ready_without_new_info_stays_noop(
    client, auth_headers, scheduled, db_session_maker
):
    # A plain duplicate of a `ready` problem (nothing new) must remain a 200 no-op.
    created = (
        await client.post(
            "/problems/ingest",
            json={"url": PROBLEMSET_URL, "editorial_text": "already have it"},
            headers=auth_headers,
        )
    ).json()
    pid = created["id"]
    async with db_session_maker() as db:
        problem = await db.get(Problem, pid)
        problem.ingestion_status = "ready"
        await db.commit()
    scheduled.clear()

    r = await client.post("/problems/ingest", json={"url": PROBLEMSET_URL}, headers=auth_headers)
    assert r.status_code == 200
    assert scheduled == []


async def test_unknown_source_returns_400(client, auth_headers, scheduled):
    r = await client.post(
        "/problems/ingest", json={"source": "leetcode", "url": PROBLEMSET_URL}, headers=auth_headers
    )
    assert r.status_code == 400
    assert r.json()["code"] == "unknown_source"
    assert scheduled == []


async def test_invalid_url_returns_400(client, auth_headers, scheduled):
    r = await client.post(
        "/problems/ingest",
        json={"url": "https://codeforces.com/profile/tourist"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_problem_url"
    assert scheduled == []


async def test_get_unknown_returns_404(client, auth_headers):
    r = await client.get("/problems/cf-9999Z", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["code"] == "problem_not_found"


def _parse_sse(text: str) -> list[dict]:
    return [
        json.loads(line[len("data:") :].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


@contextlib.asynccontextmanager
async def _fake_listen(channel):
    yield None  # no real Postgres LISTEN in tests


async def _seed_problem(db_session_maker, *, slug, status, error=None) -> int:
    async with db_session_maker() as db:
        p = Problem(
            slug=slug, source="codeforces", external_id=slug.upper(),
            ingestion_status=status, ingestion_error=error,
        )
        db.add(p)
        await db.commit()
        await db.refresh(p)
        return p.id


async def test_events_requires_auth(client, db_session_maker):
    await _seed_problem(db_session_maker, slug="cf-1z", status="ready")
    assert (await client.get("/problems/cf-1z/events")).status_code == 401


async def test_events_unknown_problem_404(client, auth_headers):
    r = await client.get("/problems/cf-nope/events", headers=auth_headers)
    assert r.status_code == 404


async def test_events_terminal_status_emits_once_and_closes(
    client, auth_headers, db_session_maker, monkeypatch
):
    await _seed_problem(db_session_maker, slug="cf-rdy", status="ready")
    monkeypatch.setattr("app.routers.problems.pg_listen", _fake_listen)
    monkeypatch.setattr("app.routers.problems.async_session_maker", db_session_maker)

    r = await client.get("/problems/cf-rdy/events", headers=auth_headers)
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events == [{"type": "status", "status": "ready", "error": None}]


async def test_events_streams_transition_then_closes(
    client, auth_headers, db_session_maker, monkeypatch
):
    pid = await _seed_problem(db_session_maker, slug="cf-run", status="pending")
    monkeypatch.setattr("app.routers.problems.pg_listen", _fake_listen)
    monkeypatch.setattr("app.routers.problems.async_session_maker", db_session_maker)

    async def flip_then_notify(conn, *, timeout):
        # Simulate the worker finishing: flip the row, then deliver its NOTIFY payload.
        async with db_session_maker() as db:
            p = await db.get(Problem, pid)
            if p.ingestion_status == "pending":
                p.ingestion_status = "ready"
                await db.commit()
        return [str(pid)]

    monkeypatch.setattr("app.routers.problems.wait_for_notification", flip_then_notify)

    r = await client.get("/problems/cf-run/events", headers=auth_headers)
    events = _parse_sse(r.text)
    assert events[0] == {"type": "status", "status": "pending", "error": None}
    assert events[-1] == {"type": "status", "status": "ready", "error": None}


async def test_reingest_failed_retries_and_updates_editorial(
    client, auth_headers, scheduled, db_session_maker
):
    created = (
        await client.post("/problems/ingest", json={"url": PROBLEMSET_URL}, headers=auth_headers)
    ).json()
    pid = created["id"]

    async with db_session_maker() as db:
        problem = await db.get(Problem, pid)
        problem.ingestion_status = "failed"
        problem.ingestion_error = "editorial not found"
        await db.commit()
    scheduled.clear()

    # Retry, this time supplying the editorial that was missing.
    r = await client.post(
        "/problems/ingest",
        json={"url": PROBLEMSET_URL, "editorial_text": "now pasted"},
        headers=auth_headers,
    )
    assert r.status_code == 202
    assert r.json()["ingestion_status"] == "pending"
    assert scheduled == [pid]

    async with db_session_maker() as db:
        problem = await db.scalar(select(Problem).where(Problem.slug == "cf-1846C"))
        assert problem.editorial_text == "now pasted"
        assert problem.ingestion_error is None


async def test_list_requires_auth(client):
    assert (await client.get("/problems")).status_code == 401


async def test_list_newest_first_and_paginates(client, auth_headers, db_session_maker):
    for slug in ("cf-l1", "cf-l2", "cf-l3"):
        await _seed_problem(db_session_maker, slug=slug, status="ready")

    r = await client.get("/problems?page=1&page_size=2", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["page"] == 1 and body["page_size"] == 2
    # Newest first (created_at desc, id desc as tiebreak): cf-l3 leads.
    assert [i["slug"] for i in body["items"]] == ["cf-l3", "cf-l2"]

    r2 = await client.get("/problems?page=2&page_size=2", headers=auth_headers)
    assert [i["slug"] for i in r2.json()["items"]] == ["cf-l1"]
