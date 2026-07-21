from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from backend.app.config import get_settings

ACCESS = "access"
REFRESH = "refresh"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def _create_token(user_id: int, token_type: str, lifetime: timedelta) -> str:
    settings = get_settings()
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "exp": datetime.now(timezone.utc) + lifetime,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    return _create_token(user_id, ACCESS, timedelta(minutes=settings.access_token_expire_minutes))


def create_refresh_token(user_id: int) -> str:
    settings = get_settings()
    return _create_token(user_id, REFRESH, timedelta(days=settings.refresh_token_expire_days))


def decode_token(token: str, expected_type: str) -> int | None:
    """Return the user id from a valid token of `expected_type`, else None.

    The `type` claim is verified, not merely decoded: without that check a long-lived
    refresh token would be accepted as an access token, silently stretching session
    lifetime to the refresh window.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != expected_type:
            return None
        return int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None
