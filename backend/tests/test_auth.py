from httpx import AsyncClient


async def test_health(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_register(client: AsyncClient):
    resp = await client.post(
        "/register", json={"email": "new@example.com", "password": "secret-password"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "new@example.com"
    assert "hashed_password" not in body


async def test_register_duplicate_email(client: AsyncClient):
    payload = {"email": "dup@example.com", "password": "secret-password"}
    await client.post("/register", json=payload)
    resp = await client.post("/register", json=payload)
    assert resp.status_code == 409
    assert resp.json()["code"] == "email_already_registered"


async def test_register_short_password_rejected(client: AsyncClient):
    resp = await client.post("/register", json={"email": "a@example.com", "password": "short"})
    assert resp.status_code == 422


async def test_login_and_me(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "user@example.com"


async def test_login_wrong_password(client: AsyncClient):
    await client.post(
        "/register", json={"email": "user@example.com", "password": "secret-password"}
    )
    resp = await client.post(
        "/login", data={"username": "user@example.com", "password": "wrong-password"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_credentials"


async def test_login_unknown_user(client: AsyncClient):
    resp = await client.post(
        "/login", data={"username": "ghost@example.com", "password": "whatever-pass"}
    )
    assert resp.status_code == 401


async def test_me_requires_auth(client: AsyncClient):
    resp = await client.get("/me")
    assert resp.status_code == 401


async def test_me_rejects_garbage_token(client: AsyncClient):
    resp = await client.get("/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_token"


async def test_login_returns_token_pair(client: AsyncClient):
    await client.post("/register", json={"email": "pair@example.com", "password": "secret-password"})
    resp = await client.post(
        "/login", data={"username": "pair@example.com", "password": "secret-password"}
    )
    body = resp.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["access_token"] != body["refresh_token"]


async def test_refresh_returns_usable_access_token(client: AsyncClient):
    await client.post("/register", json={"email": "r@example.com", "password": "secret-password"})
    login = await client.post(
        "/login", data={"username": "r@example.com", "password": "secret-password"}
    )
    refresh_token = login.json()["refresh_token"]

    resp = await client.post("/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200

    me = await client.get("/me", headers={"Authorization": f"Bearer {resp.json()['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "r@example.com"


async def test_refresh_token_is_not_accepted_as_access_token(client: AsyncClient):
    """The `type` claim must be enforced, or sessions silently last the refresh window."""
    await client.post("/register", json={"email": "t@example.com", "password": "secret-password"})
    login = await client.post(
        "/login", data={"username": "t@example.com", "password": "secret-password"}
    )
    refresh_token = login.json()["refresh_token"]

    resp = await client.get("/me", headers={"Authorization": f"Bearer {refresh_token}"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_token"


async def test_access_token_is_not_accepted_as_refresh_token(client: AsyncClient):
    await client.post("/register", json={"email": "a@example.com", "password": "secret-password"})
    login = await client.post(
        "/login", data={"username": "a@example.com", "password": "secret-password"}
    )
    resp = await client.post("/refresh", json={"refresh_token": login.json()["access_token"]})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_refresh_token"


async def test_refresh_rejects_garbage(client: AsyncClient):
    resp = await client.post("/refresh", json={"refresh_token": "not-a-token"})
    assert resp.status_code == 401


async def test_cors_headers_present_for_allowed_origin(client: AsyncClient):
    resp = await client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
