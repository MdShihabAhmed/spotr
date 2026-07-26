import pytest

from sqlalchemy import select

from app.models import Problem

PROBLEMSET_URL = "https://codeforces.com/problemset/problem/1846/C"
CONTEST_URL = "https://codeforces.com/contest/1846/problem/C"


@pytest.fixture
def scheduled(monkeypatch):
    """Replace background ingestion with a no-op recording scheduled ids, so router
    tests never touch the network or the real session maker."""
    calls: list[int] = []

    async def fake_run_ingestion(problem_id: int):
        calls.append(problem_id)

    monkeypatch.setattr("app.routers.problems.run_ingestion", fake_run_ingestion)
    return calls


async def test_ingest_requires_auth(client):
    r = await client.post("/problems/ingest", json={"url": PROBLEMSET_URL})
    assert r.status_code == 401


async def test_normalize_returns_structured_problem(client, auth_headers, monkeypatch):
    from app.normalize import NormalizedProblem

    async def fake_normalize(*, text=None, image_data_uri=None):
        assert text == "messy paste"
        return NormalizedProblem(
            statement="clean $$$n$$$",
            constraints="$$$1 \\le n$$$",
            samples=[{"input": "4", "expected_output": "2 2"}],
            time_limit_ms=2000,
            memory_limit_mb=256,
        )

    monkeypatch.setattr("app.routers.problems.normalize", fake_normalize)
    r = await client.post("/problems/normalize", json={"text": "messy paste"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["statement"] == "clean $$$n$$$"
    assert body["samples"] == [{"input": "4", "expected_output": "2 2"}]
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


async def test_reingest_other_url_form_is_idempotent(client, auth_headers, scheduled):
    first = await client.post("/problems/ingest", json={"url": PROBLEMSET_URL}, headers=auth_headers)
    pid = first.json()["id"]
    # Same problem, the OTHER URL form — must return the existing row, not a dupe.
    second = await client.post("/problems/ingest", json={"url": CONTEST_URL}, headers=auth_headers)
    assert second.status_code == 200
    assert second.json()["id"] == pid
    assert scheduled == [pid]  # not scheduled again


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
